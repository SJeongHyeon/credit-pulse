from datetime import date
from typing import Any, Literal

import pytest

from langgraph.checkpoint.memory import MemorySaver

from backend.graph.build import build_graph
from backend.graph.refusal import CorpusCatalog, CorpusTarget
from backend.graph.state import AgentState

RELATED_QUESTION = "오뚜기의 FY2025 사업보고서 차입금 현황을 알려주세요."
RELATED_CATALOG = CorpusCatalog(
    corp_names=frozenset({"오뚜기"}),
    fiscal_years=frozenset({2025}),
    report_types=frozenset({"annual"}),
    targets=frozenset(
        {CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2025)}
    ),
)
RELATED_CHUNK = {
    "chunk_id": "chunk-1",
    "corp_code": "00000001",
    "fiscal_year": 2025,
    "content": "차입금 관련 공시 내용",
    "rcept_no": "20260304000913",
    "chapter": "NOTES",
    "note_no": "1",
}


def test_agent_state_declares_minimum_contract():
    assert AgentState.__annotations__ == {
        "user_id": str,
        "review_id": str,
        "thread_id": str,
        "question": str,
        "corp_name": str,
        "corp_code": str,
        "fiscal_year": int,
        "review_target": dict[str, Any],
        "checklist_items": list[str],
        "outcome": Literal["answered", "refused", "awaiting_approval"],
        "refusal_message": str | None,
        "refusal_reason": Literal[
            "OUT_OF_DOMAIN",
            "CORP_NOT_IN_CORPUS",
            "ITEM_NOT_DISCLOSED",
            "PERIOD_NOT_IN_CORPUS",
            "PRIOR_YEAR_NOT_IN_CORPUS",
            "INJECTION_SUSPECTED",
        ] | None,
        "injection_risk": dict[str, Any] | None,
        "status": Literal[
            "open",
            "running",
            "awaiting_approval",
            "saving",
            "closed",
        ],
        "retrieval_query": str,
        "retrieval_rounds": int,
        "filters": dict[str, Any],
        "retrieved_chunks": list[dict[str, Any]],
        "retrieval_sufficient": bool,
        "rewrite_changed": bool,
        "target_required": bool,
        "comparison_targets": list[dict[str, Any]],
        "refusal_kind": Literal["unrelated", "work_related"] | None,
        "news_items": list[dict[str, Any]],
        "news_search": Literal["ok", "skipped_no_key", "error"] | None,
        "news_calls": int,
        "domain_check": str,
        "supervisor_turns": int,
        "search_calls": int,
        "writer_calls": int,
        "redraft_calls": int,
        "written_search": int,
        "supervisor_trace": list[str],
        # 104번: Supervisor 결정·지시와 서브에이전트 보고
        "supervisor_next": str,
        "supervisor_decisions": list[dict[str, Any]],
        "writer_feedback": str,
        "search_query": str,  # 165번: Supervisor LLM이 쓴 재검색 질의
        "search_report": dict[str, Any],
        "grader_missing_items": list[str],
        "writer_report": dict[str, Any],
        "retrieval_empty": bool,
        "draft_items": list[dict[str, Any]],
        "citations": list[dict[str, Any]],
        "verification": list[dict[str, Any]],
        "verification_rewrites": int,
        "applied_rules": list[dict[str, Any]],
        "approval_seq": int,
        "approval_decisions": list[dict[str, Any]],
        "saved_memo_id": str,
        "trace_id": str,
        "node_events": list[str],
        "errors": list[str],
    }


def test_build_graph_compiles_with_six_official_node_names():
    graph = build_graph(checkpointer=MemorySaver())

    assert {
        "supervisor",
        "retriever",
        "writer",
        "verifier",
        "human_approval",
        "save_memo",
    } <= set(graph.get_graph().nodes)


def test_build_graph_rejects_approval_without_checkpointer():
    with pytest.raises(ValueError, match="checkpointer"):
        build_graph(
            checkpointer=None,
            corpus_catalog=RELATED_CATALOG,
            approval_enabled=True,
        )


def test_build_graph_executes_minimal_state_with_fake_retriever_and_checkpointer():
    received_states = []

    def fake_retriever(state):
        received_states.append(state)

    checkpointer = MemorySaver()
    graph = build_graph(
        retriever=fake_retriever,
        checkpointer=checkpointer,
        corpus_catalog=RELATED_CATALOG,
    )
    state = {"question": RELATED_QUESTION, "user_id": "user-1"}
    config = {"configurable": {"thread_id": "thread-1"}}

    result = graph.invoke(state, config=config)

    assert result["question"] == RELATED_QUESTION
    assert result["user_id"] == "user-1"
    assert len(received_states) == 1
    assert received_states[0]["question"] == RELATED_QUESTION
    assert received_states[0]["user_id"] == "user-1"
    assert graph.get_state(config).values["question"] == RELATED_QUESTION


def test_supervisor_clears_stale_evidence_on_company_refusal():
    calls = []

    def fake_retriever(state):
        calls.append(state)
        return [RELATED_CHUNK]

    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
    )
    graph = build_graph(retriever=fake_retriever, corpus_catalog=catalog)

    result = graph.invoke(
        {
            "question": "가상기업의 사업보고서에서 차입금 현황을 알려주세요.",
            "citations": [{"chunk_id": "stale"}],
            "draft_items": [{"item": "loan"}],
        }
    )

    assert calls == []
    assert result["outcome"] == "refused"
    assert result["refusal_reason"] == "CORP_NOT_IN_CORPUS"
    assert result["refusal_message"] == "요청한 회사는 현재 사업보고서 코퍼스에 없습니다."
    assert result["citations"] == []
    assert result["draft_items"] == []
    assert result["node_events"] == ["supervisor:refuse"]


@pytest.mark.parametrize(
    ("question", "reason", "message"),
    [
        (
            "가상기업의 FY2025 사업보고서에서 차입금 현황을 알려주세요.",
            "CORP_NOT_IN_CORPUS",
            "요청한 회사는 현재 사업보고서 코퍼스에 없습니다.",
        ),
        (
            "오뚜기의 FY2024 사업보고서에서 차입금 현황을 알려주세요.",
            "PERIOD_NOT_IN_CORPUS",
            "요청한 기간 또는 보고서 종류는 현재 코퍼스에 없습니다.",
        ),
    ],
)
def test_supervisor_refuses_unsupported_corpus_scope_before_retrieval(
    question, reason, message
):
    calls = []
    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
    )
    graph = build_graph(
        retriever=lambda state: calls.append(state), corpus_catalog=catalog
    )

    result = graph.invoke({"question": question})

    assert calls == []
    assert result["outcome"] == "refused"
    assert result["refusal_reason"] == reason
    assert result["refusal_message"] == message
    assert result["citations"] == []
    assert result["draft_items"] == []


def test_supervisor_sends_related_question_to_retriever():
    calls = []
    graph = build_graph(
        retriever=lambda state: calls.append(state), corpus_catalog=RELATED_CATALOG
    )

    graph.invoke({"question": RELATED_QUESTION})

    assert len(calls) == 1


def test_supervisor_sets_manifest_target_and_required_search_filters():
    received = []
    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
        targets=frozenset(
            {CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2025)}
        ),
    )

    build_graph(
        draft_writer=lambda _state, _chunks: [],
        retriever=lambda state: received.append(state) or [RELATED_CHUNK],
        corpus_catalog=catalog,
    ).invoke({"question": "오뚜기의 FY2025 사업보고서 차입금 현황"})

    assert len(received) == 1
    assert received[0]["corp_code"] == "00000001"
    assert received[0]["fiscal_year"] == 2025
    assert received[0]["filters"] == {"corp_code": "00000001", "fiscal_year": 2025}


def test_supervisor_uses_review_target_instead_of_company_named_in_question():
    received = []
    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기", "농심"}),
        fiscal_years=frozenset({2024, 2025}),
        report_types=frozenset({"annual"}),
        targets=frozenset(
            {
                CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2025),
                CorpusTarget(corp_name="농심", corp_code="00000002", fiscal_year=2024),
            }
        ),
    )

    build_graph(
        draft_writer=lambda _state, _chunks: [],
        retriever=lambda state: received.append(state) or [],
        corpus_catalog=catalog,
        domain_check_enabled=False,
    ).invoke(
        {
            "question": "농심의 FY2024 차입금 현황을 알려주세요.",
            "review_target": {
                "corp_code": "00000001",
                "corp_name": "오뚜기",
                "fiscal_year": 2025,
            },
        }
    )

    assert len(received) == 1
    assert received[0]["filters"] == {"corp_code": "00000001", "fiscal_year": 2025}
    assert received[0]["comparison_targets"] == [
        {"corp_code": "00000001", "corp_name": "오뚜기", "fiscal_year": 2025}
    ]


def test_supervisor_requests_missing_target_without_retrieval():
    calls = []
    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
        targets=frozenset(
            {CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2025)}
        ),
    )

    result = build_graph(
        retriever=lambda state: calls.append(state), corpus_catalog=catalog
    ).invoke({"question": "차입금 현황을 알려주세요."})

    assert calls == []
    assert result["outcome"] == "answered"
    assert result["draft_items"] == []
    assert result["citations"] == []
    assert result["refusal_reason"] is None
    assert result["refusal_message"] == "회사명과 사업연도를 포함해 다시 질문해 주세요."


def test_refused_state_bypasses_retriever_and_records_route():
    calls = []

    def fake_retriever(state):
        calls.append(state)
        return [{"chunk_id": "chunk-1"}]

    graph = build_graph(retriever=fake_retriever)
    result = graph.invoke({"question": "질문", "outcome": "refused"})

    assert calls == []
    assert result.get("node_events") == ["supervisor:refuse"]


def test_default_sufficient_path_retrieves_once_and_records_route():
    calls = []

    def fake_retriever(state):
        calls.append(state)
        return [RELATED_CHUNK]

    graph = build_graph(retriever=fake_retriever, corpus_catalog=RELATED_CATALOG,
                        draft_writer=lambda _state, _chunks: [])
    result = graph.invoke({"question": RELATED_QUESTION})

    assert len(calls) == 1
    assert result.get("retrieval_rounds") == 1
    assert result.get("node_events", [])[:4] == [
        "supervisor:retrieve",
        "retriever:round=1",
        "scorer:write",
        "writer",
    ]

def test_insufficient_first_score_rewrites_then_retrieves_again_before_writer():
    score_results = iter([False, True])
    retrieval_queries = []

    def fake_retriever(state):
        retrieval_queries.append(state["retrieval_query"])
        return [RELATED_CHUNK]

    graph = build_graph(
        draft_writer=lambda _state, _chunks: [],
        retriever=fake_retriever,
        scorer=lambda state: next(score_results),
        rewriter=lambda state: "오뚜기 FY2025 재작성 질문",
        corpus_catalog=RELATED_CATALOG,
    )
    result = graph.invoke({"question": RELATED_QUESTION})

    assert retrieval_queries == [RELATED_QUESTION, "오뚜기 FY2025 재작성 질문"]
    assert result["retrieval_rounds"] == 2
    assert result["node_events"][:7] == [
        "supervisor:retrieve",
        "retriever:round=1",
        "scorer:rewrite",
        "rewriter:round=1",
        "retriever:round=2",
        "scorer:write",
        "writer",
    ]

def test_insufficient_scores_stop_after_exactly_three_retrievals():
    score_results = iter([False, False, False, True])
    retrieval_queries = []

    def fake_retriever(state):
        retrieval_queries.append(state["retrieval_query"])
        return [RELATED_CHUNK]

    graph = build_graph(
        retriever=fake_retriever,
        scorer=lambda state: next(score_results),
        rewriter=lambda state: f"오뚜기 FY2025 재작성 질문 {len(retrieval_queries)}",
        corpus_catalog=RELATED_CATALOG,
    )
    result = graph.invoke({"question": RELATED_QUESTION})

    assert len(retrieval_queries) == 3
    assert result["retrieval_rounds"] == 3

def test_unchanged_rewrite_skips_second_retrieval():
    retrieval_queries = []

    def fake_retriever(state):
        retrieval_queries.append(state["retrieval_query"])
        return [RELATED_CHUNK]

    graph = build_graph(
        retriever=fake_retriever,
        scorer=lambda state: False,
        rewriter=lambda state: f"  {RELATED_QUESTION}  ",
        corpus_catalog=RELATED_CATALOG,
    )
    result = graph.invoke({"question": RELATED_QUESTION})

    assert retrieval_queries == [RELATED_QUESTION]
    assert result["retrieval_rounds"] == 1
    assert result["node_events"][:5] == [
        "supervisor:retrieve",
        "retriever:round=1",
        "scorer:rewrite",
        "rewriter:unchanged",
        "writer",
    ]


@pytest.mark.parametrize("stop,searches,rewrites", [("limit", 3, 2), ("unchanged", 1, 1)])
@pytest.mark.parametrize("has_chunks", [True])
def test_insufficient_writer_refuses_or_asks_without_confirmed_claims(stop, searches, rewrites, has_chunks):
    calls = {"search": 0, "rewrite": 0}

    def retrieve(state):
        calls["search"] += 1
        return [RELATED_CHUNK]

    def rewrite(state):
        calls["rewrite"] += 1
        return (
            f"오뚜기 FY2025 재작성 질문 {calls['rewrite']}"
            if stop == "limit"
            else f"  {RELATED_QUESTION}  "
        )

    result = build_graph(
        retriever=retrieve,
        scorer=lambda _: False,
        rewriter=rewrite,
        corpus_catalog=RELATED_CATALOG,
    ).invoke({
        "question": RELATED_QUESTION, "checklist_items": ["litigation"],
        "refusal_reason": "PERIOD_NOT_IN_CORPUS",
        "draft_items": [{"item": "litigation", "verdict": "applicable"}],
        "citations": [{"chunk_id": "stale"}],
    })

    assert calls == {"search": searches, "rewrite": rewrites}
    assert result["retrieval_rounds"] == searches
    assert result["retrieval_sufficient"] is False
    assert result["citations"] == []
    assert not result.get("saved_memo_id")
    if has_chunks:
        assert result["outcome"] == "answered"
        assert result.get("refusal_reason") is None
        assert result["draft_items"] == [{
            "item": "litigation", "verdict": "needs_check",
            "reason": "근거 부족: 확인할 공시 항목이나 기간을 구체화해 주세요.",
            "citations": [],
        }]
    else:
        assert result["outcome"] == "refused"
        assert result["refusal_reason"] == "ITEM_NOT_DISCLOSED"
        assert result["draft_items"] == []
        assert result["node_events"][-1] == "writer"
        assert not {"verifier", "human_approval", "save_memo"} & set(result["node_events"])


def test_scorer_excludes_mismatched_chunks_before_one_batch_grade_and_keeps_relevant_no_disclosure():
    graded_chunk_ids = []
    matching = {**RELATED_CHUNK, "chunk_id": "not-disclosed", "content": "소송 해당 사항 없음"}
    mismatched = {**RELATED_CHUNK, "chunk_id": "other-corp", "corp_code": "99999999"}

    result = build_graph(
        draft_writer=lambda _state, _chunks: [],
        retriever=lambda _: [matching, mismatched],
        batch_grader=lambda _state, chunks: graded_chunk_ids.extend(
            chunk["chunk_id"] for chunk in chunks
        ) or {"not-disclosed"},
        llm_grading_enabled=True,
        corpus_catalog=RELATED_CATALOG,
    ).invoke({"question": RELATED_QUESTION})

    assert graded_chunk_ids == ["not-disclosed"]
    assert [chunk["chunk_id"] for chunk in result["retrieved_chunks"]] == ["not-disclosed"]
    assert result["retrieval_sufficient"] is True


def test_empty_search_becomes_item_not_disclosed_without_llm_grading():
    result = build_graph(
        retriever=lambda _: [], corpus_catalog=RELATED_CATALOG
    ).invoke({"question": RELATED_QUESTION})

    assert result["outcome"] == "refused"
    assert result["refusal_reason"] == "ITEM_NOT_DISCLOSED"


def test_llm_grade_rejection_of_all_chunks_retries_then_refuses_item_not_disclosed():
    """60번: 채점이 매번 근거를 모두 버리면 상한까지 재검색한 뒤 '공시 없음'으로 거절한다."""
    rewrites = 0

    def rewrite(_state):
        nonlocal rewrites
        rewrites += 1
        return f"오뚜기 FY2025 차입금 현황 {rewrites}"

    result = build_graph(
        retriever=lambda _: [RELATED_CHUNK],
        batch_grader=lambda _state, _chunks: set(),
        llm_grading_enabled=True,
        rewriter=rewrite,
        corpus_catalog=RELATED_CATALOG,
    ).invoke({"question": RELATED_QUESTION, "checklist_items": ["litigation"]})

    assert rewrites == 2
    assert result["retrieval_rounds"] == 3
    assert result["outcome"] == "refused"
    assert result["refusal_reason"] == "ITEM_NOT_DISCLOSED"
    assert result["draft_items"] == []


def test_llm_grade_keeping_some_chunks_but_insufficient_still_answers_needs_check():
    """60번 뒤에도 AC-02 유지: 근거가 남아 있으면 부족만으로 거절하지 않는다."""

    result = build_graph(
        retriever=lambda _: [RELATED_CHUNK],
        batch_grader=lambda _state, chunks: {c["chunk_id"] for c in chunks},
        llm_grading_enabled=True,
        scorer=lambda _state: False,
        rewriter=lambda s: f"오뚜기 FY2025 차입금 {s.get('retrieval_rounds', 0)}",
        corpus_catalog=RELATED_CATALOG,
    ).invoke({"question": RELATED_QUESTION, "checklist_items": ["litigation"]})

    assert result["retrieval_rounds"] == 3
    assert result["outcome"] == "answered"
    assert result["draft_items"][0]["verdict"] == "needs_check"


def test_graph_rejects_same_generation_and_judge_model():
    with pytest.raises(ValueError, match="생성 모델과 judge 모델"):
        build_graph(
            corpus_catalog=RELATED_CATALOG,
            generation_model="same-model",
            judge_model="same-model",
        )


def test_injected_rewriter_that_drops_target_stops_without_a_broader_search():
    calls = []
    result = build_graph(
        retriever=lambda state: calls.append(state) or [RELATED_CHUNK],
        scorer=lambda _state: False,
        rewriter=lambda _state: "차입금 현황",
        corpus_catalog=RELATED_CATALOG,
    ).invoke({"question": RELATED_QUESTION, "checklist_items": ["litigation"]})

    assert len(calls) == 1
    assert result["node_events"][3:5] == ["rewriter:target_lost", "writer"]


def test_writer_downgrades_confirmed_verdict_without_retained_citation():
    result = build_graph(
        retriever=lambda _: [RELATED_CHUNK],
        draft_writer=lambda _state, _chunks: [
            {
                "item": "litigation",
                "verdict": "applicable",
                "reason": "근거가 있다고 주장",
                "chunk_ids": ["not-retained"],
            }
        ],
        corpus_catalog=RELATED_CATALOG,
    ).invoke({"question": RELATED_QUESTION, "checklist_items": ["litigation"]})

    assert result["draft_items"][0]["verdict"] == "needs_check"
    assert result["draft_items"][0]["citations"] == []


def test_preexhausted_retrieval_state_never_calls_a_fourth_retriever():
    calls = []
    result = build_graph(
        retriever=lambda state: calls.append(state) or [RELATED_CHUNK],
        draft_writer=lambda _state, _chunks: [],
        corpus_catalog=RELATED_CATALOG,
    ).invoke(
        {
            "question": RELATED_QUESTION,
            "retrieval_rounds": 3,
            "retrieval_sufficient": False,
            "retrieved_chunks": [RELATED_CHUNK],
            "checklist_items": ["litigation"],
        }
    )

    assert calls == []
    assert result["retrieval_rounds"] == 3


def test_writer_uses_retained_chunk_id_and_marks_uncovered_item_needs_check():
    result = build_graph(
        retriever=lambda _: [RELATED_CHUNK],
        draft_writer=lambda _state, _chunks: [
            {
                "item": "litigation",
                "verdict": "not_applicable",
                "reason": "소송 해당 사항 없음",
                "chunk_ids": ["chunk-1"],
            }
        ],
        corpus_catalog=RELATED_CATALOG,
    ).invoke(
        {
            "question": RELATED_QUESTION,
            "checklist_items": ["litigation", "guarantee_collateral"],
        }
    )

    assert result["draft_items"] == [
        {
            "item": "litigation",
            "verdict": "not_applicable",
            "reason": "소송 해당 사항 없음",
            "citations": [
                {
                    "chunk_id": "chunk-1",
                    "rcept_no": "20260304000913",
                    "chapter": "NOTES",
                    "note_no": "1",
                    "quote": "차입금 관련 공시 내용",
                    "verification": {
                        "passed": True,
                        "failed_checks": [],
                    },
                }
            ],
        },
        {
            "item": "guarantee_collateral",
            "verdict": "needs_check",
            "reason": "근거 부족: 확인할 공시 항목이나 기간을 구체화해 주세요.",
            "citations": [],
        },
    ]
    assert result["citations"][0]["chunk_id"] == "chunk-1"


def test_verifier_downgrades_invalid_reason_before_human_approval():
    result = build_graph(
        corpus_catalog=RELATED_CATALOG,
        retriever=lambda _: [RELATED_CHUNK],
        draft_writer=lambda _state, _chunks: [
            {
                "item": "litigation",
                "verdict": "applicable",
                "reason": "소송 충당부채 2,000억원을 인식했습니다.",
                "chunk_ids": ["chunk-1"],
            }
        ],
    ).invoke({"question": RELATED_QUESTION, "checklist_items": ["litigation"]})

    assert result["draft_items"] == [
        {
            "item": "litigation",
            "verdict": "needs_check",
            "reason": "근거 부족: 확인할 공시 항목이나 기간을 구체화해 주세요.",
            "citations": [],
        }
    ]
    assert result["citations"] == []
    assert result["verification"] == [
        {
            "item": "litigation",
            "chunk_id": "chunk-1",
            "passed": False,
            "failed_checks": ["reason_number_or_unit_not_in_chunk"],
            "missing_numbers": ["2000억원"],  # 78번: 청크에 없는 reason 숫자
        }
    ]
    assert "verifier" in result["node_events"]


def test_frozen_v1_questions_route_with_manifest_targets():
    import json
    from pathlib import Path
    from backend.graph.refusal import load_corpus_catalog

    root = Path(__file__).resolve().parents[1]
    catalog = load_corpus_catalog(root / "data/manifest.100-b.jsonl")
    questions = [json.loads(line) for line in
                 (root / "evals/dataset_v1.jsonl").read_text(encoding="utf-8").splitlines()]
    calls = []
    graph = build_graph(corpus_catalog=catalog, retriever=lambda state: calls.append(state) or [])
    for row in questions:
        calls.clear()
        question = row["input"]
        result = graph.invoke({"question": question})
        # 59번 주제 판정 기본 켜짐: '예측' 요청(SPEC §1 범위 밖)은 키 없이도 규칙으로 거절된다
        if "예측" in question:
            assert calls == [], row["id"]
            assert result["refusal_reason"] == "OUT_OF_DOMAIN"
            continue
        if row["kind"] == "relevant" or row.get("irrelevant_type") in {"missing_attribute", "out_of_domain"}:
            assert len(calls) == 1, row["id"]
            assert result.get("domain_check") == "skipped_no_key"  # 키가 없으면 LLM 분류를 건너뜀
            assert calls[0]["filters"]["fiscal_year"] == 2025
            assert result["refusal_reason"] == "ITEM_NOT_DISCLOSED"
        else:
            assert calls == []
            assert result["outcome"] == "refused"

def test_writer_uses_question_when_checklist_is_missing():
    def draft_writer(state, chunks):
        assert state["checklist_items"] == [RELATED_QUESTION]
        return [{"item": RELATED_QUESTION, "verdict": "applicable",
                 "reason": "fixture", "chunk_ids": [chunks[0]["chunk_id"]]}]

    result = build_graph(
        corpus_catalog=RELATED_CATALOG, retriever=lambda _: [RELATED_CHUNK],
        draft_writer=draft_writer,
    ).invoke({"question": RELATED_QUESTION})

    assert result["outcome"] == "answered"
    assert result["draft_items"][0]["item"] == RELATED_QUESTION
    assert result["citations"][0]["chunk_id"] == RELATED_CHUNK["chunk_id"]


def test_empty_retry_does_not_discard_previous_evidence():
    batches = iter([[RELATED_CHUNK], [], []])
    rewrites = iter(["오뚜기 FY2025 차입금", "오뚜기 FY2025 만기"])
    result = build_graph(
        corpus_catalog=RELATED_CATALOG, retriever=lambda _: next(batches),
        scorer=lambda _: False, rewriter=lambda _: next(rewrites),
    ).invoke({"question": RELATED_QUESTION})

    assert result["retrieved_chunks"] == [RELATED_CHUNK]
    assert result["outcome"] == "answered"
    assert result["draft_items"][0]["verdict"] == "needs_check"


@pytest.mark.parametrize(
    ("question", "expected_year", "expected_reason"),
    [
        ("오뚜기 작년 차입금", 2025, None),
        ("오뚜기 25년 매출액", 2025, None),
        ("오뚜기 올해 차입금", None, "PERIOD_NOT_IN_CORPUS"),
        ("오뚜기 2026년 차입금", None, "PERIOD_NOT_IN_CORPUS"),
    ],
)
def test_supervisor_uses_one_kst_date_for_period_filter(
    monkeypatch, question, expected_year, expected_reason
):
    dates_read = []

    def fixed_date():
        dates_read.append(True)
        return date(2026, 11, 5)

    monkeypatch.setattr("backend.graph.build.kst_today", fixed_date)
    calls = []
    result = build_graph(
        retriever=lambda state: calls.append(state) or [],
        corpus_catalog=RELATED_CATALOG,
    ).invoke({"question": question})

    assert len(dates_read) == 1
    if expected_reason:
        assert calls == []
        assert result["refusal_reason"] == expected_reason
    else:
        assert len(calls) == 1
        assert calls[0]["fiscal_year"] == expected_year
        assert calls[0]["filters"] == {
            "corp_code": "00000001", "fiscal_year": expected_year
        }


@pytest.mark.parametrize(
    "question",
    [
        "오뚜기 올해 접수된 사업보고서 차입금",
        "오뚜기 2026년에 제출된 사업보고서 차입금",
    ],
)
def test_supervisor_asks_for_fiscal_year_when_filing_year_is_given(
    monkeypatch, question
):
    monkeypatch.setattr("backend.graph.build.kst_today", lambda: date(2026, 11, 5))
    calls = []
    result = build_graph(
        retriever=lambda state: calls.append(state) or [],
        corpus_catalog=RELATED_CATALOG,
    ).invoke({"question": question})
    assert calls == []
    assert result["target_required"] is True
    assert result["refusal_reason"] is None
    assert "접수연도" in result["refusal_message"]


def test_supervisor_keeps_explicit_fiscal_year_when_asking_filing_date(monkeypatch):
    monkeypatch.setattr("backend.graph.build.kst_today", lambda: date(2026, 11, 5))
    calls = []
    build_graph(
        retriever=lambda state: calls.append(state) or [],
        corpus_catalog=RELATED_CATALOG,
    ).invoke({"question": "오뚜기 FY2025 사업보고서 접수일 알려줘"})
    assert len(calls) == 1
    assert calls[0]["filters"] == {"corp_code": "00000001", "fiscal_year": 2025}


def test_supervisor_asks_to_choose_year_if_both_comparison_years_exist(monkeypatch):
    monkeypatch.setattr("backend.graph.build.kst_today", lambda: date(2026, 11, 5))
    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기"}),
        fiscal_years=frozenset({2024, 2025}),
        report_types=frozenset({"annual"}),
        targets=frozenset({
            CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2024),
            CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2025),
        }),
    )
    calls = []
    result = build_graph(
        retriever=lambda state: calls.append(state) or [], corpus_catalog=catalog
    ).invoke({"question": "오뚜기 작년과 재작년 차입금 비교"})
    assert calls == []
    assert result["target_required"] is True
    assert "한 사업연도" in result["refusal_message"]


def test_supervisor_refuses_comparison_if_prior_year_missing(monkeypatch):
    monkeypatch.setattr("backend.graph.build.kst_today", lambda: date(2026, 11, 5))
    calls = []
    result = build_graph(
        retriever=lambda state: calls.append(state) or [],
        corpus_catalog=RELATED_CATALOG,
    ).invoke({"question": "오뚜기 작년과 재작년 차입금 비교"})
    assert calls == []
    assert result["refusal_reason"] == "PERIOD_NOT_IN_CORPUS"


def test_evidence_quote_adds_its_chunk_and_unknown_quote_is_dropped():
    """69번: quote가 그대로 있는 청크를 인용에 더하고(적힌 id가 틀려도), 어디에도 없는 quote는 버린다.

    인용 quote는 63번처럼 청크 본문이라 verifier 하향이 생기지 않는다.
    """

    other = {**RELATED_CHUNK, "chunk_id": "chunk-2", "content": "유동 차입금 | 162,832,228"}
    result = build_graph(
        corpus_catalog=RELATED_CATALOG, retriever=lambda _: [RELATED_CHUNK, other],
        draft_writer=lambda s, c: [{"item": "litigation", "verdict": "applicable", "reason": "차입금 관련 공시",
                                    "chunk_ids": ["chunk-1"],
                                    "evidence": [{"chunk_id": "chunk-1", "quote": "유동 차입금 | 162,832,228"},
                                                 {"chunk_id": "chunk-1", "quote": "원문에 없는 행"}]}],
    ).invoke({"question": RELATED_QUESTION, "checklist_items": ["litigation"]})

    assert result["draft_items"][0]["verdict"] == "applicable"
    assert [c["chunk_id"] for c in result["citations"]] == ["chunk-1", "chunk-2"]
    assert result["citations"][1]["quote"] == other["content"]
