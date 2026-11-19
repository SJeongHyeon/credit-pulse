"""심사 건 API 계약. 외부 DB·LLM 없이 실제 FastAPI 경계를 시험한다."""

from dataclasses import asdict, replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.api.reviews import (
    LangGraphReviewRunner,
    PostgresReviewRepository,
    ReviewPage,
    ReviewRecord,
    get_question_target_resolver,
    get_query_guard,
    get_review_graph_runner,
    get_review_repository,
)
from backend.api.limits import QueryGuard
from backend.auth import current_user_id
from backend.errors import ApiError
from backend.graph.approval import NotAwaitingApproval
from backend.main import app

USER_A = "00000000-0000-0000-0000-0000000000a1"
USER_B = "00000000-0000-0000-0000-0000000000b2"
REVIEW_A = "10000000-0000-0000-0000-0000000000a1"
REVIEW_B = "20000000-0000-0000-0000-0000000000b2"


def _review(*, review_id: str, user_id: str, corp_code: str = "00126380") -> ReviewRecord:
    return ReviewRecord(
        id=review_id,
        user_id=user_id,
        thread_id=f"{user_id}:{review_id}",
        corp_code=corp_code,
        corp_name="테스트 주식회사",
        fiscal_year=2025,
        status="open",
        query_count=0,
        created_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
        updated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )


class FakeReviewRepository:
    def __init__(self) -> None:
        self.reviews = {
            REVIEW_A: _review(review_id=REVIEW_A, user_id=USER_A),
            REVIEW_B: _review(review_id=REVIEW_B, user_id=USER_B, corp_code="00141529"),
        }
        self.created: list[ReviewRecord] = []
        self.transitions: list[tuple[str, str]] = []  # 성공한 (from, to) 전이 기록

    def create_review(self, user_id: str, corp_query: str, fiscal_year: int) -> ReviewRecord:
        review_id = "30000000-0000-0000-0000-0000000000c3"
        review = _review(review_id=review_id, user_id=user_id)
        assert corp_query in ("테스트 주식회사", "00126380")
        assert fiscal_year == 2025
        self.reviews[review_id] = review
        self.created.append(review)
        return review

    def list_reviews(self, user_id: str, page_size: int, cursor: str | None, sort: str = "recent") -> ReviewPage:
        assert page_size == 20
        assert cursor is None
        assert sort == "recent"
        return ReviewPage(
            items=[review for review in self.reviews.values() if review.user_id == user_id],
            next_cursor=None,
        )

    def get_review(self, user_id: str, review_id: str) -> ReviewRecord | None:
        review = self.reviews.get(review_id)
        return review if review and review.user_id == user_id else None

    def delete_review(self, user_id: str, review_id: str) -> bool:
        review = self.get_review(user_id, review_id)
        if review is None:
            return False
        if review.status in ("running", "saving"):
            raise ApiError(409, "CONFLICT_REVIEW_IN_PROGRESS", "처리 중인 심사 건은 삭제할 수 없습니다")
        del self.reviews[review_id]
        return True

    def delete_all_reviews(self, user_id: str) -> None:
        owned = [review for review in self.reviews.values() if review.user_id == user_id]
        if any(review.status in ("running", "saving") for review in owned):
            raise ApiError(409, "CONFLICT_REVIEW_IN_PROGRESS", "처리 중인 심사 건은 삭제할 수 없습니다")
        for review in owned:
            del self.reviews[review.id]

    def increment_query_count(self, user_id: str, review_id: str) -> ReviewRecord | None:
        review = self.get_review(user_id, review_id)
        if review is None:
            return None
        updated = replace(review, query_count=review.query_count + 1)
        self.reviews[review_id] = updated
        return updated

    def transition_status(
        self, user_id: str, review_id: str, from_status: str, to_status: str
    ) -> ReviewRecord | None:
        review = self.get_review(user_id, review_id)
        if review is None or review.status != from_status:
            return None
        updated = replace(review, status=to_status)
        self.reviews[review_id] = updated
        self.transitions.append((from_status, to_status))
        return updated


class FakeReviewGraphRunner:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.resume_calls: list[dict] = []
        self._completed_reviews: set[str] = set()
        self.no_pending_approval = False
        self.ask_outcome = "answered"
        self.ask_error: Exception | None = None
        self.resume_error: Exception | None = None

    def ask(
        self,
        *,
        review: ReviewRecord,
        user_id: str,
        question: str,
        thread_id: str,
        review_target: dict,
    ) -> dict:
        self.calls.append(
            {
                "review": review,
                "user_id": user_id,
                "question": question,
                "thread_id": thread_id,
                "review_target": review_target,
            }
        )
        if self.ask_error is not None:
            raise self.ask_error
        return {"outcome": self.ask_outcome, "answer": "테스트 답변", "draft": []}

    def resume(
        self,
        *,
        review: ReviewRecord,
        user_id: str,
        decisions: list[dict],
        action: str,
    ) -> dict:
        if self.resume_error is not None:
            raise self.resume_error
        if self.no_pending_approval or review.id in self._completed_reviews:
            raise NotAwaitingApproval("대기 중인 승인 요청이 없습니다")
        self.resume_calls.append(
            {
                "review": review,
                "user_id": user_id,
                "decisions": decisions,
                "action": action,
            }
        )
        self._completed_reviews.add(review.id)
        if action == "cancel":
            return {"review_id": review.id, "approval_seq": 1, "status": "open"}
        return {
            "review_id": review.id,
            "approval_seq": 1,
            "memo_id": "memo-1",
            "status": "open",
        }

    def pending_approval(self, *, review: ReviewRecord, user_id: str) -> dict | None:
        if review.status != "awaiting_approval":
            return None
        return {
            "question": "소송 현황을 알려주세요.",
            "draft_items": [{
                "item": "litigation",
                "verdict": "needs_check",
                "reason": "소송 주석 확인 필요",
                "citations": [],
            }],
        }

    def ask_follow_up(self, *, review: ReviewRecord, user_id: str, question: str) -> dict:
        if self.ask_error:
            raise self.ask_error
        return {"outcome": "answered", "answer": question, "draft": []}


class RecordingCompiledGraph:
    def __init__(self) -> None:
        self.invocations: list[tuple[dict, dict]] = []

    def invoke(self, state: dict, config: dict) -> dict:
        self.invocations.append((state, config))
        return {"outcome": "answered", "draft_items": []}

    def get_state(self, _config: dict):
        return SimpleNamespace(tasks=())


@pytest.fixture
def repository() -> FakeReviewRepository:
    return FakeReviewRepository()


@pytest.fixture
def graph_runner() -> FakeReviewGraphRunner:
    return FakeReviewGraphRunner()


@pytest.fixture
def client(repository: FakeReviewRepository, graph_runner: FakeReviewGraphRunner) -> TestClient:
    app.dependency_overrides[get_review_repository] = lambda: repository
    app.dependency_overrides[get_review_graph_runner] = lambda: graph_runner
    app.dependency_overrides[current_user_id] = lambda: USER_A
    yield TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    app.dependency_overrides.clear()


class FinalStateGraph(RecordingCompiledGraph):
    """정해 둔 최종 State를 돌려주는 가짜 그래프. 실행기·라우트가 값을 어떻게 옮기는지만 본다."""

    def __init__(self, final: dict) -> None:
        super().__init__()
        self._final = final

    def invoke(self, state: dict, config: dict) -> dict:
        self.invocations.append((state, config))
        return dict(self._final)


RULE = {"rule_id": "memo-1:litigation", "item": "litigation", "instruction": "소송은 연결 기준", "source_memo_id": "memo-1"}


@pytest.mark.parametrize(
    ("final", "expected_rules"),
    [
        ({"outcome": "answered", "answer": "답", "draft_items": [], "applied_rules": [RULE]}, [RULE]),
        ({"outcome": "awaiting_approval", "draft_items": [], "applied_rules": [RULE]}, [RULE]),
        ({"outcome": "answered", "answer": "답", "draft_items": []}, []),  # 규칙 메모리 꺼짐(데모)
        ({"outcome": "refused", "refusal_reason": "OUT_OF_DOMAIN", "applied_rules": [RULE]}, []),
    ],
)
def test_query_response_always_carries_applied_rules(repository, final, expected_rules):
    """SPEC §3.3: applied_rules는 결과와 관계없이 항상 배열이고, 거절이면 빈 배열이다(review-detail-ui AC-17·18)."""

    app.dependency_overrides[get_review_repository] = lambda: repository
    app.dependency_overrides[get_review_graph_runner] = lambda: LangGraphReviewRunner(FinalStateGraph(final))
    app.dependency_overrides[current_user_id] = lambda: USER_A
    # 모듈 공용 분당 한도(_DEFAULT_GUARD)를 다른 시험과 나눠 쓰지 않게 이 시험만의 한도를 준다.
    guard = QueryGuard()
    app.dependency_overrides[get_query_guard] = lambda: guard
    try:
        response = _ask(TestClient(app, base_url="https://testserver", raise_server_exceptions=False))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json()["applied_rules"] == expected_rules


NEWS_ITEMS = [
    {"title": "뉴스 1", "description": "d", "link": "https://n/1", "originallink": "https://o1.kr/1",
     "pub_date": "Mon, 10 Nov 2026 09:00:00 +0900", "source_domain": "o1.kr"},
    {"title": "뉴스 2", "description": "d", "link": "https://n/2", "originallink": "https://o2.kr/2",
     "pub_date": "Mon, 10 Nov 2026 08:00:00 +0900", "source_domain": "o2.kr"},
]


def _ask_with_final(repository, final):
    app.dependency_overrides[get_review_repository] = lambda: repository
    app.dependency_overrides[get_review_graph_runner] = lambda: LangGraphReviewRunner(FinalStateGraph(final))
    app.dependency_overrides[current_user_id] = lambda: USER_A
    guard = QueryGuard()
    app.dependency_overrides[get_query_guard] = lambda: guard
    try:
        return _ask(TestClient(app, base_url="https://testserver", raise_server_exceptions=False))
    finally:
        app.dependency_overrides.clear()


def test_refused_response_carries_news_reference_fields(repository):
    final = {
        "outcome": "refused", "refusal_reason": "CORP_NOT_IN_CORPUS", "refusal_kind": "work_related",
        "news_items": NEWS_ITEMS, "news_search": "ok", "news_calls": 1, "citations": [], "draft_items": [],
        "refusal_message": "m",
    }

    response = _ask_with_final(repository, final)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "refused" and body["refusal_reason"] == "CORP_NOT_IN_CORPUS"
    assert body["refusal_kind"] == "work_related" and body["news_search"] == "ok"
    assert len(body["news_items"]) == 2 and body["news_items"][0]["source_domain"] == "o1.kr"
    assert body["applied_rules"] == []
    assert body["refusal_message"] == "m"  # 켜짐이면 고정 거절 멘트도 함께
    assert "news_calls" not in body and "citations" not in body  # 내부 필드는 싣지 않는다


def test_refused_unrelated_response_has_kind_and_empty_news(repository):
    final = {"outcome": "refused", "refusal_reason": "OUT_OF_DOMAIN", "refusal_kind": "unrelated",
             "news_items": [], "news_search": None, "news_calls": 0}

    body = _ask_with_final(repository, final).json()

    assert body["refusal_kind"] == "unrelated" and body["news_items"] == [] and body["news_search"] is None


def test_refused_response_is_unchanged_when_news_branch_is_off(repository):
    """꺼짐: State에 새 키가 없으니 응답 키 집합이 기존과 같다."""

    final = {"outcome": "refused", "refusal_reason": "OUT_OF_DOMAIN", "citations": [], "draft_items": []}

    body = _ask_with_final(repository, final).json()

    assert set(body) == {"outcome", "draft", "refusal_reason", "applied_rules"}


def test_refusal_message_is_not_exposed_when_news_branch_is_off(repository):
    """꺼짐: State에 거절 멘트가 있어도(기존 동작) 응답에는 싣지 않는다 — 기존 응답과 같게."""

    final = {"outcome": "refused", "refusal_reason": "OUT_OF_DOMAIN", "refusal_message": "기존 멘트"}

    body = _ask_with_final(repository, final).json()

    assert "refusal_message" not in body


def test_refusal_message_on_unrelated_response_is_sent_when_news_branch_is_on(repository):
    final = {"outcome": "refused", "refusal_reason": "OUT_OF_DOMAIN", "refusal_kind": "unrelated",
             "news_items": [], "news_search": None, "refusal_message": "고정 멘트"}

    body = _ask_with_final(repository, final).json()

    assert body["refusal_message"] == "고정 멘트"


@pytest.mark.parametrize("outcome", ["answered", "awaiting_approval"])
def test_news_fields_are_not_sent_on_non_refused_outcomes(repository, outcome):
    final = {"outcome": outcome, "draft_items": [], "refusal_kind": None, "news_items": [], "news_search": None,
             "news_calls": 0}

    body = _ask_with_final(repository, final).json()

    assert {"refusal_kind", "news_items", "news_search"}.isdisjoint(body)


def test_create_review_uses_authenticated_user_for_stored_thread(client, repository):
    response = client.post(
        "/api/v1/reviews",
        json={"corp_query": "테스트 주식회사", "fiscal_year": 2025},
    )

    assert response.status_code == 201
    assert response.json()["id"] == "30000000-0000-0000-0000-0000000000c3"
    assert repository.created[0].thread_id == (
        "00000000-0000-0000-0000-0000000000a1:30000000-0000-0000-0000-0000000000c3"
    )


def test_list_reviews_returns_only_authenticated_users_reviews(client):
    response = client.get("/api/v1/reviews?page_size=20")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [REVIEW_A]
    assert response.json()["next_cursor"] is None


def test_list_reviews_passes_sort_and_cursor_to_repository(client, repository, monkeypatch):
    cursor = f"name|{REVIEW_A}|가나다"

    def list_page(user_id, page_size, received_cursor, sort):
        assert (user_id, page_size, received_cursor, sort) == (USER_A, 20, cursor, "name")
        return ReviewPage(items=[repository.reviews[REVIEW_A]], next_cursor=None)

    monkeypatch.setattr(repository, "list_reviews", list_page)
    response = client.get("/api/v1/reviews", params={"sort": "name", "cursor": cursor})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [REVIEW_A]


@pytest.mark.parametrize(("sort", "cursor_clause"), [
    ("recent", "(r.created_at, r.id) <"),
    ("oldest", "(r.created_at, r.id) >"),
    ("name", 'COLLATE "C", r.id) >'),
])
def test_postgres_review_paging_uses_the_last_returned_key(monkeypatch, sort, cursor_clause):
    first = _review(review_id=REVIEW_A, user_id=USER_A)
    second = replace(first, id=REVIEW_B, corp_name="다른 기업")
    pages = iter(([asdict(first), asdict(second)], [asdict(second)]))
    statements = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            statements.append((sql, params))
            return self

        def fetchall(self):
            return next(pages)

    monkeypatch.setattr(PostgresReviewRepository, "_connect", lambda _self: Connection())
    repo = PostgresReviewRepository("test")
    page = repo.list_reviews(USER_A, 1, None, sort)

    assert [item.id for item in page.items] == [REVIEW_A]
    assert statements[0][1] == [USER_A, 2]
    assert page.next_cursor is not None
    next_page = repo.list_reviews(USER_A, 1, page.next_cursor, sort)
    assert [item.id for item in next_page.items] == [REVIEW_B]
    assert next_page.next_cursor is None
    sql, params = statements[1]
    assert "WHERE r.user_id = %s" in sql
    assert cursor_clause in sql
    keys = [first.corp_name, first.corp_name] if sort == "name" else [first.created_at]
    assert params == [USER_A, *keys, UUID(REVIEW_A), 2]

    with pytest.raises(ApiError) as error:
        repo.list_reviews(USER_A, 1, page.next_cursor, "oldest" if sort == "name" else "name")
    assert error.value.status_code == 400


def test_delete_own_review_removes_only_that_review(client, repository):
    response = client.delete(f"/api/v1/reviews/{REVIEW_A}")

    assert response.status_code == 204
    assert response.content == b""
    assert REVIEW_A not in repository.reviews
    assert REVIEW_B in repository.reviews


@pytest.mark.parametrize("review_id", [REVIEW_B, "40000000-0000-0000-0000-0000000000d4"])
def test_delete_hides_other_users_and_missing_reviews(client, repository, review_id):
    response = client.delete(f"/api/v1/reviews/{review_id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert len(repository.reviews) == 2


@pytest.mark.parametrize("status", ["running", "saving"])
def test_delete_rejects_review_in_progress(client, repository, status):
    repository.reviews[REVIEW_A] = replace(repository.reviews[REVIEW_A], status=status)

    response = client.delete(f"/api/v1/reviews/{REVIEW_A}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT_REVIEW_IN_PROGRESS"
    assert REVIEW_A in repository.reviews


def test_delete_all_reviews_clears_reviews_beyond_the_first_page(client, repository):
    for i in range(25):
        review_id = f"30000000-0000-0000-0000-{i:012d}"
        repository.reviews[review_id] = _review(review_id=review_id, user_id=USER_A)
    repository.reviews[REVIEW_B] = replace(repository.reviews[REVIEW_B], status="running")

    assert client.delete("/api/v1/reviews").status_code == 204
    assert client.delete("/api/v1/reviews").status_code == 204
    assert set(repository.reviews) == {REVIEW_B}


def test_delete_all_reviews_requires_login():
    with TestClient(app, base_url="https://testserver") as client:
        assert client.delete("/api/v1/reviews").status_code == 401


@pytest.mark.parametrize("bulk", [False, True])
def test_postgres_delete_removes_dependent_rows_in_one_transaction(monkeypatch, bulk):
    statements = []
    review_ids = [REVIEW_A, "30000000-0000-0000-0000-0000000000c3"] if bulk else [REVIEW_A]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            statements.append((sql, params))
            return self

        def fetchone(self):
            return {"id": REVIEW_A, "status": "closed"}

        def fetchall(self):
            return [{"id": review_id, "status": "closed"} for review_id in review_ids]

    connection = Connection()
    monkeypatch.setattr(PostgresReviewRepository, "_connect", lambda _self: connection)

    repository = PostgresReviewRepository("test")
    if bulk:
        repository.delete_all_reviews(USER_A)
        assert "WHERE user_id = %s ORDER BY id FOR UPDATE" in statements[0][0]
        assert statements[0][1] == (USER_A,)
    else:
        assert repository.delete_review(USER_A, REVIEW_A) is True
        assert "user_id = %s FOR UPDATE" in statements[0][0]
        assert statements[0][1] == (REVIEW_A, USER_A)
    assert ["review_rule_events", "review_memos", "reviews"] == [
        statement.split("DELETE FROM ", 1)[1].split(" ", 1)[0]
        for statement, _params in statements[1:]
    ]
    assert [params for _, params in statements[1:]] == [
        (review_ids,), (review_ids,), (review_ids, USER_A),
    ]


@pytest.mark.parametrize("status", ["running", "saving"])
def test_postgres_bulk_delete_checks_every_review_before_deleting(monkeypatch, status):
    statements = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            statements.append((sql, params))
            return self

        def fetchall(self):
            return [{"id": REVIEW_A, "status": "open"}, {"id": REVIEW_B, "status": status}]

    monkeypatch.setattr(PostgresReviewRepository, "_connect", lambda _self: Connection())

    with pytest.raises(ApiError, match="CONFLICT_REVIEW_IN_PROGRESS") as error:
        PostgresReviewRepository("test").delete_all_reviews(USER_A)
    assert error.value.status_code == 409
    assert len(statements) == 1


@pytest.mark.parametrize("review_id", [REVIEW_B, "40000000-0000-0000-0000-0000000000d4"])
def test_detail_hides_other_users_and_missing_reviews_with_same_not_found(client, review_id):
    response = client.get(f"/api/v1/reviews/{review_id}")

    assert response.status_code == 404


def test_detail_includes_pending_draft_only_for_awaiting_approval(client, repository):
    open_detail = client.get(f"/api/v1/reviews/{REVIEW_A}").json()
    assert open_detail["pending_question"] is None
    assert open_detail["pending_applied_rules"] == []
    repository.reviews[REVIEW_A] = replace(repository.reviews[REVIEW_A], status="awaiting_approval")

    response = client.get(f"/api/v1/reviews/{REVIEW_A}")

    assert response.status_code == 200
    assert response.json()["pending_question"] == "소송 현황을 알려주세요."
    assert response.json()["pending_applied_rules"] == []
    assert response.json()["pending_draft"] == [
        {
            "item": "litigation",
            "verdict": "needs_check",
            "reason": "소송 주석 확인 필요",
            "citations": [],
        }
    ]


@pytest.mark.parametrize("forbidden_field", ["thread_id", "user_id"])
def test_create_review_rejects_client_owned_identity_fields(client, forbidden_field):
    body = {"corp_query": "테스트 주식회사", "fiscal_year": 2025, forbidden_field: USER_B}

    response = client.post("/api/v1/reviews", json=body)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert USER_B not in response.text


def test_create_rejects_non_uuid_user_field_without_accepting_it(client):
    response = client.post(
        "/api/v1/reviews",
        json={"corp_query": "테스트 주식회사", "fiscal_year": 2025, "user_id": "not-a-user"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "not-a-user" not in response.text


def test_langgraph_runner_passes_server_target_and_thread_to_compiled_graph():
    graph = RecordingCompiledGraph()
    runner = LangGraphReviewRunner(graph)
    review = _review(review_id=REVIEW_A, user_id=USER_A)

    result = runner.ask(
        review=review,
        user_id=USER_A,
        question="차입금 만기 구조를 알려줘",
        thread_id=f"{USER_A}:{REVIEW_A}",
        review_target={
            "corp_code": "00126380",
            "corp_name": "테스트 주식회사",
            "fiscal_year": 2025,
        },
    )

    assert result == {"outcome": "answered", "draft": [], "applied_rules": []}
    assert graph.invocations == [
        (
            {
                "user_id": USER_A,
                "review_id": REVIEW_A,
                "question": "차입금 만기 구조를 알려줘",
                "review_target": {
                    "corp_code": "00126380",
                    "corp_name": "테스트 주식회사",
                    "fiscal_year": 2025,
                },
            },
            {"configurable": {"thread_id": f"{USER_A}:{REVIEW_A}"}},
        )
    ]


def test_query_uses_stored_review_and_server_generated_thread(client, repository, graph_runner):
    review_before_query = repository.reviews[REVIEW_A]

    response = client.post(
        f"/api/v1/reviews/{REVIEW_A}/queries",
        json={"question": "차입금 만기 구조를 알려줘"},
    )

    assert response.status_code == 200
    assert response.json() == {"outcome": "answered", "answer": "테스트 답변", "draft": [], "applied_rules": []}
    assert graph_runner.calls == [
        {
            "review": review_before_query,
            "user_id": USER_A,
            "question": "차입금 만기 구조를 알려줘",
            "thread_id": f"{USER_A}:{REVIEW_A}",
            "review_target": {
                "corp_code": "00126380",
                "corp_name": "테스트 주식회사",
                "fiscal_year": 2025,
            },
        }
    ]
    assert client.get(f"/api/v1/reviews/{REVIEW_A}").json()["query_count"] == 1


@pytest.mark.parametrize(
    ("record", "expected_status"),
    [
        (replace(_review(review_id=REVIEW_A, user_id=USER_A), status="closed"), 409),
        (replace(_review(review_id=REVIEW_A, user_id=USER_A), status="running"), 409),
        (replace(_review(review_id=REVIEW_A, user_id=USER_A), status="awaiting_approval"), 409),
    ],
)
def test_closed_or_non_open_status_does_not_run_graph(
    client, repository, graph_runner, record, expected_status
):
    repository.reviews[REVIEW_A] = record

    response = client.post(
        f"/api/v1/reviews/{REVIEW_A}/queries",
        json={"question": "차입금 만기 구조를 알려줘"},
    )

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == (
        "CONFLICT_REVIEW_CLOSED" if record.status == "closed" else "CONFLICT_REVIEW_NOT_OPEN"  # 닫힌 건은 별도 코드
    )
    assert graph_runner.calls == []
    assert repository.transitions == []  # 상태 검사에서 막히면 상태를 바꾸지 않는다
    assert repository.reviews[REVIEW_A].query_count == record.query_count


def test_query_hides_other_users_review_before_running_graph(client, graph_runner):
    response = client.post(
        f"/api/v1/reviews/{REVIEW_B}/queries",
        json={"question": "차입금 만기 구조를 알려줘"},
    )

    assert response.status_code == 404
    assert graph_runner.calls == []


DECISIONS = [{"item": "litigation", "verdict": "applicable", "reason": "소송 없음"}]


def test_approval_resumes_once_without_client_approver_or_sequence(client, repository, graph_runner):
    repository.reviews[REVIEW_A] = replace(repository.reviews[REVIEW_A], status="awaiting_approval")

    response = client.post(f"/api/v1/reviews/{REVIEW_A}/approvals", json={"decisions": DECISIONS})

    assert response.status_code == 200
    assert response.json() == {"review_id": REVIEW_A, "approval_seq": 1, "memo_id": "memo-1"}
    assert graph_runner.resume_calls == [
        {
            "review": replace(_review(review_id=REVIEW_A, user_id=USER_A), status="awaiting_approval"),
            "user_id": USER_A,
            "decisions": DECISIONS,
            "action": "approve",
        }
    ]
    assert client.get(f"/api/v1/reviews/{REVIEW_A}").json()["status"] == "closed"  # 승인이 끝나면 닫힌다

    replay = client.post(f"/api/v1/reviews/{REVIEW_A}/approvals", json={"decisions": DECISIONS})
    assert replay.status_code == 409
    assert len(graph_runner.resume_calls) == 1


def test_cancel_resumes_without_saving_memo(client, repository, graph_runner):
    repository.reviews[REVIEW_A] = replace(repository.reviews[REVIEW_A], status="awaiting_approval")

    response = client.post(f"/api/v1/reviews/{REVIEW_A}/approvals:cancel")

    assert response.status_code == 200
    assert response.json() == {"review_id": REVIEW_A, "approval_seq": 1, "status": "open"}
    assert graph_runner.resume_calls == [
        {
            "review": replace(_review(review_id=REVIEW_A, user_id=USER_A), status="awaiting_approval"),
            "user_id": USER_A,
            "decisions": [],
            "action": "cancel",
        }
    ]
    assert client.get(f"/api/v1/reviews/{REVIEW_A}").json()["status"] == "open"


def test_approval_hides_other_users_review_before_resume(client, graph_runner):
    response = client.post(f"/api/v1/reviews/{REVIEW_B}/approvals", json={"decisions": DECISIONS})

    assert response.status_code == 404
    assert graph_runner.resume_calls == []


def test_approval_maps_missing_graph_checkpoint_to_conflict(client, repository, graph_runner):
    repository.reviews[REVIEW_A] = replace(repository.reviews[REVIEW_A], status="awaiting_approval")
    graph_runner.no_pending_approval = True

    response = client.post(f"/api/v1/reviews/{REVIEW_A}/approvals", json={"decisions": DECISIONS})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT_NOT_AWAITING_APPROVAL"
    assert graph_runner.resume_calls == []


@pytest.mark.parametrize("forbidden_field", ["approved_by", "approval_seq", "user_id"])
def test_approval_rejects_client_owned_identity_and_sequence_fields(client, forbidden_field):
    body = {"decisions": DECISIONS, forbidden_field: USER_B}

    response = client.post(f"/api/v1/reviews/{REVIEW_A}/approvals", json=body)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert USER_B not in response.text


# ── 상태 전이: open → running → awaiting_approval → saving → closed(승인) 또는 open(취소) (SPEC §3·§4) ──────────

def _ask(client, review_id=REVIEW_A):
    return client.post(f"/api/v1/reviews/{review_id}/queries", json={"question": "차입금 만기 구조를 알려줘"})


def test_answered_query_moves_open_to_running_and_back_to_open(client, repository, graph_runner):
    response = _ask(client)

    assert response.status_code == 200
    assert repository.transitions == [("open", "running"), ("running", "open")]
    assert repository.reviews[REVIEW_A].status == "open"
    assert repository.reviews[REVIEW_A].query_count == 1


def test_query_that_waits_for_approval_leaves_review_awaiting_approval(client, repository, graph_runner):
    graph_runner.ask_outcome = "awaiting_approval"

    assert _ask(client).status_code == 200

    assert repository.transitions == [("open", "running"), ("running", "awaiting_approval")]
    assert repository.reviews[REVIEW_A].status == "awaiting_approval"
    detail = client.get(f"/api/v1/reviews/{REVIEW_A}").json()
    assert detail["status"] == "awaiting_approval" and detail["pending_draft"] is not None


def test_full_flow_query_then_approval_closes_the_review(client, repository, graph_runner):
    graph_runner.ask_outcome = "awaiting_approval"
    assert _ask(client).status_code == 200

    approved = client.post(f"/api/v1/reviews/{REVIEW_A}/approvals", json={"decisions": DECISIONS})

    assert approved.status_code == 200
    assert repository.transitions == [
        ("open", "running"),
        ("running", "awaiting_approval"),
        ("awaiting_approval", "saving"),
        ("saving", "closed"),
    ]
    assert repository.reviews[REVIEW_A].status == "closed"
    graph_runner.ask_outcome = "answered"
    blocked = _ask(client)  # 승인이 끝난 건에는 더 질의할 수 없다(SPEC §4). 새 심사 건을 만들어야 한다
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "CONFLICT_REVIEW_CLOSED"
    assert len(graph_runner.calls) == 1


def test_cancelled_approval_returns_to_open_and_can_ask_again(client, repository, graph_runner):
    graph_runner.ask_outcome = "awaiting_approval"
    assert _ask(client).status_code == 200

    cancelled = client.post(f"/api/v1/reviews/{REVIEW_A}/approvals:cancel")

    assert cancelled.status_code == 200
    assert repository.transitions[-1] == ("saving", "open")  # 취소는 메모를 저장하지 않으므로 닫지 않는다
    graph_runner.ask_outcome = "answered"
    assert _ask(client).status_code == 200


def test_new_query_cannot_overwrite_a_pending_approval(client, repository, graph_runner):
    graph_runner.ask_outcome = "awaiting_approval"
    assert _ask(client).status_code == 200
    assert len(graph_runner.calls) == 1

    second = _ask(client)

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CONFLICT_REVIEW_NOT_OPEN"
    assert len(graph_runner.calls) == 1  # 그래프를 다시 부르지 않아 interrupt가 그대로 남는다
    assert repository.reviews[REVIEW_A].status == "awaiting_approval"


def test_follow_up_answers_without_replacing_pending_approval(client, repository, graph_runner):
    graph_runner.ask_outcome = "awaiting_approval"
    assert _ask(client).status_code == 200
    detail_path = f"/api/v1/reviews/{REVIEW_A}"
    before = client.get(detail_path).json()

    response = client.post(detail_path + "/queries:follow-up", json={"question": "담보 내역도 알려주세요."})

    assert response.status_code == 200
    assert response.json()["answer"] == "담보 내역도 알려주세요."
    after = client.get(detail_path).json()
    assert after["status"] == "awaiting_approval"
    assert after["query_count"] == before["query_count"] + 1
    assert after["pending_draft"] == before["pending_draft"]
    assert after["pending_question"] == before["pending_question"]
    assert graph_runner.resume_calls == []
    assert repository.transitions[-2:] == [("awaiting_approval", "running"), ("running", "awaiting_approval")]

    graph_runner.ask_error = RuntimeError("test failure")
    assert client.post(detail_path + "/queries:follow-up", json={"question": "추가 근거는?"}).status_code == 500
    assert client.get(detail_path).json()["pending_draft"] == before["pending_draft"]
    assert client.post(f"/api/v1/reviews/{REVIEW_B}/queries:follow-up", json={"question": "추가 근거는?"}).status_code == 404


@pytest.mark.parametrize(
    ("question", "reason", "quote", "expected"),
    [
        ("2025년 말 담보 장부금액은?", "장부금액은 474,746,299천원입니다. 다만 당기/전기 구분이 명확하지 않아 확인이 필요합니다.",
         "담보제공처 합계 | 474,746,299", "needs_check"),
        ("2025년 말 담보 장부금액은?", "장부금액은 474,746,299천원입니다.",
         "당기 | (단위 : 천원) 합계 | 435,804,698 전기 | (단위 : 천원) 합계 | 474,746,299", "needs_check"),
        ("2025년 말 담보 장부금액은?", "장부금액은 435,804,698천원입니다.",
         "당기 | (단위 : 천원) 합계 | 435,804,698 전기 | (단위 : 천원) 합계 | 474,746,299", "applicable"),
        ("2025년 말과 전기를 비교해 주세요.", "전기 장부금액은 474,746,299천원입니다.",
         "전기 | (단위 : 천원) 합계 | 474,746,299", "applicable"),
    ],
)
def test_follow_up_withholds_unconfirmed_period_amount(client, graph_runner, monkeypatch, question, reason, quote, expected):
    graph_runner.ask_outcome = "awaiting_approval"
    assert _ask(client).status_code == 200
    path = f"/api/v1/reviews/{REVIEW_A}"
    pending = client.get(path).json()["pending_draft"]
    citations = [{"quote": quote, "verification": {"passed": True}}]
    item = {"item": question, "verdict": "applicable", "reason": reason, "citations": citations}
    monkeypatch.setattr(graph_runner, "ask_follow_up", lambda **_: {"outcome": "answered", "draft": [item]})

    result = client.post(path + "/queries:follow-up", json={"question": question})

    assert result.status_code == 200
    draft, = result.json()["draft"]
    assert draft["verdict"] == expected
    assert draft["citations"] == citations
    if expected == "needs_check":
        assert "금액을 확정하지 않았습니다" in draft["reason"]
        assert "474,746,299" not in draft["reason"]
    else:
        assert draft["reason"] == reason
    assert item["verdict"] == "applicable"
    assert client.get(path).json()["pending_draft"] == pending


def test_concurrent_query_losing_the_open_to_running_race_does_not_run_graph(client, repository, graph_runner):
    # get_review로 open을 읽은 뒤 다른 요청이 먼저 상태를 바꾼 경우(전이가 실패)
    repository.transition_status = lambda *_args, **_kwargs: None

    response = _ask(client)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT_REVIEW_NOT_OPEN"
    assert graph_runner.calls == []
    assert repository.reviews[REVIEW_A].query_count == 0


def test_query_failure_releases_running_and_does_not_count(client, repository, graph_runner):
    graph_runner.ask_error = RuntimeError("graph exploded")

    response = _ask(client)

    assert response.status_code == 500
    assert "graph exploded" not in response.text
    assert repository.transitions == [("open", "running"), ("running", "open")]
    assert repository.reviews[REVIEW_A].status == "open"
    assert repository.reviews[REVIEW_A].query_count == 0


def test_concurrent_approval_losing_the_saving_race_does_not_resume(client, repository, graph_runner):
    repository.reviews[REVIEW_A] = replace(repository.reviews[REVIEW_A], status="awaiting_approval")
    repository.transition_status = lambda *_args, **_kwargs: None

    response = client.post(f"/api/v1/reviews/{REVIEW_A}/approvals", json={"decisions": DECISIONS})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT_NOT_AWAITING_APPROVAL"
    assert graph_runner.resume_calls == []


def test_approval_with_bad_decisions_returns_400_and_keeps_waiting(client, repository, graph_runner):
    from backend.graph.approval import ApprovalInputError

    repository.reviews[REVIEW_A] = replace(repository.reviews[REVIEW_A], status="awaiting_approval")
    graph_runner.resume_error = ApprovalInputError("초안에 없는 항목입니다: 'x'")

    response = client.post(f"/api/v1/reviews/{REVIEW_A}/approvals", json={"decisions": DECISIONS})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "초안에 없는 항목" not in response.text
    assert repository.reviews[REVIEW_A].status == "awaiting_approval"  # 다시 보낼 수 있다


def test_approval_server_failure_returns_to_awaiting_approval(client, repository, graph_runner):
    repository.reviews[REVIEW_A] = replace(repository.reviews[REVIEW_A], status="awaiting_approval")
    graph_runner.resume_error = RuntimeError("db down")

    response = client.post(f"/api/v1/reviews/{REVIEW_A}/approvals", json={"decisions": DECISIONS})

    assert response.status_code == 500
    assert repository.transitions == [("awaiting_approval", "saving"), ("saving", "awaiting_approval")]
    assert repository.reviews[REVIEW_A].status == "awaiting_approval"


def test_stale_awaiting_status_without_checkpoint_becomes_open_with_conflict(client, repository, graph_runner):
    repository.reviews[REVIEW_A] = replace(repository.reviews[REVIEW_A], status="awaiting_approval")
    graph_runner.no_pending_approval = True

    response = client.post(f"/api/v1/reviews/{REVIEW_A}/approvals", json={"decisions": DECISIONS})

    assert response.status_code == 409
    assert repository.reviews[REVIEW_A].status == "open"


# ── 오류 코드는 SPEC §6과 같다 ───────────────────────────────────────────────

def test_hidden_review_uses_the_common_not_found_code(client):
    for path, method in (
        (f"/api/v1/reviews/{REVIEW_B}", "get"),
        (f"/api/v1/reviews/{REVIEW_B}/queries", "post"),
        (f"/api/v1/reviews/{REVIEW_B}/approvals:cancel", "post"),
    ):
        response = getattr(client, method)(path, **({"json": {"question": "q"}} if path.endswith("queries") else {}))
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND", path


def test_missing_database_url_is_service_unavailable_not_internal_error(monkeypatch):
    from backend.api.reviews import get_review_repository

    monkeypatch.delenv("DATABASE_URL", raising=False)
    app.dependency_overrides[current_user_id] = lambda: USER_A
    try:
        response = TestClient(app, base_url="https://testserver", raise_server_exceptions=False).get(
            "/api/v1/reviews"
        )
    finally:
        app.dependency_overrides.clear()

    assert get_review_repository.__name__ == "get_review_repository"
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE_DB"


# ── 가짜 러너가 아닌 실제 LangGraph + MemorySaver로 같은 흐름 ─────────────────

@pytest.mark.parametrize("direct", [False, True])
def test_real_graph_full_flow_query_pending_approval_cannot_be_overwritten(repository, direct):
    """가짜 저장소에 status를 미리 넣어 통과하던 시험을 대신한다: 상태는 API가 직접 옮긴다."""
    from langgraph.checkpoint.memory import MemorySaver

    from backend.api.reviews import LangGraphReviewRunner
    from backend.graph.build import build_graph
    from backend.graph.refusal import CorpusCatalog, CorpusTarget

    catalog = CorpusCatalog(
        corp_names=frozenset({"테스트 주식회사"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
        targets=frozenset({CorpusTarget(corp_name="테스트 주식회사", corp_code="00126380", fiscal_year=2025)}),
    )
    chunk = {
        "chunk_id": "chunk-1",
        "corp_code": "00126380",
        "fiscal_year": 2025,
        "content": "차입금 관련 공시 내용. 계류 중인 소송 없음",
        "rcept_no": "20260304000913",
        "chapter": "NOTES",
        "note_no": "1",
    }
    memos: dict[tuple, str] = {}
    retrievals = []
    rules = [{"rule_id": "memo-1:borrowing_structure", "item": "borrowing_structure",
              "instruction": "차입금은 연결 기준", "source_memo_id": "memo-1"}]
    expected_rules = rules.copy()
    rule_searches = []
    writer_rules = []

    def memo_saver(_record, key):
        return memos.setdefault(key, f"memo-{len(memos) + 1}")

    def search_rules(user_id, question):
        rule_searches.append((user_id, question))
        return rules

    def draft_writer(state, _chunks):
        writer_rules.append(state["applied_rules"])
        return [
            {"item": "litigation", "verdict": "not_applicable", "reason": "계류 중인 소송 없음", "chunk_ids": ["chunk-1"]},
            {"item": "borrowing_structure", "verdict": "applicable", "reason": "차입금 공시 있음", "chunk_ids": ["chunk-1"]},
        ]

    graph = build_graph(
        retriever=lambda _state: retrievals.append(1) or [chunk],
        draft_writer=draft_writer,
        corpus_catalog=catalog,
        domain_check_enabled=False,
        checkpointer=MemorySaver(),
        memo_saver=memo_saver,
        memory_store=SimpleNamespace(search_rules=search_rules, save_edited_rules=lambda *_args: None),
        approval_enabled=True,
    )
    app.dependency_overrides[get_review_repository] = lambda: repository
    app.dependency_overrides[get_review_graph_runner] = lambda: LangGraphReviewRunner(graph)
    app.dependency_overrides[get_question_target_resolver] = lambda: lambda _question: {
        "corp_code": "00126380", "fiscal_year": 2025,
    }
    app.dependency_overrides[current_user_id] = lambda: USER_A
    client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    try:
        question = "테스트 주식회사의 2025년 말 차입금 만기 구조를 알려줘"
        ask_path = "/api/v1/queries" if direct else f"/api/v1/reviews/{REVIEW_A}/queries"
        asked = client.post(ask_path, json={"question": question})
        assert asked.status_code == 200, asked.text
        assert asked.json()["outcome"] == "awaiting_approval"
        assert asked.json()["applied_rules"] == expected_rules
        assert writer_rules and all(applied == expected_rules for applied in writer_rules)
        review_id = asked.json()["review"]["id"] if direct else REVIEW_A
        review_path = f"/api/v1/reviews/{review_id}"
        assert repository.reviews[review_id].status == "awaiting_approval"
        rules.clear()
        detail = client.get(review_path).json()
        assert detail["pending_applied_rules"] == expected_rules
        assert rule_searches == [(USER_A, question)]
        # 질문이 한 체크리스트 주제를 분명히 가리키면 그 코드로 항목을 붙인다(checklist.infer_checklist_item)
        pending_items = [d["item"] for d in detail["pending_draft"]]
        assert pending_items == ["borrowing_structure"]

        # 승인 대기 중 새 질의: 409이고 그래프도 검색도 다시 돌지 않으며 대기는 그대로다
        searches_before = len(retrievals)
        blocked = client.post(f"{review_path}/queries", json={"question": question})
        assert blocked.status_code == 409
        assert len(retrievals) == searches_before
        assert client.get(review_path).json()["pending_draft"] is not None

        decisions = [{"item": item, "verdict": "applicable", "reason": "차입금 공시 있음"} for item in pending_items]
        approved = client.post(f"{review_path}/approvals", json={"decisions": decisions})
        assert approved.status_code == 200, approved.text
        assert approved.json()["memo_id"] == "memo-1"
        assert repository.reviews[review_id].status == "closed"
        assert client.get(review_path).json()["pending_applied_rules"] == []
        assert repository.transitions == [
            ("open", "running"),
            ("running", "awaiting_approval"),
            ("awaiting_approval", "saving"),
            ("saving", "closed"),
        ]

        replay = client.post(f"{review_path}/approvals", json={"decisions": decisions})
        assert replay.status_code == 409
        assert len(memos) == 1
    finally:
        app.dependency_overrides.clear()
