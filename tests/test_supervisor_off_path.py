"""104번: LLM이 없을 때(키 없음·데모 모드)의 코드 규칙 경로가 78번 기본 경로와 같은 응답을 내는지 확인한다.

기대값 `tests/fixtures/supervisor_off_path_78.json`은 78번 코드(커밋 847b1d7)에서 저장했다(80번 시험에서 만듦).
104번은 Supervisor 판단·검색 채점·다시 쓰기 경로를 바꿨으므로 State 키 전체가 아니라 응답에 드러나는 칸과
검색 질의·횟수만 비교한다. 78번의 '검색 에이전트 안쪽 재작성 루프'(inner_rewrite_loop)는 104번에서 없앴으므로 뺀다.
78번 redraft는 104번에서 writer_feedback을 실은 writer_agent 호출로 바뀌었다(작성 결과는 같아야 한다).
"""
import json
from pathlib import Path

from backend.graph.build import build_supervisor_graph
from tests.test_supervisor_flow import CATALOG, CHUNK, QUESTION

FIXTURE = Path(__file__).parent / "fixtures" / "supervisor_off_path_78.json"
REMOVED_SCENARIOS_104 = {"inner_rewrite_loop"}
COMPARED_KEYS = ("outcome", "refusal_reason", "refusal_message", "draft_items", "citations", "verification",
                 "target_required", "domain_check", "supervisor_turns", "search_calls", "redraft_calls")


def _writer(verdict="applicable", reasons=None):
    replies = iter(reasons or [])

    def write(state, chunks):
        reason = next(replies, "차입금 공시")
        return [{"item": state["question"], "verdict": verdict, "reason": reason, "chunk_ids": ["c1"]}]

    return write


def _run(name, question=QUESTION, *, writer=None, classifier=lambda q: True):
    calls = []
    graph = build_supervisor_graph(
        retriever=lambda s: calls.append(s.get("retrieval_query") or s["question"]) or [CHUNK],
        draft_writer=writer or _writer(),
        corpus_catalog=CATALOG,
        domain_check_enabled=True,
        domain_classifier=classifier,
        # 78번 고정 결과는 순차 경로(초안 1개, 미리 실행 없음)의 것이다. 기본 경로(초안 2개·미리 실행)가 순차 경로와
        # 같은 응답인지는 tests/test_supervisor_flow.py의 동등성 시험이 본다.
        dual_draft=False, speculative=False,
    )  # Supervisor·채점은 기본값(llm) → 시험에는 키가 없어 코드 규칙으로 돈다
    final = graph.invoke({"question": question})
    return name, {"calls": calls, "final": json.loads(json.dumps(final, ensure_ascii=False, default=str))}


def run_scenarios() -> dict:
    return dict([
        _run("answered"),
        _run("writer_not_disclosed", writer=_writer("not_disclosed"), classifier=lambda q: "not_disclosed_item"),
        _run("intake_out_of_domain", "오뚜기의 FY2025 사업보고서로 날씨를 알려주세요.", classifier=lambda q: False),
        _run("ask_target", "차입금 현황을 알려주세요."),
        _run("redraft_recovers", writer=_writer(reasons=["999,999,999원", "차입금 공시"])),
        _run("redraft_still_mismatch", writer=_writer(reasons=["999,999,999원", "888,888원"])),
    ])


def test_code_rule_path_matches_stage_78_responses():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = run_scenarios()
    assert set(actual) == set(expected) - REMOVED_SCENARIOS_104
    for name, got in actual.items():
        want = expected[name]
        assert got["calls"] == want["calls"], name
        for key in COMPARED_KEYS:
            assert got["final"].get(key) == want["final"].get(key), (name, key)
        # 사용자에게 보이는 이벤트 순서도 같다(104번에서 생긴 채점 이벤트만 뺀다)
        events = [e for e in got["final"]["node_events"] if not e.startswith("grader:")]
        assert events == want["final"]["node_events"], name
