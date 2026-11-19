"""사람 승인(interrupt)·체크포인트 재개·멱등 저장 시험 (docs/specs/approval-checkpoint.md AC-01~08).

단일(build_graph)과 멀티(build_supervisor_graph)가 같은 승인 노드를 쓰는지 두 그래프 모두로 잰다.
키·DB 없이 MemorySaver와 가짜 검색기·작성기·저장소만 쓴다. Postgres·재시작 재개는
tests/test_approval_checkpoint_postgres.py. 상한·횟수는 리터럴과 비교한다.
"""
import logging

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from backend.graph.approval import (
    ApprovalInputError,
    NotAwaitingApproval,
    pending_approval,
    resume_approval,
    thread_id_for,
    validate_resume,
)
from backend.graph.build import build_graph, build_supervisor_graph
from backend.graph.refusal import CorpusCatalog, CorpusTarget
from backend.memory import SemanticRuleStore, namespace
from langgraph.store.memory import InMemoryStore

from tests.semantic_store import indexed_store

QUESTION = "오뚜기의 FY2025 사업보고서 차입금 현황을 알려주세요."
CATALOG = CorpusCatalog(
    corp_names=frozenset({"오뚜기"}),
    fiscal_years=frozenset({2025}),
    report_types=frozenset({"annual"}),
    targets=frozenset({CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2025)}),
)
CHUNK = {
    "chunk_id": "chunk-1",
    "corp_code": "00000001",
    "fiscal_year": 2025,
    "content": "차입금 관련 공시 내용. 계류 중인 소송 없음",
    "rcept_no": "20260304000913",
    "chapter": "NOTES",
    "note_no": "1",
}
ITEMS = ["litigation", "borrowing_structure"]
BUILDERS = pytest.mark.parametrize("builder", [build_graph, build_supervisor_graph], ids=["single", "multi"])


class MemoStore:
    """멱등 저장소 흉내: 같은 키는 같은 id를 돌려준다(실서비스는 DB UNIQUE, 2주11)."""

    def __init__(self):
        self.records: dict[tuple, dict] = {}
        self.calls = 0

    def __call__(self, record, key):
        self.calls += 1
        if key not in self.records:
            self.records[key] = {"memo_id": f"memo-{len(self.records) + 1}", **record}
        return self.records[key]["memo_id"]


class Calls:
    def __init__(self):
        self.retriever = 0
        self.writer = 0


def _graph(builder, store, calls, *, checkpointer=None, memory_store=None):
    def retriever(_state):
        calls.retriever += 1
        return [CHUNK]

    def writer(_state, _chunks):
        calls.writer += 1
        return [
            {"item": "litigation", "verdict": "not_applicable", "reason": "계류 중인 소송 없음", "chunk_ids": ["chunk-1"]},
            {"item": "borrowing_structure", "verdict": "applicable", "reason": "차입금 공시 있음", "chunk_ids": ["chunk-1"]},
        ]

    return builder(
        retriever=retriever,
        draft_writer=writer,
        # 멀티 그래프는 순차 경로로 본다(기본 경로는 초안 2개라 작성기가 두 번 불린다). 승인·재개 노드는 같다
        **({} if builder is build_graph else {"dual_draft": False}),
        corpus_catalog=CATALOG,
        checkpointer=checkpointer or MemorySaver(),
        memo_saver=store,
        memory_store=memory_store,
        approval_enabled=True,
    )


def _start(graph, user="user-1", review="review-1"):
    config = {"configurable": {"thread_id": thread_id_for(user, review)}}
    result = graph.invoke(
        {"question": QUESTION, "user_id": user, "review_id": review, "checklist_items": ITEMS},
        config=config,
    )
    return config, result


def _approve_all(payload, **overrides):
    decisions = [{"item": d["item"], "verdict": d["verdict"], "reason": d["reason"]} for d in payload["draft_items"]]
    return {"approval_seq": payload["approval_seq"], "decisions": decisions, **overrides}


# ── AC-01 승인 대기 interrupt ─────────────────────────────────

@BUILDERS
def test_interrupts_before_save_and_keeps_awaiting_state(builder):
    store, calls = MemoStore(), Calls()
    graph = _graph(builder, store, calls)
    config, result = _start(graph)

    interrupts = result["__interrupt__"]
    assert len(interrupts) == 1
    payload = interrupts[0].value
    assert payload["approval_seq"] == 1
    assert [d["item"] for d in payload["draft_items"]] == ITEMS
    snapshot = graph.get_state(config)
    assert snapshot.next == ("human_approval",)
    assert snapshot.values["status"] == "awaiting_approval"
    assert snapshot.values["outcome"] == "awaiting_approval"
    assert store.calls == 0


# ── AC-02 재개 뒤 한 번 저장, 앞 단계는 다시 돌지 않음 ──────────

@BUILDERS
def test_resume_saves_once_without_rerunning_search_or_writer(builder):
    store, calls = MemoStore(), Calls()
    graph = _graph(builder, store, calls)
    config, result = _start(graph)

    done = resume_approval(graph, config, _approve_all(result["__interrupt__"][0].value))

    assert done["saved_memo_id"] == "memo-1"
    assert done["status"] == "open"
    assert done["outcome"] == "answered"
    assert store.calls == 1
    assert list(store.records) == [("user-1", "review-1", 1)]
    # 재개는 멈춘 노드부터: 검색·작성은 처음 실행의 1회뿐(2주09)
    assert calls.retriever == 1
    assert calls.writer == 1
    assert graph.get_state(config).next == ()
    assert done["node_events"][-3:] == ["approval_prepare:awaiting", "human_approval:resumed", "save_memo:saved"]


# ── AC-03 같은 승인 재전송은 저장을 늘리지 않음 ─────────────────

@BUILDERS
def test_repeated_resume_after_completion_does_not_save_again(builder):
    store, calls = MemoStore(), Calls()
    graph = _graph(builder, store, calls)
    config, result = _start(graph)
    resume = _approve_all(result["__interrupt__"][0].value)

    resume_approval(graph, config, resume)
    with pytest.raises(NotAwaitingApproval):
        resume_approval(graph, config, resume)
    # 입구를 거치지 않은 재전송도 저장을 늘리지 않는다(대기 중인 interrupt가 없음)
    graph.invoke(Command(resume=resume), config=config)

    assert len(store.records) == 1
    assert store.calls == 1


def test_memo_store_contract_returns_same_id_for_same_key():
    store = MemoStore()
    key = ("user-1", "review-1", 1)
    assert store({"items": []}, key) == store({"items": []}, key) == "memo-1"
    assert len(store.records) == 1


# ── AC-04 수정 항목 표시, 승인자는 서버 값 ─────────────────────

@BUILDERS
def test_edited_items_are_marked_and_approver_comes_from_thread_user(builder):
    store, calls = MemoStore(), Calls()
    graph = _graph(builder, store, calls)
    config, result = _start(graph)
    resume = _approve_all(result["__interrupt__"][0].value, approved_by="attacker")
    resume["decisions"][1] = {"item": "borrowing_structure", "verdict": "needs_check", "reason": "만기 구조 확인 필요"}

    resume_approval(graph, config, resume)

    record = store.records[("user-1", "review-1", 1)]
    assert record["approved_by"] == "user-1"
    assert [d["edited"] for d in record["items"]] == [False, True]
    assert record["items"][1]["verdict"] == "needs_check"


@BUILDERS
def test_save_memo_stores_only_edited_items_as_user_rules(builder):
    memo_store, calls = MemoStore(), Calls()
    raw_store = indexed_store()
    rule_store = SemanticRuleStore(raw_store)
    graph = _graph(builder, memo_store, calls, memory_store=rule_store)
    config, result = _start(graph)
    resume = _approve_all(result["__interrupt__"][0].value)
    resume["decisions"][1] = {
        "item": "borrowing_structure",
        "verdict": "needs_check",
        "reason": "만기 구조를 별도로 확인한다.",
    }

    resume_approval(graph, config, resume)

    user_namespace = namespace("user-1")
    assert raw_store.get(user_namespace, "memo-1:litigation") is None
    saved = raw_store.get(user_namespace, "memo-1:borrowing_structure")
    assert saved is not None
    assert saved.value == {
        "item": "borrowing_structure",
        "instruction": "만기 구조를 별도로 확인한다.",
        "source_memo_id": "memo-1",
    }


@BUILDERS
def test_rule_limit_does_not_block_approval_and_keeps_existing_rules(builder, caplog):
    """규칙이 200건이어도 승인(메모 저장)은 끝난다. 규칙 저장만 건너뛰고 경고를 남긴다(SPEC §4)."""
    memo_store, calls = MemoStore(), Calls()
    raw_store = indexed_store()
    user_namespace = namespace("user-1")
    for number in range(200):
        raw_store.put(
            user_namespace,
            f"existing-{number}",
            {"item": f"item-{number}", "instruction": "기존 규칙", "source_memo_id": "old-memo"},
        )
    graph = _graph(builder, memo_store, calls, memory_store=SemanticRuleStore(raw_store))
    config, result = _start(graph)
    resume = _approve_all(result["__interrupt__"][0].value)
    resume["decisions"][1] = {
        "item": "borrowing_structure",
        "verdict": "needs_check",
        "reason": "만기 구조를 별도로 확인한다.",
    }

    with caplog.at_level(logging.WARNING, logger="backend.graph.approval"):
        done = resume_approval(graph, config, resume)

    assert memo_store.calls == 1
    assert done["status"] == "open" and done["outcome"] == "answered"
    assert done["saved_memo_id"] == "memo-1"
    assert "save_memo:saved" in done["node_events"]
    assert len(raw_store.search(user_namespace, limit=300)) == 200  # 기존 200건 그대로
    assert raw_store.get(user_namespace, "memo-1:borrowing_structure") is None
    assert [r.message for r in caplog.records if "200건 상한" in r.message] == [
        "규칙 200건 상한이라 승인에서 고친 규칙을 저장하지 않았습니다: memo_id=memo-1"
    ]
    # 다시 보내도 메모는 1건, 대기 중인 승인이 없다는 409 상당 오류
    with pytest.raises(NotAwaitingApproval):
        resume_approval(graph, config, resume)
    assert len(memo_store.records) == 1


@BUILDERS
def test_non_limit_rule_store_failure_is_not_hidden(builder):
    """상한이 아닌 오류(Store 장애 등)는 삼키지 않는다. 메모 저장이 멱등이라 재시도가 안전하다."""

    class FailingRules:
        def save_edited_rules(self, user_id, memo_id, items):
            raise RuntimeError("store down")

        def search_rules(self, user_id, question):
            return []

    memo_store, calls = MemoStore(), Calls()
    graph = _graph(builder, memo_store, calls, memory_store=FailingRules())
    config, result = _start(graph)
    resume = _approve_all(result["__interrupt__"][0].value)
    resume["decisions"][1] = {"item": "borrowing_structure", "verdict": "needs_check", "reason": "확인 필요"}

    with pytest.raises(RuntimeError, match="store down"):
        resume_approval(graph, config, resume)

    assert len(memo_store.records) == 1  # 메모는 이미 저장됐고, 재시도해도 같은 키라 중복되지 않는다


# ── AC-05 취소는 저장하지 않음 ─────────────────────────────────

@BUILDERS
def test_cancel_closes_pending_approval_without_saving(builder):
    store, calls = MemoStore(), Calls()
    graph = _graph(builder, store, calls)
    config, result = _start(graph)

    done = resume_approval(
        graph, config, {"approval_seq": result["__interrupt__"][0].value["approval_seq"], "action": "cancel"}
    )

    assert store.calls == 0
    assert done["status"] == "open"
    assert done["outcome"] == "answered"
    assert "save_memo:skip" in done["node_events"]
    assert "saved_memo_id" not in done


@BUILDERS
def test_cancel_does_not_store_edited_rules(builder):
    memo_store, calls = MemoStore(), Calls()
    raw_store = indexed_store()
    graph = _graph(
        builder,
        memo_store,
        calls,
        memory_store=SemanticRuleStore(raw_store),
    )
    config, result = _start(graph)

    resume_approval(
        graph,
        config,
        {
            "approval_seq": result["__interrupt__"][0].value["approval_seq"],
            "action": "cancel",
        },
    )

    assert memo_store.calls == 0
    assert raw_store.search(namespace("user-1"), limit=200) == []


# ── AC-06 잘못된 재개 값은 저장 없이 멈추고, 대기는 그대로 남음 ────

BAD_RESUMES = {
    "not_dict": "approve",
    # 승인 번호만 틀리고 나머지는 올바른 값이어야 번호 검사를 본다. 전에는 decisions가 비어 있어
    # 번호 검사를 꺼도 항목 수 검사에서 거절돼 시험이 통과했다(11/12 변이 확인에서 찾음).
    "wrong_seq": {"approval_seq": 999, "decisions": [
        {"item": "litigation", "verdict": "not_applicable", "reason": "x"},
        {"item": "borrowing_structure", "verdict": "applicable", "reason": "x"},
    ]},
    "bad_action": {"approval_seq": 1, "action": "delete", "decisions": []},
    "missing_item": {"approval_seq": 1, "decisions": [{"item": "litigation", "verdict": "not_applicable", "reason": "x"}]},
    "unknown_item": {"approval_seq": 1, "decisions": [
        {"item": "litigation", "verdict": "not_applicable", "reason": "x"},
        {"item": "borrowing_structure", "verdict": "applicable", "reason": "x"},
        {"item": "fraud", "verdict": "applicable", "reason": "x"},
    ]},
    "bad_verdict": {"approval_seq": 1, "decisions": [
        {"item": "litigation", "verdict": "Approved", "reason": "x"},
        {"item": "borrowing_structure", "verdict": "applicable", "reason": "x"},
    ]},
    "duplicate_item": {"approval_seq": 1, "decisions": [
        {"item": "litigation", "verdict": "not_applicable", "reason": "x"},
        {"item": "litigation", "verdict": "not_applicable", "reason": "x"},
    ]},
}


@BUILDERS
@pytest.mark.parametrize("case", sorted(BAD_RESUMES))
def test_invalid_resume_is_rejected_and_approval_stays_pending(builder, case):
    store, calls = MemoStore(), Calls()
    graph = _graph(builder, store, calls)
    config, result = _start(graph)

    with pytest.raises(ApprovalInputError):
        resume_approval(graph, config, BAD_RESUMES[case])

    assert store.calls == 0
    assert graph.get_state(config).next == ("human_approval",)
    assert pending_approval(graph, config)["approval_seq"] == 1
    # 올바른 값으로 다시 재개하면 정상 저장된다
    done = resume_approval(graph, config, _approve_all(result["__interrupt__"][0].value))
    assert done["saved_memo_id"] == "memo-1"
    assert store.calls == 1


# ── AC-07 대화 격리: thread_id는 user_id:review_id ─────────────

@BUILDERS
def test_thread_not_matching_review_identity_is_rejected(builder):
    graph = _graph(builder, MemoStore(), Calls())
    with pytest.raises(ApprovalInputError, match="thread_id"):
        graph.invoke(
            {"question": QUESTION, "user_id": "user-1", "review_id": "review-1", "checklist_items": ITEMS},
            config={"configurable": {"thread_id": thread_id_for("user-2", "review-1")}},
        )


@BUILDERS
def test_two_users_keep_separate_pending_approvals(builder):
    store, calls = MemoStore(), Calls()
    saver = MemorySaver()
    graph = _graph(builder, store, calls, checkpointer=saver)
    config_a, result_a = _start(graph, user="user-a", review="review-1")
    config_b, _ = _start(graph, user="user-b", review="review-1")

    resume_approval(graph, config_a, _approve_all(result_a["__interrupt__"][0].value))

    assert list(store.records) == [("user-a", "review-1", 1)]
    assert graph.get_state(config_b).next == ("human_approval",)


# ── AC-08 승인을 끈 실행(평가 러너)은 interrupt 없이 지나감 ──────

@BUILDERS
def test_approval_disabled_runs_through_without_interrupt(builder):
    store = MemoStore()
    graph = builder(
        retriever=lambda _s: [CHUNK],
        draft_writer=lambda _s, _c: [
            {"item": "litigation", "verdict": "not_applicable", "reason": "계류 중인 소송 없음", "chunk_ids": ["chunk-1"]}
        ],
        corpus_catalog=CATALOG,
        memo_saver=store,
    )
    result = graph.invoke({"question": QUESTION, "user_id": "eval", "review_id": "eval-1", "checklist_items": ["litigation"]})

    assert "__interrupt__" not in result
    assert result["outcome"] == "answered"
    assert result["node_events"][-3:] == ["approval_prepare:skip", "human_approval:skip", "save_memo:skip"]
    assert store.calls == 0


@BUILDERS
def test_approval_requires_checkpointer(builder):
    with pytest.raises(ValueError, match="checkpointer"):
        builder(corpus_catalog=CATALOG, approval_enabled=True, memo_saver=MemoStore())


def test_validate_resume_ignores_client_approver_field():
    drafts = [{"item": "litigation", "verdict": "not_applicable", "reason": "없음"}]
    result = validate_resume(
        {"approval_seq": 1, "approved_by": "someone", "decisions": [{"item": "litigation", "verdict": "not_applicable", "reason": "없음"}]},
        approval_seq=1,
        draft_items=drafts,
    )
    assert result == {"action": "approve", "decisions": [
        {"item": "litigation", "verdict": "not_applicable", "reason": "없음", "edited": False}
    ]}


def test_raw_invalid_resume_poisons_pending_approval_which_is_why_resume_approval_validates_first():
    """LangGraph는 재개 값을 먼저 기록한다: 입구를 건너뛰고 잘못된 값을 넣으면 다음 올바른 재개도 실패한다.

    resume_approval()이 그래프에 넘기기 전에 검증하는 이유를 남기는 시험이다(라이브러리 동작이 바뀌면 다시 본다).
    """

    store, calls = MemoStore(), Calls()
    graph = _graph(build_graph, store, calls)
    config, result = _start(graph)

    with pytest.raises(ApprovalInputError):
        graph.invoke(Command(resume=BAD_RESUMES["bad_verdict"]), config=config)
    with pytest.raises(ApprovalInputError):
        graph.invoke(Command(resume=_approve_all(result["__interrupt__"][0].value)), config=config)

    assert store.calls == 0
