"""사람 승인(interrupt)과 멱등 메모 저장 노드 (D5, docs/specs/approval-checkpoint.md).

단일(build_graph)·멀티(build_supervisor_graph) 그래프가 같은 노드를 쓰도록 여기 한 곳에 둔다.
강의 근거(2주):
  - 05 checkpointer: 상태는 thread_id별로 체크포인트에 남고, 같은 thread_id로 이어서 실행한다.
  - 09 interrupt: interrupt()는 예외가 아니라 정상 반환이며, Command(resume=값)의 값이 interrupt()의 반환값이 된다.
    재개하면 멈춘 노드는 첫 줄부터 다시 돈다 → interrupt 앞에 부작용(저장·LLM 호출)을 두지 않는다.
  - 11 재실행 부작용·멱등성: 저장은 승인 뒤 별도 노드(save_memo)에서, 요청을 유일하게 식별하는 멱등키로 한다.
  - 12 승인 흐름: 준비 → 승인요청 → 실행 3노드. resume 값은 클라이언트가 보내므로 서버가 허용목록으로 검증하고,
    승인자는 클라이언트 값이 아니라 인증된 사용자로 덮어쓴다.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import interrupt

from backend.graph.checklist import infer_checklist_item
from backend.memory import RuleLimitError

logger = logging.getLogger(__name__)

VERDICTS = frozenset({"applicable", "not_applicable", "needs_check"})
RESUME_ACTIONS = frozenset({"approve", "cancel"})


class ApprovalInputError(ValueError):
    """재개 값이 대기 중인 승인 요청과 맞지 않을 때 발생한다(저장하지 않고 멈춘다)."""


def _append_event(state: dict, event: str) -> list[str]:
    return [*state.get("node_events", []), event]


def thread_id_for(user_id: str, review_id: str) -> str:
    """SPEC §3.5: thread_id = "{user_id}:{review_id}". 서버가 인증 정보로 만든다."""

    return f"{user_id}:{review_id}"


def is_identified_review(state: dict) -> bool:
    return bool(state.get("user_id") and state.get("review_id"))


def _active(state: dict, approval_enabled: bool) -> bool:
    # 평가 러너처럼 승인을 끈 실행은 interrupt 없이 지나간다(D4·58·64번 측정과 같은 경로).
    return approval_enabled and is_identified_review(state)


def approval_prepare_node(approval_enabled: bool):
    """승인 대기 상태를 체크포인트에 먼저 남긴다. 부작용 없음(읽기와 값 계산만)."""

    def run(state: dict, config: RunnableConfig) -> dict:
        if not _active(state, approval_enabled):
            return {"node_events": _append_event(state, "approval_prepare:skip")}
        expected = thread_id_for(str(state["user_id"]), str(state["review_id"]))
        if config.get("configurable", {}).get("thread_id") != expected:
            # 다른 사용자의 thread로 승인 대기를 만들지 않는다(대화 격리, 2주06).
            raise ApprovalInputError("thread_id가 심사 건(user_id:review_id)과 다릅니다")
        # 작성기의 입력(item=질문)을 그대로 둬 키 없는 데모의 기록 해시를 유지한다.
        # 승인 직전에만 명확한 단일 주제를 정식 코드로 정리해 화면·메모가 같은 항목을 쓴다.
        drafts = list(state.get("draft_items") or [])
        question = str(state.get("question") or "").strip()
        mapped = None
        if len(drafts) == 1 and str(drafts[0].get("item", "")).strip() == question:
            item = infer_checklist_item(question)
            if item is not None:
                mapped = [{**drafts[0], "item": item}]
        return {
            "status": "awaiting_approval",
            "outcome": "awaiting_approval",
            "approval_seq": int(state.get("approval_seq", 0)) + 1,
            **({"draft_items": mapped} if mapped is not None else {}),
            "node_events": _append_event(state, "approval_prepare:awaiting"),
        }

    return run


def validate_resume(resume: Any, *, approval_seq: int, draft_items: list[dict]) -> dict:
    """재개 값을 허용목록으로 검증하고 정규화한다.

    형식(승인 화면 docs/specs/approval-ui.md와 같음):
      {"approval_seq": int, "action": "approve"|"cancel"(기본 approve),
       "decisions": [{"item", "verdict", "reason"}, ...]}  ← 원본 초안의 모든 항목
    승인자(approved_by)는 받지 않는다. 보내와도 무시하고 저장 노드가 thread의 사용자로 정한다.
    """

    if not isinstance(resume, dict):
        raise ApprovalInputError("재개 값은 딕셔너리여야 합니다")
    if resume.get("approval_seq") != approval_seq:
        raise ApprovalInputError("approval_seq가 대기 중인 승인 요청과 다릅니다")
    action = resume.get("action", "approve")
    if action not in RESUME_ACTIONS:
        raise ApprovalInputError(f"action은 {sorted(RESUME_ACTIONS)} 중 하나여야 합니다")
    if action == "cancel":
        return {"action": "cancel", "decisions": []}

    decisions = resume.get("decisions")
    if not isinstance(decisions, list):
        raise ApprovalInputError("decisions는 목록이어야 합니다")
    drafts = {str(item["item"]): item for item in draft_items}
    seen: set[str] = set()
    normalized = []
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ApprovalInputError("decisions의 원소는 딕셔너리여야 합니다")
        item = str(decision.get("item", ""))
        if item not in drafts:
            raise ApprovalInputError(f"초안에 없는 항목입니다: {item!r}")
        if item in seen:
            raise ApprovalInputError(f"같은 항목이 두 번 왔습니다: {item!r}")
        seen.add(item)
        verdict = decision.get("verdict")
        if verdict not in VERDICTS:
            raise ApprovalInputError(f"verdict는 {sorted(VERDICTS)} 중 하나여야 합니다")
        reason = decision.get("reason")
        if not isinstance(reason, str):
            raise ApprovalInputError("reason은 문자열이어야 합니다")
        original = drafts[item]
        normalized.append({
            "item": item,
            "verdict": verdict,
            "reason": reason,
            # 고친 항목 판단은 서버가 원본과 비교해 한다(승인 화면 계약).
            "edited": verdict != original.get("verdict") or reason != original.get("reason"),
        })
    if seen != set(drafts):
        raise ApprovalInputError("원본 초안의 모든 항목에 대한 결정이 필요합니다")
    return {"action": "approve", "decisions": normalized}


def human_approval_node(approval_enabled: bool):
    """interrupt로 멈추고, 재개 값을 검증만 한다. 저장·규칙 갱신은 하지 않는다(SPEC §5.5)."""

    def run(state: dict) -> dict:
        if not _active(state, approval_enabled):
            return {"node_events": _append_event(state, "human_approval:skip")}
        draft_items = list(state.get("draft_items") or [])
        # payload는 JSON으로 직렬화되는 값만 담는다(2주09: datetime 등은 직렬화 실패).
        resume = interrupt({
            "review_id": str(state["review_id"]),
            "approval_seq": int(state["approval_seq"]),
            "draft_items": draft_items,
        })
        result = validate_resume(resume, approval_seq=int(state["approval_seq"]), draft_items=draft_items)
        if result["action"] == "cancel":
            return {
                "approval_decisions": [],
                "status": "open",
                "outcome": "answered",
                "node_events": _append_event(state, "human_approval:cancelled"),
            }
        return {
            "approval_decisions": result["decisions"],
            "status": "saving",
            "node_events": _append_event(state, "human_approval:resumed"),
        }

    return run


def save_memo_node(memo_saver, approval_enabled: bool, memory_store=None):
    """승인된 결정만 저장한다. 멱등키 (user_id, review_id, approval_seq).

    memo_saver(record, idempotency_key) -> memo_id 는 같은 키에 기존 id를 돌려줘야 한다
    (DB UNIQUE 제약 등, 2주11). 그래프는 저장 함수가 멱등이라는 계약에 기댄다.
    """

    def run(state: dict) -> dict:
        if not _active(state, approval_enabled) or state.get("status") != "saving":
            return {"node_events": _append_event(state, "save_memo:skip")}
        if memo_saver is None:
            raise ValueError("승인을 켠 그래프에는 memo_saver가 필요합니다")
        user_id = str(state["user_id"])
        idempotency_key = (user_id, str(state["review_id"]), int(state["approval_seq"]))
        record = {
            "user_id": user_id,
            "review_id": str(state["review_id"]),
            "approval_seq": int(state["approval_seq"]),
            # 승인자는 재개 값이 아니라 thread의 사용자(서버가 인증으로 만든 thread_id)로 정한다(SPEC §5.5).
            "approved_by": user_id,
            "items": list(state.get("approval_decisions") or []),
        }
        memo_id = str(memo_saver(record, idempotency_key))
        if memory_store is not None:
            # 규칙은 승인된 메모에서 만든 부가 저장이다. 200건 상한에 걸리면 다시 시도해도 같은 결과라,
            # 승인(메모 저장)이 끝나지 못하고 막히지 않도록 경고만 남긴다(SPEC §4, store-semantic-memory EC-08).
            # 그 밖의 오류(Store 장애 등)는 그대로 올린다: 메모 저장이 멱등이라 재시도가 안전하다.
            try:
                memory_store.save_edited_rules(
                    user_id, memo_id, list(state.get("approval_decisions") or [])
                )
            except RuleLimitError:
                logger.warning("규칙 200건 상한이라 승인에서 고친 규칙을 저장하지 않았습니다: memo_id=%s", memo_id)
        return {
            "saved_memo_id": memo_id,
            "status": "open",
            "outcome": "answered",
            "node_events": _append_event(state, "save_memo:saved"),
        }

    return run


class NotAwaitingApproval(ApprovalInputError):
    """대기 중인 승인 요청이 없는데 재개하려 할 때(SPEC §5.5: 409 CONFLICT_NOT_AWAITING_APPROVAL)."""


def pending_approval(graph, config: dict) -> dict | None:
    """승인 대기 payload와 초안을 작성할 때 사용한 질문·참고 규칙을 돌려준다."""

    snapshot = graph.get_state(config)
    for task in snapshot.tasks:
        if task.name == "human_approval" and task.interrupts:
            return {
                **task.interrupts[0].value,
                "question": snapshot.values.get("question"),
                "applied_rules": list(snapshot.values.get("applied_rules") or []),
            }
    return None


def resume_approval(graph, config: dict, resume: Any) -> dict:
    """승인 재개의 유일한 입구. 재개 값을 **그래프에 넘기기 전에** 검증한다.

    LangGraph는 Command(resume=값)의 값을 체크포인트에 먼저 기록한 뒤 노드를 다시 돌린다.
    노드 안에서 검증에 실패하면 잘못된 값이 기록에 남아, 다음 재개에서도 interrupt()가 그 값을
    돌려준다(같은 승인 건이 영구히 막힘, tests/test_approval.py 실측). 그래서 API·러너는 이 함수로만
    재개한다. 노드 안의 validate_resume은 이 입구를 거치지 않은 호출에 대한 마지막 방어선이다.
    """

    from langgraph.types import Command

    payload = pending_approval(graph, config)
    if payload is None:
        raise NotAwaitingApproval("대기 중인 승인 요청이 없습니다")
    validate_resume(resume, approval_seq=int(payload["approval_seq"]), draft_items=list(payload["draft_items"]))
    return graph.invoke(Command(resume=resume), config=config)


def add_approval_nodes(
    graph, *, memo_saver, approval_enabled: bool, memory_store=None, traced: bool = False
) -> str:
    """승인 3노드를 그래프에 붙이고 시작 노드 이름을 돌려준다(앞 단계가 이 이름으로 엣지를 잇는다).

    traced(멀티 그래프만 켠다): 세 노드의 실행을 Langfuse의 supervisor > approval 아래 관측으로 남긴다.
    노드 함수는 그대로 부른다. interrupt(승인 대기)는 오류가 아니라 interrupted=true로 남는다.
    """

    nodes = {
        "approval_prepare": approval_prepare_node(approval_enabled),
        "human_approval": human_approval_node(approval_enabled),
        "save_memo": save_memo_node(memo_saver, approval_enabled, memory_store),
    }
    for name, fn in nodes.items():
        if traced:
            from backend.services import tracing

            fn = tracing.node(name, fn, group=tracing.APPROVAL)
        graph.add_node(name, fn)
    graph.add_edge("approval_prepare", "human_approval")
    graph.add_edge("human_approval", "save_memo")
    graph.add_edge("save_memo", END)
    return "approval_prepare"
