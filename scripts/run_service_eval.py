"""배포된 서비스(HTTP API)에 평가 질문을 보내 응답 시간과 응답 지표를 잰다.

  $env:SERVICE_EVAL_EMAIL = "<평가용 계정 이메일>"; $env:SERVICE_EVAL_PASSWORD = "<비밀번호>"   # 저장소·명령 기록에 남기지 않는다
  python -X utf8 scripts/run_service_eval.py --base-url https://<배포 주소> --output evals/results/service/svc_r1.jsonl

`scripts/run_graph_eval.py`는 그래프를 이 컴퓨터에서 직접 돌린다(메모리 색인, DB·인증·한도 없음). 이 스크립트는 실제
서비스 경로(로그인 → 심사 건 만들기 → 질의 → 승인 대기 취소)를 거치므로 pgvector 검색·DB 저장·한도 검사가 포함된다.
유료다(질문마다 서비스가 LLM·리랭크를 부른다. 질문당 약 $0.004~0.006).

보낼 수 있는 문항: 서비스는 심사 건이 회사·연도 하나에 묶여 있어, Supervisor 필터 파일에 회사가 정해진 문항만 보낸다
(v2 50건 중 34건: 관련 24, 없는 항목 6, 주제 밖 4). 코퍼스 밖 회사·기간, 회사 이름이 없는 질문 16건은 심사 건을
만들 수 없어 뺀다. 그래서 여기서 나온 지표의 분모는 로컬 평가(25·25)와 다르다.

--direct(11/13, 바로 질문 API): 심사 건을 먼저 만들지 않고 질문만 `POST /api/v1/queries`로 보낸다. 서비스가 질문에서
회사·연도를 찾으므로 평가셋 50건을 모두 보낼 수 있고 분모가 로컬 평가와 같아진다(관련 25·비관련 25).
하루 한도가 50회라 --warmup과 함께 쓰려면 --ids로 줄인다.

--clarifications(11/17, 162번): 첫 응답이 되묻기(회사 이름 없음)인 문항에 되묻기 답을 한 번 더 보낸다(로컬 평가의
`run_graph_eval.py --clarifications`와 같은 파일). 50문항 + 두 번째 턴 1회 = 51회라 계정 하나의 하루 한도를 넘는다.
두 번째 계정을 SERVICE_EVAL_EMAIL_2·SERVICE_EVAL_PASSWORD_2에 넣으면 두 번째 턴만 그 계정으로 보낸다(서비스는
대화를 기억하지 않고 원래 질문이 요청에 실려 가므로 계정이 달라도 같은 처리다).
  python -X utf8 scripts/run_service_eval.py --base-url <주소> --direct --clarifications evals/mappings/dataset_v2.clarifications.jsonl --output ...

한도: 사용자당 분당 5회·하루 50회라 질문 사이를 12.5초 띄운다(34건에 약 8분). 계정 하나로 하루 한 번만 돌릴 수 있다.
첫 질문에는 인스턴스·DB 연결이 처음 준비되는 시간이 섞인다(11/11 측정: 1번 12.7초). --warmup을 주면 재기 전에 한 번 데운다.
문항마다 심사 건을 새로 만들고, 승인 대기로 끝나면 취소해서 대기 중인 승인을 남기지 않는다(심사 건 행은 DB에 남는다).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
Send = Callable[[str, str, dict | None], tuple[int, Any]]


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def http_sender(base_url: str, timeout: float) -> Send:
    """쿠키를 들고 다니는 JSON 요청 함수. (상태 코드, 본문) 을 돌려주고 4xx·5xx도 예외 없이 돌려준다."""

    parsed = urlparse(base_url)
    if parsed.scheme != "https" and parsed.hostname not in LOCAL_HOSTS:
        raise SystemExit("--base-url은 https여야 합니다(로컬 주소만 http 허용). 로그인 정보를 평문으로 보내지 않습니다")
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    origin = base_url.rstrip("/")

    def send(method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(origin + path, data=data, method=method,
                                         headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with opener.open(request, timeout=timeout) as response:
                raw, status = response.read(), response.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read(), exc.code
        try:
            return status, json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            return status, None

    return send


def error_code(payload: Any) -> str | None:
    """오류 응답에서 코드만 꺼낸다(형식이 달라도 죽지 않게)."""

    if not isinstance(payload, dict):
        return None
    inner = payload.get("error") if isinstance(payload.get("error"), dict) else payload
    code = inner.get("code") or payload.get("code")
    return str(code) if code else None


def service_cases(dataset: list[dict], filters: list[dict], ids: set[int] | None = None) -> tuple[list[dict], list[int]]:
    """서비스로 보낼 수 있는 문항(회사·연도가 정해진 것)과 뺀 문항 id."""

    targets = {int(row["id"]): row for row in filters}
    cases, skipped = [], []
    for row in dataset:
        row_id = int(row["id"])
        if ids is not None and row_id not in ids:
            continue
        target = targets.get(row_id) or {}
        if not target.get("corp_code") or not target.get("fiscal_year"):
            skipped.append(row_id)
            continue
        cases.append({"id": row_id, "question": row["input"], "kind": row["kind"],
                      "corp_code": target["corp_code"], "fiscal_year": int(target["fiscal_year"])})
    return cases, skipped


def direct_cases(dataset: list[dict], ids: set[int] | None = None) -> list[dict]:
    """바로 질문으로 보낼 문항: 평가셋 전부(회사·연도는 서비스가 질문에서 찾는다)."""

    return [{"id": int(row["id"]), "question": row["input"], "kind": row["kind"]}
            for row in dataset if ids is None or int(row["id"]) in ids]


def ask_one(send: Send, case: dict, *, cancel: bool, sleep: Callable[[float], None] = time.sleep,
            clock: Callable[[], float] = time.perf_counter, rate_limit_wait: float = 61.0, direct: bool = False,
            clarify: str | None = None, clarify_send: Send | None = None, clarify_pause: float = 0.0) -> dict:
    """문항 하나: 심사 건 만들기 → 질의(시간 측정) → 승인 대기면 취소. 실패해도 예외 없이 기록으로 돌려준다.

    direct=True면 심사 건을 만들지 않고 질문만 보낸다(POST /api/v1/queries). 서비스가 만든 심사 건 id는 응답에서 읽는다.

    clarify(162번): 첫 응답이 되묻기(target_required)면 이 답을 원래 질문과 함께(`clarifies`) 한 번 더 보낸다. 서비스가
    둘을 합쳐 다시 처리한다. clarify_send를 주면 두 번째 요청을 그 세션(다른 계정)으로 보낸다: 하루 한도가 계정당 50회라
    50문항 + 되묻기 답 1회는 계정 하나로 보낼 수 없다. 첫 턴은 `first_turn`에 남기고 seconds는 두 턴의 합이다.
    """

    record: dict[str, Any] = {"id": case["id"], "kind": case["kind"], "corp_code": case.get("corp_code"), "status_code": None,
                              "error_code": None, "seconds": None, "outcome": None, "refusal_reason": None,
                              "citations": [], "draft_items": [], "review_id": None, "rate_limited_retries": 0,
                              "cancelled": None}
    if direct:
        path = "/api/v1/queries"
    else:
        status, body = send("POST", "/api/v1/reviews", {"corp_query": case["corp_code"], "fiscal_year": case["fiscal_year"]})
        if status != 201 or not isinstance(body, dict):
            return {**record, "status_code": status, "error_code": error_code(body) or "REVIEW_CREATE_FAILED"}
        record["review_id"] = body.get("id")
        path = f"/api/v1/reviews/{record['review_id']}/queries"
    for attempt in range(3):  # 분당 한도(429)에 걸리면 기다렸다가 다시(상한 2번)
        started = clock()
        status, body = send("POST", path, {"question": case["question"]})
        seconds = clock() - started
        if status != 429 or attempt == 2:
            break
        record["rate_limited_retries"] += 1
        sleep(rate_limit_wait)
    record.update({"status_code": status, "seconds": round(seconds, 3)})
    if status != 200 or not isinstance(body, dict):
        return {**record, "error_code": error_code(body) or f"HTTP_{status}"}
    if direct and clarify and body.get("target_required") is True:
        record["first_turn"] = {"status_code": status, "seconds": round(seconds, 3), "outcome": body.get("outcome"),
                                "target_required": True, "reply": clarify,
                                "account": "second" if clarify_send is not None else "same"}
        if clarify_pause:
            sleep(clarify_pause)  # 같은 계정이면 분당 한도 때문에 띄운다
        started = clock()
        status, body = (clarify_send or send)("POST", path, {"question": clarify, "clarifies": case["question"]})
        seconds += clock() - started
        record.update({"status_code": status, "seconds": round(seconds, 3)})
        if status != 200 or not isinstance(body, dict):
            return {**record, "error_code": error_code(body) or f"HTTP_{status}"}
        send = clarify_send or send  # 두 번째 턴이 만든 심사 건의 취소도 그 계정으로 한다
    if direct:
        review = body.get("review") if isinstance(body.get("review"), dict) else {}
        record["review_id"] = review.get("id")
    draft = [item for item in body.get("draft") or [] if isinstance(item, dict)]
    record.update({
        "outcome": body.get("outcome"), "refusal_reason": body.get("refusal_reason"), "draft_items": draft,
        "citations": [{"chunk_id": c.get("chunk_id")} for item in draft for c in item.get("citations") or []
                      if isinstance(c, dict) and c.get("chunk_id")],
    })
    if cancel and body.get("outcome") == "awaiting_approval" and record["review_id"]:
        cancel_status, _ = send("POST", f"/api/v1/reviews/{record['review_id']}/approvals:cancel", None)
        record["cancelled"] = cancel_status == 200
    return record


def pct(values: list[float], q: float) -> float:
    """가까운 순위 방식: 정렬한 뒤 round(q × (n − 1))번째 값(run_graph_eval·D8 도표와 같은 방식)."""

    values = sorted(values)
    return values[min(len(values) - 1, round(q * (len(values) - 1)))]


def summarize(records: list[dict], gold_rows: dict[int, dict], searched_ids: set[int] | None) -> dict:
    """응답 지표(보낸 문항 기준)와 응답 시간. 실패한 문항은 지표에서 빼고 따로 센다."""

    from evals.response_metrics import per_question_flags

    done = [r for r in records if r["status_code"] == 200]
    totals: dict[str, list[int]] = {}
    for record in done:
        for name, value in per_question_flags(gold_rows[record["id"]], record).items():
            totals.setdefault(name, []).append(int(value))
    seconds = [r["seconds"] for r in done]
    timed = [r["seconds"] for r in done if searched_ids is None or r["id"] in searched_ids]
    cited = {c["chunk_id"] for r in done for c in r["citations"]}
    known = {chunk for row in gold_rows.values() for chunk in row.get("gold_chunks") or []}

    def spread(values: list[float]) -> dict | None:
        if not values:
            return None
        return {"n": len(values), "p50": round(statistics.median(values), 2), "p95": round(pct(values, 0.95), 2),
                "mean": round(statistics.mean(values), 2), "max": round(max(values), 2)}

    return {
        "sent": len(records), "ok": len(done),
        "failed": [{"id": r["id"], "status_code": r["status_code"], "error_code": r["error_code"]} for r in records if r["status_code"] != 200],
        "response": {name: {"numerator": sum(values), "denominator": len(values)} for name, values in sorted(totals.items())},
        "seconds_all": spread(seconds),
        "seconds_searched": spread(timed),  # 로컬 기준 실행에서 검색까지 간 문항만(로컬 수치와 같은 모집단)
        "first_query_seconds": done[0]["seconds"] if done else None,  # 콜드 스타트가 섞일 수 있어 따로 적는다
        "rate_limited_retries": sum(r["rate_limited_retries"] for r in records),
        "not_cancelled": [r["id"] for r in records if r["cancelled"] is False],
        "citation_ids_overlap_gold": bool(cited & known),  # False면 서비스 코퍼스의 청크 id가 평가 정답과 다른 것이다
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True, help="서비스 주소(예: https://...run.app). 로컬 주소만 http 허용")
    parser.add_argument("--output", required=True, help="문항별 결과 JSONL. 요약은 같은 이름의 .summary.json")
    parser.add_argument("--dataset", default="evals/dataset_v2.jsonl")
    parser.add_argument("--filters", default="evals/filters_v2.graph.domain.jsonl")
    parser.add_argument("--chunks", default="data/experiments/b100-v9-fixed_window/chunks.jsonl", help="정답 청크를 만들 청크 파일")
    parser.add_argument("--reference", default="evals/results/graph50/w122_final_r1_multi_d3-55_v9_v2.jsonl",
                        help="로컬 기준 실행. 여기서 검색까지 간 문항만 '검색 문항 시간'에 넣는다")
    parser.add_argument("--ids", help="이 문항 id만(쉼표 구분)")
    parser.add_argument("--pace-seconds", type=float, default=12.5, help="질문 사이 간격(분당 5회 한도)")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--no-cancel", action="store_true", help="승인 대기를 취소하지 않고 남긴다")
    parser.add_argument("--warmup", action="store_true",
                        help="재기 전에 첫 문항을 한 번 보내 인스턴스·연결을 데운다(지표·시간에 넣지 않고 warmup_seconds로만 기록, 하루 횟수 1회 사용)")
    parser.add_argument("--clarifications",
                        help="(162번, --direct 전용) 문항별 되묻기 답 JSONL({id, reply}). 첫 응답이 되묻기인 문항에만 두 번째 턴을 보낸다. "
                             "SERVICE_EVAL_EMAIL_2·SERVICE_EVAL_PASSWORD_2가 있으면 두 번째 턴을 그 계정으로 보낸다")
    parser.add_argument("--direct", action="store_true",
                        help="심사 건을 만들지 않고 질문만 보낸다(POST /api/v1/queries). 평가셋 50건을 모두 보낼 수 있다")
    args = parser.parse_args()

    email, password = os.environ.get("SERVICE_EVAL_EMAIL"), os.environ.get("SERVICE_EVAL_PASSWORD")
    if not email or not password:
        parser.error("환경변수 SERVICE_EVAL_EMAIL, SERVICE_EVAL_PASSWORD가 필요합니다(값은 출력·저장하지 않습니다)")

    from scripts.run_graph_eval import load_span_chunks, span_gold_rows

    dataset = _read_jsonl(ROOT / args.dataset)
    for row in dataset:
        row["id"] = int(row["id"])
    ids = {int(v) for v in args.ids.split(",")} if args.ids else None
    if args.direct:
        cases, skipped = direct_cases(dataset, ids), []
    else:
        cases, skipped = service_cases(dataset, _read_jsonl(ROOT / args.filters), ids)
    gold_rows = {int(r["id"]): r for r in span_gold_rows(dataset, load_span_chunks(dataset, ROOT / args.chunks))}
    reference = ROOT / args.reference
    searched_ids = ({int(r["id"]) for r in _read_jsonl(reference) if int(r.get("search_calls") or 0) > 0}
                    if reference.exists() else None)
    clarifications: dict[int, str] = {}
    if args.clarifications:
        if not args.direct:
            parser.error("--clarifications는 --direct에서만 씁니다")
        started_ids = {case["id"] for case in cases}
        clarifications = {int(row["id"]): str(row["reply"]) for row in _read_jsonl(ROOT / args.clarifications)
                          if int(row["id"]) in started_ids}
    email_2, password_2 = os.environ.get("SERVICE_EVAL_EMAIL_2"), os.environ.get("SERVICE_EVAL_PASSWORD_2")
    second_account = bool(clarifications and email_2 and password_2)
    if len(cases) + int(args.warmup) + (0 if second_account else len(clarifications)) > 50:
        parser.error("사용자당 하루 50회 한도를 넘습니다. --ids로 줄이거나, 되묻기 답을 보낼 두 번째 계정을 "
                     "SERVICE_EVAL_EMAIL_2·SERVICE_EVAL_PASSWORD_2에 넣으세요")

    send = http_sender(args.base_url, args.timeout)
    status, _ = send("GET", "/health", None)
    print(f"/health {status} | 보낼 문항 {len(cases)}건, 뺀 문항 {len(skipped)}건(회사·연도가 정해지지 않음), 예상 {len(cases) * args.pace_seconds / 60:.0f}분")
    status, body = send("POST", "/api/v1/auth/login", {"email": email, "password": password})
    if status != 204:
        raise SystemExit(f"로그인 실패: HTTP {status} {error_code(body) or ''}")
    send_2 = None
    if second_account:
        send_2 = http_sender(args.base_url, args.timeout)
        status, body = send_2("POST", "/api/v1/auth/login", {"email": email_2, "password": password_2})
        if status != 204:
            raise SystemExit(f"두 번째 계정 로그인 실패: HTTP {status} {error_code(body) or ''}")

    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    warmup_seconds = None
    if args.warmup and cases:
        warm = ask_one(send, cases[0], cancel=True, direct=args.direct)  # 결과는 쓰지 않는다. 승인 대기는 남기지 않는다
        warmup_seconds = warm["seconds"]
        print(f"  데우기 id={warm['id']} HTTP {warm['status_code']} {warm['seconds']}초(지표에 넣지 않음)")
        time.sleep(args.pace_seconds)
    records = []
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for index, case in enumerate(cases):
            if index:
                time.sleep(args.pace_seconds)
            record = ask_one(send, case, cancel=not args.no_cancel, direct=args.direct,
                             clarify=clarifications.get(case["id"]), clarify_send=send_2,
                             clarify_pause=0.0 if send_2 is not None else args.pace_seconds)
            records.append(record)
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            print(f"  {index + 1}/{len(cases)} id={record['id']} HTTP {record['status_code']} {record['outcome'] or record['error_code']}"
                  f" {record['seconds'] if record['seconds'] is not None else '-'}초")
            if record["status_code"] == 429:
                # 기다렸다 다시 보내도 429면 분당 한도가 아니라 하루 한도다(11/11 2차 측정: 남은 19건이 전부 429로 2분씩 걸렸다)
                print(f"  한도 초과(429)가 풀리지 않아 멈춥니다. 남은 {len(cases) - index - 1}건은 보내지 않았습니다")
                break
    send("POST", "/api/v1/auth/logout", None)
    if send_2 is not None:
        send_2("POST", "/api/v1/auth/logout", None)

    summary = {"base_url_host": urlparse(args.base_url).hostname, "direct": bool(args.direct), "started_ids": [c["id"] for c in cases], "skipped_ids": skipped,
               "reference": args.reference if searched_ids is not None else None, "warmup_seconds": warmup_seconds,
               # 162번: 되묻기 답으로 두 번째 턴을 보낸 문항과, 그 턴을 보낸 계정(same·second)
               "clarified_ids": [r["id"] for r in records if r.get("first_turn")],
               "clarification_account": ("second" if second_account else "same") if clarifications else None,
               "stopped_on_rate_limit": bool(records) and records[-1]["status_code"] == 429 and len(records) < len(cases),
               **summarize(records, gold_rows, searched_ids)}
    output.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps({k: summary[k] for k in ("sent", "ok", "failed", "response", "seconds_searched", "seconds_all",
                                              "first_query_seconds", "citation_ids_overlap_gold", "clarified_ids")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
