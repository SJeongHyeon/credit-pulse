"""Langfuse 트레이스 계층 시험(docs/specs/trace-hierarchy.md). 실제 Langfuse로는 보내지 않는다.

- 가짜 클라이언트로 관측의 부모-자식 관계·이름·메타데이터를 본다(정상 / Supervisor 판단 / 다시 쓰기 / 입구 거절 / 미리 실행 후 버림).
- 기록을 켜도 그래프 결과(node_events·outcome 등)가 끈 것과 같은지 본다(그래프 동작 불변).
- 실제 SDK + 메모리 exporter로 스레드를 넘어도 고아 관측이 생기지 않는지 본다(네트워크 없음).
"""
import threading
import time
import types
from contextlib import contextmanager

import pytest
from langgraph.checkpoint.memory import MemorySaver

from backend.graph.approval import resume_approval, thread_id_for
from backend.graph.build import build_supervisor_graph
from backend.graph.refusal import CorpusCatalog, CorpusTarget
from backend.services import llm, tracing

CATALOG = CorpusCatalog(
    corp_names=frozenset({"오뚜기"}),
    fiscal_years=frozenset({2025}),
    report_types=frozenset({"annual"}),
    targets=frozenset({CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2025)}),
)
CHUNK = {"chunk_id": "c1", "corp_code": "00000001", "fiscal_year": 2025, "content": "차입금 공시 본문표식-ZQ7",
         "rcept_no": "r", "chapter": "NOTES", "note_no": "1"}
QUESTION = "오뚜기의 FY2025 사업보고서 차입금 현황을 알려주세요."
INSUFFICIENT = {"relevant_chunk_ids": ["c1"], "sufficient": False, "missing_items": ["차입금 만기"]}


# ── 가짜 Langfuse 클라이언트 ─────────────────────────────────────
class FakeObservation:
    """부모 객체에서 자식을 만드는 langfuse 5.1 관측 흉내. 닫힌 뒤의 update는 실제처럼 반영하지 않고 따로 적는다."""

    def __init__(self, client, parent, name, as_type="span", **kwargs):
        self.client, self.parent, self.name, self.as_type = client, parent, name, as_type
        self.metadata = dict(kwargs.get("metadata") or {})
        self.fields = {key: value for key, value in kwargs.items() if key != "metadata"}
        self.ended, self.end_time = False, None
        self.trace_id, self.id = "trace-1", f"obs-{len(client.observations)}"
        with client.lock:
            client.observations.append(self)

    def start_observation(self, **kwargs):
        return FakeObservation(self.client, self, **kwargs)

    def update(self, **kwargs):
        if self.ended:
            self.client.problems.append(f"닫힌 뒤 update: {self.name}")
            return self
        self.metadata.update(kwargs.pop("metadata", None) or {})
        self.fields.update(kwargs)
        return self

    def end(self, *, end_time=None):
        if self.ended:
            self.client.problems.append(f"두 번 닫음: {self.name}")
        self.ended, self.end_time = True, end_time
        return self

    @property
    def path(self):
        return self.name if self.parent is None else f"{self.parent.path}/{self.name}"


class FakeLangfuse:
    def __init__(self):
        self.lock = threading.Lock()
        self.observations: list[FakeObservation] = []
        self.problems: list[str] = []
        self.scores = []

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        root = FakeObservation(self, None, **kwargs)
        try:
            yield root
        finally:
            root.end()

    def create_score(self, **kwargs):
        self.scores.append(kwargs)

    def flush(self):
        pass

    # ── 시험 도우미 ──
    def settle(self, timeout=5.0):
        """쓰지 않은 초안·이중 요청의 늦은 쪽은 답이 나간 뒤에 끝난다. 모두 닫힐 때까지 기다린다."""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if all(o.ended for o in self.observations):
                return
            time.sleep(0.01)
        raise AssertionError(f"닫히지 않은 관측: {[o.path for o in self.observations if not o.ended]}")

    def paths(self):
        return sorted(o.path for o in self.observations)

    def named(self, name):
        return [o for o in self.observations if o.name == name]


@pytest.fixture
def fake(monkeypatch):
    client = FakeLangfuse()
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setattr(tracing, "_client", client)
    import langfuse

    monkeypatch.setattr(langfuse, "propagate_attributes", lambda **kwargs: contextmanager(lambda: (yield))())
    return client


def _llm(name):
    """가짜 LLM 호출 한 번(프롬프트 없이 관측만). 실제 호출처(llm.py)와 같은 자리에서 불린다."""

    with tracing.external_call("chat", model="fake/model", input={"attempt": 1}, name=name) as call:
        call.done(input_tokens=10, output_tokens=2, cost_usd=0.0001)


def _draft(state, reason="차입금 공시"):
    return [{"item": state["question"], "verdict": "applicable", "reason": reason, "chunk_ids": ["c1"]}]


def _graph(*, decider=None, writer=None, grader=None, classifier=True, retriever=None, **kwargs):
    # 125번: 채점 결과를 작성기에 넘기는 기본값(grade_to_writer)은 초안을 채점 뒤에 시작한다.
    # 이 파일의 미리 실행 시험은 그 옵션이 꺼진 경로를 본다(켠 경로는 tests/test_supervisor_flow.py의 test_grade_to_writer_* 가 본다).
    kwargs.setdefault("grade_to_writer", False)
    def classify(question):
        _llm("classify_domain")
        return classifier

    def decide(state):
        _llm("decide_next")
        return decider(state) if decider else llm.SupervisorDecision(next="writer_agent")

    def grade(state, chunks):
        _llm("grade_search_round")
        return grader(state, chunks) if grader else {"relevant_chunk_ids": ["c1"], "sufficient": True, "missing_items": []}

    def write(state, chunks):
        _llm("write_drafts")
        return writer(state, chunks) if writer else _draft(state)

    return build_supervisor_graph(
        retriever=retriever or (lambda state: [CHUNK]),
        draft_writer=write, corpus_catalog=CATALOG, domain_check_enabled=True, domain_classifier=classify,
        supervisor_decider=decide, search_grader=grade, **kwargs,
    )


def _run(graph, client=None, state=None, config=None):
    with tracing.question("eval", question=QUESTION, metadata={"row_id": 1}) as trace:
        result = graph.invoke(state or {"question": QUESTION}, **({"config": config} if config else {}))
        trace.finish({"outcome": result.get("outcome")})
    if client is not None:
        client.settle()
        assert client.problems == []
    return result


AGENT = "eval/supervisor/search_agent"
WRITER = "eval/supervisor/writer_agent"
SEARCH_STEPS = [f"{AGENT}/retriever", f"{AGENT}/scorer", f"{AGENT}/grader", f"{AGENT}/grader/grade_search_round"]


def _draft_steps(kind):
    return [f"{WRITER}/{kind}", f"{WRITER}/{kind}/writer", f"{WRITER}/{kind}/writer/write_drafts",
            f"{WRITER}/{kind}/verifier", f"{WRITER}/{kind}/report"]


APPROVAL_STEPS = ["eval/supervisor/approval", "eval/supervisor/approval/approval_prepare",
                  "eval/supervisor/approval/human_approval", "eval/supervisor/approval/save_memo"]


# ── 1. 정상 경로 ───────────────────────────────────────────────
def test_normal_path_tree_matches_the_agent_graph(fake):
    result = _run(_graph(), fake)
    assert result["outcome"] == "answered" and result["supervisor_trace"] == ["search_agent", "writer_agent"]
    # 160번: 보통 질문도 검색 뒤 작성 전에 Supervisor 판단 호출(decide_next)이 1회 있다(supervisor 바로 아래)
    assert [o.path for o in fake.named("decide_next")] == ["eval/supervisor/decide_next"]  # 리터럴: 1회
    assert fake.paths() == sorted([
        "eval", "eval/load_rules", "eval/supervisor",
        "eval/supervisor/classify_domain", "eval/supervisor/decide_next",
        AGENT, *SEARCH_STEPS,
        WRITER, *_draft_steps("draft_normal"), *_draft_steps("draft_strict"),
        *APPROVAL_STEPS,
    ])
    # 관측 종류: supervisor·에이전트는 agent, 검색은 retriever, LLM 호출은 generation(호출 종류는 metadata.kind)
    kinds = {o.path: o.as_type for o in fake.observations}
    assert kinds["eval/supervisor"] == "agent" and kinds[AGENT] == "agent" and kinds[WRITER] == "agent"
    assert kinds[f"{AGENT}/retriever"] == "retriever"
    for name in ("classify_domain", "grade_search_round", "decide_next", "write_drafts"):
        assert fake.named(name), name
        assert all(o.as_type == "generation" and o.metadata["kind"] == "chat" for o in fake.named(name)), name
    # 미리 시작한 검색·작성도 시작한 자리(주제 분류·채점) 아래가 아니라 에이전트 아래에 있고, 표시가 남는다
    search, writer = fake.named("search_agent")[0], fake.named("writer_agent")[0]
    assert len(fake.named("search_agent")) == 1 and len(fake.named("retriever")) == 1  # 리터럴: 검색 관측은 하나(159번)
    assert search.parent.name == "supervisor" and writer.parent.name == "supervisor"
    assert search.metadata == {"speculative": True, "discarded": False}  # 159번: 검색 회차(round) 칸은 없다
    assert writer.metadata == {"attempt": 1, "redraft": False, "speculative": True, "discarded": False}
    assert search.fields["output"] == {"sufficient": True, "n_chunks": 1, "top1_score": None, "graded": True}
    # 쓴 초안과 쓰지 않은 초안
    assert fake.named("draft_normal")[0].metadata == {"discarded": False}
    assert fake.named("draft_strict")[0].metadata == {"discarded": True}
    assert writer.fields["output"]["selected"] == "normal"


def test_trace_keeps_no_prompt_or_chunk_text(fake):
    """프롬프트·청크 본문은 남기지 않는다(P19 R7). 질문은 루트 input에만 있다."""

    _run(_graph(), fake)
    for observation in fake.observations:
        recorded = str({"metadata": observation.metadata, **observation.fields})
        assert "본문표식-ZQ7" not in recorded, observation.path
        if observation.parent is not None:
            assert QUESTION not in recorded, observation.path


# ── 2. Supervisor 판단 호출: 두 갈림길(검색 뒤, 확인 필요 초안 뒤)에서 supervisor 바로 아래에 남는다(160번) ─────────
def test_decide_next_spans_appear_at_the_search_fork_and_the_flagged_draft_fork(fake):
    """159번부터 재검색이 없어 search_agent 관측은 늘 하나다. LLM 판단(decide_next)은 검색 뒤 1회(160번) + 확인 필요 초안 뒤 1회 남는다."""

    flagged = lambda s, c: [{"item": s["question"], "verdict": "needs_check", "reason": "차입금 공시", "chunk_ids": ["c1"]}]
    asked = []

    def decider(state):
        asked.append((state["search_calls"], state.get("writer_calls", 0)))
        return {"next": "search_agent"}  # 형식 밖 출력: 검색을 골라도 guard가 작성(검색 뒤)·승인(작성 뒤)으로 고친다

    result = _run(_graph(decider=decider, writer=flagged, grader=lambda s, c: INSUFFICIENT), fake)
    assert result["supervisor_trace"] == ["search_agent", "writer_agent"]
    assert asked == [(1, 0), (1, 1)]  # 리터럴: (검색 횟수, 작성 횟수). 검색 뒤 작성 전, 그리고 작성 뒤
    decides = fake.named("decide_next")
    assert [o.path for o in decides] == ["eval/supervisor/decide_next"] * 2  # 리터럴: 2회, 둘 다 supervisor 바로 아래
    assert all(o.as_type == "generation" and o.metadata["kind"] == "chat" for o in decides)
    searches = fake.named("search_agent")
    assert [(o.metadata["speculative"], o.metadata["discarded"]) for o in searches] == [(True, False)]
    assert all(o.parent.name == "supervisor" for o in searches)
    assert len(fake.named("retriever")) == 1  # 리터럴: 검색 1회
    assert [o.metadata["discarded"] for o in fake.named("writer_agent")] == [False]


# ── 3. 미리 실행 후 버림: 가정한 입력이 실제와 다르면 미리 쓴 초안은 discarded로 남는다 ──
def test_discarded_speculative_drafts_stay_in_the_tree(fake):
    """미리 쓴 초안은 주제 판정을 'in_domain'으로 가정하고 시작한다(113번). 실제 판정이 다르면 그 초안을 버리고 다시 쓴다.

    159번 전에는 재검색으로 청크가 바뀌는 경우로 봤다. 재검색이 없어져 가정이 틀리는 경우로 같은 불변식을 본다:
    버린 초안도 writer_agent 자리에 discarded로 남고 호출 기록은 지우지 않는다.
    분류가 초안 2개(보통·엄격)가 시작될 때까지 기다리게 해 순서를 고정한다(타이밍에 기대지 않는다).
    """

    drafts_started = threading.Semaphore(0)
    seen_domain_checks = []

    def slow_classifier(question):
        _llm("classify_domain")
        for _ in range(2):  # 가정값으로 미리 시작한 보통·엄격 초안
            assert drafts_started.acquire(timeout=10)
        return "not_disclosed_item"  # 거절은 아니지만 작성기 입력(domain_check)이 가정과 달라진다

    def writer(state, chunks):
        seen_domain_checks.append(state.get("domain_check"))
        drafts_started.release()
        _llm("write_drafts")
        return _draft(state)

    graph = build_supervisor_graph(
        retriever=lambda state: [CHUNK], corpus_catalog=CATALOG, domain_check_enabled=True,
        domain_classifier=slow_classifier, draft_writer=writer, grade_to_writer=False,
        search_grader=lambda s, c: _llm("grade_search_round") or INSUFFICIENT,
    )
    result = _run(graph, fake)
    assert result["outcome"] == "answered" and result["domain_check"] == "not_disclosed_item"
    assert sorted(seen_domain_checks) == ["in_domain"] * 2 + ["not_disclosed_item"] * 2
    writers = fake.named("writer_agent")
    assert [(o.metadata["speculative"], o.metadata["discarded"]) for o in writers] == [(True, True), (True, False)]
    assert all(o.parent.name == "supervisor" for o in writers)  # 버린 것도 writer_agent 자리에 남는다
    discarded = [o for o in fake.observations if o.parent is writers[0]]
    assert sorted(o.name for o in discarded) == ["draft_normal", "draft_strict"]
    assert all(o.metadata == {"discarded": True} for o in discarded)
    assert len(fake.named("write_drafts")) == 4  # 리터럴: 버린 초안 2 + 쓴 초안 2, 호출은 지우지 않는다
    assert len(fake.named("search_agent")) == 1  # 검색은 다시 하지 않는다


# ── 4. 다시 쓰기 ───────────────────────────────────────────────
def test_redraft_adds_a_second_writer_agent_span(fake):
    graph = _graph(writer=lambda s, c: _draft(s, "999,999,999원"), dual_draft=False, speculative=False)
    result = _run(graph, fake)
    assert result["supervisor_trace"] == ["search_agent", "writer_agent", "writer_agent:redraft"]
    writers = fake.named("writer_agent")
    assert [o.metadata for o in writers] == [
        {"attempt": 1, "redraft": False, "speculative": False, "discarded": False},
        {"attempt": 2, "redraft": True, "speculative": False, "discarded": False}]
    assert [o.name for o in fake.observations if o.parent in writers] == ["draft_normal", "draft_redraft"]
    assert writers[1].fields["output"]["verification_passed"] is False
    assert fake.named("search_agent")[0].metadata == {"speculative": False, "discarded": False}


# ── 5. 입구 거절: 미리 시작한 검색은 버린 것으로 남고, 작성·승인은 없다 ───────────
def test_intake_refusal_keeps_the_cancelled_search_and_drafts_as_discarded(fake):
    """주제 분류가 '범위 밖'으로 끝나기 전에 미리 시작한 검색·초안(113번)은 지우지 않고 discarded로 남긴다.

    초안은 채점이 끝난 뒤에 시작된다(125번: 채점 결과를 작성기에 넘긴다). 그래서 분류가 초안 2개(보통·엄격)가 실제로
    시작될 때까지 기다리게 해 순서를 고정한다(타이밍에 기대지 않는다). 예전에는 채점 시작만 기다려서, 초안이 시작되기 전에
    거절이 끝나면 초안 관측이 없어 가끔 실패했다.
    """

    drafts_started = threading.Semaphore(0)

    def slow_refusal(question):
        _llm("classify_domain")
        for _ in range(2):  # 보통 초안과 엄격 초안이 모두 시작될 때까지
            assert drafts_started.acquire(timeout=10)
        return False

    def writer(state, chunks):
        drafts_started.release()
        _llm("write_drafts")
        return _draft(state)

    graph = build_supervisor_graph(
        retriever=lambda state: [CHUNK], corpus_catalog=CATALOG, domain_check_enabled=True,
        domain_classifier=slow_refusal,
        draft_writer=writer,
        search_grader=lambda s, c: _llm("grade_search_round") or INSUFFICIENT,
    )
    result = _run(graph, fake)
    assert result["outcome"] == "refused" and result["refusal_reason"] == "OUT_OF_DOMAIN"
    assert fake.paths() == sorted([
        "eval", "eval/load_rules", "eval/supervisor", "eval/supervisor/classify_domain",
        AGENT, *SEARCH_STEPS, WRITER, *_draft_steps("draft_normal"), *_draft_steps("draft_strict"),
    ])  # 승인(approval)은 없다
    assert fake.named("search_agent")[0].metadata == {"speculative": True, "discarded": True}
    assert fake.named("writer_agent")[0].metadata == {
        "attempt": 1, "redraft": False, "speculative": True, "discarded": True}
    assert [o.metadata for o in fake.named("draft_normal") + fake.named("draft_strict")] == [{"discarded": True}] * 2


def test_corpus_out_refusal_has_only_supervisor(fake):
    result = _run(_graph(), fake, state={"question": "삼성전자의 FY2025 사업보고서 차입금 현황을 알려주세요."})
    assert result["refusal_reason"] == "CORP_NOT_IN_CORPUS"
    assert fake.paths() == ["eval", "eval/load_rules", "eval/supervisor"]


def test_unclaimed_speculation_is_closed_when_the_question_ends(fake):
    """아무도 가져가지 않은 미리 실행(다음 질문의 reset도 없는 마지막 질문)은 질문이 끝날 때 버린 것으로 닫힌다."""

    with tracing.question("eval", question="q"):
        agent = tracing.open_agent("writer_agent", runs=False, metadata={"speculative": True})
        draft = agent.child("draft_normal")
        draft.run(lambda payload: payload, {})
    fake.settle()
    assert fake.problems == []
    assert fake.named("writer_agent")[0].metadata == {"speculative": True, "discarded": True}
    assert fake.named("draft_normal")[0].metadata == {"discarded": True}


# ── 6. 승인: interrupt는 오류가 아니고, 재개 트레이스에 저장이 남는다 ────────────
def test_approval_interrupt_and_resume_spans(fake):
    saved = []
    graph = _graph(dual_draft=False, speculative=False, checkpointer=MemorySaver(), approval_enabled=True,
                   memo_saver=lambda record, key: saved.append(key) or "memo-1")
    config = {"configurable": {"thread_id": thread_id_for("user-1", "review-1")}}
    result = _run(graph, fake, state={"question": QUESTION, "user_id": "user-1", "review_id": "review-1"}, config=config)
    payload = result["__interrupt__"][0].value
    waiting = fake.named("human_approval")[0]
    assert waiting.path == "eval/supervisor/approval/human_approval"
    assert waiting.metadata == {"interrupted": True} and "level" not in waiting.fields
    assert fake.named("save_memo") == []

    resume = {"approval_seq": payload["approval_seq"], "action": "approve", "decisions": [
        {"item": d["item"], "verdict": d["verdict"], "reason": d["reason"]} for d in payload["draft_items"]]}
    with tracing.question("review-approval", question="approve #1"):
        resume_approval(graph, config, resume)
    fake.settle()
    assert len(saved) == 1 and fake.problems == []
    assert [o.path for o in fake.observations if o.path.startswith("review-approval")] == [
        "review-approval", "review-approval/supervisor", "review-approval/supervisor/approval",
        "review-approval/supervisor/approval/human_approval", "review-approval/supervisor/approval/save_memo"]


# ── 7. 그래프 동작 불변: 기록을 켠 결과 = 끈 결과 ───────────────────────────
SCENARIOS = {
    "normal": lambda: _graph(),
    # Supervisor LLM이 검색 뒤에는 작성을(첫 작성에는 지시가 실리지 않는다), 확인 필요 초안 뒤에는 다시 쓰기를 고르는 경우
    "llm_fork": lambda: _graph(
        decider=lambda s: (llm.SupervisorDecision(next="writer_agent", writer_feedback="다시")
                           if not s["redraft_calls"] else llm.SupervisorDecision(next="approval")),
        writer=lambda s, c: [{"item": s["question"], "verdict": "needs_check", "reason": "차입금 공시", "chunk_ids": ["c1"]}],
        grader=lambda s, c: INSUFFICIENT),
    # 결정자가 검색을 골라도(형식 밖 출력) 결과가 같다
    "search_choice": lambda: _graph(
        decider=lambda s: {"next": "search_agent"},
        writer=lambda s, c: [{"item": s["question"], "verdict": "needs_check", "reason": "차입금 공시", "chunk_ids": ["c1"]}]),
    "redraft": lambda: _graph(writer=lambda s, c: _draft(s, "999,999,999원"), dual_draft=False, speculative=False),
    "dual_strict": lambda: _graph(writer=lambda s, c: _draft(s, "차입금 공시" if s.get("writer_feedback") else "999,999,999원")),
    "refused": lambda: _graph(classifier=False),
    "no_speculation": lambda: _graph(speculative=False),
}


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_tracing_does_not_change_graph_result(scenario, monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert not tracing.enabled()
    off = _run(SCENARIOS[scenario]())

    client = FakeLangfuse()
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setattr(tracing, "_client", client)
    import langfuse

    monkeypatch.setattr(langfuse, "propagate_attributes", lambda **kwargs: contextmanager(lambda: (yield))())
    on = _run(SCENARIOS[scenario](), client)
    assert len(client.observations) > 3  # 기록이 실제로 켜져 있었다
    for key in ("node_events", "outcome", "refusal_reason", "supervisor_trace", "supervisor_decisions",
                "draft_items", "citations", "search_calls", "writer_calls", "redraft_calls"):
        assert on.get(key) == off.get(key), key
    assert on == off


# ── 8. 꺼져 있으면 아무 일도 하지 않는다 ─────────────────────────────────
def test_disabled_helpers_are_passthrough():
    assert not tracing.enabled()
    with tracing.span("retriever") as handle:
        handle.update(output={"n": 1})
    with tracing.supervisor():
        pass
    agent = tracing.open_agent("search_agent", metadata={"speculative": False})
    assert agent.run(lambda payload: payload + 1, 1) == 2
    agent.child("draft_normal").resolve(discarded=True)
    agent.resolve(discarded=False)
    wrapped = tracing.node("scorer", lambda state: {"ok": state})
    assert wrapped(3) == {"ok": 3}
    context, fate = tracing.hedged_context()
    assert fate is None and context.run(lambda: 5) == 5
    assert tracing._client is None  # langfuse 클라이언트를 만들지도 않았다


def test_enabled_helpers_outside_a_question_do_nothing(fake):
    """질문 트레이스 밖(스크립트 단독 호출)에서는 노드·에이전트 관측을 만들지 않는다."""

    wrapped = tracing.node("scorer", lambda state: state)
    assert wrapped(1) == 1 and tracing.open_agent("search_agent").run(lambda payload: payload, 2) == 2
    assert fake.observations == []


def test_tracing_failure_never_breaks_the_node(fake, monkeypatch):
    """Langfuse 쪽 오류는 로그로만 남고 노드 결과·예외는 그대로다."""

    def broken(self, **kwargs):
        raise RuntimeError("langfuse down")

    monkeypatch.setattr(FakeObservation, "start_observation", broken)
    result = _run(_graph())
    assert result["outcome"] == "answered" and result["supervisor_trace"] == ["search_agent", "writer_agent"]


# ── 9. LLM 호출 이름과 이중 요청 ──────────────────────────────────────
def _fake_openai(monkeypatch, content, delays=()):
    import sys

    waits = iter(delays)

    class FakeCompletions:
        def create(self, **kwargs):
            time.sleep(next(waits, 0))
            usage = types.SimpleNamespace(prompt_tokens=120, completion_tokens=30, cost=0.0004)
            choice = types.SimpleNamespace(message=types.SimpleNamespace(content=content), finish_reason="stop")
            return types.SimpleNamespace(choices=[choice], usage=usage)

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=FakeCompletions())

        def with_options(self, **kwargs):
            return self

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI, APIStatusError=RuntimeError))
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_llm_calls_are_named_by_role_and_keep_kind(fake, monkeypatch):
    _fake_openai(monkeypatch, '{"in_domain": true, "reason": "r"}')
    monkeypatch.setattr(llm, "SMALL_CALL_HEDGE_SECONDS", None)
    with tracing.question("eval", question="q"):
        with tracing.supervisor():
            assert llm.classify_domain(question="오뚜기 차입금") == "in_domain"
    fake.settle()
    call = fake.named("classify_domain")[0]
    assert call.path == "eval/supervisor/classify_domain" and call.as_type == "generation"
    assert call.metadata["kind"] == "chat" and "hedged" not in call.metadata
    assert call.fields["usage_details"] == {"input": 120, "output": 30} and call.fields["cost_details"] == {"total": 0.0004}
    assert fake.named("chat") == []


def test_hedged_call_keeps_both_and_marks_the_late_one_discarded(fake, monkeypatch):
    """이중 요청: 두 호출 모두 남기고, 답을 쓰지 않은 늦은 쪽에 discarded=true를 적는다."""

    _fake_openai(monkeypatch, '{"in_domain": true, "reason": "r"}', delays=(0.4, 0.0))
    monkeypatch.setattr(llm, "SMALL_CALL_HEDGE_SECONDS", 0.05)
    with tracing.question("eval", question="q"):
        with tracing.supervisor():
            llm.classify_domain(question="오뚜기 차입금")
    fake.settle()
    calls = fake.named("classify_domain")
    assert len(calls) == 2 and all(o.parent.name == "supervisor" for o in calls)  # 리터럴
    assert sorted((o.metadata["hedged"], o.metadata["discarded"]) for o in calls) == [(True, False), (True, True)]
    assert fake.problems == []


# ── 10. 실제 SDK: 스레드를 넘어도 고아 관측이 없다(네트워크 없음) ─────────────────
def test_real_sdk_tree_has_no_orphans(monkeypatch):
    pytest.importorskip("langfuse")
    from langfuse import Langfuse
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    client = Langfuse(public_key="pk-lf-test", secret_key="sk-lf-test", base_url="http://127.0.0.1:9",
                      tracer_provider=TracerProvider(), span_exporter=exporter)
    monkeypatch.setattr(tracing, "_client", client)
    try:
        with tracing.question("eval", question=QUESTION, session_id="run-1", tags=["eval"]) as trace:
            result = _graph().invoke({"question": QUESTION})
        assert result["outcome"] == "answered" and trace.trace_id
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:  # 쓰지 않은 엄격 초안이 끝날 때까지
            client.flush()
            if len([s for s in exporter.get_finished_spans() if s.name == "report"]) == 2:
                break
            time.sleep(0.05)
        spans = exporter.get_finished_spans()
    finally:
        client.shutdown()
    by_id = {s.context.span_id: s for s in spans}

    def path(s):
        return s.name if s.parent is None else f"{path(by_id[s.parent.span_id])}/{s.name}"  # 부모가 없으면 KeyError

    assert len({s.context.trace_id for s in spans}) == 1
    assert [s.name for s in spans if s.parent is None] == ["eval"]  # 루트는 하나, 고아 없음
    paths = sorted(path(s) for s in spans)
    assert paths == sorted([
        "eval", "eval/load_rules", "eval/supervisor", "eval/supervisor/classify_domain",
        "eval/supervisor/decide_next",  # 160번: 검색 뒤 작성 전 판단 호출 1회
        AGENT, *SEARCH_STEPS, WRITER, *_draft_steps("draft_normal"), *_draft_steps("draft_strict"), *APPROVAL_STEPS,
    ])
    meta ={path(s): {k.rsplit(".", 1)[-1]: v for k, v in s.attributes.items() if ".metadata." in k} for s in spans}
    assert meta[AGENT]["speculative"] == "true" and meta[AGENT]["discarded"] == "false"
    assert meta[f"{WRITER}/draft_strict"]["discarded"] == "true"
    # 미리 실행한 관측은 실제 끝난 시각으로 닫힌다(운명이 늦게 정해져도 길이가 늘어나지 않는다)
    agent = next(s for s in spans if path(s) == AGENT)
    grader = next(s for s in spans if path(s) == f"{AGENT}/grader")
    assert agent.end_time >= grader.end_time and (agent.end_time - grader.end_time) < 50_000_000  # 50ms 안


# ── 11. 바깥 OTel 문맥(샘플링 안 함)이 있어도 질문 트레이스는 새 루트로 남는다 ─────
def test_question_ignores_an_unsampled_ambient_context(monkeypatch):
    """11/11 배포 서비스에서 질의 34건 중 1건만 남은 원인의 재현.

    FastAPI 내장 telemetry가 요청마다 서버 span을 만들고 Cloud Run의 traceparent('샘플링 안 함')를 물려준다.
    그 문맥 아래에서 만든 관측은 기록되지 않는다. question()은 바깥 문맥과 무관하게 새 루트를 연다.
    """

    pytest.importorskip("langfuse")
    from langfuse import Langfuse
    from opentelemetry import context as otel_context
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    # 공개 키가 같으면 SDK가 앞 시험의(이미 닫힌) 클라이언트를 돌려주므로 다른 키를 쓴다
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test-ambient")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    client = Langfuse(public_key="pk-lf-test-ambient", secret_key="sk-lf-test", base_url="http://127.0.0.1:9",
                      tracer_provider=TracerProvider(), span_exporter=exporter)
    monkeypatch.setattr(tracing, "_client", client)
    remote = otel_trace.SpanContext(trace_id=0x1E5BF89967C40778C533ACF0A65BFCD2, span_id=0xFB699786CB9B44C9, is_remote=True,
                                    trace_flags=otel_trace.TraceFlags(0))  # 샘플링 안 함
    ambient = otel_trace.set_span_in_context(otel_trace.NonRecordingSpan(remote))
    token = otel_context.attach(ambient)
    try:
        with tracing.question("review-query", question=QUESTION) as trace:
            with tracing.supervisor():
                with tracing.span("retriever"):
                    pass
            trace.finish({"outcome": "answered"})
        assert otel_trace.get_current_span().get_span_context().span_id == remote.span_id  # 바깥 문맥은 그대로 되돌아온다
        client.flush()
        spans = exporter.get_finished_spans()
    finally:
        otel_context.detach(token)
        client.shutdown()

    assert sorted(s.name for s in spans) == ["retriever", "review-query", "supervisor"]  # 하나도 버려지지 않는다
    root = next(s for s in spans if s.name == "review-query")
    assert root.parent is None and root.context.trace_id != remote.trace_id  # 바깥 요청의 자식이 아니라 새 트레이스
