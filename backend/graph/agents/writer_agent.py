"""작성·검증 에이전트 서브그래프 (D5 A3, docs/specs/d5-supervisor-split.md, 104번 보고 추가).

writer → verifier → report를 단일 그래프(build.py)의 노드 함수로 그대로 조립한다(verifier는 backend/graph/verifier.py 하나). 작성 로직(근거 부족·0건
거절·인용 없는 확정 판정 하향)은 build.py의 _writer_node에 있고 복사하지 않는다.
Supervisor는 select_writer_input()으로 고른 키만 넘긴다(선별 인계, 3주16).
104번: 끝에 report 노드가 Supervisor가 읽을 보고(writer_report: has_draft·verification_passed·mismatch_items)를 만든다.
다시 쓰기는 Supervisor가 writer_feedback을 실어 이 서브그래프를 한 번 더 부르는 것이다(별도 redraft 노드 없음).
"""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.graph.build import _route_after_writer, _verifier_node, _writer_node
from backend.services import llm, tracing


class WriterInput(TypedDict, total=False):
    """Supervisor → 작성 에이전트 인계 계약.

    retrieval_sufficient·retrieval_empty는 _writer_node가 0건 거절·근거 부족 분기에 쓰므로 함께 넘긴다
    (넘기지 않으면 노드 함수를 고쳐야 해서 '구조만 바꾼다'를 어긴다. 설계 문서 '확인 필요 1').
    """

    question: str
    checklist_items: list[str]
    retrieved_chunks: list[dict[str, Any]]
    retrieval_sufficient: bool
    retrieval_empty: bool
    # 72번: supervisor 주제 판정 결과. 작성기의 '공시 안 됨'과 함께 볼 때만 ITEM_NOT_DISCLOSED로 거절한다.
    domain_check: str
    applied_rules: list[dict[str, Any]]
    # 104번: Supervisor가 다시 쓰기를 지시할 때만 값이 있다. 평소엔 빈 값이라 작성기 지시문이 그대로다.
    writer_feedback: str
    # 125번(grade_to_writer): 채점이 청크에서 찾지 못했다고 본 항목. 비어 있으면 작성기 지시문·입력이 그대로다.
    grader_missing_items: list[str]


class WriterOutput(TypedDict, total=False):
    """작성 에이전트 → Supervisor 인계 계약."""

    outcome: str
    refusal_reason: str | None
    draft_items: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    node_events: list[str]
    verification: list[dict[str, Any]]
    writer_report: dict[str, Any]  # 104번: Supervisor가 읽는 보고(본문·인용문 없음)


class WriterState(WriterInput, WriterOutput, total=False):
    """작성 에이전트 내부 State."""


WRITER_INPUT_KEYS = WriterInput.__required_keys__ | WriterInput.__optional_keys__
WRITER_OUTPUT_KEYS = WriterOutput.__required_keys__ | WriterOutput.__optional_keys__


def select_writer_input(state: dict[str, Any]) -> WriterInput:
    """부모 State에서 계약 키만 고른다. 검색 질의·재작성 이력·대화 식별자는 넘기지 않는다."""

    return {key: state[key] for key in WRITER_INPUT_KEYS if key in state}  # type: ignore[return-value]


# 112번: 초안 2개 동시 작성의 엄격 지시. 105번 다시 쓰기 문장에서 숫자 목록만 뺀 것이다.
STRICT_DRAFT_FEEDBACK = (
    "reason에는 인용 청크에 적힌 숫자만 그대로 쓰고, 직접 세거나 계산한 숫자나 질문의 표현을 숫자로 옮겨 쓰지 마세요. "
    "인용과 reason에는 청크 본문에 그대로 있는 내용만 쓰세요."
)
_DUAL_EXECUTOR = None
# 120번: 엄격 초안이 먼저 끝났을 때 보통 초안을 더 기다려 주는 시간. 이 안에 보통 초안이 끝나면 지금처럼 보통을 먼저 본다.
# 거의 동시에 끝난 두 초안의 순서가 스레드 타이밍에 따라 뒤바뀌지 않게 하는 여유이기도 하다.
STRICT_DRAFT_LEAD_SECONDS = 1.0
CONFIDENT_VERDICTS = {"applicable", "not_applicable"}


def _confident(output: dict[str, Any]) -> bool:
    """기다리지 않고 써도 되는 초안인가: 거절이 아니고, 검증을 통과했고, 모든 항목이 확정 판정이다.

    확인 필요·공시 안 됨·형식 오류 자리 표시 답은 보통 초안이 더 나을 수 있으므로 기다린다.
    """

    report = output.get("writer_report") or {}
    drafts = output.get("draft_items") or []
    return (
        output.get("outcome") != "refused"
        and report.get("verification_passed") is True
        and bool(drafts)
        and all(str(item.get("verdict")) in CONFIDENT_VERDICTS for item in drafts)
    )


def _strict_if_clearly_first(normal, strict) -> dict[str, Any] | None:
    """120번: 엄격 초안이 보통 초안보다 STRICT_DRAFT_LEAD_SECONDS 넘게 먼저 끝났고 확정·검증 통과면 그 결과를 돌려준다.

    그 밖에는 None(지금처럼 보통 초안을 먼저 본다). 보통 초안은 취소하지 않는다(끝나면 버린다. 비용은 든다).
    """

    from concurrent.futures import FIRST_COMPLETED, wait

    wait([normal, strict], return_when=FIRST_COMPLETED)
    if normal.done():
        return None
    try:
        candidate = strict.result()
    except Exception:  # 엄격 초안이 실패했으면 보통 초안을 기다린다
        return None
    if not _confident(candidate):
        return None
    done, _ = wait([normal], timeout=STRICT_DRAFT_LEAD_SECONDS)
    return None if done else candidate


def _submit(fn, *args):
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    global _DUAL_EXECUTOR
    if _DUAL_EXECUTOR is None:
        _DUAL_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dual-draft")
    return _DUAL_EXECUTOR.submit(contextvars.copy_context().run, fn, *args)


def writer_agent_node(writer_agent, speculation=None, strict_feedback: str | None = None, first_verified: bool = True):
    """Supervisor 그래프의 어댑터 노드: 계약 키만 넘겨 부르고, 결과를 부모 State에 취합한다.

    112번(strict_feedback이 있을 때): 다시 쓰기가 남은 첫 작성은 초안 2개를 동시에 만든다(보통 + 엄격 지시).
    보통 초안이 검증을 통과하면 그것을, 실패하면 엄격 초안이 통과했을 때 그것을 쓴다. 엄격 초안까지 봤으면
    다시 쓰기 1회를 쓴 것으로 센다(_redraft_used, supervisor_flow.writer_call이 센다).
    120번(first_verified): 엄격 초안이 확실히 먼저 끝났고 확정·검증 통과면 보통 초안을 기다리지 않고 그것을 쓴다
    (보통 초안이 실패한 것이 아니므로 다시 쓰기로 세지 않는다). 초안 하나가 늦는 회차의 꼬리 시간을 줄이려는 것이다.
    """

    def run(state: dict[str, Any]) -> dict[str, Any]:
        handoff = select_writer_input(state)
        dual = bool(strict_feedback) and not handoff.get("writer_feedback") and not int(state.get("redraft_calls", 0))
        # 110번: 같은 입력(같은 청크·같은 지시)으로 미리 돌린 결과가 있으면 그것을 쓴다
        prefetched = speculation.take_writer(handoff) if speculation is not None else None
        # Langfuse: 이 방문의 writer_agent 관측과 초안별 관측(draft_normal·draft_strict·draft_redraft).
        # 초안 하나 = 서브그래프(writer → verifier → report) 실행 한 번. 미리 쓴 것을 쓰면 그 관측을 이어받는다.
        redraft = bool(handoff.get("writer_feedback"))
        agent_meta = {"attempt": int(state.get("writer_calls", 0)) + 1, "redraft": redraft, "speculative": False}
        strict_trace = None
        if prefetched is not None:
            normal, strict = prefetched
            agent_trace, normal_trace, strict_trace = speculation.taken_writer_trace or (
                tracing.open_agent("writer_agent", runs=False), None, None)
        elif dual:
            agent_trace = tracing.open_agent("writer_agent", metadata=agent_meta, runs=False)
            normal_trace, strict_trace = agent_trace.child("draft_normal"), agent_trace.child("draft_strict")
            normal = _submit(normal_trace.run, writer_agent.invoke, handoff)
            strict = _submit(strict_trace.run, writer_agent.invoke, {**handoff, "writer_feedback": strict_feedback})
        else:
            normal, strict = None, None
            agent_trace = tracing.open_agent("writer_agent", metadata=agent_meta, runs=False)
            normal_trace = agent_trace.child("draft_redraft" if redraft else "draft_normal")
        if not dual:
            strict = None
        selected = "normal"  # 어느 초안을 썼는가(기록용)
        strict_first = None
        if first_verified and normal is not None and strict is not None:
            strict_first = _strict_if_clearly_first(normal, strict)
        try:
            if strict_first is not None:
                output, selected = strict_first, "strict"
            else:
                output = normal.result() if normal is not None else normal_trace.run(writer_agent.invoke, handoff)
        except BaseException:
            for handle in (normal_trace, strict_trace):
                if handle is not None:
                    handle.resolve(discarded=False)
            agent_trace.resolve(discarded=False)
            raise
        extra_events: list[str] = []
        redraft_used = False
        placeholder = any(str(item.get("reason")) == FORMAT_ERROR_REASON for item in output.get("draft_items") or [])
        if strict is not None and placeholder:
            # 114번: 보통 초안이 형식 오류 자리 표시 답이면, 정상으로 끝난 엄격 초안을 쓴다(검증 결과와 무관)
            try:
                candidate = strict.result()
            except Exception:
                candidate = None
            if candidate is not None and not any(
                str(item.get("reason")) == FORMAT_ERROR_REASON for item in candidate.get("draft_items") or []
            ):
                output = candidate
                selected = "strict"
                extra_events = ["writer:strict_draft_after_format_error"]
                redraft_used = True
        elif strict is not None and (output.get("writer_report") or {}).get("verification_passed") is False:
            redraft_used = True
            try:
                candidate = strict.result()
            except Exception:  # 엄격 초안이 실패해도 보통 초안은 그대로 쓴다
                candidate = None
            if candidate is not None and (candidate.get("writer_report") or {}).get("verification_passed") is True:
                output = candidate
                selected = "strict"
                extra_events = ["writer:strict_draft"]
        if strict_first is not None:
            extra_events = ["writer:strict_draft_first"]
        # 쓰지 않은 초안은 discarded=true(아직 돌고 있으면 끝났을 때 닫힌다). 기록만 하고 결과에는 관여하지 않는다.
        if normal_trace is not None:
            normal_trace.resolve(discarded=selected != "normal")
        if strict_trace is not None:
            strict_trace.resolve(discarded=selected != "strict")
        agent_trace.resolve(discarded=False, output=_report_summary(output.get("writer_report"), selected))
        return {
            **output,
            "node_events": [*state.get("node_events", []), *output.get("node_events", []), *extra_events],
            "_redraft_used": redraft_used,
        }

    return run


def _report_summary(report: Any, selected: str) -> dict[str, Any]:
    """Langfuse에 남길 작성 보고 요약(판정·검사 결과만. 초안 문장·인용문은 넣지 않는다)."""

    report = report if isinstance(report, dict) else {}
    return {"selected": selected, **{key: report.get(key) for key in (
        "outcome", "refusal_reason", "has_draft", "verification_passed", "failed_checks")}}


def writer_report_node(state: dict[str, Any]) -> dict[str, Any]:
    """작성 결과를 Supervisor용 보고로 줄인다. 초안 문장·인용문은 넣지 않는다(항목 이름·판정·검사 이름만)."""

    failed = [v for v in state.get("verification") or [] if not v.get("passed")]
    drafts = list(state.get("draft_items") or [])
    return {
        "writer_report": {
            "outcome": state.get("outcome"),
            "refusal_reason": state.get("refusal_reason"),
            "has_draft": bool(drafts),
            "verdicts": {str(d.get("item")): d.get("verdict") for d in drafts},
            "verification_passed": None if state.get("outcome") == "refused" else not failed,
            "mismatch_items": sorted({str(v.get("item")) for v in failed}),
            "failed_checks": sorted({str(c) for v in failed for c in v.get("failed_checks") or []}),
        }
    }


FORMAT_ERROR_REASON = "작성 형식 오류: 초안을 만들지 못했습니다. 확인이 필요합니다."


def tolerate_format_error(draft_writer):
    """111번: 작성 LLM이 형식이 틀린 답을 끝까지 내면(llm.ExternalApiResponseError) 질문 전체를 실패시키지 않고
    항목마다 needs_check·인용 없음으로 돌려준다(이전: 서비스 502, 평가 실행 전체 중단). 다른 오류는 그대로 올린다."""

    def run(state, chunks):
        try:
            return draft_writer(state, chunks)
        except llm.ExternalApiResponseError:
            pass
        try:  # 114번: 드문 흔들림이라 한 번 더 부른다
            return draft_writer(state, chunks)
        except llm.ExternalApiResponseError:
            items = list(state.get("checklist_items") or []) or [str(state.get("question", ""))]
            return [{"item": item, "verdict": "needs_check", "reason": FORMAT_ERROR_REASON, "chunk_ids": [], "evidence": []}
                    for item in items]

    return run


def build_writer_agent(
    *,
    draft_writer=None,
    generation_model: str = llm.DEFAULT_GENERATION_MODEL,
    writer_reasoning: dict | None = None,
    writer_reason_numbers: bool = False,
):
    # 기본 작성 함수는 build_graph와 같은 llm.write_drafts·같은 모델을 쓴다.
    # writer_reasoning(97번): 작성(다시 쓰기 포함) 호출에만 OpenRouter 추론 설정을 보낸다. None이면 이전과 같다.
    if draft_writer is None:
        def draft_writer(state, chunks):
            return llm.write_drafts(
                question=str(state.get("question", "")),
                checklist_items=list(state.get("checklist_items", [])),
                chunks=chunks,
                applied_rules=list(state.get("applied_rules") or []),
                model=generation_model,
                # 73번: 주제 판정이 '기재 사항 아님'으로 표시한 질문에만 '공시 안 됨' 선택지를 준다
                allow_not_disclosed=state.get("domain_check") == "not_disclosed_item",
                feedback=str(state.get("writer_feedback") or "") or None,
                **({"missing_items": list(state["grader_missing_items"])} if state.get("grader_missing_items") else {}),
                **({"reasoning": writer_reasoning} if writer_reasoning is not None else {}),
                # 98번: reason에 근거 숫자 적기 문장(끄면 지시문이 이전과 같다)
                **({"reason_numbers": True} if writer_reason_numbers else {}),
            )

    graph = StateGraph(WriterState, input_schema=WriterInput, output_schema=WriterOutput)
    # tracing.node: 노드 실행 한 번을 Langfuse 관측 하나로 남기는 래퍼(인자·반환값은 그대로, 꺼져 있으면 통과)
    graph.add_node("writer", tracing.node("writer", _writer_node(tolerate_format_error(draft_writer))))
    graph.add_node("verifier", tracing.node("verifier", _verifier_node))
    graph.add_node("report", tracing.node("report", writer_report_node))

    graph.add_edge(START, "writer")
    # 단일 그래프와 같은 분기: 거절(ITEM_NOT_DISCLOSED)이면 verifier 없이 보고로 간다.
    graph.add_conditional_edges(
        "writer", _route_after_writer, {"verifier": "verifier", END: "report"}
    )
    graph.add_edge("verifier", "report")
    graph.add_edge("report", END)
    return graph.compile()
