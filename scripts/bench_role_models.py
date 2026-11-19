"""역할별 모델 단독 비교(122번): 주제 분류와 Supervisor 판단 호출만 후보 모델에 보내 정확도·시간·비용을 잰다.

  python -X utf8 scripts/bench_role_models.py --role domain --models openai/gpt-4o-mini,openai/gpt-4.1-nano --output evals/results/role_bench/domain.jsonl
  python -X utf8 scripts/bench_role_models.py --role supervisor --models ... --source evals/results/graph50/w121_new_r1_multi_d3-55_v9_v2.jsonl --output ...

그래프 전체를 돌리지 않는다. LLM 호출은 서비스가 쓰는 `backend.services.llm`의 함수(classify_domain, decide_next)를
모델만 바꿔 그대로 부른다(지시문·이중 요청·형식 재시도 포함). 유료 호출이다(호출당 수백 토큰).

- domain: 평가셋 질문 전부. 기대값은 평가셋의 질문 종류에서 온다(relevant는 out_of_domain이 아니어야 하고, 비관련 중
  irrelevant_type이 out_of_domain이면 out_of_domain). 나머지 비관련(코퍼스 밖 회사·기간, 없는 항목)은 채점하지 않는다.
  필터 파일(evals/filters_v2.graph.domain.jsonl)은 코드 규칙의 결과만 담고 있어 기대값으로 쓸 수 없다(122번 첫 실행에서 확인).
- supervisor: 저장된 실행에서 Supervisor LLM이 실제로 불린 방문(첫 검색 뒤)의 입력을 다시 만든다. 기대값은 그 실행에서
  지금 모델이 고른 값과, 지시문 규칙(검색 보고가 sufficient면 writer_agent)이다.
- grader-suff(135번): '충분·부족'에 정답이 있는 비교. 관련 문항의 첫 검색 결과 그대로(정답 청크 있음 → 충분이 정답)와
  거기서 정답 청크만 뺀 것(→ 부족이 정답)을 한 쌍으로 준다. 123번은 '충분하다고 한 비율'을 점수로 쳤는데 실제로 충분한지의
  정답이 없었다.
- supervisor-mix(136번): 정답이 한 가지가 아닌 비교. 같은 문항으로 네 상황을 만든다: 검색 충분(정답 작성), 검색 부족 + 검색
  남음(정답 재검색), 검색 부족 + 검색 못 함(정답 작성), 초안 검증 통과(정답 승인). 122번 비교셋은 정답이 모두 작성이었다.
  부족 상황의 빠진 항목은 135번에서 채택 채점 모델이 실제로 낸 것을 쓴다(--suff-results).
- grader(123번): 저장된 실행의 첫 검색 결과(청크 10개, 비교 질문은 회사별 10개씩)를 그대로 `llm.grade_search_round`에 준다.
  정답은 평가셋의 정답 청크(gold_chunks)다. 관련 문항은 '정답 청크를 골랐는가·몇 개 골랐는가·충분하다고 했는가'를,
  없는 항목 문항(정답 청크 없음)은 '부족하다고 했는가'를 본다. 리랭커 순위만으로 고른 결과(상위 k개)를 비교용으로 함께 낸다.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def domain_want(row: dict) -> str:
    if row.get("kind") == "relevant":
        return "not_out_of_domain"
    if row.get("irrelevant_type") == "out_of_domain":
        return "out_of_domain"
    return "any"  # 코퍼스 밖 회사·기간, 없는 항목: 주제 분류만으로 정답이 정해지지 않아 채점하지 않는다


def domain_cases(dataset: Path) -> list[dict]:
    return [{"id": int(row["id"]), "question": row["input"], "want": domain_want(row)} for row in _read_jsonl(dataset)]


def supervisor_cases(source: Path, filters: Path, chunks: Path) -> list[dict]:
    """저장된 실행에서 'Supervisor LLM이 불린 방문'의 입력을 다시 만든다(검색 1회 뒤, 작성 전)."""

    from backend.graph.supervisor_flow import supervisor_reports

    corp_codes = {int(row["id"]): row for row in _read_jsonl(filters)}
    names: dict[str, str] = {}
    with chunks.open(encoding="utf-8") as stream:
        for line in stream:
            chunk = json.loads(line)
            code = chunk.get("corp_code") or (chunk.get("metadata") or {}).get("corp_code")
            name = chunk.get("corp_name") or (chunk.get("metadata") or {}).get("corp_name")
            if code and name and code not in names:
                names[code] = name
    dataset = {int(row["id"]): row["input"] for row in _read_jsonl(ROOT / "evals" / "dataset_v2.jsonl")}
    cases = []
    for row in _read_jsonl(source):
        llm_turns = [d for d in row.get("supervisor_decisions") or [] if d.get("llm_next")]
        if not llm_turns or int(row.get("search_calls") or 0) != 1:
            continue  # 검색이 1회인 문항만: 저장된 search_report가 그 방문의 입력과 같다
        target = corp_codes[int(row["id"])]
        question = dataset[int(row["id"])]
        state = {
            "question": question, "corp_name": names.get(target.get("corp_code") or "", ""),
            "fiscal_year": target.get("fiscal_year") or 0, "supervisor_turns": 1, "search_calls": 1, "writer_calls": 0,
            "redraft_calls": 0, "low_score_searches": 0, "search_report": row.get("search_report") or {},
            "writer_report": {}, "supervisor_queries": [question],
        }
        sufficient = bool((row.get("search_report") or {}).get("sufficient"))
        cases.append({
            "id": int(row["id"]), "question": question, "corp_name": state["corp_name"], "fiscal_year": state["fiscal_year"],
            "reports": supervisor_reports(state), "baseline_next": llm_turns[0]["llm_next"],
            "want": "writer_agent" if sufficient else "any",
        })
    return cases


def grader_cases(source: Path, chunks: Path) -> list[dict]:
    """저장된 실행에서 검색까지 간 문항의 첫 검색 결과를 채점기 입력으로 만든다."""

    dataset = {int(row["id"]): row for row in _read_jsonl(ROOT / "evals" / "dataset_v2.jsonl")}
    rows = [row for row in _read_jsonl(source) if int(row.get("search_calls") or 0) > 0]
    wanted = {chunk_id for row in rows for call_ in row["retrieval_calls"] for chunk_id in call_["final_ids"]}
    content: dict[str, str] = {}
    with chunks.open(encoding="utf-8") as stream:
        for line in stream:
            chunk = json.loads(line)
            if chunk["chunk_id"] in wanted:
                content[chunk["chunk_id"]] = chunk.get("content", "")
    cases = []
    for row in rows:
        row_id = int(row["id"])
        lists = [list(call_["final_ids"]) for call_ in row["retrieval_calls"]]
        if len(lists) > 1 and int(row["search_calls"]) == 1:  # 비교 질문: 회사별 결과를 번갈아 합친다(그래프와 같은 순서)
            ids = [chunk_id for group in zip(*lists) for chunk_id in group]
        else:
            ids = lists[0]
        ids = list(dict.fromkeys(ids))
        gold = [chunk_id for chunk_id in (dataset[row_id].get("gold_chunks") or []) if chunk_id in ids]
        cases.append({
            "id": row_id, "question": dataset[row_id]["input"], "kind": dataset[row_id]["kind"],
            "chunks": [{"chunk_id": chunk_id, "content": content[chunk_id]} for chunk_id in ids],
            "gold": gold, "gold_ranks": [ids.index(chunk_id) + 1 for chunk_id in gold],
            "want": "relevant" if dataset[row_id]["kind"] == "relevant" else "insufficient",
        })
    return cases


def grader_suff_cases(source: Path, chunks: Path) -> list[dict]:
    """135번: 관련 문항마다 (정답 청크 있음 → 충분) (정답 청크 뺌 → 부족) 한 쌍을 만든다.

    뺀 자리는 그 검색의 1차 후보(50개) 중 다음 순위의 정답 아닌 청크로 채워 10개를 유지한다('첫 검색이 정답을 놓친' 상황).
    검색 지표를 재는 관련 23문항만 쓴다(비교 질문 31·32번 제외).
    """

    dataset = {int(row["id"]): row for row in _read_jsonl(ROOT / "evals" / "dataset_v2.jsonl")}
    subset = {int(row["id"]) for row in _read_jsonl(ROOT / "evals" / "mappings" / "dataset_v2.retrieval23.jsonl")}
    source_rows = {int(row["id"]): row for row in _read_jsonl(source)}
    base = [case for case in grader_cases(source, chunks) if case["kind"] == "relevant" and case["gold"] and case["id"] in subset]
    fillers: dict[int, list[str]] = {}
    for case in base:
        all_gold = set(dataset[case["id"]].get("gold_chunks") or [])
        shown = {chunk["chunk_id"] for chunk in case["chunks"]}
        candidates = source_rows[case["id"]]["retrieval_calls"][0]["first_stage_ids"]
        fillers[case["id"]] = [cid for cid in candidates if cid not in shown and cid not in all_gold]
    wanted = {cid for ids in fillers.values() for cid in ids[:10]}
    content: dict[str, str] = {}
    with chunks.open(encoding="utf-8") as stream:
        for line in stream:
            chunk = json.loads(line)
            if chunk["chunk_id"] in wanted:
                content[chunk["chunk_id"]] = chunk.get("content", "")
    cases = []
    for case in base:
        gold = set(case["gold"])
        cases.append({**case, "variant": "with_gold", "want": "sufficient"})
        kept = [chunk for chunk in case["chunks"] if chunk["chunk_id"] not in gold]
        removed = len(case["chunks"]) - len(kept)
        extra = [{"chunk_id": cid, "content": content[cid]} for cid in fillers[case["id"]][:removed]]
        cases.append({**case, "variant": "gold_removed", "want": "insufficient", "chunks": kept + extra, "gold": [], "gold_ranks": [],
                      "removed": removed, "filled": len(extra)})
    return cases


def supervisor_mix_cases(suff_results: Path, model: str, filters: Path, chunks: Path) -> list[dict]:
    """136번: 135번 결과에서 한 문항당 네 상황을 만든다. 보고는 그래프와 같은 함수(supervisor_reports)로 만든다."""

    from backend.graph.supervisor_flow import supervisor_reports

    corp_codes = {int(row["id"]): row for row in _read_jsonl(filters)}
    names: dict[str, str] = {}
    with chunks.open(encoding="utf-8") as stream:
        for line in stream:
            chunk = json.loads(line)
            if chunk.get("corp_code") and chunk.get("corp_name") and chunk["corp_code"] not in names:
                names[chunk["corp_code"]] = chunk["corp_name"]
    dataset = {int(row["id"]): row["input"] for row in _read_jsonl(ROOT / "evals" / "dataset_v2.jsonl")}
    removed = {int(r["id"]): r for r in _read_jsonl(suff_results)
               if r["model"] == model and r.get("variant") == "gold_removed" and r.get("sufficient") is False and r.get("missing_items")}
    cases = []
    for row_id, result in sorted(removed.items()):
        target, question = corp_codes[row_id], dataset[row_id]
        base = {"question": question, "corp_name": names.get(target.get("corp_code") or "", ""), "fiscal_year": target.get("fiscal_year") or 0,
                "supervisor_turns": 1, "search_calls": 1, "writer_calls": 0, "redraft_calls": 0, "low_score_searches": 0,
                "writer_report": {}, "supervisor_queries": [question]}
        enough = {"n_chunks": 10, "top1_score": 0.78, "graded": True, "sufficient": True, "missing_items": []}
        lacking = {"n_chunks": result["n_chunks"], "top1_score": 0.78, "graded": True, "sufficient": False,
                   "missing_items": list(result["missing_items"])[:5]}
        passed = {"outcome": "answered", "refusal_reason": None, "has_draft": True, "verdicts": {question: "applicable"},
                  "verification_passed": True, "mismatch_items": [], "failed_checks": []}
        situations = [
            ("sufficient", "writer_agent", {**base, "search_report": enough}),
            ("insufficient_can_search", "search_agent", {**base, "search_report": lacking}),
            ("insufficient_no_search_left", "writer_agent",
             {**base, "supervisor_turns": 2, "search_calls": 2, "search_report": {**lacking, "no_research": True},
              "supervisor_queries": [question, f"{base['corp_name']} {base['fiscal_year']} {lacking['missing_items'][0]}"]}),
            ("draft_verified", "approval", {**base, "supervisor_turns": 2, "writer_calls": 1, "search_report": enough, "writer_report": passed}),
        ]
        for name, want, state in situations:
            cases.append({"id": row_id, "situation": name, "question": question, "corp_name": state["corp_name"],
                          "fiscal_year": state["fiscal_year"], "reports": supervisor_reports(state), "want": want})
    return cases


def grader_suff_line(results: list[dict]) -> str:
    with_gold = [r for r in results if r["variant"] == "with_gold"]
    removed = [r for r in results if r["variant"] == "gold_removed"]
    said_enough = sum(1 for r in with_gold if r["sufficient"] is True)
    said_lacking = sum(1 for r in removed if r["sufficient"] is False)
    picked = sum(1 for r in with_gold if set(r["selected_ranks"]) & set(r["gold_ranks"]))
    balanced = (said_enough / len(with_gold) + said_lacking / len(removed)) / 2 if with_gold and removed else 0.0
    seconds = [r["seconds"] for r in results]
    return (f"정답 있을 때 충분이라고 함 {said_enough}/{len(with_gold)}, 정답 뺐을 때 부족이라고 함 {said_lacking}/{len(removed)}, "
            f"균형 정확도 {balanced:.3f}, 정답 청크 고름 {picked}/{len(with_gold)}, 오류 {sum(1 for r in results if r['error'])}, "
            f"p50 {statistics.median(seconds):.2f}초 p95 {pct(seconds, 0.95):.2f}초, 비용 ${sum(r['cost_usd'] for r in results):.4f}")


def supervisor_mix_line(results: list[dict]) -> str:
    parts = []
    for name in ("sufficient", "insufficient_can_search", "insufficient_no_search_left", "draft_verified"):
        group = [r for r in results if r["situation"] == name]
        parts.append(f"{name} {sum(1 for r in group if r['ok'])}/{len(group)}")
    seconds = [r["seconds"] for r in results]
    return (f"전체 {sum(1 for r in results if r['ok'])}/{len(results)} | " + ", ".join(parts)
            + f" | 오류 {sum(1 for r in results if r['error'])}, p50 {statistics.median(seconds):.2f}초, 비용 ${sum(r['cost_usd'] for r in results):.4f}")


def call_grader(model: str, case: dict) -> dict:
    from backend.services import llm

    started = time.perf_counter()
    error, result = None, None
    with llm.request_scope() as usage:
        try:
            result = llm.grade_search_round(question=case["question"], checklist_items=[], chunks=case["chunks"], model=model)
        except Exception as exc:  # 형식 오류·공급자 오류를 그대로 기록한다(다시 부르지 않음)
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
        seconds = time.perf_counter() - started
        entries = list(usage)
    selected = list((result or {}).get("relevant_chunk_ids") or [])
    sufficient = None if result is None else bool(result.get("sufficient"))
    gold = set(case["gold"])
    if error:
        ok = False
    elif case["want"] == "sufficient":
        ok = sufficient is True  # 135번: 정답 청크가 있는 입력
    elif case["want"] == "relevant":
        ok = bool(gold & set(selected))  # 정답 청크를 하나라도 골랐는가
    else:
        ok = sufficient is False  # 없는 항목: 부족하다고 해야 맞다
    ids = [chunk["chunk_id"] for chunk in case["chunks"]]
    return {
        "role": "grader", "model": model, "id": case["id"], "kind": case["kind"], "want": case["want"], "ok": ok, "error": error,
        "variant": case.get("variant"),
        "selected_ranks": [ids.index(chunk_id) + 1 for chunk_id in selected if chunk_id in ids], "gold_ranks": case["gold_ranks"],
        "n_chunks": len(ids), "sufficient": sufficient, "missing_items": list((result or {}).get("missing_items") or []),
        "seconds": round(seconds, 3), "calls": len(entries), "cost_usd": sum(e.get("cost_usd") or 0 for e in entries),
        "cost_unknown_calls": sum(1 for e in entries if e.get("cost_usd") is None),
        "input_tokens": sum(e.get("input_tokens") or 0 for e in entries),
        "output_tokens": sum(e.get("output_tokens") or 0 for e in entries),
        "reasoning_tokens": sum(e.get("reasoning_tokens") or 0 for e in entries),
        "providers": sorted({str(e.get("provider")) for e in entries if e.get("provider")}),
    }


def grader_line(results: list[dict]) -> str:
    relevant = [r for r in results if r["want"] == "relevant"]
    missing = [r for r in results if r["want"] == "insufficient"]
    hard = [r for r in relevant if r["gold_ranks"] and min(r["gold_ranks"]) > 1]  # 리랭커 1위가 정답이 아닌 문항
    picked = [r for r in relevant if not r["error"]]
    recall = [len(set(r["selected_ranks"]) & set(r["gold_ranks"])) / len(r["gold_ranks"]) for r in picked if r["gold_ranks"]]
    precision = [len(set(r["selected_ranks"]) & set(r["gold_ranks"])) / len(r["selected_ranks"]) for r in picked if r["selected_ranks"]]
    seconds = [r["seconds"] for r in results]
    return (f"정답 청크 고름 {sum(1 for r in relevant if r['ok'])}/{len(relevant)}(어려운 문항 {sum(1 for r in hard if r['ok'])}/{len(hard)}), "
            f"정답 청크 회수율 {statistics.mean(recall) if recall else 0:.2f}, 고른 것 중 정답 비율 {statistics.mean(precision) if precision else 0:.2f}, "
            f"고른 개수 평균 {statistics.mean(len(r['selected_ranks']) for r in picked) if picked else 0:.1f}, "
            f"관련 문항을 충분하다고 함 {sum(1 for r in relevant if r['sufficient'])}/{len(relevant)}, "
            f"없는 항목을 부족하다고 함 {sum(1 for r in missing if r['ok'])}/{len(missing)}, 오류 {sum(1 for r in results if r['error'])}, "
            f"p50 {statistics.median(seconds):.2f}초 p95 {pct(seconds, 0.95):.2f}초, 비용 ${sum(r['cost_usd'] for r in results):.4f}, "
            f"추론 토큰 {sum(r['reasoning_tokens'] for r in results)}")


def call(role: str, model: str, case: dict) -> dict:
    from backend.services import llm

    if role in ("grader", "grader-suff"):
        return call_grader(model, case)
    started = time.perf_counter()
    error, search_query = None, None
    with llm.request_scope() as usage:
        try:
            if role == "domain":
                got = llm.classify_domain(question=case["question"], model=model)
            elif role == "supervisor-mix":
                decision = llm.decide_next(question=case["question"], corp_name=case["corp_name"], fiscal_year=case["fiscal_year"],
                                           reports=case["reports"], model=model)
                got, search_query = decision.next, decision.search_query
            else:
                got = llm.decide_next(question=case["question"], corp_name=case["corp_name"], fiscal_year=case["fiscal_year"],
                                      reports=case["reports"], model=model).next
        except Exception as exc:  # 형식 오류·공급자 오류를 그대로 기록한다(다시 부르지 않음)
            got, error = None, f"{type(exc).__name__}: {str(exc)[:200]}"
        seconds = time.perf_counter() - started
        entries = list(usage)
    if case["want"] == "any":
        ok = None
    elif case["want"] == "not_out_of_domain":
        ok = got is not None and got != "out_of_domain"
    else:
        ok = got == case["want"]
    return {
        "role": role, "model": model, "id": case["id"], "got": got, "want": case["want"], "ok": ok, "error": error,
        "situation": case.get("situation"), "search_query": search_query,
        "baseline_next": case.get("baseline_next"), "seconds": round(seconds, 3), "calls": len(entries),
        "cost_usd": sum(e.get("cost_usd") or 0 for e in entries),
        "cost_unknown_calls": sum(1 for e in entries if e.get("cost_usd") is None),
        "output_tokens": sum(e.get("output_tokens") or 0 for e in entries),
        "reasoning_tokens": sum(e.get("reasoning_tokens") or 0 for e in entries),
        "providers": sorted({str(e.get("provider")) for e in entries if e.get("provider")}),
    }


def pct(values: list[float], q: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, round(q * (len(values) - 1)))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--role", choices=("domain", "supervisor", "grader", "grader-suff", "supervisor-mix"), required=True)
    parser.add_argument("--suff-results", help="supervisor-mix: 135번 결과 파일(부족 상황의 빠진 항목을 여기서 가져온다)")
    parser.add_argument("--suff-model", default="google/gemini-3.5-flash-lite", help="supervisor-mix: 빠진 항목을 가져올 채점 모델")
    parser.add_argument("--models", required=True, help="쉼표로 구분한 OpenRouter 모델 id")
    parser.add_argument("--dataset", default="evals/dataset_v2.jsonl")
    parser.add_argument("--filters", default="evals/filters_v2.graph.domain.jsonl")
    parser.add_argument("--chunks", default="data/experiments/b100-v9-fixed_window/chunks.jsonl")
    parser.add_argument("--source", help="supervisor·grader: 입력을 다시 만들 저장된 실행(.jsonl)")
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=2, help="모델 하나 안에서 동시에 보낼 호출 수")
    args = parser.parse_args()
    if args.role in ("supervisor", "supervisor-mix"):
        # 159번에서 재검색과 Supervisor의 검색 선택지를 없앴다. 이 두 비교(122·136번)는 재검색 대 작성을 고르는
        # 판단을 재던 것이라 지금 코드로는 돌릴 수 없다. 결과는 evals/results/role_bench/에 남아 있다.
        parser.error(f"--role {args.role}은 159번(재검색 제거) 뒤로 돌릴 수 없습니다. 지난 결과: evals/results/role_bench/")

    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True), override=False)
    if args.role == "domain":
        cases = domain_cases(ROOT / args.dataset)
    elif args.role == "supervisor-mix":
        if not args.suff_results:
            parser.error("--role supervisor-mix에는 --suff-results(135번 결과 파일)가 필요합니다")
        cases = supervisor_mix_cases(ROOT / args.suff_results, args.suff_model, ROOT / args.filters, ROOT / args.chunks)
    elif not args.source:
        parser.error(f"--role {args.role}에는 --source가 필요합니다")
    elif args.role == "grader-suff":
        cases = grader_suff_cases(ROOT / args.source, ROOT / args.chunks)
    elif args.role == "grader":
        cases = grader_cases(ROOT / args.source, ROOT / args.chunks)
        relevant = [c for c in cases if c["want"] == "relevant"]
        hard = [c for c in relevant if min(c["gold_ranks"]) > 1]
        for k in (1, 2, 3, 4, 5):  # 비교용: LLM 없이 리랭커 순위 상위 k개를 고른 경우(호출·비용 없음)
            hit = sum(1 for c in relevant if any(rank <= k for rank in c["gold_ranks"]))
            recall = statistics.mean(sum(1 for rank in c["gold_ranks"] if rank <= k) / len(c["gold_ranks"]) for c in relevant)
            precision = statistics.mean(sum(1 for rank in c["gold_ranks"] if rank <= k) / k for c in relevant)
            print(f"  (리랭커 상위 {k}개) 정답 청크 고름 {hit}/{len(relevant)}(어려운 문항 "
                  f"{sum(1 for c in hard if any(rank <= k for rank in c['gold_ranks']))}/{len(hard)}), 회수율 {recall:.2f}, 고른 것 중 정답 비율 {precision:.2f}")
    else:
        cases = supervisor_cases(ROOT / args.source, ROOT / args.filters, ROOT / args.chunks)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"{args.role}: 문항 {len(cases)}건 × 모델 {len(models)}개")
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for model in models:  # 모델은 차례로(서로의 시간에 영향을 주지 않게), 모델 안에서는 workers개씩
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                results = list(pool.map(lambda case: call(args.role, model, case), cases))
            for result in results:
                stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            stream.flush()
            if args.role == "grader":
                print(f"  {model}: {grader_line(results)}")
                continue
            if args.role == "grader-suff":
                print(f"  {model}: {grader_suff_line(results)}")
                continue
            if args.role == "supervisor-mix":
                print(f"  {model}: {supervisor_mix_line(results)}")
                continue
            scored = [r for r in results if r["ok"] is not None]
            seconds = [r["seconds"] for r in results]
            same = [r for r in results if r.get("baseline_next") and r["got"] == r["baseline_next"]]
            print(f"  {model}: 맞음 {sum(1 for r in scored if r['ok'])}/{len(scored)}, 오류 {sum(1 for r in results if r['error'])}, "
                  f"p50 {statistics.median(seconds):.2f}초 p95 {pct(seconds, 0.95):.2f}초, "
                  f"비용 ${sum(r['cost_usd'] for r in results):.4f}, 추론 토큰 {sum(r['reasoning_tokens'] for r in results)}"
                  + (f", 지금 모델과 같은 선택 {len(same)}/{len(results)}" if args.role == "supervisor" else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
