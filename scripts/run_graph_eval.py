"""D4 기준 점수: 동결 평가셋(v1 30건·v2 50건)을 build_graph로 돌려 응답 지표 5개와 검색 Recall·MRR을 잰다.

- 검색기: backend/retrieval_combo.py 팩토리(.env의 RETRIEVAL_COMBO·경로)
- 재작성·채점·작성: --agents real(서정현 구현, build_graph 기본값) 또는 fake(키 없는 시험)
- 필터: supervisor가 질문에서 추출한다. --filters 파일(측정 전 커밋)과 대조해 다르면 멈춘다
- 지표: evals/response_metrics.py(5개) + backend/rag/evaluation.py(Recall·MRR 등)
- 정답: v1은 --gold 청크 id 매핑. v2(gold_spans)는 --chunks 청크 파일에서 evaluation.py로
  정답 청크를 만든다(검색 Recall은 item 단위, 응답의 '정답 근거 인용'은 item 정답 청크 합집합)
- 오류는 빈 결과로 숨기지 않고 그대로 멈춘다.

실행 예(저장소 루트):
  # v1(사전 등록 32번 재현)
  python -X utf8 scripts/run_graph_eval.py --gold evals/mappings/dataset_v1.v9_fixed_window.jsonl \
      --filters evals/filters_v1.graph.jsonl --chunk-version v9 \
      --agents real --output evals/results/graph30/dryrun.jsonl --limit 3
  # v2(검색 지표는 31·32번을 뺀 23건)
  python -X utf8 scripts/run_graph_eval.py --dataset evals/dataset_v2.jsonl \
      --chunks data/experiments/b100-v9-fixed_window/chunks.jsonl \
      --retrieval-subset evals/mappings/dataset_v2.retrieval23.jsonl \
      --query-vectors evals/results/v2_query_vectors_oai_small.npz \
      --filters evals/filters_v2.graph.jsonl --chunk-version v9 --output evals/results/graph50/x.jsonl
  # supervisor 추출만(키·검색 산출물 없이)
  python -X utf8 scripts/run_graph_eval.py --dataset evals/dataset_v2.jsonl \
      --extract-filters evals/filters_v2.graph.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.rag.evaluation import (  # noqa: E402
    compute_retrieval_metrics,
    load_chunk_contents,
    span_item_chunks,
)
from evals.response_metrics import calculate_response_metrics  # noqa: E402

FROZEN_PATH = Path("evals/FROZEN.json")
DEFAULT_DATASET_PATH = Path("evals/dataset_v1.jsonl")  # 기본값 v1: 32번 측정을 그대로 재현
MAX_RETRIEVAL_ROUNDS = 3  # SPEC §4: 최초 1 + 재검색 2


class GraphEvalError(ValueError):
    """평가 입력이 서로 맞지 않을 때 발생한다."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


# ── 키 없는 시험용 가짜 LLM(--agents fake). 실제 측정은 서정현 구현(build_graph 기본값)을 쓴다 ──
def fake_rewriter(state: dict[str, Any]) -> str:
    """질의를 바꾸지 않는다(그래프는 재검색 없이 writer로 간다)."""

    return str(state.get("retrieval_query") or state.get("question", ""))


def fake_batch_grader(state: dict[str, Any], chunks: list[dict]) -> set[str]:
    """모든 후보를 relevant로 본다."""

    return {str(chunk["chunk_id"]) for chunk in chunks}


def fake_draft_writer(state: dict[str, Any], chunks: list[dict]) -> list[dict]:
    """항목마다 1위 청크를 인용한다."""

    return [
        {"item": item, "verdict": "applicable", "reason": "가짜 작성기", "chunk_ids": [chunks[0]["chunk_id"]]}
        for item in state.get("checklist_items", [])
        if chunks
    ]


def fake_supervisor_decider(state: dict[str, Any]) -> None:
    """Supervisor LLM 대신 None을 돌려 코드 규칙(fallback_decision)으로 가게 한다."""

    return None


def fake_search_grader(state: dict[str, Any], chunks: list[dict]) -> dict:
    """모든 청크를 관련·충분으로 본다."""

    return {"relevant_chunk_ids": [str(chunk["chunk_id"]) for chunk in chunks], "sufficient": True, "missing_items": []}


FAKE_AGENTS = {
    "rewriter": fake_rewriter,
    "batch_grader": fake_batch_grader,
    "draft_writer": fake_draft_writer,
}


def counting_news_search(calls: list[str]):
    """사전 등록 118번: 실제 네이버 API를 부르지 않고 호출 횟수만 세는 뉴스 클라이언트(일일 한도·키 보호).

    항목 5건을 항상 돌려준다. calls에 질문을 쌓아 그래프 State의 news_calls와 대조한다.
    """

    def search(question: str) -> dict[str, Any]:
        calls.append(question)
        items = [
            {"title": f"가짜 뉴스 {n}", "description": "", "link": f"https://news.invalid/{n}",
             "originallink": f"https://news.invalid/{n}", "pub_date": "", "source_domain": "news.invalid"}
            for n in range(5)
        ]
        return {"status": "ok", "items": items, "calls": 1}

    return search


def supervisor_record(row_id: int, final: dict[str, Any]) -> dict[str, Any]:
    """그래프 최종 상태에서 supervisor가 정한 것만 뽑는다(거절 사유·대상·target_required).

    거절 사유는 supervisor가 거절했을 때만 적는다. 검색 결과가 비어 writer가 낸
    ITEM_NOT_DISCLOSED는 supervisor 판단이 아니므로 여기 넣지 않는다.
    """

    events = list(final.get("node_events", []))
    supervisor_events = [event for event in events if event.startswith("supervisor:")]
    filters = final.get("filters") or {}
    return {
        "id": row_id,
        "supervisor": supervisor_events[0] if supervisor_events else None,
        "refusal_reason": final.get("refusal_reason") if "supervisor:refuse" in supervisor_events else None,
        "corp_code": filters.get("corp_code"),
        "fiscal_year": int(filters["fiscal_year"]) if filters.get("fiscal_year") is not None else None,
        "target_required": bool(final.get("target_required")),
    }


def extract_supervisor_targets(
    rows: list[dict], *, corpus_catalog: Any = None, domain_rule: bool = False
) -> list[dict[str, Any]]:
    """키·검색 산출물 없이 supervisor 추출만 확인한다(측정 전 필터 파일 만들기).

    그래프를 그대로 돌리되 검색기는 대상 회사·연도의 빈 청크 1개를 돌려주는 자리표시자,
    재작성·작성은 가짜(FAKE_AGENTS)라 LLM·임베딩·리랭커를 부르지 않는다.
    """

    from backend.graph.build import build_graph

    def placeholder_retriever(state: dict[str, Any]) -> list[dict[str, Any]]:
        filters = state["filters"]
        return [{
            "chunk_id": "extract-placeholder",
            "corp_code": filters["corp_code"],
            "fiscal_year": filters["fiscal_year"],
            "content": "",
        }]

    # domain_rule=True(59번): 결정적인 '예측' 규칙만 반영하고 LLM 분류는 늘 '주제 안'으로 둔다(키 없이 추출).
    graph = build_graph(
        retriever=placeholder_retriever, corpus_catalog=corpus_catalog,
        domain_check_enabled=domain_rule, domain_classifier=(lambda _q: True) if domain_rule else None, **FAKE_AGENTS,
    )
    records = []
    for row in rows:
        row_id = _row_id(row)
        final = graph.invoke({
            "user_id": "eval",
            "review_id": f"extract-{row_id}",
            "question": row["input"],
            "node_events": [],
            "retrieval_rounds": 0,
        })
        records.append(supervisor_record(row_id, final))
    return records


def _row_id(row: dict) -> int:
    """문항 id를 정수로 맞춘다(평가셋에 '1'처럼 문자열로 있어도 같은 문항으로 본다)."""

    try:
        return int(str(row["id"]).strip())
    except (KeyError, ValueError) as exc:
        raise GraphEvalError(f"문항 id가 정수로 읽히지 않습니다: {row.get('id')!r}") from exc


def _with_int_ids(rows: list[dict]) -> list[dict]:
    return [{**row, "id": _row_id(row)} for row in rows]


def check_frozen(dataset_path: Path, dataset_sha256: str) -> None:
    """FROZEN.json에 기록된 해시와 평가셋 파일 해시가 같은지 확인한다(키는 파일 이름)."""

    frozen = json.loads(FROZEN_PATH.read_text(encoding="utf-8"))
    if dataset_path.name not in frozen:
        raise GraphEvalError(f"{dataset_path.name}는 FROZEN.json에 없는 평가셋입니다")
    if frozen[dataset_path.name]["sha256"] != dataset_sha256:
        raise GraphEvalError(f"{dataset_path} 해시가 FROZEN.json과 다릅니다")


def check_corpus(rows: list[dict], corpus_ids: list[str], label: str) -> None:
    versions = {row.get("corpus_version") for row in rows}
    if len(versions) != 1 or [*versions] != corpus_ids:
        raise GraphEvalError(
            f"{label} corpus_version {sorted(map(str, versions))}이 청크 corpus_id {corpus_ids}와 다릅니다"
        )


def check_inputs(
    frozen_rows: list[dict],
    gold_rows: list[dict],
    corpus_ids: list[str],
    frozen_sha256: str,
    dataset_path: Path = DEFAULT_DATASET_PATH,
) -> None:
    """(v1) 동결 해시, 정답 매핑과 동결 질문의 일치, 정답 매핑과 청크 코퍼스의 일치를 확인한다."""

    check_frozen(dataset_path, frozen_sha256)
    gold_by_id = {_row_id(row): row for row in gold_rows}
    for row in frozen_rows:
        gold = gold_by_id.get(_row_id(row))
        if gold is None or gold["input"] != row["input"] or gold["kind"] != row["kind"]:
            raise GraphEvalError(f"정답 매핑 id={row['id']}가 동결 평가셋과 다릅니다")
    check_corpus(gold_rows, corpus_ids, "정답 매핑")


def check_subset(dataset_rows: list[dict], subset_rows: list[dict]) -> None:
    """검색 지표용 파생 파일(예: 23건)의 문항이 동결 평가셋의 같은 문항과 내용이 같은지 확인한다."""

    by_id = {_row_id(row): row for row in dataset_rows}
    for row in subset_rows:
        original = by_id.get(_row_id(row))
        if original is None or {**original, "id": None} != {**row, "id": None}:
            raise GraphEvalError(f"검색 지표용 파일의 id={row['id']}가 동결 평가셋과 다릅니다")


def load_span_chunks(dataset_rows: list[dict], chunks_path: Path) -> dict[str, list[tuple[str, str]]]:
    """gold_spans가 나온 문서(rcept_no)의 청크만 읽는다(evaluation.py 함수 재사용)."""

    needed = {
        location["rcept_no"]
        for row in dataset_rows
        for span in row.get("gold_spans") or []
        for location in span.get("locations") or []
    }
    return load_chunk_contents(chunks_path, needed)


def span_gold_rows(
    dataset_rows: list[dict], chunks_by_rcept: dict[str, list[tuple[str, str]]]
) -> list[dict]:
    """(v2) 응답 지표용 정답: 관련 문항의 gold_chunks를 '모든 item 정답 청크의 합집합'으로 바꾼 사본.

    '정답 근거 인용'은 v1과 같은 정의(인용 청크 중 정답 청크가 하나라도 있으면 맞음)를 쓰고,
    정답 청크만 span에서 측정 청크 파일 기준으로 다시 만든다. 동결 파일은 바꾸지 않는다.
    """

    output = []
    for row in dataset_rows:
        if row["kind"] == "relevant":
            if not row.get("gold_spans"):
                raise GraphEvalError(f"관련 id={row['id']}에 gold_spans가 없습니다")
            gold = set().union(*span_item_chunks(row, chunks_by_rcept).values())
            if not gold:
                raise GraphEvalError(f"관련 id={row['id']}의 span을 포함한 청크가 없습니다")
            output.append({**row, "gold_chunks": sorted(gold)})
        else:
            output.append(dict(row))
    return output


class PinnedQueryEmbedder:
    """저장한 질의 벡터 파일에 있는 질의는 저장 벡터를, 없는 질의는 새로 임베딩한다(사전 등록 31번).

    OpenRouter 질의 임베딩이 같은 문장에도 가끔 다른 벡터를 돌려줘서, 1회차 검색(원래 질문)은
    v2 검색 측정 때 만든 벡터를 다시 쓴다. 재작성 질의·파일에 없는 질문은 새로 임베딩하고
    sources에 어느 쪽을 썼는지 남긴다. 파일은 읽기만 하고 바꾸지 않는다.
    """

    def __init__(self, embedder: Any, path: Path, *, model_name: str) -> None:
        from backend.rag.offline_eval import CachedQueryEmbedder

        if not path.exists():
            raise GraphEvalError(f"질의 벡터 파일이 없습니다: {path}")
        # 파일 읽기·키 규칙(sha256(모델 이름 + 줄바꿈 + 질의))은 offline_eval과 같은 것을 쓴다
        self._stored = CachedQueryEmbedder(embedder, path, model_name=model_name)
        self.embedder = embedder
        self.dimension = embedder.dimension
        self.sources: list[str] = []  # 호출 순서대로 "pinned" 또는 "fresh"

    def encode_queries(self, texts: list[str]) -> Any:
        import numpy as np

        vectors = []
        for text in texts:
            stored = self._stored.vectors.get(self._stored._key(text))
            if stored is not None:
                vectors.append(np.asarray(stored, dtype=np.float32))
                self.sources.append("pinned")
            else:
                vectors.append(np.asarray(self.embedder.encode_queries([text]), dtype=np.float32)[0])
                self.sources.append("fresh")
        return np.stack(vectors).astype(np.float32)


def run_eval(
    frozen_rows: list[dict],
    filters_by_id: dict[int, dict],
    *,
    retriever: Any,
    graph_kwargs: dict[str, Any] | None = None,
    corpus_catalog: Any = None,
    query_embedder: PinnedQueryEmbedder | None = None,
    graph_kind: str = "single",
    node_timings: list[dict[str, Any]] | None = None,
    trace_ids: dict[int, str] | None = None,
    run_name: str = "eval",
    clarifications: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    """문항마다 그래프를 돌린다. graph_kind: single(build_graph, D4) 또는 multi(build_supervisor_graph, D5).

    node_timings: 리스트를 주면 문항마다 노드별 소요 시간을 {"id", "node_ms"}로 덧붙인다(콜백만 붙이고
    그래프 동작은 같음). None이면 지금처럼 config 없이 부른다.

    필터는 supervisor가 질문에서 추출한다(W4.3). filters_by_id는 넣지 않고 대조만 한다:
    추출 결과가 측정 전에 커밋한 필터 파일과 다르면 멈춘다(사전 등록 32번). 필터 파일 행에
    refusal_reason·target_required 칸이 있으면(v2 추출 파일) 그것도 대조한다.

    clarifications(162번): {문항 id: 되묻기에 대한 답}. 첫 턴이 되묻기(target_required)로 끝난 문항에만, 그 답을
    원래 질문과 합쳐(서비스와 같은 규칙 `clarified_question`) 두 번째 턴을 돌린다. 필터 파일 대조는 첫 턴으로 하고,
    첫 턴 결과는 응답의 `first_turn`에 남긴다.
    """

    from backend.graph.build import build_graph, build_supervisor_graph
    from backend.services.llm import drain_usage

    builders = {"single": build_graph, "multi": build_supervisor_graph}
    if graph_kind not in builders:
        raise GraphEvalError(f"graph는 single 또는 multi여야 합니다: {graph_kind!r}")
    drain_usage()  # 이전 호출분이 첫 문항에 섞이지 않게 비운다
    # 그래프 기본값은 59번 주제 판정 켜짐이지만, 러너는 명시하지 않으면 끈다(58·64번 등 이전 측정 재현).
    kwargs = {"domain_check_enabled": False, **(graph_kwargs or {})}
    graph = builders[graph_kind](retriever=retriever, corpus_catalog=corpus_catalog, **kwargs)
    responses: list[dict[str, Any]] = []
    for row in frozen_rows:
        row_id = _row_id(row)
        state: dict[str, Any] = {
            "user_id": "eval",
            "review_id": f"eval-{row_id}",
            "question": row["input"],
            "node_events": [],
            "retrieval_rounds": 0,
        }
        calls_before = len(retriever.calls)
        sources_before = len(query_embedder.sources) if query_embedder else 0
        started = time.perf_counter()
        from backend.services import tracing

        # G6: 문항 하나 = Langfuse 트레이스 하나(키가 없으면 아무것도 안 함). 점수는 main()이 지표를 계산한 뒤 붙인다.
        with tracing.question("eval", question=row["input"], metadata={"row_id": row_id, "kind": row.get("kind"),
                                                                       "graph": graph_kind},
                              session_id=run_name, tags=["eval", run_name]) as trace:
            if node_timings is None:
                final = graph.invoke(state)
            else:
                from scripts.node_timing import NodeTimer

                timer = NodeTimer()
                final = graph.invoke(state, config={"callbacks": [timer]})
                node_timings.append({"id": row_id, "node_ms": timer.totals})
            first_final = final
            first_turn = None
            if clarifications and row_id in clarifications and final.get("target_required"):
                from backend.graph.refusal import clarified_question

                first_turn = {
                    "outcome": final.get("outcome"), "target_required": True,
                    "refusal_message": final.get("refusal_message"),
                    "node_events": list(final.get("node_events", [])),
                    "reply": clarifications[row_id],
                    "question": clarified_question(row["input"], clarifications[row_id]),
                }
                final = graph.invoke({**state, "question": first_turn["question"], "review_id": f"eval-{row_id}-clarified"})
            trace.finish({"outcome": final.get("outcome"), "refusal_reason": final.get("refusal_reason"),
                          "citation_ids": [c.get("chunk_id") for c in final.get("citations") or []]})
        if trace_ids is not None and trace.trace_id:
            trace_ids[row_id] = trace.trace_id
        elapsed_ms = (time.perf_counter() - started) * 1000
        calls = retriever.calls[calls_before:]
        companies = max(1, len(final.get("comparison_targets") or []))  # 63번: 비교 질문은 회사마다 검색
        if len(calls) > MAX_RETRIEVAL_ROUNDS * companies:
            raise GraphEvalError(f"id={row_id} 검색 {len(calls)}회: 상한 {MAX_RETRIEVAL_ROUNDS}회×회사 {companies}곳 초과")
        events = list(final.get("node_events", []))
        # 74번: 멀티 허브 supervisor 방문 상한(단일 그래프는 이 필드가 없어 0)
        from backend.graph.supervisor_flow import MAX_SUPERVISOR_TURNS

        if int(final.get("supervisor_turns") or 0) > MAX_SUPERVISOR_TURNS:
            raise GraphEvalError(f"id={row_id} supervisor 방문 {final.get('supervisor_turns')}회: 상한 {MAX_SUPERVISOR_TURNS}회 초과")
        domain_refused = "supervisor:refuse_domain" in events
        # 59번: LLM 주제 분류 거절은 비결정적이라 필터 파일 대조에서 빼고 응답에 표시한다.
        expected = None if domain_refused else filters_by_id.get(row_id)
        expected_target = (
            (expected["corp_code"], int(expected["fiscal_year"]))
            if expected and expected.get("corp_code")
            else None
        )
        got = first_final.get("filters")  # 필터 파일 대조는 첫 턴으로 한다(두 번째 턴은 평가자가 회사를 준 것)
        got_target = (got["corp_code"], int(got["fiscal_year"])) if got else None
        if not domain_refused and got_target != expected_target:
            raise GraphEvalError(
                f"id={row_id} supervisor 추출 {got_target}이 필터 파일 {expected_target}과 다릅니다"
            )
        got_supervisor = supervisor_record(row_id, first_final)
        for key in ("refusal_reason", "target_required"):
            if expected and key in expected and expected[key] != got_supervisor[key]:
                raise GraphEvalError(
                    f"id={row_id} supervisor {key} {got_supervisor[key]!r}이 필터 파일 {expected[key]!r}과 다릅니다"
                )
        responses.append(
            {
                "id": row_id,
                "kind": row["kind"],
                "irrelevant_type": row.get("irrelevant_type"),
                "outcome": final.get("outcome"),
                "refusal_reason": final.get("refusal_reason"),
                "citations": list(final.get("citations") or []),
                "draft_items": list(final.get("draft_items") or []),
                "domain_refused": domain_refused,
                # verifier 검사 기록(인용별 passed·failed_checks). 하향 항목 수·사유를 결과 파일에서 세려고 남긴다
                "verification": list(final.get("verification") or []),
                "node_events": events,
                "retrieval_rounds": int(final.get("retrieval_rounds", 0)),
                "rewrites": sum(event.startswith("rewriter:round") for event in events),
                # 104번: 멀티 그래프 Supervisor 결정(방문마다 next·reason·llm_next·fallback·guard)과 보고
                "supervisor_decisions": list(final.get("supervisor_decisions") or []),
                "search_calls": int(final.get("search_calls") or 0),
                "redraft_calls": int(final.get("redraft_calls") or 0),
                "search_report": final.get("search_report"),
                "writer_report": final.get("writer_report"),
                "grader_events": [event for event in events if event.startswith("grader:")],
                "retrieval_calls": [asdict(call) for call in calls],
                # 검색 호출마다 질의 벡터를 저장 파일에서 썼는지(pinned) 새로 만들었는지(fresh)
                "query_vector_sources": list(query_embedder.sources[sources_before:]) if query_embedder else None,
                "llm_usage": drain_usage(),
                "elapsed_ms": round(elapsed_ms, 1),
                # 162번: 되묻기 답으로 두 번째 턴을 돈 문항만 첫 턴 기록을 남긴다(다른 문항은 칸 없음)
                **({"first_turn": first_turn} if first_turn is not None else {}),
                # 118번: 뉴스 분기가 켜진 그래프에서만 칸을 더한다(꺼짐이면 응답 기록이 기존과 같다)
                **({
                    "refusal_kind": final.get("refusal_kind"),
                    "news_calls": int(final.get("news_calls") or 0),
                    "news_search": final.get("news_search"),
                    "news_item_count": len(final.get("news_items") or []),
                } if "news_calls" in final else {}),
            }
        )
    return responses


def _retrieval_results(responses: list[dict], which: int) -> list[dict]:
    return [
        {
            "id": r["id"],
            "retrieved_chunks": r["retrieval_calls"][which]["final_ids"]
            if r["kind"] == "relevant" and r["retrieval_calls"]
            else [],
        }
        for r in responses
    ]


def usage_totals(responses: list[dict]) -> dict[str, Any]:
    """호출별 사용량을 종류·모델별로 합친다. 응답에 비용이 없는 호출은 cost_unknown_calls로 센다."""

    from backend.rag.offline_eval import VOYAGE_PRICE_PER_MILLION_TOKENS

    by_model: dict[str, dict[str, Any]] = {}
    for record in (u for r in responses for u in r.get("llm_usage", [])):
        key = f"{record['kind']}:{record['model']}"
        total = by_model.setdefault(
            key, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "cost_unknown_calls": 0}
        )
        total["calls"] += 1
        total["input_tokens"] += record.get("input_tokens") or 0
        total["output_tokens"] += record.get("output_tokens") or 0
        cost = record.get("cost_usd")
        if cost is None and record["kind"] == "rerank" and record["model"] in VOYAGE_PRICE_PER_MILLION_TOKENS:
            cost = (record.get("input_tokens") or 0) * VOYAGE_PRICE_PER_MILLION_TOKENS[record["model"]] / 1e6
        if cost is None:
            total["cost_unknown_calls"] += 1
        else:
            total["cost_usd"] += cost
    return {
        "by_model": by_model,
        "cost_usd_known_total": round(sum(t["cost_usd"] for t in by_model.values()), 6),
        "cost_unknown_calls": sum(t["cost_unknown_calls"] for t in by_model.values()),
    }


def summarize(
    gold_rows: list[dict],
    responses: list[dict],
    *,
    retrieval_rows: list[dict] | None = None,
    chunks_by_rcept: dict[str, list[tuple[str, str]]] | None = None,
) -> dict[str, Any]:
    """응답 지표는 gold_rows 전부(응답이 있는 문항), 검색 지표는 retrieval_rows(없으면 gold_rows)로 잰다.

    v2: gold_rows = span 합집합으로 바꾼 50건, retrieval_rows = 31·32번을 뺀 동결 원본 23건(+비관련),
    chunks_by_rcept = 검색 정답을 item 단위로 다시 만들 청크 본문.
    """

    responses = _with_int_ids(responses)
    ids = {r["id"] for r in responses}
    gold = [row for row in _with_int_ids(gold_rows) if row["id"] in ids]
    retrieval_gold = [row for row in _with_int_ids(retrieval_rows or gold_rows) if row["id"] in ids]
    retrieval_ids = {row["id"] for row in retrieval_gold}
    retrieval_responses = [r for r in responses if r["id"] in retrieval_ids]
    # response_metrics는 outcome이 문자열이길 기대한다. writer stub이 outcome을 비우면 None 그대로 둔다
    # (refused가 아니므로 비관련 문항에서는 환각 위험으로 센다).
    metrics: dict[str, Any] = {
        "n": len(responses),
        "response": calculate_response_metrics(gold, responses),
        # 1회차 = 원래 질문으로 한 검색(18번 실험과 같은 정의), 마지막 회차 = 재작성 뒤 검색
        "retrieval_n": len(retrieval_gold),
        "retrieval_first_round": compute_retrieval_metrics(
            retrieval_gold, _retrieval_results(retrieval_responses, 0), chunks_by_rcept=chunks_by_rcept
        ),
        "retrieval_last_round": compute_retrieval_metrics(
            retrieval_gold, _retrieval_results(retrieval_responses, -1), chunks_by_rcept=chunks_by_rcept
        ),
        "relevant_answered_without_citations": sum(
            r["kind"] == "relevant" and r["outcome"] != "refused" and not r["citations"] for r in responses
        ),
        "retrieval_calls_total": sum(len(r["retrieval_calls"]) for r in responses),
        "domain_refused_ids": [r["id"] for r in responses if r.get("domain_refused")],
        "rerank_input_chars_total": sum(c["rerank_input_chars"] for r in responses for c in r["retrieval_calls"]),
    }
    return metrics


def news_summary(responses: list[dict], client_calls: list[str]) -> dict[str, Any]:
    """118번 측정표용 요약: 문항별 뉴스 호출·거절 종류와, 가짜 클라이언트가 센 호출 수(State 합과 일치해야 함)."""

    def ids(predicate) -> list[int]:
        return [r["id"] for r in responses if predicate(r)]

    return {
        "client": "fake(호출 수만 셈, 실제 네이버 API 호출 없음)",
        "client_calls": len(client_calls),
        "news_calls_total": sum(r.get("news_calls", 0) for r in responses),
        "max_news_calls_per_question": max((r.get("news_calls", 0) for r in responses), default=0),
        "news_called_ids": ids(lambda r: r.get("news_calls", 0) > 0),
        "refusal_kind_unrelated_ids": ids(lambda r: r.get("refusal_kind") == "unrelated"),
        "refusal_kind_work_related_ids": ids(lambda r: r.get("refusal_kind") == "work_related"),
        "classifier_skipped_ids": ids(lambda r: r.get("news_search") == "skipped_no_key"),
        "out_of_domain_refused_ids": ids(lambda r: r.get("refusal_reason") == "OUT_OF_DOMAIN" and r.get("domain_refused")),
        "relevant_with_news_ids": ids(lambda r: r.get("kind") == "relevant" and r.get("news_calls", 0) > 0),
        "relevant_with_refusal_kind_ids": ids(lambda r: r.get("kind") == "relevant" and r.get("refusal_kind") is not None),
        "max_news_item_count": max((r.get("news_item_count", 0) for r in responses), default=0),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dataset", default=str(DEFAULT_DATASET_PATH),
        help=f"동결 평가셋(기본 {DEFAULT_DATASET_PATH}: 32번 재현). FROZEN.json에 있어야 한다",
    )
    parser.add_argument("--gold", help="(v1) 청크 버전에 맞춘 정답 매핑 JSONL")
    parser.add_argument("--chunks", help="(v2) gold_spans로 정답 청크를 만들 청크 파일(검색 청크와 같아야 함)")
    parser.add_argument(
        "--retrieval-subset",
        help="검색 지표만 이 파생 파일의 문항으로 잰다(v2: dataset_v2.retrieval23.jsonl, 31·32번 제외)",
    )
    parser.add_argument(
        "--query-vectors",
        help="1회차 검색에 재사용할 질의 벡터 파일(.npz). 파일에 없는 질의(재작성 등)는 새로 임베딩",
    )
    parser.add_argument(
        "--extract-filters", metavar="PATH",
        help="supervisor 추출만 키 없이 돌려 PATH에 저장하고 끝낸다(측정하지 않음)",
    )
    parser.add_argument("--filters")
    parser.add_argument("--chunk-version", choices=("v8", "v9", "v10"))
    parser.add_argument("--allow-corpus-mismatch", action="store_true",
                        help="(158번) 평가셋의 corpus_version과 청크 corpus_id가 달라도 돌린다. 청킹을 바꾼 실험용: "
                             "정답 청크는 gold_spans의 글자로 그 청크 파일에서 다시 찾는다. 요약에 표시가 남는다")
    parser.add_argument("--output")
    parser.add_argument(
        "--agents", choices=("real", "fake"), default="real",
        help="real: 서정현 구현(build_graph 기본값, LLM 호출). fake: 가짜 재작성·채점·작성(LLM 호출 없음)",
    )
    parser.add_argument(
        "--graph", choices=("single", "multi"), default="single",
        help="single: 단일 에이전트(build_graph, 기본). multi: Supervisor+서브에이전트(build_supervisor_graph)",
    )
    parser.add_argument(
        "--domain-check", action="store_true",
        help="59번 Supervisor 주제 밖 판정('예측' 규칙 + LLM 분류 1회)을 켠다. --extract-filters와 함께면 규칙만 반영",
    )
    parser.add_argument("--domain-model", help="59번 주제 분류기 모델(기본: 단일 그래프는 생성 모델, 멀티 그래프는 gpt-4o-mini). 67번 실험")
    parser.add_argument(
        "--node-timing", action="store_true",
        help="노드별 소요 시간을 <output>.node_ms.jsonl로 따로 쓴다(콜백만 붙이고 그래프 동작은 같음)",
    )
    parser.add_argument("--llm-grading", action="store_true", help="2단 LLM 문서 채점을 켠다(기본 꺼짐)")
    parser.add_argument("--generation-model", help="재작성·작성 모델(기본: 단일 그래프 llm.DEFAULT_GENERATION_MODEL, 멀티 그래프 llm.DEFAULT_WRITER_MODEL)")
    parser.add_argument("--judge-model", help="채점 모델(기본 llm.DEFAULT_JUDGE_MODEL)")
    parser.add_argument("--grade-to-writer", action="store_true",
                        help="125번: 채점 결과를 작성기에 넘긴다(고른 청크를 앞에, 부족한 항목 알림, 그때는 재검색 없음)")
    parser.add_argument("--search-grader-model",
                        help="124번: 멀티 그래프 검색 채점(grade_search_round) 모델(기본 llm.DEFAULT_SEARCH_GRADER_MODEL)")
    parser.add_argument(
        "--writer-reasoning-effort", choices=["none", "minimal", "low"],
        help="97번: 작성·redraft 호출에만 OpenRouter reasoning.effort를 보낸다(멀티 그래프만, 기본: 보내지 않음)",
    )
    parser.add_argument(
        "--writer-reason-numbers", action="store_true",
        help="98번: 작성기 지시문에 'reason에 근거 숫자를 그대로 적기' 문장을 넣는다(멀티 그래프만)",
    )
    parser.add_argument(
        "--news-search", action="store_true",
        help="118번: 거절 질문 뉴스 분기(news_search_enabled)를 켠다. 뉴스 클라이언트는 호출 수만 세는 가짜(실제 API 호출 없음). "
             "거절 종류 분류는 실제 LLM을 부른다. --graph multi --domain-check가 필요하다",
    )
    parser.add_argument("--grade-reorder", action="store_true",
                        help="117번(미채택, 기본 꺼짐): 채점이 맞다고 본 청크를 작성기 입력 앞쪽에 둔다(멀티 그래프만)")
    parser.add_argument("--supplementary", action="store_true",
                        help="137번: 동결하지 않은 보충 평가셋을 돌린다(FROZEN.json 검사 생략). 채택 판정에 쓰는 점수는 동결 평가셋으로만 잰다")
    parser.add_argument("--clarifications",
                        help="(162번) 문항별 되묻기 답 JSONL({id, reply}). 첫 턴이 되묻기로 끝난 문항에만 두 번째 턴을 돌린다")
    parser.add_argument("--limit", type=int, help="앞에서 N건만(dry-run)")
    parser.add_argument("--ids", help="이 문항 id만(쉼표 구분, 예: 1,7). 105번처럼 바뀐 분기를 지나는 문항만 잴 때")
    args = parser.parse_args(argv)
    if (args.writer_reasoning_effort or args.writer_reason_numbers) and args.graph != "multi":
        parser.error("--writer-reasoning-effort·--writer-reason-numbers는 --graph multi에서만 씁니다")
    if args.news_search and not (args.graph == "multi" and args.domain_check):
        parser.error("--news-search는 --graph multi --domain-check에서만 씁니다")
    if args.grade_reorder and args.graph != "multi":
        parser.error("--grade-reorder는 --graph multi에서만 씁니다")
    if args.llm_grading and args.graph == "multi":
        # 104번: 멀티 그래프는 검색 에이전트가 늘 라운드당 LLM 채점을 한다(단일 그래프의 --llm-grading과 다른 함수)
        parser.error("--llm-grading은 단일 그래프 전용입니다(멀티 그래프는 채점이 기본 경로)")

    dataset_path = Path(args.dataset)
    frozen_rows = _read_jsonl(dataset_path)
    dataset_sha256 = _sha256(dataset_path)
    if args.supplementary:
        # 137번: 동결 평가셋이 아닌 보충 세트(예: 말바꿈 질문). 요약에 표시가 남고, 해시가 달라 평가 회귀 검사는 통과하지 못한다.
        print(f"보충 평가셋으로 실행합니다(동결 검사 생략): {dataset_path}", file=sys.stderr)
    else:
        check_frozen(dataset_path, dataset_sha256)
    rows = frozen_rows[: args.limit] if args.limit else frozen_rows
    if args.ids:
        wanted = {int(part) for part in args.ids.split(",") if part.strip()}
        rows = [row for row in rows if int(row["id"]) in wanted]
        missing = wanted - {int(row["id"]) for row in rows}
        if missing:
            parser.error(f"--ids에 평가셋에 없는 id가 있습니다: {sorted(missing)}")

    if args.extract_filters:
        records = extract_supervisor_targets(rows, domain_rule=args.domain_check)
        output = Path(args.extract_filters)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8", newline="\n") as stream:
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"supervisor 추출 {len(records)}건 → {output}")
        return 0

    missing = [name for name in ("filters", "chunk_version", "output") if not getattr(args, name)]
    if missing:
        parser.error("측정에는 --" + ", --".join(name.replace("_", "-") for name in missing) + " 가 필요합니다")
    uses_spans = any(row.get("gold_spans") for row in frozen_rows)
    if uses_spans and (args.gold or not args.chunks):
        parser.error("gold_spans 평가셋(v2)은 --gold 대신 --chunks로 정답을 만든다")
    if not uses_spans and not args.gold:
        parser.error("청크 id 정답 평가셋(v1)은 --gold가 필요합니다")

    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True), override=False)
    from backend.retrieval_combo import build_retriever, settings_from_env
    from backend.services import llm

    news_client_calls: list[str] = []
    generation_model = args.generation_model or (
        llm.DEFAULT_WRITER_MODEL if args.graph == "multi" else llm.DEFAULT_GENERATION_MODEL)  # 122번
    judge_model = args.judge_model or llm.DEFAULT_JUDGE_MODEL
    graph_kwargs: dict[str, Any] = {
        "domain_check_enabled": args.domain_check,
        **({"domain_model": args.domain_model} if args.domain_model else {}),
        "generation_model": generation_model,
        "judge_model": judge_model,
        **({"writer_reasoning": {"effort": args.writer_reasoning_effort}} if args.writer_reasoning_effort else {}),
        **({"writer_reason_numbers": True} if args.writer_reason_numbers else {}),
    }
    if args.graph == "single":
        graph_kwargs["llm_grading_enabled"] = args.llm_grading
        if args.agents == "fake":
            graph_kwargs.update(FAKE_AGENTS)
    elif args.grade_reorder:
        graph_kwargs["grade_reorder"] = True
    if args.grade_to_writer:
        if args.graph != "multi":
            parser.error("--grade-to-writer는 --graph multi에서만 씁니다")
        graph_kwargs["grade_to_writer"] = True
    if args.search_grader_model:
        if args.graph != "multi":
            parser.error("--search-grader-model은 --graph multi에서만 씁니다")
        graph_kwargs["search_grader_model"] = args.search_grader_model
    if args.news_search:
        graph_kwargs.update({"news_search_enabled": True, "news_search": counting_news_search(news_client_calls)})
    if args.graph == "multi" and args.agents == "fake":
        # 104번 멀티 그래프의 가짜: 작성기·채점은 가짜, Supervisor는 코드 규칙(LLM 호출 없음)
        graph_kwargs.update({"draft_writer": fake_draft_writer, "supervisor_decider": fake_supervisor_decider,
                             "search_grader": fake_search_grader})

    filters_path = Path(args.filters)
    filters_by_id = {_row_id(r): r for r in _read_jsonl(filters_path)}
    settings = settings_from_env()
    query_embedder: PinnedQueryEmbedder | None = None
    if args.query_vectors:
        from backend.rag.embedding import create_embedder

        query_embedder = PinnedQueryEmbedder(
            create_embedder(settings.combo.embedding_model),
            Path(args.query_vectors),
            model_name=settings.combo.embedding_model,
        )
    retriever = build_retriever(settings, embedder=query_embedder)
    if f"-{args.chunk_version}-" not in str(retriever.info["chunks_path"]).replace("\\", "/"):
        raise GraphEvalError("--chunk-version이 RETRIEVAL_CHUNKS_PATH와 다릅니다")

    chunks_by_rcept = None
    retrieval_rows = None
    if uses_spans:
        # v2: 정답 청크는 검색과 같은 청크 파일에서 만들어야 한다(다르면 정답 id가 검색 결과와 안 맞음)
        chunks_path = Path(args.chunks)
        if _sha256(chunks_path) != retriever.info["chunks_sha256"]:
            raise GraphEvalError("--chunks가 RETRIEVAL_CHUNKS_PATH와 다른 파일입니다")
        # 정답을 만드는 관련 문항만 대조한다. v2 비관련 16~30번은 v1에서 옮겨 와 corpus_version이
        # v7 코퍼스로 적혀 있지만 정답 청크가 없어 채점에 쓰이지 않는다.
        if not args.allow_corpus_mismatch:
            check_corpus(
                [row for row in frozen_rows if row["kind"] == "relevant"], retriever.info["corpus_ids"], "평가셋 관련 문항"
            )
        chunks_by_rcept = load_span_chunks(frozen_rows, chunks_path)
        gold_rows = span_gold_rows(frozen_rows, chunks_by_rcept)
        gold_info = {
            "source": "gold_spans(evaluation.span_item_chunks)",
            "chunks_path": str(chunks_path),
            "chunks_sha256": retriever.info["chunks_sha256"],
            "citation_gold": "관련 문항의 모든 item 정답 청크 합집합(인용 중 하나라도 있으면 맞음)",
        }
    else:
        gold_rows = _read_jsonl(Path(args.gold))
        check_inputs(frozen_rows, gold_rows, retriever.info["corpus_ids"], dataset_sha256, dataset_path)
        gold_info = {"path": args.gold, "sha256": _sha256(Path(args.gold))}
    subset_info = None
    if args.retrieval_subset:
        retrieval_rows = _read_jsonl(Path(args.retrieval_subset))
        check_subset(frozen_rows, retrieval_rows)
        subset_info = {
            "path": args.retrieval_subset,
            "sha256": _sha256(Path(args.retrieval_subset)),
            "relevant": sum(row["kind"] == "relevant" for row in retrieval_rows),
        }

    started_at = datetime.now(timezone.utc).isoformat()
    node_timings: list[dict[str, Any]] | None = [] if args.node_timing else None
    trace_ids: dict[int, str] = {}
    responses = run_eval(
        rows,
        filters_by_id,
        retriever=retriever,
        graph_kwargs=graph_kwargs,
        query_embedder=query_embedder,
        graph_kind=args.graph,
        node_timings=node_timings,
        trace_ids=trace_ids,
        run_name=Path(args.output).stem,
        clarifications=(
            {int(row["id"]): str(row["reply"]) for row in _read_jsonl(Path(args.clarifications))}
            if args.clarifications else None
        ),
    )
    combo = retriever.combo
    summary = {
        "started_at": started_at,
        "git_commit": _git_commit(),
        "combo_id": combo.combo_id,
        "retrieval": retriever.info,
        "chunk_version": args.chunk_version,
        "allow_corpus_mismatch": bool(args.allow_corpus_mismatch),
        "models": {
            "embedding": f"{combo.embedding_model}@{combo.embedding_dim}",
            "reranker": combo.reranker,
            "scorer": judge_model if args.llm_grading else "off(회사·연도 결정적 검사만)",
            "rewriter": generation_model if args.agents == "real" else "fake",
            "writer": generation_model if args.agents == "real" else "fake",
            "writer_reasoning_effort": args.writer_reasoning_effort or (
                (llm.default_writer_reasoning(generation_model) or {}).get("effort") if args.graph == "multi" else None
            ) or "unset(공급자 기본값)",
            "writer_reason_numbers": bool(args.writer_reason_numbers),
            "supervisor": ("off(단일 그래프)" if args.graph == "single" else
                           "fake(코드 규칙)" if args.agents == "fake" else llm.DEFAULT_SUPERVISOR_MODEL),
            "supervisor_prompt_version": llm.SUPERVISOR_PROMPT_VERSION if args.graph == "multi" else None,
            "search_grader": ("off(단일 그래프)" if args.graph == "single" else
                              "fake" if args.agents == "fake" else
                              f"{args.search_grader_model or llm.DEFAULT_SEARCH_GRADER_MODEL}(grade_search_round)"),
            "rerank_top1_min_score": combo.rerank_top1_min_score if args.graph == "multi" else None,
            "domain_classifier": (args.domain_model or (
                llm.DEFAULT_GENERATION_MODEL if args.graph == "multi" else generation_model)) if args.domain_check else "off",
        },
        "graph": args.graph,
        "domain_check": args.domain_check,
        "agents": args.agents,
        "llm_grading": args.llm_grading,
        "dataset": {"path": str(dataset_path), "sha256": dataset_sha256, "supplementary": bool(args.supplementary)},
        "gold": gold_info,
        "retrieval_subset": subset_info,
        "query_vectors": None if query_embedder is None else {
            "path": args.query_vectors,
            "sha256": _sha256(Path(args.query_vectors)),
            "pinned_calls": query_embedder.sources.count("pinned"),
            "fresh_calls": query_embedder.sources.count("fresh"),
        },
        "filters": {
            "path": str(filters_path), "sha256": _sha256(filters_path),
            "source": "supervisor 추출(필터 파일과 대조, 불일치 시 중단)",
        },
        "limit": args.limit,
        "ids": args.ids,
        **({"news_search": news_summary(responses, news_client_calls)} if args.news_search else {}),
        "llm_usage": usage_totals(responses),
        "metrics": summarize(
            gold_rows, responses, retrieval_rows=retrieval_rows, chunks_by_rcept=chunks_by_rcept
        ),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for row in responses:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    if node_timings is not None:
        with output.with_suffix(".node_ms.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for row in node_timings:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    output.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(summary["metrics"], ensure_ascii=False, indent=2))
    _score_traces(gold_rows, responses, trace_ids, output.stem)
    return 0


def _score_traces(gold_rows: list[dict], responses: list[dict], trace_ids: dict[int, str], run_name: str) -> None:
    """G6: 문항별 응답 지표(0/1)를 그 문항의 Langfuse 트레이스에 점수로 붙이고 보낸다. 키가 없으면 아무것도 안 한다."""

    from backend.services import tracing
    from evals.response_metrics import per_question_flags

    if not trace_ids:
        return
    responses_by_id = {row["id"]: row for row in responses}
    for gold in gold_rows:
        row_id = _row_id(gold)
        if row_id not in trace_ids or row_id not in responses_by_id:
            continue
        for name, value in per_question_flags(gold, responses_by_id[row_id]).items():
            tracing.score(trace_ids[row_id], name, value, comment=run_name)
    tracing.flush()
    print(f"Langfuse: 트레이스 {len(trace_ids)}개에 문항별 점수를 붙였습니다")


if __name__ == "__main__":
    raise SystemExit(main())
