"""서비스용 심사 그래프 조립(backend/api/runtime.py)과 질의 한도(backend/api/limits.py). 키 없이 가짜만 쓴다.

Postgres가 필요한 시험(메모 멱등 저장, 누적 비용)은 CHECKPOINT_DATABASE_URL이 있을 때만 돈다.
"""
import os
import uuid

import numpy as np
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from backend.api import limits, runtime
from backend.api.limits import PerUserRateLimiter, QueryGuard, usage_cost
from backend.graph.approval import NotAwaitingApproval
from backend.services import llm

FULL_ENV = {
    "DATABASE_URL": "postgresql://x", "OPENAI_API_KEY": "k", "VOYAGE_API_KEY": "k",
    "RETRIEVAL_COMBO": "d3-55", "RETRIEVAL_CHUNKS_PATH": "c.jsonl", "RETRIEVAL_EMBEDDING_PREFIX": "p",
}


def test_missing_runtime_env_names_only():
    assert runtime.missing_runtime_env(FULL_ENV) == []
    missing = runtime.missing_runtime_env({**FULL_ENV, "VOYAGE_API_KEY": " ", "RETRIEVAL_COMBO": ""})
    assert missing == ["VOYAGE_API_KEY", "RETRIEVAL_COMBO"]
    assert "OPENROUTER_API_KEY" in runtime.missing_runtime_env({"DEMO_MODE": "0"})


def test_missing_runtime_env_follows_vector_backend():
    """pgvector면 벡터 파일 prefix 대신 활성 corpus_id가 필요하다. 모르는 값은 이름으로 알린다(사전 등록 90번)."""

    pgvector_env = {key: value for key, value in FULL_ENV.items() if key != "RETRIEVAL_EMBEDDING_PREFIX"}
    pgvector_env["VECTOR_BACKEND"] = "pgvector"
    assert runtime.missing_runtime_env(pgvector_env) == ["RETRIEVAL_CORPUS_ID"]
    assert runtime.missing_runtime_env({**pgvector_env, "RETRIEVAL_CORPUS_ID": "corpus-x"}) == []
    assert runtime.missing_runtime_env({**FULL_ENV, "VECTOR_BACKEND": "faiss"}) == ["VECTOR_BACKEND"]


def test_news_search_is_on_only_with_the_flag_and_outside_demo_mode():
    keyed = {"OPENROUTER_API_KEY": "k", "DEMO_MODE": "0"}
    assert runtime.news_search_enabled(keyed) is False  # 기본 꺼짐
    assert runtime.news_search_enabled({**keyed, "NEWS_SEARCH_ENABLED": "0"}) is False
    assert runtime.news_search_enabled({**keyed, "NEWS_SEARCH_ENABLED": "true"}) is False  # "1"만 받는다
    assert runtime.news_search_enabled({**keyed, "NEWS_SEARCH_ENABLED": "1"}) is True
    assert runtime.news_search_enabled({"NEWS_SEARCH_ENABLED": "1"}) is False  # 키 없는 데모 모드


def test_demo_mode_needs_only_database_and_snapshot():
    """키가 없고 DEMO_MODE가 0이 아니면 데모 모드: DB와 커밋된 스냅샷만 있으면 된다(G2)."""

    assert runtime.demo_mode({}) and runtime.demo_mode({"DEMO_MODE": "1"})
    assert not runtime.demo_mode({"DEMO_MODE": "0"}) and not runtime.demo_mode({"OPENAI_API_KEY": "k"})
    assert runtime.missing_runtime_env({"DATABASE_URL": "postgresql://x"}) == []
    assert runtime.missing_runtime_env({}) == ["DATABASE_URL"]


# ---- 실제 조립 + 가짜 LLM: 질문 → 승인 대기 → 승인 → 메모 ----

class FakePool:
    """메모 저장·비용 기록 SQL을 흉내 낸다(같은 승인 번호는 같은 id)."""

    def __init__(self):
        self.memos = {}
        self.saved_items = {}
        self.cost = {}     # (scope, scope_id, day) -> 달러
        self.queries = {}  # (scope, scope_id, day) -> 횟수
        self.closed = False

    def connection(self):
        pool = self

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=()):
                class Result:
                    def __init__(self, row):
                        self.row = row

                    def fetchone(self):
                        return self.row

                if sql.startswith("INSERT INTO review_memos"):
                    key = (params[0], params[1])
                    if key in pool.memos:
                        return Result(None)
                    pool.memos[key] = str(uuid.uuid4())
                    pool.saved_items[key] = params[2].obj
                    return Result({"id": pool.memos[key]})
                if sql.startswith("SELECT id FROM review_memos"):
                    return Result({"id": pool.memos[(params[0], params[1])]})
                if sql.startswith("SELECT cost_usd FROM daily_usage"):
                    return Result({"cost_usd": pool.cost.get(tuple(params), 0.0)})
                if sql.startswith("INSERT INTO daily_usage (scope, scope_id, day, cost_usd)"):
                    key = tuple(params[:3])
                    pool.cost[key] = pool.cost.get(key, 0.0) + params[3]
                    return Result(None)
                if sql.startswith("INSERT INTO daily_usage (scope, scope_id, day, queries)"):
                    key, limit = tuple(params[:3]), params[3]
                    if pool.queries.get(key, 0) >= limit:
                        return Result(None)  # ON CONFLICT ... WHERE queries < limit: 한도면 돌아오는 행이 없다
                    pool.queries[key] = pool.queries.get(key, 0) + 1
                    return Result({"queries": pool.queries[key]})
                if sql.startswith("UPDATE daily_usage SET queries = GREATEST"):
                    key = tuple(params)
                    pool.queries[key] = max(pool.queries.get(key, 0) - 1, 0)
                    return Result(None)
                raise AssertionError(sql)

        return Conn()

    def close(self):
        self.closed = True


CHUNK = {"chunk_id": "c1", "corp_code": "00141529", "fiscal_year": 2025, "content": "차입금 합계 | 1,000",
         "rcept_no": "r", "chapter": "NOTES", "note_no": "1"}


class FakeEmbedder:
    def encode_queries(self, texts):
        return np.ones((len(texts), runtime.EMBEDDING_DIMS), dtype=np.float32)


@pytest.fixture
def fake_llm(monkeypatch):
    monkeypatch.setattr(llm, "classify_domain", lambda **_: "in_domain")
    monkeypatch.setattr(
        llm, "write_drafts",
        lambda **kw: [{"item": kw["checklist_items"][0], "verdict": "applicable", "reason": "1,000",
                       "chunk_ids": ["c1"], "evidence": [{"chunk_id": "c1", "quote": "차입금 합계 | 1,000"}]}],
    )


def _runtime(pool, rate_limiter=None):
    return runtime.build_review_runtime(
        FULL_ENV,
        pool=pool,
        retriever=lambda state: [CHUNK],
        embedder=FakeEmbedder(),
        store=InMemoryStore(index=runtime.rule_index_config(FakeEmbedder())),
        checkpointer=InMemorySaver(),
        rate_limiter=rate_limiter,
    )


class Review:
    id = str(uuid.uuid4())
    corp_code = "00141529"
    corp_name = "오뚜기"
    fiscal_year = 2025


USER = str(uuid.uuid4())
TARGET = {"corp_code": "00141529", "corp_name": "오뚜기", "fiscal_year": 2025}


def test_runtime_runs_question_to_saved_memo_once(fake_llm):
    pool = FakePool()
    built = _runtime(pool)
    review = Review()
    thread_id = f"{USER}:{review.id}"
    asked = built.runner.ask(review=review, user_id=USER, question="오뚜기 2025 차입금 합계는?",
                             thread_id=thread_id, review_target=TARGET)
    assert asked["outcome"] == "awaiting_approval"
    pending = built.runner.pending_approval(review=review, user_id=USER)
    assert pending["question"] == "오뚜기 2025 차입금 합계는?"
    draft = pending["draft_items"]
    assert draft and draft[0]["verdict"] == "applicable"
    assert draft[0]["item"] == asked["draft"][0]["item"] == "borrowing_structure"

    for question in ("차입금 합계가 얼마인가요?", "차입금 총액을 알려주세요."):
        follow_up = built.runner.ask_follow_up(review=review, user_id=USER, question=question)
        assert follow_up["outcome"] == "answered"
        assert follow_up["draft"] and follow_up["draft"][0]["citations"]
        assert built.runner.pending_approval(review=review, user_id=USER) == pending
        assert not pool.memos

    decisions = [{"item": item["item"], "verdict": item["verdict"], "reason": item["reason"]} for item in draft]
    saved = built.runner.resume(review=review, user_id=USER, decisions=decisions, action="approve")
    assert saved["approval_seq"] == 1 and saved["memo_id"] == pool.memos[(review.id, 1)]
    assert pool.saved_items[(review.id, 1)][0]["item"] == "borrowing_structure"
    with pytest.raises(NotAwaitingApproval):  # 같은 승인 재전송: 대기가 없어 409 경로, 메모는 그대로 1건
        built.runner.resume(review=review, user_id=USER, decisions=decisions, action="approve")
    assert len(pool.memos) == 1
    built.close()
    assert pool.closed


def test_direct_question_resolves_the_company_itself_and_reaches_the_same_approval(fake_llm):
    """바로 질문: 대상을 주지 않아도 실제 그래프가 질문에서 회사·연도를 찾아 승인 대기까지 가고, 승인하면 메모가 저장된다."""

    pool = FakePool()
    built = _runtime(pool)
    question = "오뚜기 2025 차입금 합계는?"
    assert built.resolve_target(question) == TARGET  # API가 이 대상으로 심사 건을 만든다

    review = Review()
    asked = built.runner.ask(review=review, user_id=USER, question=question,
                             thread_id=f"{USER}:{review.id}", review_target=None)
    assert asked["outcome"] == "awaiting_approval"
    pending = built.runner.pending_approval(review=review, user_id=USER)
    assert pending["question"] == question
    draft = pending["draft_items"]
    decisions = [{"item": item["item"], "verdict": item["verdict"], "reason": item["reason"]} for item in draft]
    saved = built.runner.resume(review=review, user_id=USER, decisions=decisions, action="approve")
    assert saved["memo_id"] == pool.memos[(review.id, 1)]
    built.close()


@pytest.mark.parametrize(("question", "reason", "asks_back"), [
    ("없는회사 주식회사의 2025년 사업보고서에서 차입금 합계는?", "CORP_NOT_IN_CORPUS", False),
    ("오뚜기의 2019년 사업보고서에서 차입금 합계는?", "PERIOD_NOT_IN_CORPUS", False),
    ("차입금 합계가 얼마인가요?", None, True),
])
def test_direct_question_without_a_target_ends_at_the_entrance(fake_llm, question, reason, asks_back):
    """대상이 정해지지 않는 질문: 심사 건 없이 실제 그래프가 거절하거나 되묻고 끝난다(검색·작성·승인 없음)."""

    built = _runtime(FakePool())
    assert built.resolve_target(question) is None

    result = built.runner.ask_without_review(user_id=USER, question=question)

    assert result.get("refusal_reason") == reason
    assert result["outcome"] == ("answered" if asks_back else "refused")
    assert bool(result.get("target_required")) is asks_back
    assert result["refusal_message"]
    assert result["draft"] == []
    built.close()


def test_memo_saver_returns_same_id_for_same_approval():
    saver = runtime.PostgresMemoSaver(FakePool())
    record = {"items": [], "approved_by": USER}
    first = saver(record, (USER, "rv", 1))
    assert saver(record, (USER, "rv", 1)) == first
    assert saver(record, (USER, "rv", 2)) != first


# ---- 질의 한도 ---- (한도별 경계값·날짜 초기화는 tests/test_query_limits.py)

def test_rate_limit_is_five_per_minute_per_user():
    now = [0.0]
    limiter = PerUserRateLimiter(clock=lambda: now[0])
    for _ in range(5):  # 리터럴: SPEC §4 분당 5
        limiter.check("a")
    with pytest.raises(limits.ApiError) as raised:
        limiter.check("a")
    assert raised.value.status_code == 429 and raised.value.code == "RATE_LIMITED"
    limiter.check("b")  # 다른 사용자는 따로 센다
    now[0] = 60.0
    limiter.check("a")  # 60초가 지나면 다시 된다


def test_api_ask_is_rate_limited_before_locking_review(monkeypatch):
    """6번째 질의는 그래프를 돌리지 않고, 심사 건 상태도 바꾸지 않은 채 429."""

    from tests.test_reviews_api import FakeReviewGraphRunner, FakeReviewRepository
    from backend.api.reviews import get_query_guard, get_review_graph_runner, get_review_repository
    from backend.auth import current_user_id
    from backend.main import app

    repository = FakeReviewRepository()
    runner = FakeReviewGraphRunner()
    review = repository.create_review(USER, "테스트 주식회사", 2025)
    guard = QueryGuard(rate_limiter=PerUserRateLimiter(clock=lambda: 0.0))
    app.dependency_overrides[get_review_repository] = lambda: repository
    app.dependency_overrides[get_review_graph_runner] = lambda: runner
    app.dependency_overrides[current_user_id] = lambda: USER
    app.dependency_overrides[get_query_guard] = lambda: guard
    try:
        client = TestClient(app)
        for _ in range(5):  # 리터럴: SPEC §4 분당 5
            assert client.post(f"/api/v1/reviews/{review.id}/queries", json={"question": "차입금은?"}).status_code == 200
        response = client.post(f"/api/v1/reviews/{review.id}/queries", json={"question": "차입금은?"})
        assert response.status_code == 429 and response.json()["error"]["code"] == "RATE_LIMITED"
        assert repository.get_review(USER, review.id).status == "open"
        assert len(runner.calls) == 5
    finally:
        app.dependency_overrides.clear()


# ---- 실제 Postgres(시험용 컨테이너) ----

PG_URL = os.getenv("CHECKPOINT_DATABASE_URL")


@pytest.mark.skipif(not PG_URL, reason="CHECKPOINT_DATABASE_URL(시험용 Postgres)이 있을 때만 실행")
def test_daily_usage_table_counts_and_resets_by_day_in_postgres():
    """실제 Postgres에서 '세면서 검사' 한 문장이 한도를 지키고, 날짜가 바뀌면 0부터 세는지 본다."""
    from datetime import date

    pool = runtime.open_pool(PG_URL)
    scope_id = f"test-{uuid.uuid4()}"
    today, tomorrow = date(2026, 11, 11), date(2026, 11, 12)
    try:
        with pool.connection() as connection:
            connection.execute(limits.limit_setup_statements()[0])  # daily_usage 표만(reviews 표는 이 DB에 없을 수 있다)
        usage = limits.PostgresDailyUsage(pool)
        assert [usage.admit("user", scope_id, today, 2) for _ in range(3)] == [True, True, False]
        assert usage.admit("user", scope_id, tomorrow, 2) is True  # 날짜가 바뀌면 다시 센다
        usage.release("user", scope_id, today)
        assert usage.admit("user", scope_id, today, 2) is True
        usage.add_cost("user", scope_id, today, 0.25)
        usage.add_cost("user", scope_id, today, 0.5)
        assert usage.cost("user", scope_id, today) == pytest.approx(0.75)
        assert usage.cost("user", scope_id, tomorrow) == 0.0
    finally:
        with pool.connection() as connection:
            connection.execute("DELETE FROM daily_usage WHERE scope_id = %s", (scope_id,))
        pool.close()


# ---- 데모 모드(키 없음): 기록 재생(SPEC §5.6, G2) ----

def test_demo_replay_hits_and_misses(monkeypatch, tmp_path):
    from backend.services import demo_replay

    recorder = demo_replay.Recorder()
    payload = {"texts": ["질문"], "model": "m", "dimensions": 2}
    recorder.record("embeddings", payload, [[0.5, 0.25]])
    path = tmp_path / "rec.jsonl.gz"
    recorder.save(path)
    first = path.read_bytes()
    recorder.save(path)
    assert path.read_bytes() == first  # 같은 기록이면 같은 파일(mtime 0, 키 순서 고정)

    monkeypatch.setattr(demo_replay, "RECORDINGS_PATH", path)
    monkeypatch.setattr(demo_replay, "_loaded", None)
    assert demo_replay.replay_embeddings(["질문"], model="m", dimensions=2) == [[0.5, 0.25]]
    with pytest.raises(demo_replay.DemoMiss):
        demo_replay.replay_embeddings(["다른 질문"], model="m", dimensions=2)
    assert issubclass(demo_replay.DemoMiss, llm.ExternalApiKeyMissing)  # 키 없음과 같은 갈래(주제 판정은 건너뜀)


def test_openrouter_structured_replays_without_key(monkeypatch, tmp_path):
    from backend.services import demo_replay

    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    recorder = demo_replay.Recorder()
    recorder.record("chat", demo_replay.chat_payload(messages, model="m", response_model=llm._RewriteResponse, max_tokens=None),
                    {"retrieval_query": "오뚜기 2025 차입금"})
    path = tmp_path / "rec.jsonl.gz"
    recorder.save(path)
    monkeypatch.setattr(demo_replay, "RECORDINGS_PATH", path)
    monkeypatch.setattr(demo_replay, "_loaded", None)
    monkeypatch.setattr(llm, "DEMO_MODE", True)
    with pytest.raises(llm.ExternalApiKeyMissing):  # 데모 런타임이 켜기 전에는 재생하지 않는다
        llm.openrouter_structured(messages, model="m", response_model=llm._RewriteResponse)
    monkeypatch.setattr(demo_replay, "_enabled", True)
    result = llm.openrouter_structured(messages, model="m", response_model=llm._RewriteResponse)
    assert result.retrieval_query == "오뚜기 2025 차입금"
    with pytest.raises(demo_replay.DemoMiss):
        llm.openrouter_structured([{"role": "user", "content": "기록 없음"}], model="m", response_model=llm._RewriteResponse)
    monkeypatch.setattr(llm, "DEMO_MODE", False)
    with pytest.raises(llm.ExternalApiKeyMissing):
        llm.openrouter_structured(messages, model="m", response_model=llm._RewriteResponse)


def test_committed_demo_recordings_cover_demo_questions():
    """커밋된 기록이 데모 질문의 외부 호출을 모두 담고 있다(scripts/record_demo.py가 재생 결과까지 확인한 뒤 씀)."""

    import gzip
    import json
    from pathlib import Path

    from backend.services import demo_replay

    questions = [json.loads(line) for line in Path("data/demo/questions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    with gzip.open(demo_replay.RECORDINGS_PATH, "rt", encoding="utf-8") as stream:
        kinds = [json.loads(line)["kind"] for line in stream if line.strip()]
    assert len(questions) == 7
    assert kinds.count("chat") >= len(questions)  # 질문마다 주제 판정, 답한 질문은 작성까지
    assert kinds.count("embeddings") >= 5 and kinds.count("rerank") >= 5


def test_api_demo_miss_is_503_with_guidance_and_releases_review():
    from tests.test_reviews_api import FakeReviewRepository
    from backend.api.reviews import get_query_guard, get_review_graph_runner, get_review_repository
    from backend.auth import current_user_id
    from backend.main import app
    from backend.services.demo_replay import DemoMiss

    class MissRunner:
        def ask(self, **_):
            raise DemoMiss("기록 없음")

    repository = FakeReviewRepository()
    review = repository.create_review(USER, "테스트 주식회사", 2025)
    app.dependency_overrides[get_review_repository] = lambda: repository
    app.dependency_overrides[get_review_graph_runner] = lambda: MissRunner()
    app.dependency_overrides[current_user_id] = lambda: USER
    app.dependency_overrides[get_query_guard] = lambda: QueryGuard()
    try:
        response = TestClient(app).post(f"/api/v1/reviews/{review.id}/queries", json={"question": "기록 없는 질문"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE_GRAPH"
        assert "데모" in response.json()["error"]["message"]
        assert repository.get_review(USER, review.id).status == "open"
    finally:
        app.dependency_overrides.clear()


def test_pool_disables_prepared_statements_for_transaction_pooler():
    """운영 DB는 Supabase 트랜잭션 풀러(6543)라 prepare를 꺼야 한다. psycopg에서 끄는 값은 None이다(0은 '항상 prepare').

    11/6 배포에서 0으로 두어 질의마다 DuplicatePreparedStatement가 났다. 로컬 pgbouncer(transaction 모드,
    max_prepared_statements=0)로 0은 첫 실행에서 실패, None은 12/12 성공을 재현했다.
    """

    assert runtime.POOL_PREPARE_THRESHOLD is None
