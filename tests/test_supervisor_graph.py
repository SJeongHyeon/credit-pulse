"""D5 Supervisor + 서브에이전트 시험 (docs/specs/d5-supervisor-split.md AC-01~04).

가짜 검색기·가짜 LLM만 쓴다(키 없음). 상한은 상수가 아니라 리터럴과 비교한다(165번부터 검색은 최대 2회: 첫 검색 1 + 재검색 1).
"""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from types import SimpleNamespace

import pytest

from backend.graph import build as graph_build
from backend.graph.agents import search_agent as search_module
from backend.graph.agents import writer_agent as writer_module
from backend.graph.agents.search_agent import SEARCH_INPUT_KEYS, SearchState
from backend.graph.agents.writer_agent import WRITER_INPUT_KEYS, WriterState
from backend.graph.build import build_graph, build_supervisor_graph
from backend.graph.refusal import CorpusCatalog, CorpusTarget, load_corpus_catalog
from backend.memory import RuleStore, SemanticRuleStore
from langgraph.store.memory import InMemoryStore

from tests.semantic_store import FAKE_INDEX_CONFIG, indexed_store

ROOT = Path(__file__).resolve().parents[1]
V2_ROWS = [
    json.loads(line)
    for line in (ROOT / "evals/dataset_v2.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
V2_FILTERS = {
    int(row["id"]): row
    for row in map(json.loads, (ROOT / "evals/filters_v2.graph.jsonl").read_text(encoding="utf-8").splitlines())
}
CATALOG = load_corpus_catalog(ROOT / "data/manifest.jsonl")

RELATED_QUESTION = "오뚜기의 FY2025 사업보고서 차입금 현황을 알려주세요."
RELATED_CATALOG = CorpusCatalog(
    corp_names=frozenset({"오뚜기"}),
    fiscal_years=frozenset({2025}),
    report_types=frozenset({"annual"}),
    targets=frozenset({CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2025)}),
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


@dataclass
class RecordedCall:
    """검색 호출 기록(러너가 asdict로 저장하므로 dataclass로 둔다)."""

    query: str
    filters: dict


class FakeRetriever:
    """질의·필터로 정해지는 결정적 청크를 돌려준다. 다른 회사 청크 1개를 섞어 scorer 검사도 지나게 한다.

    '비공개'가 든 질문은 0건(ITEM_NOT_DISCLOSED 경로)을 돌려준다.
    """

    def __init__(self) -> None:
        self.calls: list[RecordedCall] = []

    def __call__(self, state):
        query = " ".join(str(state.get("retrieval_query") or state.get("question", "")).split())
        self.calls.append(RecordedCall(query=query, filters=dict(state["filters"])))
        if "비공개" in str(state.get("question", "")):
            return []
        filters = state["filters"]
        digest = hashlib.sha1(query.encode("utf-8")).hexdigest()[:8]
        chunks = [
            {
                "chunk_id": f"{filters['corp_code']}:{digest}:{rank}",
                "corp_code": filters["corp_code"],
                "fiscal_year": filters["fiscal_year"],
                "content": f"{query} 근거 {rank}",
                "rcept_no": "20260300000000",
                "chapter": "NOTES",
                "note_no": str(rank),
            }
            for rank in range(1, 4)
        ]
        chunks.append({**chunks[0], "chunk_id": f"other:{digest}", "corp_code": "99999999"})
        return chunks


class UserScopedSemanticStore:
    index_config = FAKE_INDEX_CONFIG

    def __init__(self):
        self.search_namespaces = []

    def search(self, namespace_prefix, *, query=None, limit=None):
        self.search_namespaces.append(namespace_prefix)
        rules = {
            ("memories", "user-a"): [
                SimpleNamespace(
                    key="rule-a",
                    value={
                        "item": "차입금",
                        "instruction": "연결 기준으로 판단한다.",
                        "source_memo_id": "memo-a",
                    },
                )
            ],
            ("memories", "user-b"): [
                SimpleNamespace(
                    key="rule-b",
                    value={
                        "item": "소송",
                        "instruction": "우발부채 주석을 우선 확인한다.",
                        "source_memo_id": "memo-b",
                    },
                )
            ],
        }
        return rules.get(namespace_prefix, [])[:limit]


def fake_draft_writer(state, chunks):
    """항목마다 1위 청크와 없는 id를 인용한다(없는 id는 writer가 버려야 한다)."""

    return [
        {"item": item, "verdict": "applicable", "reason": "가짜", "chunk_ids": [chunks[0]["chunk_id"], "no-such-id"]}
        for item in state.get("checklist_items", [])
        if chunks
    ]


# 104번: 멀티 그래프에는 안쪽 재작성 루프가 없어 '채점 부족·재작성' 시나리오는 단일 그래프 시험(test_graph.py)이 맡는다.
SCENARIOS = {
    # 기본: 채점기 없음(남은 청크 있으면 충분), 재작성은 호출되지 않음
    "default": {"scorer": None, "rewriter": lambda s: str(s.get("retrieval_query") or s["question"])},
}


def _run(builder, row, scenario):
    retriever = FakeRetriever()
    graph = builder(
        retriever=retriever,
        corpus_catalog=CATALOG,
        scorer=scenario["scorer"],
        draft_writer=fake_draft_writer,
        **({"rewriter": scenario["rewriter"]} if scenario.get("rewriter") else {}),
    )
    final = graph.invoke({
        "user_id": "eval",
        "review_id": f"eval-{row['id']}",
        "question": row["input"],
        "node_events": [],
        "retrieval_rounds": 0,
    })
    return {
        "outcome": final.get("outcome"),
        "refusal_reason": final.get("refusal_reason"),
        "citation_ids": [c["chunk_id"] for c in final.get("citations") or []],
        "draft_items": final.get("draft_items"),
        "retrieval_calls": len(retriever.calls),
        "retrieval_rounds": final.get("retrieval_rounds"),
        "node_events": final.get("node_events"),
        "filters": final.get("filters"),
    }


def test_supervisor_graph_records_empty_applied_rules_for_user_without_rules():
    graph = build_supervisor_graph(
        retriever=FakeRetriever(),
        corpus_catalog=RELATED_CATALOG,
        draft_writer=fake_draft_writer,
        rule_store=RuleStore(InMemoryStore()),
    )

    final = graph.invoke({
        "user_id": "user-a",
        "review_id": "review-a",
        "question": RELATED_QUESTION,
        "node_events": [],
        "retrieval_rounds": 0,
    })

    assert final["applied_rules"] == []


def test_supervisor_graph_keeps_empty_memory_writer_input_equal_to_baseline():
    received = []

    def recording_writer(state, chunks):
        received.append((dict(state), list(chunks)))
        return fake_draft_writer(state, chunks)

    baseline = build_supervisor_graph(
        retriever=FakeRetriever(),
        corpus_catalog=RELATED_CATALOG,
        draft_writer=recording_writer,
        dual_draft=False, speculative=False,  # 순차 경로: 이 시험은 작성기가 받는 입력 계약을 본다(기본 경로는 초안 2개라 두 번 불린다)
    )
    baseline.invoke(
        {
            "user_id": "user-a",
            "review_id": "review-a",
            "question": RELATED_QUESTION,
            "node_events": [],
            "retrieval_rounds": 0,
        }
    )
    baseline_input = received.pop()

    with_memory = build_supervisor_graph(
        retriever=FakeRetriever(),
        corpus_catalog=RELATED_CATALOG,
        draft_writer=recording_writer,
        memory_store=SemanticRuleStore(indexed_store()),
        dual_draft=False, speculative=False,  # 순차 경로: 이 시험은 작성기가 받는 입력 계약을 본다(기본 경로는 초안 2개라 두 번 불린다)
    )
    with_memory.invoke(
        {
            "user_id": "user-a",
            "review_id": "review-a",
            "question": RELATED_QUESTION,
            "node_events": [],
            "retrieval_rounds": 0,
        }
    )

    assert received == [baseline_input]


def test_supervisor_graph_applies_only_each_users_semantic_rules():
    raw_store = UserScopedSemanticStore()
    graph = build_supervisor_graph(
        retriever=FakeRetriever(),
        corpus_catalog=RELATED_CATALOG,
        draft_writer=fake_draft_writer,
        memory_store=SemanticRuleStore(raw_store),
    )

    user_a = graph.invoke(
        {
            "user_id": "user-a",
            "review_id": "review-a",
            "question": RELATED_QUESTION,
            "node_events": [],
            "retrieval_rounds": 0,
        }
    )
    user_b = graph.invoke(
        {
            "user_id": "user-b",
            "review_id": "review-b",
            "question": RELATED_QUESTION,
            "node_events": [],
            "retrieval_rounds": 0,
        }
    )

    assert [rule["rule_id"] for rule in user_a["applied_rules"]] == ["rule-a"]
    assert [rule["rule_id"] for rule in user_b["applied_rules"]] == ["rule-b"]
    # 사용자마다 조회 2회(규칙이 있는지 확인 + 의미 검색), 모두 자기 namespace만 본다
    assert raw_store.search_namespaces == [
        ("memories", "user-a"),
        ("memories", "user-a"),
        ("memories", "user-b"),
        ("memories", "user-b"),
    ]


def test_supervisor_graph_loads_only_calling_users_rules():
    rule_store = RuleStore(InMemoryStore())
    rule_store.create_rule(
        "user-a",
        "rule-a",
        {
            "item": "차입금",
            "instruction": "연결 기준으로 판단한다.",
            "source_memo_id": "memo-a",
        },
    )
    rule_store.create_rule(
        "user-b",
        "rule-b",
        {
            "item": "소송",
            "instruction": "우발부채 주석을 우선 확인한다.",
            "source_memo_id": "memo-b",
        },
    )
    graph = build_supervisor_graph(
        retriever=FakeRetriever(),
        corpus_catalog=RELATED_CATALOG,
        draft_writer=fake_draft_writer,
        rule_store=rule_store,
    )

    final = graph.invoke({
        "user_id": "user-a",
        "review_id": "review-a",
        "question": RELATED_QUESTION,
        "node_events": [],
        "retrieval_rounds": 0,
    })

    assert [rule["rule_id"] for rule in final["applied_rules"]] == ["rule-a"]


def test_supervisor_graph_passes_applied_rules_to_writer_agent():
    rule_store = RuleStore(InMemoryStore())
    rule_store.create_rule(
        "user-a",
        "rule-a",
        {
            "item": "차입금",
            "instruction": "연결 기준으로 판단한다.",
            "source_memo_id": "memo-a",
        },
    )
    received_rules = []

    def recording_draft_writer(state, chunks):
        received_rules.append(state.get("applied_rules"))
        return fake_draft_writer(state, chunks)

    graph = build_supervisor_graph(
        retriever=FakeRetriever(),
        corpus_catalog=RELATED_CATALOG,
        draft_writer=recording_draft_writer,
        rule_store=rule_store,
        dual_draft=False, speculative=False,  # 순차 경로: 이 시험은 작성기가 받는 입력 계약을 본다(기본 경로는 초안 2개라 두 번 불린다)
    )
    graph.invoke({
        "user_id": "user-a",
        "review_id": "review-a",
        "question": RELATED_QUESTION,
        "node_events": [],
        "retrieval_rounds": 0,
    })

    assert len(received_rules) == 1
    assert len(received_rules[0]) == 1
    saved_rule = received_rules[0][0]
    assert {
        key: saved_rule[key]
        for key in ("rule_id", "item", "instruction", "source_memo_id")
    } == {
        "rule_id": "rule-a",
        "item": "차입금",
        "instruction": "연결 기준으로 판단한다.",
        "source_memo_id": "memo-a",
    }
    assert saved_rule["created_at"]
    assert saved_rule["updated_at"]


def test_supervisor_graph_passes_rules_to_default_llm_writer(monkeypatch):
    rule_store = RuleStore(InMemoryStore())
    rule_store.create_rule(
        "user-a",
        "rule-a",
        {
            "item": "차입금",
            "instruction": "연결 기준으로 판단한다.",
            "source_memo_id": "memo-a",
        },
    )
    captured = {}

    def fake_write_drafts(*, checklist_items, chunks, **kwargs):
        captured["applied_rules"] = kwargs["applied_rules"]
        return fake_draft_writer({"checklist_items": checklist_items}, chunks)

    monkeypatch.setattr(writer_module.llm, "write_drafts", fake_write_drafts)
    graph = build_supervisor_graph(
        retriever=FakeRetriever(),
        corpus_catalog=RELATED_CATALOG,
        rule_store=rule_store,
    )
    graph.invoke({
        "user_id": "user-a",
        "review_id": "review-a",
        "question": RELATED_QUESTION,
        "node_events": [],
        "retrieval_rounds": 0,
    })

    assert [rule["rule_id"] for rule in captured["applied_rules"]] == ["rule-a"]


# ── ① 구조 동등성 ─────────────────────────────────────────────
# 104번: 멀티 그래프에는 안쪽 재작성 루프가 없다(재검색은 Supervisor 지시로만). 그래서 동등성은 LLM이 없을 때의
# 코드 규칙 경로(키 없음 → Supervisor·채점 fallback)가 단일 그래프의 '재작성 없는' 경로와 같은지로 본다.
# 근거 0건일 때 단일은 작성기가, 멀티는 Supervisor가 거절해 이벤트가 다르므로 응답 칸만 비교한다.

RESPONSE_KEYS = ("outcome", "refusal_reason", "citation_ids", "draft_items", "retrieval_calls", "retrieval_rounds", "filters")


def _response(result):
    return {key: result[key] for key in RESPONSE_KEYS}


def test_single_and_multi_match_on_frozen_v2_50_without_llm():
    scenario = SCENARIOS["default"]
    assert len(V2_ROWS) == 50
    mismatches = []
    for row in V2_ROWS:
        single = _run(build_graph, row, scenario)
        multi = _run(build_supervisor_graph, row, {**scenario, "rewriter": None})
        if _response(single) != _response(multi):
            mismatches.append((row["id"], single, multi))
    assert mismatches == []


def test_equivalence_fixture_exercises_every_route():
    """동등성 시험이 빈 비교가 되지 않도록, 시나리오가 실제로 각 경로를 지나는지 확인한다."""

    seen = [_run(build_supervisor_graph, row, {**SCENARIOS["default"], "rewriter": None}) for row in V2_ROWS]
    # 63번: 회사 이름이 둘인 비교 질문(v2 32번)은 회사마다 한 번씩 검색한다
    assert {r["retrieval_calls"] for r in seen} == {0, 1, 2}
    assert any(r["citation_ids"] for r in seen)
    reasons = {r["refusal_reason"] for r in seen}
    assert {"CORP_NOT_IN_CORPUS", "PERIOD_NOT_IN_CORPUS", "ITEM_NOT_DISCLOSED"} <= reasons
    assert any(r["filters"] is None and r["outcome"] == "answered" for r in seen)  # target_required
    # 근거 0건(ITEM_NOT_DISCLOSED)은 멀티에서 작성기 없이 Supervisor가 거절한다
    refused = [r for r in seen if r["refusal_reason"] == "ITEM_NOT_DISCLOSED"]
    assert refused and all("writer" not in r["node_events"] for r in refused)


def test_runner_multi_matches_single_with_fake_agents():
    """러너 --graph multi 경로(run_eval)가 single과 같은 응답을 낸다(멀티 전용 기록 칸·시간·이벤트 제외)."""

    from scripts.run_graph_eval import (
        FAKE_AGENTS,
        fake_draft_writer as runner_writer,
        fake_search_grader,
        fake_supervisor_decider,
        run_eval,
    )

    rows = V2_ROWS[:5]
    filters = {int(r["id"]): V2_FILTERS[int(r["id"])] for r in rows}
    kwargs = {
        "single": dict(FAKE_AGENTS),
        "multi": {"draft_writer": runner_writer, "supervisor_decider": fake_supervisor_decider,
                  "search_grader": fake_search_grader},
    }
    multi_only = {"elapsed_ms", "node_events", "supervisor_decisions", "search_calls", "redraft_calls",
                  "search_report", "writer_report", "grader_events"}
    outputs = {}
    for kind in ("single", "multi"):
        responses = run_eval(
            rows, filters, retriever=FakeRetriever(), graph_kwargs=kwargs[kind],
            corpus_catalog=CATALOG, graph_kind=kind,
        )
        outputs[kind] = [{k: v for k, v in r.items() if k not in multi_only} for r in responses]
    assert outputs["single"] == outputs["multi"]


def test_runner_saves_verifier_record_per_response():
    """러너 응답에 verifier 기록이 남아, 하향 항목 수·사유를 결과 파일에서 셀 수 있다."""

    from scripts.run_graph_eval import FAKE_AGENTS, run_eval

    rows = [row for row in V2_ROWS if int(row["id"]) in {1, 20}]  # 검색까지 가는 관련 1 · 사전 거절 1
    filters = {int(r["id"]): V2_FILTERS[int(r["id"])] for r in rows}
    responses = {
        r["id"]: r
        for r in run_eval(rows, filters, retriever=FakeRetriever(), graph_kwargs=dict(FAKE_AGENTS), corpus_catalog=CATALOG)
    }
    assert responses[1]["verification"]
    assert {"item", "chunk_id", "passed", "failed_checks"} <= set(responses[1]["verification"][0])
    assert responses[20]["verification"] == []


def test_runner_default_graph_is_single():
    from scripts import run_graph_eval

    with pytest.raises(run_graph_eval.GraphEvalError):
        run_graph_eval.run_eval([], {}, retriever=FakeRetriever(), graph_kind="parallel")
    source = (ROOT / "scripts/run_graph_eval.py").read_text(encoding="utf-8")
    assert 'choices=("single", "multi"), default="single"' in source


def test_supervisor_graph_rejects_same_generation_and_judge_model():
    with pytest.raises(ValueError):
        build_supervisor_graph(
            corpus_catalog=RELATED_CATALOG, generation_model="same/model", judge_model="same/model"
        )


# ── ② 인계 계약 ──────────────────────────────────────────────

POLLUTED_STATE = {
    "user_id": "user-1",
    "review_id": "review-1",
    "thread_id": "user-1:review-1",
    "question": RELATED_QUESTION,
    "checklist_items": ["borrowing_structure"],
    "retrieval_query": "오염된 이전 턴 질의",
    "rewrite_changed": True,
    "target_required": False,
    "approval_seq": 3,
    "approval_decisions": [{"item": "x"}],
    "draft_items": [{"item": "old"}],
    "citations": [{"chunk_id": "old"}],
    "errors": ["old"],
    "node_events": [],
}


def test_handoff_selectors_keep_only_contract_keys():
    full_state = {
        **POLLUTED_STATE,
        "corp_code": "00000001", "corp_name": "오뚜기", "fiscal_year": 2025,
        "filters": {"corp_code": "00000001", "fiscal_year": 2025},
        "retrieved_chunks": [RELATED_CHUNK], "retrieval_rounds": 2,
        "retrieval_sufficient": True, "retrieval_empty": False, "domain_check": "in_domain",
    }
    # 검색 회차·횟수, 이전 턴의 retrieval_query·보고는 State에 남아 있어도 넘기지 않는다
    assert set(search_module.select_search_input({**full_state, "search_round": 2, "search_calls": 1})) == {
        "question", "corp_code", "corp_name", "fiscal_year", "checklist_items", "filters",
    }
    # 165·166번: 재검색 칸(search_query·previous_chunks·previous_missing_items)은 고르는 함수가 넘기지 않는다. 재검색일 때만 어댑터
    # (search_agent_node)가 채운다. 그래야 미리 돌린 첫 검색과 실제 첫 검색의 인계가 같다(110번).
    research = {**full_state, "search_query": "오뚜기 2025 다른 질의", "search_round": 2, "search_calls": 1,
                "search_report": {"n_chunks": 1, "sufficient": False, "missing_items": ["차입금 만기"]},
                "previous_chunks": [RELATED_CHUNK], "previous_missing_items": ["차입금 만기"]}
    assert set(search_module.select_search_input(research)) == {
        "question", "corp_code", "corp_name", "fiscal_year", "checklist_items", "filters",
    }
    assert SEARCH_INPUT_KEYS == {
        "question", "corp_code", "corp_name", "fiscal_year", "checklist_items", "filters", "comparison_targets",
        "search_query", "previous_chunks", "previous_missing_items"}
    assert search_module.RESEARCH_KEYS == {"search_query", "previous_chunks", "previous_missing_items"}
    assert set(writer_module.select_writer_input(full_state)) == {
        "question", "checklist_items", "retrieved_chunks", "retrieval_sufficient", "retrieval_empty", "domain_check",
    }
    # 104번: Supervisor 다시 쓰기 지시(평소엔 비어 있음)도 계약 키다
    assert "writer_feedback" in writer_module.select_writer_input({**full_state, "writer_feedback": ""})


def test_subagents_never_see_keys_outside_contract():
    retriever_states: list[dict] = []
    writer_states: list[dict] = []

    def recording_retriever(state):
        retriever_states.append(dict(state))
        return [RELATED_CHUNK]

    def recording_writer(state, chunks):
        writer_states.append(dict(state))
        return fake_draft_writer(state, chunks)

    result = build_supervisor_graph(
        retriever=recording_retriever,
        draft_writer=recording_writer,
        corpus_catalog=RELATED_CATALOG,
        dual_draft=False, speculative=False,  # 순차 경로: 이 시험은 작성기가 받는 입력 계약을 본다(기본 경로는 초안 2개라 두 번 불린다)
    ).invoke(dict(POLLUTED_STATE))

    assert result["outcome"] == "answered"
    assert len(retriever_states) == 1 and len(writer_states) == 1
    search_state_keys = SearchState.__required_keys__ | SearchState.__optional_keys__
    writer_state_keys = WriterState.__required_keys__ | WriterState.__optional_keys__
    forbidden = {"user_id", "review_id", "thread_id", "approval_seq", "approval_decisions", "errors", "target_required"}
    for state in retriever_states:
        assert set(state) <= search_state_keys
        assert not set(state) & forbidden
    # 질의는 이전 턴의 retrieval_query가 아니라 이번 질문에서 만든다
    assert retriever_states[0]["retrieval_query"] == RELATED_QUESTION
    # 165·166번: 첫 검색에는 재검색 칸(질의·첫 검색 청크·첫 채점의 빠진 항목)이 넘어가지 않는다
    assert not set(retriever_states[0]) & {"search_query", "previous_chunks", "previous_missing_items"}
    assert set(writer_states[0]) <= writer_state_keys
    assert not set(writer_states[0]) & (forbidden | {"retrieval_query", "rewrite_changed", "retrieval_rounds"})
    assert set(writer_states[0]) - {"node_events"} <= WRITER_INPUT_KEYS
    assert set(retriever_states[0]) - {"node_events", "retrieval_rounds", "retrieval_query"} <= SEARCH_INPUT_KEYS


# ── ③ 상한과 제자리걸음 ───────────────────────────────────────

@pytest.mark.parametrize("speculative", [False, True], ids=["plain", "speculative"])
def test_multi_stops_after_exactly_two_retrievals(speculative):
    """165번: 검색은 질문당 최대 2회(첫 검색 1 + 재검색 1). 채점이 근거 부족이라 하고 Supervisor LLM이 새 질의로
    재검색을 계속 골라도 세 번째 검색은 없다. 166번: 재검색 뒤(작성 전)에는 LLM을 부르지 않고 정해진 길 (h)로 작성에 간다.
    작성 뒤에 고른 검색도 받지 않는다(search_after_write)."""

    retrieval_queries = []
    asked = []

    def fake_retriever(state):
        retrieval_queries.append(state.get("retrieval_query") or state["question"])
        return [RELATED_CHUNK]

    def keep_searching(state):
        asked.append(state["search_calls"])
        return {"next": "search_agent", "search_query": f"재검색 {state['search_calls']}"}

    result = build_supervisor_graph(
        retriever=fake_retriever, corpus_catalog=RELATED_CATALOG, supervisor_decider=keep_searching,
        search_grader=lambda s, c: {"relevant_chunk_ids": [], "sufficient": False, "missing_items": ["차입금 만기"]},
        draft_writer=lambda s, c: [],  # 빈 초안 → 확인 필요 항목이라 작성 뒤에도 Supervisor LLM이 불린다
        speculative=speculative,
    ).invoke({"question": RELATED_QUESTION})

    # 리터럴: 2회. 첫 검색은 원래 질문으로(106번), 두 번째는 LLM이 쓴 질의로
    assert retrieval_queries == ["오뚜기의 FY2025 사업보고서 차입금 현황을 알려주세요.", "재검색 1"]
    # 결정자는 첫 검색 뒤와 작성 뒤에 불렸고 두 번 다 검색을 골랐다(리터럴 2회. 재검색 뒤·작성 전에는 불리지 않는다)
    assert asked == [1, 2]
    assert result["retrieval_rounds"] == 2 and result["search_calls"] == 2
    assert [d["shortcut"] for d in result["supervisor_decisions"]] == ["(a) 검색 전", None, "(h) 재검색 뒤", None]
    assert [d["guard"] for d in result["supervisor_decisions"]] == [None, None, None, "search_after_write"]
    assert [d["next"] for d in result["supervisor_decisions"]] == ["search_agent", "search_agent", "writer_agent", "approval"]
    assert [d.get("search_query") for d in result["supervisor_decisions"]] == [None, "재검색 1", None, None]
    assert result["supervisor_trace"] == ["search_agent", "search_agent", "writer_agent"]
    assert result["outcome"] == "answered"
    assert result["draft_items"][0]["verdict"] == "needs_check"


# ── ④ 사전 거절은 서브에이전트 0회 ────────────────────────────

def test_pre_refused_questions_never_call_subagents(monkeypatch):
    handoffs = {"search": 0, "writer": 0}
    original_search = search_module.select_search_input
    original_writer = writer_module.select_writer_input

    def counting_search(state):
        handoffs["search"] += 1
        return original_search(state)

    def counting_writer(state):
        handoffs["writer"] += 1
        return original_writer(state)

    monkeypatch.setattr(search_module, "select_search_input", counting_search)
    monkeypatch.setattr(writer_module, "select_writer_input", counting_writer)

    stopped_ids = [
        row_id for row_id, row in V2_FILTERS.items()
        if row["supervisor"] == "supervisor:refuse" or row["target_required"]
    ]
    # 없는 회사 6·다른 기간 6(59번 반기보고서 포함)·target_required 1(31번)
    assert len(stopped_ids) == 13
    rows = [row for row in V2_ROWS if int(row["id"]) in stopped_ids]
    for row in rows:
        retriever = FakeRetriever()
        draft_calls = []
        result = build_supervisor_graph(
            retriever=retriever, corpus_catalog=CATALOG,
            draft_writer=lambda s, c: draft_calls.append(1) or [],
        ).invoke({"question": row["input"], "node_events": []})
        assert retriever.calls == [], row["id"]
        assert draft_calls == [], row["id"]
        assert not any(e.startswith(("retriever", "writer")) for e in result["node_events"]), row["id"]
    assert handoffs == {"search": 0, "writer": 0}

    # 같은 계측으로 검색까지 가는 질문은 각 서브에이전트를 한 번씩 부른다(계측이 살아 있음을 확인)
    build_supervisor_graph(
        retriever=lambda s: [RELATED_CHUNK], corpus_catalog=RELATED_CATALOG, draft_writer=fake_draft_writer,
        speculative=False, dual_draft=False,
    ).invoke({"question": RELATED_QUESTION})
    assert handoffs == {"search": 1, "writer": 1}
    # 기본 경로(110·112번): 미리 실행과 초안 2개 때문에 인계 선별·작성 호출은 늘지만, 검색은 한 번뿐이다
    searches, drafts = [], []
    build_supervisor_graph(
        retriever=lambda s: searches.append(1) or [RELATED_CHUNK], corpus_catalog=RELATED_CATALOG,
        draft_writer=lambda s, c: drafts.append(1) or fake_draft_writer(s, c),
    ).invoke({"question": RELATED_QUESTION})
    # 키가 없어 주제 판정이 가정값(in_domain)과 달라지므로 가정으로 시작한 초안 2개는 버리고 다시 쓴다(2~4회)
    assert searches == [1] and 2 <= len(drafts) <= 4


def test_build_graph_module_still_exposes_single_graph_unchanged():
    """기존 단일 그래프의 노드 이름이 그대로인지(멀티 추가가 단일을 건드리지 않음)."""

    graph = build_graph(corpus_catalog=RELATED_CATALOG, retriever=lambda s: [RELATED_CHUNK])
    assert {"supervisor", "retriever", "scorer", "rewriter", "writer", "verifier", "human_approval", "save_memo"} <= set(
        graph.get_graph().nodes
    )
    assert graph_build.MAX_RETRIEVAL_ROUNDS == 3


# ── verifier (#80, 단일·멀티 공통) ─────────────────────────────

def test_multi_applies_the_same_verifier_as_single():
    chunks = [
        {**RELATED_CHUNK, "chunk_id": "c-borrow", "content": "단기차입금 1,234백만원, 이자율 3.5%"},
        {**RELATED_CHUNK, "chunk_id": "c-other", "content": "소송 관련 공시 내용"},
    ]

    def writer(state, retrieved):
        return [
            {"item": "borrowing_structure", "verdict": "applicable",
             "reason": "단기차입금 1,234백만원", "chunk_ids": ["c-borrow"]},
            {"item": "litigation", "verdict": "applicable",
             "reason": "소송가액 9,999백만원", "chunk_ids": ["c-other"]},
        ]

    results = {}
    for name, builder in (("single", build_graph), ("multi", build_supervisor_graph)):
        results[name] = builder(
            retriever=lambda s: chunks, corpus_catalog=RELATED_CATALOG, draft_writer=writer,
        ).invoke({"question": RELATED_QUESTION, "checklist_items": ["borrowing_structure", "litigation"]})

    multi = results["multi"]
    assert {d["item"]: d["verdict"] for d in multi["draft_items"]} == {
        "borrowing_structure": "applicable", "litigation": "needs_check",
    }
    assert [c["chunk_id"] for c in multi["citations"]] == ["c-borrow"]
    assert [v["passed"] for v in multi["verification"]] == [True, False]
    assert multi["draft_items"] == results["single"]["draft_items"]
    assert multi["verification"] == results["single"]["verification"]
