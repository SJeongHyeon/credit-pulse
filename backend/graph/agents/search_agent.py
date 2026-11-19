"""검색 에이전트 서브그래프 (사전 등록 104번, SPEC §5.3).

retriever → scorer(회사·연도 결정적 검사) → grader(리랭커 1위 점수 게이트 → LLM 채점 1회) → 끝.
안쪽 재작성 루프는 없다. 첫 검색은 원래 질문으로 하고, Supervisor LLM이 재검색을 고르면 그 질의로 한 번 더 한다(165번, 상한 2회).

- 입력(SearchInput): 질문·회사·연도·필터·비교 대상뿐(P16 R5·R6).
- 출력(SearchOutput, output_schema): 작성기에 넘길 청크와 Supervisor가 읽을 보고(search_report)만 부모에 돌려준다(P11 R7).
  보고: {sufficient, missing_items, chunk_ids, n_chunks, top1_score, graded}. Supervisor LLM에는 청크 본문이 가지 않는다.
- 채점은 청크를 걸러 내지 않는다(작성기 입력은 회사·연도 검사 뒤 그대로). 채점 결과는 Supervisor 판단에만 쓴다.
- 리랭커 1위 점수가 임계값(사전 등록 103번, 검색 조합의 설정값) 미만이면 LLM 채점을 부르지 않고 '부족'으로 보고한다(P10 R3).
- 채점 LLM이 없거나(키 없음·데모 모드) 실패하면 채점 없이 '청크가 있으면 충분'으로 보고하고 이벤트에 남긴다.
"""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.graph.build import _retriever_node, _scorer_node
from backend.graph.trust_boundary import safe_missing_items
from backend.services import llm, tracing

BELOW_THRESHOLD = "리랭커 점수 임계값 미달"
# 165번: 재검색이 덧붙이는 새 청크 수 상한. 첫 검색 청크는 빼지 않는다(139번: 재검색이 있던 정답을 잃었다).
RESEARCH_NEW_CHUNKS = 5
NO_EVIDENCE = "검색된 근거 없음"


class SearchInput(TypedDict, total=False):
    """Supervisor → 검색 에이전트 인계 계약."""

    question: str
    corp_code: str
    corp_name: str
    fiscal_year: int
    checklist_items: list[str]
    filters: dict[str, Any]
    comparison_targets: list[dict[str, Any]]  # 63번: 비교 질문의 회사별 검색 대상
    search_query: str  # 165번: 재검색 질의(Supervisor LLM이 씀). 없으면 원래 질문으로 검색한다
    previous_chunks: list[dict[str, Any]]  # 165번: 재검색일 때 첫 검색의 청크(빼지 않고 앞에 둔다)
    previous_missing_items: list[str]  # 166번: 재검색일 때 첫 채점이 찾지 못했다고 본 항목(작성기에 그대로 알린다)


class SearchOutput(TypedDict, total=False):
    """검색 에이전트 → Supervisor 인계 계약."""

    retrieved_chunks: list[dict[str, Any]]
    retrieval_sufficient: bool  # 작성기용: 회사·연도 검사를 통과한 청크가 있는가(LLM 채점과 무관, 지금 작성기 규칙 그대로)
    retrieval_empty: bool
    node_events: list[str]
    search_report: dict[str, Any]
    grader_missing_items: list[str]  # 125번: 채점이 찾지 못했다고 본 항목(grade_to_writer일 때만 채운다)


class SearchState(SearchInput, SearchOutput, total=False):
    """검색 에이전트 내부 State."""

    retrieval_query: str
    retrieval_rounds: int


SEARCH_INPUT_KEYS = SearchInput.__required_keys__ | SearchInput.__optional_keys__
RESEARCH_KEYS = frozenset({"search_query", "previous_chunks", "previous_missing_items"})
SEARCH_OUTPUT_KEYS = SearchOutput.__required_keys__ | SearchOutput.__optional_keys__


def select_search_input(state: dict[str, Any]) -> SearchInput:
    """부모 State에서 계약 키만 고른다. 대화 식별자·이전 보고·작성 결과는 넘기지 않는다.

    재검색 칸(search_query·previous_chunks)은 여기서 넘기지 않는다. 재검색일 때만 어댑터 노드가 채운다(165번).
    미리 돌린 첫 검색(110번)과 실제 첫 검색의 인계가 같아야 그 결과를 쓸 수 있다.
    """

    return {key: state[key] for key in SEARCH_INPUT_KEYS - RESEARCH_KEYS if key in state}  # type: ignore[return-value]


def search_agent_node(search_agent, speculation=None):
    """Supervisor 그래프의 어댑터 노드: 계약 키만 넘겨 부르고, 결과를 부모 State에 취합한다.

    첫 검색은 원래 질문으로 한다. Supervisor가 재검색을 고른 방문에서는 그 질의로 한 번 더 검색한다(165번, 상한 2회).
    """

    def run(state: dict[str, Any]) -> dict[str, Any]:
        handoff = select_search_input(state)
        research = bool(state.get("search_calls")) and bool(state.get("search_query"))
        if research:  # 165번: 재검색일 때만 질의와 첫 검색 청크를 넘긴다
            handoff["search_query"] = str(state["search_query"])
            handoff["previous_chunks"] = list(state.get("retrieved_chunks") or [])
            handoff["previous_missing_items"] = [
                str(item) for item in (state.get("search_report") or {}).get("missing_items") or []]
        # 110번: 같은 입력으로 미리 돌린 결과가 있으면 그것을 쓴다(결과·예외는 직접 부른 것과 같다)
        prefetched = speculation.take_search(handoff) if speculation is not None else None
        # Langfuse: 이 방문의 search_agent 관측. 미리 돌린 것을 쓰면 그 관측을 이어받는다(speculative=true 그대로).
        if prefetched is not None:
            trace = speculation.taken_search_trace or tracing.open_agent("search_agent", runs=False)
        else:
            trace = tracing.open_agent("search_agent", metadata={"speculative": False})
        try:
            output = prefetched.result() if prefetched is not None else trace.run(search_agent.invoke, handoff)
        except BaseException:
            trace.resolve(discarded=False)
            raise
        trace.resolve(discarded=False, output=_report_summary(output.get("search_report")))
        report = dict(output.get("search_report") or {})
        updates: dict[str, Any] = {
            **output,
            "retrieval_rounds": 2 if research else 1,
            "node_events": [*state.get("node_events", []), *output.get("node_events", [])],
        }
        updates["search_report"] = report
        return updates

    return run


def _report_summary(report: Any) -> dict[str, Any]:
    """Langfuse에 남길 검색 보고 요약(개수·점수·충분 여부만. 청크 본문·빠진 항목 문장은 넣지 않는다)."""

    report = report if isinstance(report, dict) else {}
    return {key: report.get(key) for key in ("sufficient", "n_chunks", "top1_score", "graded")}


def _with_query(node):
    """retriever 노드에 원래 질문을 검색 질의로 넣는다(노드 로직은 build.py 그대로. _retriever_node가 회차에 1을 더한다)."""

    def run(state: dict[str, Any]) -> dict[str, Any]:
        # 165번: 재검색이면 Supervisor LLM이 쓴 질의로, 아니면 원래 질문으로 검색한다.
        query = str(state.get("search_query") or state.get("question", ""))
        return node({**state, "retrieval_query": query, "retrieval_rounds": 1 if state.get("search_query") else 0})

    return run


def merge_research_chunks(previous: list[dict[str, Any]], found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """165번: 첫 검색 청크를 그대로 두고, 재검색이 새로 찾은 청크를 순위대로 RESEARCH_NEW_CHUNKS개까지 덧붙인다."""

    seen = {str(chunk.get("chunk_id")) for chunk in previous}
    fresh = [chunk for chunk in found if str(chunk.get("chunk_id")) not in seen]
    return [*previous, *fresh[:RESEARCH_NEW_CHUNKS]]


def _top1_score(chunks: list[dict[str, Any]]) -> float | None:
    scores = [chunk.get("score") for chunk in chunks]
    if not scores or not all(isinstance(score, (int, float)) for score in scores):
        return None  # 리랭커 점수가 없는 검색기(시험용 가짜 등)는 게이트를 쓰지 않는다
    return float(max(scores))


def grader_node(grader, min_score: float | None, on_chunks=None, reorder: bool = False,
                to_writer: bool = False, graph_low_score_review: bool = False):
    """grader(state, chunks) -> {"relevant_chunk_ids", "sufficient", "missing_items"}. None이면 채점하지 않는다.

    to_writer(125·126·140번): 채점 결과를 작성기에 넘긴다. 고른 청크를 앞에 둔다(reorder와 같음).
    채점이 부족하다고 하면 찾지 못한 항목을 grader_missing_items로 작성기에 알린다(재검색 여부는 Supervisor가 정한다, 165번).
    """

    reorder = reorder or to_writer

    def run(state: dict[str, Any]) -> dict[str, Any]:
        chunks = list(state.get("retrieved_chunks") or [])
        previous = list(state.get("previous_chunks") or [])
        if previous:  # 165번: 재검색. 첫 검색 청크 뒤에 새 청크(최대 5개)를 붙인다. 새 청크는 아래에서 채점해 거른다(168번)
            chunks = merge_research_chunks(previous, chunks)
        if on_chunks is not None and not reorder:
            on_chunks(state)  # 110번: 채점을 기다리지 않고 작성을 미리 시작할 수 있게 청크를 알린다
        top1 = _top1_score(chunks)
        reordered: list[dict[str, Any]] | None = None
        report: dict[str, Any] = {"n_chunks": len(chunks), "top1_score": None if top1 is None else round(top1, 4),
                                  "graded": False, "chunk_ids": [str(c.get("chunk_id")) for c in chunks]}
        low_graph_review = bool(
            graph_low_score_review and chunks and min_score is not None and top1 is not None
            and top1 < min_score and str(chunks[0].get("retrieval_method") or "").endswith("+graph:source")
        )
        if low_graph_review:
            report["graph_low_score_reviewed"] = True
        if not chunks:
            report.update({"sufficient": False, "missing_items": [NO_EVIDENCE]})
            event = "grader:empty"
        elif previous:
            # 168번: 재검색이 덧붙인 새 청크만 채점하고, 맞다고 본 것만 남긴다(첫 검색 청크는 그대로).
            # 맞는 새 청크가 없으면 작성기 입력이 첫 검색 때와 같아져 미리 써 둔 초안을 그대로 쓴다(110번).
            # 작성기에는 첫 채점이 찾지 못했다고 본 항목을 그대로 알린다.
            fresh = chunks[len(previous):]
            kept, graded = fresh, False
            if grader is not None and fresh:
                try:
                    result = grader(state, fresh)
                except (llm.ExternalApiKeyMissing, llm.ExternalApiResponseError,
                        llm.ExternalApiStatusError, llm.ExternalApiTimeout):
                    kept = []  # 채점하지 못한 새 청크는 덧붙이지 않는다(167번: 채점 없이 넘기자 오답 근거가 났다)
                else:
                    wanted = {str(cid) for cid in result.get("relevant_chunk_ids") or []}
                    kept, graded = [c for c in fresh if str(c.get("chunk_id")) in wanted], True
            chunks = [*previous, *kept]
            top1 = _top1_score(chunks)
            report.update({
                "n_chunks": len(chunks), "top1_score": None if top1 is None else round(top1, 4),
                "chunk_ids": [str(c.get("chunk_id")) for c in chunks], "graded": graded,
                # 임계값 미달·근거 없음 표식은 작성기에 알릴 '빠진 항목'이 아니므로 뺀다
                "sufficient": False, "missing_items": [
                    item for item in state.get("previous_missing_items") or [] if item not in (BELOW_THRESHOLD, NO_EVIDENCE)],
                "research": True, "n_new_chunks": len(kept),
            })
            event = f"grader:research_kept={len(kept)}"
        elif min_score is not None and top1 is not None and top1 < min_score and not low_graph_review:
            report.update({"sufficient": False, "missing_items": [BELOW_THRESHOLD]})
            event = "grader:below_threshold"
        elif grader is None:
            report.update({"sufficient": not low_graph_review,
                           "missing_items": [BELOW_THRESHOLD] if low_graph_review else []})
            event = "grader:review_unavailable" if low_graph_review else "grader:off"
        else:
            try:
                result = grader(state, chunks)
            except (llm.ExternalApiKeyMissing, llm.ExternalApiResponseError,
                    llm.ExternalApiStatusError, llm.ExternalApiTimeout) as exc:
                report.update({"sufficient": not low_graph_review,
                               "missing_items": [BELOW_THRESHOLD] if low_graph_review else []})
                event = f"grader:fallback:{type(exc).__name__}"
            else:
                relevant = [str(cid) for cid in result.get("relevant_chunk_ids") or []]
                sufficient = bool(result.get("sufficient"))
                if low_graph_review and str(chunks[0].get("chunk_id")) not in relevant:
                    sufficient = False
                # 130번: 빠진 항목은 Supervisor LLM과 작성기 입력으로 넘어간다. 지시형 문장이 실려 가지 않게 거른다.
                missing, dropped = safe_missing_items(result.get("missing_items") or [])
                if dropped:
                    report["missing_items_dropped"] = dropped
                if low_graph_review and not sufficient and not missing:
                    missing = [BELOW_THRESHOLD]
                report.update({
                    "sufficient": sufficient,
                    "missing_items": [] if sufficient else missing,
                    "chunk_ids": relevant,
                    "graded": True,
                })
                # 117번(기본 꺼짐): 채점이 '맞다'고 본 청크를 작성기 입력 앞쪽에 둔다(순서만 바꾸고 빼지 않는다).
                # 켜면 3회 측정에서 정답 근거 +1·오답 근거 −1/회, 대신 초안을 채점 뒤에 시작해 p50 +1.9초·p95 +3.3초.
                wanted = set(relevant)
                if reorder and wanted and len(wanted) < len(chunks):
                    reordered = ([c for c in chunks if str(c.get("chunk_id")) in wanted]
                                 + [c for c in chunks if str(c.get("chunk_id")) not in wanted])
                event = f"grader:{'sufficient' if sufficient else 'insufficient'}"
        updates: dict[str, Any] = {"search_report": report, "node_events": [*state.get("node_events", []), event]}
        if reordered is not None:
            updates["retrieved_chunks"] = reordered
        elif previous:
            updates["retrieved_chunks"] = chunks
        if previous:  # 재검색이 0건이어도 첫 검색 청크가 있으면 근거가 있는 것이다
            updates["retrieval_empty"] = not chunks
            updates["retrieval_sufficient"] = bool(chunks)
        if to_writer:
            still_missing = (report.get("graded") or report.get("research")) and not report.get("sufficient")
            updates["grader_missing_items"] = list(report.get("missing_items") or []) if still_missing else []
        if on_chunks is not None and reorder:  # 117번: 순서를 정한 뒤(채점 뒤)에 알린다
            on_chunks({**state, **{key: updates[key] for key in ("retrieved_chunks", "grader_missing_items") if key in updates}})
        return updates

    return run


def build_search_agent(
    *,
    retriever=None,
    scorer=None,
    grader=None,
    judge_model: str = llm.DEFAULT_JUDGE_MODEL,
    grading_enabled: bool = True,
    rerank_min_score: float | None = None,
    graph_low_score_review: bool = False,
    on_chunks=None,
    grade_reorder: bool = False,
    grade_to_writer: bool = False,
):
    """grader를 주입하지 않으면 llm.grade_search_round(judge_model)를 쓴다. grading_enabled=False면 채점하지 않는다."""

    if grading_enabled and grader is None:
        def grader(state, chunks):
            return llm.grade_search_round(
                question=str(state.get("question", "")),
                checklist_items=list(state.get("checklist_items", [])),
                chunks=chunks,
                model=judge_model,
            )
    if not grading_enabled:
        grader = None

    graph = StateGraph(SearchState, input_schema=SearchInput, output_schema=SearchOutput)
    # tracing.node: 노드 실행 한 번을 Langfuse 관측 하나로 남기는 래퍼(인자·반환값은 그대로, 꺼져 있으면 통과)
    graph.add_node("retriever", tracing.node("retriever", _with_query(_retriever_node(retriever)), as_type="retriever"))
    # 회사·연도 결정적 검사만(LLM 채점은 grader 노드가 따로 한다)
    graph.add_node("scorer", tracing.node("scorer", _scorer_node(scorer, None, False)))
    graph.add_node("grader", tracing.node(
        "grader", grader_node(grader, rerank_min_score, on_chunks, grade_reorder, grade_to_writer,
                              graph_low_score_review),
        summarize=lambda result: _report_summary(result.get("search_report"))))
    graph.add_edge(START, "retriever")
    graph.add_edge("retriever", "scorer")
    graph.add_edge("scorer", "grader")
    graph.add_edge("grader", END)
    # 서브그래프는 checkpointer 없이 compile한다(부모 그래프의 checkpointer만 쓴다).
    return graph.compile()
