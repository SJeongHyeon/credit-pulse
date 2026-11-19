"""처리 상한(SPEC §4, 2026-11-11 강화) 시험. 키·DB 없이 가짜만 쓴다.

- 한도는 상수끼리 비교하지 않고 리터럴(5, 50, 20, 16KB, $2, $0.5, 60초)과 비교한다(docs/GATES.md 변이 방식).
- 한도마다 '직전까지 통과 / 넘으면 그 상태 코드·에러 코드'를 본다.
- 하루 한도는 날짜를 고정해 넣고, 날짜가 바뀌면 0부터 다시 세는지 본다.
- 처리 시간 상한은 마감이 지나면 새 외부 호출을 하지 않는지, 끊긴 뒤 상태·비용이 어긋나지 않는지 본다.
"""
import contextvars
import threading
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from backend import limit_values
from backend.api import limits
from backend.api.limits import InMemoryDailyUsage, PerUserRateLimiter, QueryGuard
from backend.api.reviews import get_query_guard, get_review_graph_runner, get_review_repository
from backend.auth import current_user_id
from backend.graph.approval import pending_approval
from backend.graph.build import build_supervisor_graph
from backend.graph.refusal import CorpusCatalog, CorpusTarget
from backend.main import app
from backend.services import llm
from tests.test_reviews_api import REVIEW_A, USER_A, FakeReviewGraphRunner, FakeReviewRepository, _review

DAY, NEXT_DAY = date(2026, 11, 11), date(2026, 11, 12)
REVIEW_2 = "40000000-0000-0000-0000-0000000000d4"


def _guard(day=None, usage=None, **kwargs):
    """분당 한도는 사실상 꺼 두고(하루 한도만 보려고) 날짜를 고정한 가드. day는 [날짜] 목록이라 시험이 바꿀 수 있다."""

    day = day if day is not None else [DAY]
    usage = usage if usage is not None else InMemoryDailyUsage()
    guard = QueryGuard(rate_limiter=PerUserRateLimiter(limit=10**6), usage=usage, today=lambda: day[0], **kwargs)
    return guard, usage, day


def _error(call) -> limits.ApiError:
    with pytest.raises(limits.ApiError) as raised:
        call()
    return raised.value


def test_limit_values_are_the_spec_literals():
    assert limit_values.USER_QUERIES_PER_MINUTE == 5
    assert limit_values.USER_QUERIES_PER_DAY == 50
    assert limit_values.REVIEW_QUERIES_PER_DAY == 20
    assert limit_values.MAX_REQUEST_BODY_BYTES == 16384
    assert limit_values.MAX_QUESTION_CHARS == 1000
    assert limit_values.SERVICE_DAILY_BUDGET_USD == 2.0
    assert limit_values.USER_DAILY_BUDGET_USD == 0.5
    assert limit_values.QUERY_TIMEOUT_SECONDS == 60.0


def test_day_key_is_the_korean_date(monkeypatch):
    """하루는 한국 시간 자정에 바뀐다: UTC 14:59는 한국 23:59(같은 날), UTC 15:00은 한국 다음 날 0시."""

    class Clock(datetime):
        current = datetime(2026, 11, 11, 14, 59, 59, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

    monkeypatch.setattr(limit_values, "datetime", Clock)
    assert limit_values.kst_today() == date(2026, 11, 11)
    Clock.current = datetime(2026, 11, 11, 15, 0, 0, tzinfo=timezone.utc)
    assert limit_values.kst_today() == date(2026, 11, 12)


# ── 사용자 하루 50회 ─────────────────────────────────────


def test_user_gets_50_queries_a_day_and_the_51st_is_429():
    guard, usage, day = _guard(review_queries_per_day=10**6)
    for _ in range(50):
        guard.before_query("a", "r1")

    error = _error(lambda: guard.before_query("a", "r1"))

    assert (error.status_code, error.code) == (429, "RATE_LIMITED")
    assert usage.queries("user", "a", DAY) == 50  # 막힌 질의는 세지 않는다
    guard.before_query("b", "r2")  # 다른 사용자는 따로 센다
    day[0] = NEXT_DAY
    guard.before_query("a", "r1")  # 날짜가 바뀌면 다시 된다
    assert usage.queries("user", "a", NEXT_DAY) == 1


def test_minute_and_day_limits_are_both_checked():
    """분당 5회 안이어도 하루 50회가 차 있으면 막히고, 하루가 남아도 분당 5회를 넘으면 막힌다."""

    now = [0.0]
    usage = InMemoryDailyUsage()
    guard = QueryGuard(rate_limiter=PerUserRateLimiter(clock=lambda: now[0]), usage=usage, today=lambda: DAY,
                       review_queries_per_day=10**6)
    for _ in range(5):
        guard.before_query("a", "r1")
    assert _error(lambda: guard.before_query("a", "r1")).code == "RATE_LIMITED"  # 분당
    assert usage.queries("user", "a", DAY) == 5
    for minute in range(1, 10):  # 9분 더, 분마다 5회: 합 50회
        now[0] = minute * 60.0
        for _ in range(5):
            guard.before_query("a", "r1")
    now[0] = 600.0  # 분당 창은 비었지만 하루 50회가 찼다
    error = _error(lambda: guard.before_query("a", "r1"))
    assert (error.status_code, error.code) == (429, "RATE_LIMITED") and "오늘" in error.message


# ── 심사 건 하루 20회 ────────────────────────────────────


def test_review_gets_20_queries_a_day_and_the_21st_is_409_without_spending_the_user_count():
    guard, usage, day = _guard()
    for _ in range(20):
        guard.before_query("a", "r1")

    error = _error(lambda: guard.before_query("a", "r1"))

    assert (error.status_code, error.code) == (409, "CONFLICT_REVIEW_QUERY_LIMIT")
    assert usage.queries("review", "r1", DAY) == 20
    assert usage.queries("user", "a", DAY) == 20  # 21번째는 돌지 않았으니 사용자 횟수도 그대로다
    guard.before_query("a", "r2")  # 같은 사용자의 다른 심사 건은 된다
    day[0] = NEXT_DAY
    guard.before_query("a", "r1")  # 날짜가 바뀌면 같은 심사 건도 다시 된다


def test_cancel_query_gives_back_both_counts():
    guard, usage, _ = _guard()
    guard.before_query("a", "r1")
    guard.cancel_query("a", "r1")
    assert (usage.queries("user", "a", DAY), usage.queries("review", "r1", DAY)) == (0, 0)


# ── 하루 LLM 비용: 서비스 $2, 사용자 $0.5 ─────────────────


def test_user_daily_budget_blocks_at_half_a_dollar():
    guard, usage, day = _guard()
    guard.record("a", [{"cost_usd": 0.49}, {"cost_usd": None}])
    guard.before_query("a", "r1")  # $0.49: 아직 된다
    guard.record("a", [{"cost_usd": 0.01}])

    error = _error(lambda: guard.before_query("a", "r1"))

    assert (error.status_code, error.code) == (503, "SERVICE_BUDGET_EXHAUSTED")
    assert usage.queries("user", "a", DAY) == 1  # 비용에 막힌 질의는 세지 않는다
    guard.before_query("b", "r2")  # 다른 사용자는 자기 비용만 본다
    day[0] = NEXT_DAY
    guard.before_query("a", "r1")  # 날짜가 바뀌면 다시 된다
    assert usage.cost("user", "a", NEXT_DAY) == 0.0


def test_service_daily_budget_blocks_everyone_at_two_dollars():
    guard, usage, day = _guard()
    for user in ("a", "b", "c", "d"):  # 사용자마다 $0.4975씩(각자 한도 $0.5 아래): 합 $1.99
        guard.record(user, [{"cost_usd": 0.4975}])
    guard.before_query("e", "r5")  # 서비스 $1.99: 아직 된다
    guard.record("e", [{"cost_usd": 0.01}])
    assert usage.cost("service", limits.SERVICE_SCOPE_ID, DAY) == pytest.approx(2.0)

    error = _error(lambda: guard.before_query("f", "r6"))  # 한 푼도 안 쓴 사용자도 막힌다

    assert (error.status_code, error.code) == (503, "SERVICE_BUDGET_EXHAUSTED")
    day[0] = NEXT_DAY
    guard.before_query("f", "r6")


def test_late_usage_counts_for_the_service_only():
    guard, usage, _ = _guard()
    guard.record("a", [{"cost_usd": 0.10}], late_usage=[{"cost_usd": 0.03}])
    assert usage.cost("user", "a", DAY) == pytest.approx(0.10)
    assert usage.cost("service", limits.SERVICE_SCOPE_ID, DAY) == pytest.approx(0.13)


# ── API 경계 ─────────────────────────────────────────────


@pytest.fixture
def api():
    repository, runner = FakeReviewRepository(), FakeReviewGraphRunner()
    guard, usage, day = _guard()
    app.dependency_overrides[get_review_repository] = lambda: repository
    app.dependency_overrides[get_review_graph_runner] = lambda: runner
    app.dependency_overrides[current_user_id] = lambda: USER_A
    app.dependency_overrides[get_query_guard] = lambda: guard
    client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    yield client, repository, runner, usage, day
    app.dependency_overrides.clear()


def _ask(client, question="차입금 만기 구조를 알려줘", review_id=REVIEW_A):
    return client.post(f"/api/v1/reviews/{review_id}/queries", json={"question": question})


def test_api_21st_query_on_a_review_is_409_and_does_not_run_or_lock(api):
    client, repository, runner, usage, day = api
    for _ in range(20):
        assert _ask(client).status_code == 200

    blocked = _ask(client)

    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "CONFLICT_REVIEW_QUERY_LIMIT"
    assert len(runner.calls) == 20
    assert repository.reviews[REVIEW_A].status == "open"
    assert repository.reviews[REVIEW_A].query_count == 20  # 누적 표시용 횟수는 평생 50에서 멈추지 않는다
    day[0] = NEXT_DAY
    assert _ask(client).status_code == 200
    assert repository.reviews[REVIEW_A].query_count == 21


def test_api_51st_query_of_the_day_is_429_across_reviews(api):
    client, repository, runner, usage, _ = api
    reviews = [REVIEW_A, REVIEW_2, "50000000-0000-0000-0000-0000000000e5"]
    for review_id in reviews[1:]:
        repository.reviews[review_id] = _review(review_id=review_id, user_id=USER_A)
    for index in range(50):  # 심사 건 3개에 나눠 50회(건당 20회 이하)
        assert _ask(client, review_id=reviews[index % 3]).status_code == 200

    blocked = _ask(client, review_id=reviews[2])

    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert len(runner.calls) == 50


def test_api_question_of_1000_chars_passes_and_1001_is_rejected(api):
    client, _, runner, _, _ = api
    assert _ask(client, "가" * 1000).status_code == 200
    rejected = _ask(client, "가" * 1001)
    assert rejected.status_code == 400 and rejected.json()["error"]["code"] == "VALIDATION_ERROR"
    assert len(runner.calls) == 1


def test_api_body_over_16kb_is_413_before_reaching_the_route(api):
    client, _, runner, _, _ = api
    padding = "x" * 17000
    response = client.post(
        f"/api/v1/reviews/{REVIEW_A}/queries", content=f'{{"question": "{padding}"}}'.encode(),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413 and response.json()["error"]["code"] == "VALIDATION_PAYLOAD_TOO_LARGE"
    assert runner.calls == []


def test_api_losing_the_lock_race_gives_the_daily_count_back(api):
    client, repository, runner, usage, _ = api
    repository.transition_status = lambda *_args, **_kwargs: None  # 다른 요청이 먼저 잠갔다

    assert _ask(client).status_code == 409

    assert runner.calls == []
    assert (usage.queries("user", USER_A, DAY), usage.queries("review", REVIEW_A, DAY)) == (0, 0)


def test_api_failed_query_still_counts_and_its_cost_is_recorded_once(api):
    """실패한 질의도 비용을 썼으므로 오늘 횟수와 비용에 들어간다. 비용은 한 번만 더한다."""

    client, repository, runner, usage, _ = api

    def ask(**_kwargs):
        llm._record_usage("chat", "m", None, cost=0.02)
        raise RuntimeError("graph exploded")

    runner.ask = ask

    assert _ask(client).status_code == 500

    assert usage.queries("user", USER_A, DAY) == 1 and usage.queries("review", REVIEW_A, DAY) == 1
    assert usage.cost("user", USER_A, DAY) == pytest.approx(0.02)
    assert usage.cost("service", limits.SERVICE_SCOPE_ID, DAY) == pytest.approx(0.02)
    assert repository.reviews[REVIEW_A].status == "open" and repository.reviews[REVIEW_A].query_count == 0


# ── 처리 시간 60초 ───────────────────────────────────────


def test_api_query_over_the_deadline_is_504_and_review_returns_to_open(api):
    client, repository, runner, usage, _ = api
    seen = {}

    def ask(**_kwargs):
        seen["deadline"] = llm._REQUEST_DEADLINE.get() - llm.time.monotonic()
        llm._record_usage("chat", "m", None, cost=0.03)  # 끊기기 전에 쓴 비용
        raise llm.QueryDeadlineExceeded("질의 처리 시간 상한을 넘었습니다")

    runner.ask = ask

    response = _ask(client)

    assert response.status_code == 504 and response.json()["error"]["code"] == "UPSTREAM_LLM_TIMEOUT"
    assert "60초" in response.json()["error"]["message"]
    assert 59.0 < seen["deadline"] <= 60.0  # 리터럴: SPEC §4 질의 처리 시간 60초
    assert repository.transitions == [("open", "running"), ("running", "open")]  # running에 갇히지 않는다
    assert repository.reviews[REVIEW_A].query_count == 0
    assert usage.cost("user", USER_A, DAY) == pytest.approx(0.03)  # 한 번만 더해진다
    assert usage.cost("service", limits.SERVICE_SCOPE_ID, DAY) == pytest.approx(0.03)
    assert llm._REQUEST_DEADLINE.get() is None  # 요청이 끝나면 마감도 사라진다
    runner.ask = FakeReviewGraphRunner().ask
    assert _ask(client).status_code == 200  # 다음 질의는 정상으로 돈다


def test_call_timeout_clips_to_the_remaining_time_and_refuses_after_the_deadline(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(llm.time, "monotonic", lambda: now[0])
    assert llm._call_timeout(30.0) == (30.0, False)  # 요청 범위 밖(평가 러너 등): 그대로

    with llm.request_scope(timeout_seconds=60.0):
        assert llm._call_timeout(30.0) == (30.0, False)  # 남은 60초 > 30초
        now[0] = 150.0
        assert llm._call_timeout(30.0) == (10.0, True)  # 남은 10초로 줄인다
        now[0] = 160.0
        with pytest.raises(llm.QueryDeadlineExceeded):
            llm._call_timeout(30.0)  # 마감: 호출하지 않는다
    assert llm._call_timeout(30.0) == (30.0, False)


def test_no_external_call_starts_after_the_deadline(monkeypatch):
    """마감이 지난 뒤에는 채팅·리랭크 HTTP 요청이 나가지 않는다(비용이 더 생기지 않는다)."""

    from pydantic import BaseModel

    class Answer(BaseModel):
        text: str

    posted = []
    created = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = self
            self.completions = self

        def create(self, **kwargs):
            created.append(kwargs)
            raise AssertionError("마감 뒤에 채팅 호출이 나갔다")

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(llm, "DEMO_MODE", False)
    with llm.request_scope(timeout_seconds=0.0):
        with pytest.raises(llm.QueryDeadlineExceeded):
            llm.openrouter_structured([{"role": "user", "content": "q"}], model="openai/gpt-4o-mini", response_model=Answer)
        with pytest.raises(llm.QueryDeadlineExceeded):
            llm.openrouter_rerank("q", ["d"], model="m", post=lambda *a, **k: posted.append(1))
    assert created == [] and posted == []


def test_deadline_is_not_an_llm_fallback_error():
    """Supervisor의 '실패하면 코드 규칙으로 대신' 폴백이 마감 예외를 삼키면 그래프가 계속 돈다. 하위 클래스가 아니어야 한다."""

    from backend.graph.supervisor_flow import LLM_FALLBACK_ERRORS

    assert not issubclass(llm.QueryDeadlineExceeded, LLM_FALLBACK_ERRORS)
    assert not issubclass(llm.QueryDeadlineExceeded, llm.ExternalApiTimeout)


CATALOG = CorpusCatalog(
    corp_names=frozenset({"오뚜기"}),
    fiscal_years=frozenset({2025}),
    report_types=frozenset({"annual"}),
    targets=frozenset({CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2025)}),
)
CHUNK = {"chunk_id": "c1", "corp_code": "00000001", "fiscal_year": 2025, "content": "차입금 공시",
         "rcept_no": "r", "chapter": "NOTES", "note_no": "1"}
QUESTION = "오뚜기의 FY2025 사업보고서 차입금 현황을 알려주세요."


@pytest.mark.parametrize("stage", ["grader", "supervisor_after_search", "writer", "supervisor_after_draft"])
def test_graph_stopped_by_the_deadline_leaves_no_pending_approval_and_the_next_question_runs(stage):
    """마감 예외로 멈춘 그래프: 승인 대기가 남지 않고 메모도 저장되지 않으며, 같은 thread의 다음 질문은 처음부터 정상으로 돈다.

    단계는 그래프가 도는 순서대로다. 160번: Supervisor LLM(decider)은 두 자리에서 불린다.
    - supervisor_after_search: 검색·채점 뒤 작성 전(작성 대 거절을 고르는 자리). 초안이 없는 채로 마감된다.
    - supervisor_after_draft: '검증은 통과했지만 확인 필요인 초안' 뒤. 그 초안을 내게 해 초안이 이미 있는 채로 마감된다.
    """

    state = {"expired": True}
    memos = []
    reached = []  # 마감을 낸 자리(시험이 보려는 호출이 실제로 불렸는지 확인)

    def expire(here):
        if state["expired"] and stage == here:
            reached.append(here)
            raise llm.QueryDeadlineExceeded("마감")

    def grader(s, c):
        expire("grader")
        return {"relevant_chunk_ids": ["c1"], "sufficient": False, "missing_items": ["만기"]}

    def writer(s, c):
        expire("writer")
        verdict = "needs_check" if stage == "supervisor_after_draft" else "applicable"
        return [{"item": s["question"], "verdict": verdict, "reason": "차입금 공시", "chunk_ids": ["c1"]}]

    def decider(s):
        expire("supervisor_after_draft" if s.get("writer_calls") else "supervisor_after_search")
        return None  # 결정 없음 → 코드 규칙(근거가 있으면 작성, 초안이 있으면 승인)

    graph = build_supervisor_graph(
        retriever=lambda s: [CHUNK], draft_writer=writer, corpus_catalog=CATALOG, domain_classifier=lambda q: True,
        supervisor_decider=decider, checkpointer=InMemorySaver(), approval_enabled=True,
        memo_saver=lambda *a, **k: memos.append(1) or "memo-1", speculative=False,
        search_grader=grader,
    )
    # 서비스와 같은 입력: 승인 단계는 thread_id가 user_id:review_id일 때만 돈다
    ask = lambda review: graph.invoke(
        {"question": QUESTION, "user_id": "u", "review_id": review}, config={"configurable": {"thread_id": f"u:{review}"}})
    config = {"configurable": {"thread_id": "u:r1"}}

    with pytest.raises(llm.QueryDeadlineExceeded):
        ask("r1")

    assert reached and set(reached) == {stage}  # 보려는 자리에서만 마감됐다(작성은 초안 2개가 동시에 돌아 2회일 수 있다)
    assert pending_approval(graph, config) is None and memos == []
    state["expired"] = False
    final = ask("r1")
    fresh = ask("r2")
    assert pending_approval(graph, config) is not None  # 이번에는 승인 대기까지 간다
    for key in ("outcome", "search_calls", "writer_calls", "draft_items", "citations", "refusal_reason"):
        assert final[key] == fresh[key], key  # 끊긴 적 없는 새 thread와 결과가 같다(반쯤 쓰인 상태를 물려받지 않는다)
    assert final["search_calls"] >= 1 and final["draft_items"]


# ── 요청별 사용량: 동시에 들어온 요청의 비용이 섞이지 않는다 ───────


def test_usage_is_collected_per_request_even_when_two_requests_overlap():
    first_started, second_done = threading.Event(), threading.Event()
    collected = {}

    def request(name, cost, wait_for, signal):
        with llm.request_scope() as usage:
            llm._record_usage("chat", "m", None, cost=cost)
            signal.set()
            wait_for.wait(timeout=5)
            llm._record_usage("rerank", "m", None, cost=cost)
            collected[name] = list(usage)

    a = threading.Thread(target=request, args=("a", 0.01, second_done, first_started))
    a.start()
    first_started.wait(timeout=5)
    request("b", 0.5, threading.Event(), second_done)  # a가 도는 중에 b가 시작해서 끝난다
    a.join(timeout=5)

    assert [entry["cost_usd"] for entry in collected["a"]] == [0.01, 0.01]
    assert [entry["cost_usd"] for entry in collected["b"]] == [0.5, 0.5]


def test_worker_threads_started_with_copy_context_report_to_the_same_request_and_late_calls_go_aside():
    llm.drain_late_usage()
    llm.drain_usage()
    release = threading.Event()

    def late_call():
        release.wait(timeout=5)
        llm._record_usage("chat", "m", None, cost=0.07)

    with llm.request_scope() as usage:
        worker = threading.Thread(target=contextvars.copy_context().run, args=(lambda: llm._record_usage("chat", "m", None, cost=0.02),))
        worker.start()
        worker.join(timeout=5)
        late = threading.Thread(target=contextvars.copy_context().run, args=(late_call,))  # 이중 요청의 늦은 쪽처럼
        late.start()
    release.set()
    late.join(timeout=5)

    assert [entry["cost_usd"] for entry in usage] == [0.02]  # 미리 실행·이중 요청 스레드의 호출도 이 요청 것으로 모인다
    assert [entry["cost_usd"] for entry in llm.drain_late_usage()] == [0.07]  # 요청이 끝난 뒤 끝난 호출은 따로
    assert llm.drain_usage() == []  # 전역 목록(평가 러너용)에는 섞이지 않는다


def test_calls_outside_a_request_scope_still_go_to_the_global_log_for_the_eval_runner():
    llm.drain_usage()
    llm._record_usage("chat", "m", None, cost=0.04)
    assert [entry["cost_usd"] for entry in llm.drain_usage()] == [0.04]


# ── 승인된 건 ────────────────────────────────────────────


def test_api_closed_review_is_409_with_its_own_code_and_spends_no_count(api):
    client, repository, runner, usage, _ = api
    repository.reviews[REVIEW_A] = replace(repository.reviews[REVIEW_A], status="closed")

    response = _ask(client)

    assert response.status_code == 409 and response.json()["error"]["code"] == "CONFLICT_REVIEW_CLOSED"
    assert runner.calls == [] and usage.queries("user", USER_A, DAY) == 0


def test_cost_lookup_retries_once_on_a_dropped_connection_then_gives_up():
    """한도 조회(읽기)는 연결이 끊겼으면 새 연결로 한 번만 더 한다. 두 번 다 끊기면 그 예외를 그대로 올린다."""

    from contextlib import contextmanager
    from datetime import date

    import psycopg

    from backend.api.limits import PostgresDailyUsage

    class Connection:
        def __init__(self, outcome):
            self.outcome = outcome

        def execute(self, sql, params):
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self

        def fetchone(self):
            return self.outcome

    class Pool:
        def __init__(self, outcomes):
            self.outcomes, self.checkouts = list(outcomes), 0

        @contextmanager
        def connection(self):
            self.checkouts += 1
            yield Connection(self.outcomes.pop(0))

    dropped = psycopg.OperationalError("server closed the connection unexpectedly")
    pool = Pool([dropped, {"cost_usd": 0.25}])
    assert PostgresDailyUsage(pool).cost("service", "all", date(2026, 11, 17)) == 0.25
    assert pool.checkouts == 2

    pool = Pool([dropped, dropped, {"cost_usd": 9.0}])
    with pytest.raises(psycopg.OperationalError):
        PostgresDailyUsage(pool).cost("service", "all", date(2026, 11, 17))
    assert pool.checkouts == 2
