"""노드별 소요 시간 기록(scripts/node_timing.py). 키 없이 가짜 함수만 쓴다."""
from backend.graph.build import build_supervisor_graph
from scripts.node_timing import NodeTimer
from tests.test_supervisor_flow import CATALOG, CHUNK, QUESTION


def _graph(**kwargs):
    return build_supervisor_graph(
        **kwargs,
        retriever=lambda s: [CHUNK],
        draft_writer=lambda s, c: [{"item": s["question"], "verdict": "applicable", "reason": "차입금 공시", "chunk_ids": ["c1"]}],
        corpus_catalog=CATALOG,
        domain_check_enabled=True,
        domain_classifier=lambda q: True,
    )


def test_timer_does_not_change_final_state():
    plain = _graph().invoke({"question": QUESTION})
    timer = NodeTimer()
    timed = _graph().invoke({"question": QUESTION}, config={"callbacks": [timer]})
    assert timed == plain


def test_timer_counts_each_node_once_per_visit_with_subgraph_paths():
    timer = NodeTimer()
    _graph(speculative=False, dual_draft=False).invoke({"question": QUESTION}, config={"callbacks": [timer]})
    calls = {path: entry["calls"] for path, entry in timer.totals.items()}
    assert calls["supervisor"] == 3  # 입구, 검색 뒤, 작성 뒤(라우팅 실행은 따로 세지 않음)
    assert calls["search_agent"] == 1 and calls["search_agent/retriever"] == 1 and calls["search_agent/grader"] == 1
    assert calls["writer_agent"] == 1 and calls["writer_agent/writer"] == 1 and calls["writer_agent/verifier"] == 1
    assert calls["writer_agent/report"] == 1  # 104번: Supervisor가 읽는 작성 보고
    assert all(entry["ms"] >= 0 for entry in timer.totals.values())


def test_timer_with_speculation_still_counts_each_subagent_node_once():
    """110번: 미리 실행하면 서브에이전트 안쪽 노드는 시작한 곳(supervisor) 아래 경로로 잡힌다. 횟수는 그대로 1회씩이다."""

    timer = NodeTimer()
    result = _graph().invoke({"question": QUESTION}, config={"callbacks": [timer]})
    assert result == _graph(speculative=False).invoke({"question": QUESTION})  # 최종 State는 같다
    calls = {path: entry["calls"] for path, entry in timer.totals.items()}
    # 112번: 초안 2개. 미리 시작한 작성 에이전트는 시작한 노드(grader) 아래 경로로 잡혀 'grader' 경로 횟수에는
    # 그 서브그래프 실행이 섞인다. 그래서 grader는 세지 않고, 실제 일(검색 1회, 작성·검증 2회)만 센다.
    for leaf, expected in (("retriever", 1), ("writer", 2), ("verifier", 2)):
        assert sum(n for path, n in calls.items() if path.split("/")[-1] == leaf) == expected, leaf
    assert calls["search_agent"] == 1 and calls["writer_agent"] == 1 and calls["supervisor"] == 3
