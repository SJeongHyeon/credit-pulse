"""이미 잰 RAGAS 점수를 그 답의 평가 트레이스에 Langfuse 점수로 붙인다(G6 '평가 점수 연결', EVAL_REPORT 4-54).

RAGAS를 다시 재지 않는다(LLM 호출 없음). 점수 파일을 읽어 트레이스에 붙이기만 한다.

  python -X utf8 scripts/attach_ragas_scores.py            # 기본은 dry-run: 매칭 표와 붙일 점수만 출력
  python -X utf8 scripts/attach_ragas_scores.py --apply    # 실제로 보낸다

- 트레이스 찾기: 평가 러너(`run_graph_eval.py`)는 문항마다 트레이스 하나를 만들고 session_id와 태그에 회차 이름을
  넣는다. 이 회차의 트레이스를 모두 읽어, 트레이스의 input.question을 평가셋 질문과 글자 그대로 맞춰
  문항 id → trace_id를 만든다(결과 jsonl에는 trace_id가 없다).
- 같은 질문의 트레이스가 2개 이상이거나 못 찾은 문항이 있으면 목록을 출력하고 멈춘다(추측으로 고르지 않는다).
- 같은 이름의 점수가 이미 붙어 있으면 건너뛴다(두 번 돌려도 중복이 생기지 않는다).

Langfuse 읽기 API(SDK 5.1.0의 코드로 확인): 이 버전의 `api.trace`에는 목록 조회가 없다(삭제만). 읽기는
`api.observations.get_many`(v2, cursor 페이지 넘김)와 `api.scores.get_many`(v3)로 한다. 트레이스의 태그는 관측의
`trace_context` 필드 묶음에 있고 서버 쪽 태그 필터 인자는 없어, session_id로 거른 뒤 태그를 코드에서 확인한다.
키는 `.env`의 LANGFUSE_PUBLIC_KEY·LANGFUSE_SECRET_KEY를 쓴다(출력하지 않는다).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_RUN = "w146_now_r1_multi_d3-55_v9_v2"
DEFAULT_RAGAS = "evals/results/ragas/ragas_w146_now_r1.jsonl"
DEFAULT_DATASET = "evals/dataset_v2.jsonl"
# ragas 결과 파일의 칸 → Langfuse 점수 이름
SCORE_NAMES = {"faithfulness": "ragas_faithfulness", "answer_relevancy": "ragas_answer_relevancy"}
SCORE_COMMENT = "ragas 0.4.3, judge openai/gpt-4.1, 4-54"
PAGE_LIMIT = 100


class MatchError(Exception):
    """질문과 트레이스를 하나씩 맞추지 못했다. 메시지에 문항 목록이 들어 있다."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def trace_question(raw_input: Any) -> str | None:
    """관측의 input에서 질문을 꺼낸다. v2 API는 input을 JSON 문자열로 돌려준다."""

    value = raw_input
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if isinstance(value, dict) and isinstance(value.get("question"), str):
        return value["question"]
    return None


def match_traces(dataset: list[dict[str, Any]], traces: list[dict[str, Any]]) -> dict[int, str]:
    """문항 id → trace_id. 질문을 글자 그대로 맞춘다.

    traces: [{"trace_id", "question"}]. 문항마다 트레이스가 정확히 하나여야 한다. 없거나 둘 이상이면 MatchError.
    """

    by_question: dict[str, list[str]] = {}
    for trace in traces:
        if trace.get("question") is not None:
            by_question.setdefault(str(trace["question"]), []).append(str(trace["trace_id"]))
    matched: dict[int, str] = {}
    missing: list[int] = []
    duplicated: list[tuple[int, list[str]]] = []
    for row in dataset:
        row_id, found = int(row["id"]), by_question.get(str(row["input"]), [])
        if not found:
            missing.append(row_id)
        elif len(set(found)) > 1:
            duplicated.append((row_id, sorted(set(found))))
        else:
            matched[row_id] = found[0]
    if missing or duplicated:
        lines = []
        if missing:
            lines.append(f"트레이스를 찾지 못한 문항 {len(missing)}건: {missing}")
        for row_id, ids in duplicated:
            lines.append(f"같은 질문의 트레이스가 {len(ids)}개인 문항 {row_id}: {ids}")
        raise MatchError("\n".join(lines))
    return matched


def planned_scores(ragas_rows: list[dict[str, Any]], trace_ids: dict[int, str],
                   existing: dict[str, set[str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(붙일 점수, 건너뛸 점수). existing: trace_id → 이미 붙어 있는 점수 이름들.

    값이 없는(None) 칸은 붙이지 않는다. ragas 파일에 있는데 트레이스가 없는 문항은 MatchError.
    """

    unknown = [int(row["id"]) for row in ragas_rows if int(row["id"]) not in trace_ids]
    if unknown:
        raise MatchError(f"RAGAS 점수는 있는데 트레이스가 없는 문항: {unknown}")
    send, skip = [], []
    for row in ragas_rows:
        row_id = int(row["id"])
        trace_id = trace_ids[row_id]
        for column, name in SCORE_NAMES.items():
            if row.get(column) is None:
                continue
            item = {"id": row_id, "trace_id": trace_id, "name": name, "value": float(row[column])}
            (skip if name in existing.get(trace_id, set()) else send).append(item)
    return send, skip


def fetch_traces(client: Any, run: str) -> list[dict[str, Any]]:
    """회차의 평가 트레이스(문항당 하나)를 모두 읽는다. 루트 관측의 input이 트레이스의 input이다."""

    traces, cursor = [], None
    while True:
        page = client.api.observations.get_many(
            session_id=run, is_root_observation=True, fields="core,basic,io,trace_context", limit=PAGE_LIMIT, cursor=cursor)
        for observation in page.data:
            tags = list(getattr(observation, "tags", None) or [])
            if "eval" not in tags or run not in tags:
                continue  # 같은 session_id를 쓴 다른 종류의 트레이스는 세지 않는다
            traces.append({"trace_id": str(observation.trace_id), "question": trace_question(observation.input)})
        cursor = getattr(page.meta, "cursor", None)
        if not cursor:
            return traces


def fetch_score_names(client: Any, trace_id: str) -> list[str]:
    """트레이스에 붙어 있는 점수 이름(붙은 순서와 무관)."""

    names, cursor = [], None
    while True:
        page = client.api.scores.get_many(trace_id=trace_id, limit=PAGE_LIMIT, cursor=cursor)
        names.extend(str(score.name) for score in page.data)
        cursor = getattr(page.meta, "cursor", None)
        if not cursor:
            return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", default=DEFAULT_RUN, help="회차 이름(평가 트레이스의 session_id이자 태그)")
    parser.add_argument("--ragas", default=DEFAULT_RAGAS, help="RAGAS 문항별 결과 JSONL(id, faithfulness, answer_relevancy)")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--apply", action="store_true", help="실제로 점수를 보낸다(없으면 출력만)")
    parser.add_argument("--verify-trace", help="끝난 뒤 이 trace_id에 붙은 점수 이름을 다시 읽어 출력한다")
    args = parser.parse_args(argv)

    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True), override=False)
    from backend.services import tracing

    if not tracing.enabled():
        raise SystemExit("Langfuse 키가 없습니다(LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY)")
    client = tracing._langfuse()
    dataset = _read_jsonl(ROOT / args.dataset)
    ragas_rows = _read_jsonl(ROOT / args.ragas)
    traces = fetch_traces(client, args.run)
    print(f"회차 {args.run}: 트레이스 {len(traces)}건, 평가셋 {len(dataset)}문항, RAGAS 점수 {len(ragas_rows)}문항")
    try:
        trace_ids = match_traces(dataset, traces)
    except MatchError as exc:
        print(str(exc))
        print("멈춥니다. 추측으로 고르지 않습니다.")
        return 1
    print(f"질문 매칭: {len(trace_ids)}건(문항마다 트레이스 하나)")
    scored_ids = sorted(int(row["id"]) for row in ragas_rows)
    existing = {trace_ids[row_id]: set(fetch_score_names(client, trace_ids[row_id])) for row_id in scored_ids}
    try:
        send, skip = planned_scores(ragas_rows, trace_ids, existing)
    except MatchError as exc:
        print(str(exc))
        return 1
    print("id | trace_id | 이미 있는 점수 | 붙일 점수")
    for row_id in scored_ids:
        mine = [f"{item['name']}={item['value']:.4f}" for item in send if item["id"] == row_id]
        print(f"{row_id} | {trace_ids[row_id]} | {sorted(existing[trace_ids[row_id]])} | {mine}")
    print(f"붙일 점수 {len(send)}개, 이미 있어 건너뛸 점수 {len(skip)}개, RAGAS 대상이 아닌 문항 {len(dataset) - len(scored_ids)}건(거절·되묻기)")
    if not args.apply:
        print("dry-run입니다. 보내려면 --apply를 붙이세요.")
        return 0
    for item in send:
        tracing.score(item["trace_id"], item["name"], item["value"], comment=SCORE_COMMENT)
    tracing.flush()
    print(f"보냄: 점수 {len(send)}개(comment: {SCORE_COMMENT})")
    if args.verify_trace:
        import time

        names: list[str] = []
        for _ in range(12):  # 점수 조회는 반영이 조금 늦을 수 있어 잠깐 기다리며 다시 읽는다
            names = fetch_score_names(client, args.verify_trace)
            if all(name in names for name in SCORE_NAMES.values()):
                break
            time.sleep(5)
        print(f"확인 {args.verify_trace}: 점수 {len(names)}개 {sorted(names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
