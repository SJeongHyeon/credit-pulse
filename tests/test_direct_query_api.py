"""바로 질문 API(POST /api/v1/queries) 계약. 회사를 고르지 않고 질문만 보낸다(SPEC §3.3).

외부 DB·LLM 없이 실제 FastAPI 경계를 시험한다. 대상 찾기는 저장소의 실제 코퍼스 목록(data/manifest.jsonl)으로 본다.
"""

from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.api.limits import QueryGuard
from backend.api.reviews import (
    LangGraphReviewRunner,
    ReviewRecord,
    direct_scope_id,
    get_query_guard,
    get_question_target_resolver,
    get_review_graph_runner,
    get_review_repository,
)
from backend.api.runtime import CORPUS_MANIFEST, question_target_resolver
from backend.auth import current_user_id
from backend.errors import ApiError
from backend.graph.refusal import load_corpus_catalog
from backend.main import app

USER = "00000000-0000-0000-0000-0000000000a1"
NEW_REVIEW = "30000000-0000-0000-0000-0000000000c3"
TARGET = {"corp_code": "00126380", "corp_name": "테스트 주식회사", "fiscal_year": 2025}


class Repository:
    """만든 심사 건·지운 심사 건·상태 전이를 적어 두는 가짜 저장소."""

    def __init__(self) -> None:
        self.reviews: dict[str, ReviewRecord] = {}
        self.created: list[tuple[str, int]] = []
        self.deleted: list[str] = []

    def create_review(self, user_id: str, corp_query: str, fiscal_year: int) -> ReviewRecord:
        self.created.append((corp_query, fiscal_year))
        now = datetime(2026, 11, 13, tzinfo=timezone.utc)
        review = ReviewRecord(
            id=NEW_REVIEW, user_id=user_id, thread_id=f"{user_id}:{NEW_REVIEW}", corp_code=corp_query,
            corp_name=TARGET["corp_name"], fiscal_year=fiscal_year, status="open", query_count=0,
            created_at=now, updated_at=now,
        )
        self.reviews[review.id] = review
        return review

    def get_review(self, user_id: str, review_id: str) -> ReviewRecord | None:
        return self.reviews.get(review_id)

    def delete_review(self, user_id: str, review_id: str) -> bool:
        self.deleted.append(review_id)
        return self.reviews.pop(review_id, None) is not None

    def transition_status(self, user_id: str, review_id: str, from_status: str, to_status: str) -> ReviewRecord | None:
        review = self.reviews.get(review_id)
        if review is None or review.status != from_status:
            return None
        self.reviews[review_id] = replace(review, status=to_status)
        return self.reviews[review_id]

    def increment_query_count(self, user_id: str, review_id: str) -> ReviewRecord | None:
        review = self.reviews.get(review_id)
        if review is not None:
            self.reviews[review_id] = replace(review, query_count=review.query_count + 1)
        return self.reviews.get(review_id)


class Runner:
    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self.result = result or {}
        self.error = error
        self.review_calls: list[dict] = []
        self.direct_calls: list[dict] = []

    def ask(self, **kwargs) -> dict:
        self.review_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return dict(self.result)

    def ask_without_review(self, **kwargs) -> dict:
        self.direct_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return dict(self.result)


class Guard(QueryGuard):
    def __init__(self) -> None:
        super().__init__()
        self.scopes: list[str] = []

    def before_query(self, user_id: str, review_id: str) -> None:
        self.scopes.append(review_id)
        super().before_query(user_id, review_id)


def _post(repository, runner, resolver, guard=None, question="테스트 주식회사의 2025년 차입금은?", clarifies=None):
    guard = guard or Guard()
    app.dependency_overrides[get_review_repository] = lambda: repository
    app.dependency_overrides[get_review_graph_runner] = lambda: runner
    app.dependency_overrides[get_question_target_resolver] = lambda: resolver
    app.dependency_overrides[get_query_guard] = lambda: guard
    app.dependency_overrides[current_user_id] = lambda: USER
    try:
        client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
        body = {"question": question} if clarifies is None else {"question": question, "clarifies": clarifies}
        return client.post("/api/v1/queries", json=body)
    finally:
        app.dependency_overrides.clear()


def test_resolved_question_creates_a_review_and_stops_for_approval():
    """대상이 정해지고 승인 대기로 끝나면 심사 건이 남고, 응답이 그 심사 건을 알려 준다."""

    repository, guard = Repository(), Guard()
    runner = Runner({"outcome": "awaiting_approval", "draft": [{"item": "borrowings"}], "applied_rules": []})

    response = _post(repository, runner, lambda question: dict(TARGET), guard)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "awaiting_approval"
    assert body["review"] == {"id": NEW_REVIEW, "corp_name": "테스트 주식회사", "fiscal_year": 2025}
    assert repository.created == [("00126380", 2025)]
    assert repository.deleted == []
    assert repository.reviews[NEW_REVIEW].status == "awaiting_approval"
    assert repository.reviews[NEW_REVIEW].query_count == 1
    # 그래프에는 대상을 주지 않는다: 그래프가 질문에서 다시 찾고, 회사 비교 질문은 회사별로 검색한다.
    assert runner.review_calls[0]["review_target"] is None
    assert runner.direct_calls == []
    assert guard.scopes == [NEW_REVIEW]


def test_resolved_question_that_is_refused_leaves_no_review():
    """검색 뒤 거절(근거 없음·공시 안 됨·주제 밖)로 끝난 질문은 빈 심사 건을 남기지 않는다."""

    repository = Repository()
    runner = Runner({"outcome": "refused", "refusal_reason": "ITEM_NOT_DISCLOSED", "draft": []})

    response = _post(repository, runner, lambda question: dict(TARGET))

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["outcome"], body["refusal_reason"], body["review"]) == ("refused", "ITEM_NOT_DISCLOSED", None)
    assert body["applied_rules"] == []
    assert repository.deleted == [NEW_REVIEW]
    assert repository.reviews == {}


def test_unresolved_question_runs_without_a_review():
    """코퍼스 밖 회사·기간, 회사 이름이 없는 질문: 심사 건을 만들지 않고 그래프의 거절·되묻기를 그대로 준다."""

    repository, guard = Repository(), Guard()
    runner = Runner({"outcome": "answered", "draft": [], "refusal_message": "회사명과 사업연도를 포함해 다시 질문해 주세요.",
                     "target_required": True})

    response = _post(repository, runner, lambda question: None, guard, question="차입금이 얼마인가요?")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["review"] is None
    assert body["target_required"] is True
    assert body["refusal_message"] == "회사명과 사업연도를 포함해 다시 질문해 주세요."
    assert repository.created == []
    assert runner.direct_calls == [{"user_id": USER, "question": "차입금이 얼마인가요?"}]
    assert runner.review_calls == []
    assert guard.scopes == [direct_scope_id(USER)]


def test_failed_question_removes_the_review_it_created():
    """외부 호출 실패로 답을 내지 못하면 방금 만든 심사 건을 지우고 오류를 그대로 돌려준다."""

    repository = Repository()
    runner = Runner(error=ApiError(503, "SERVICE_UNAVAILABLE_GRAPH", "심사 그래프가 준비되지 않았습니다"))

    response = _post(repository, runner, lambda question: dict(TARGET))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE_GRAPH"
    assert repository.deleted == [NEW_REVIEW]


def test_direct_query_is_rate_limited_like_a_review_query():
    """분당 5회 한도는 바로 질문에도 걸린다(대상이 정해지지 않은 질문 포함)."""

    repository, guard = Repository(), Guard()
    runner = Runner({"outcome": "refused", "refusal_reason": "CORP_NOT_IN_CORPUS", "draft": []})

    statuses = [_post(repository, runner, lambda question: None, guard).status_code for _ in range(6)]

    assert statuses[:5] == [200] * 5
    assert statuses[5] == 429


def test_question_body_is_validated():
    response = _post(Repository(), Runner(), lambda question: None, question="")

    assert response.status_code == 400


class FinalStateGraph:
    def __init__(self, final: dict) -> None:
        self._final = final
        self.invocations: list[tuple[dict, dict]] = []

    def invoke(self, state: dict, config: dict) -> dict:
        self.invocations.append((state, config))
        return dict(self._final)


def test_runner_without_review_passes_the_refusal_message_and_uses_a_throwaway_thread():
    graph = FinalStateGraph({
        "outcome": "refused", "refusal_reason": "CORP_NOT_IN_CORPUS",
        "refusal_message": "요청한 회사는 현재 코퍼스에 없습니다.", "draft_items": [],
    })

    result = LangGraphReviewRunner(graph).ask_without_review(user_id=USER, question="없는회사의 2025년 차입금은?")

    assert result["refusal_reason"] == "CORP_NOT_IN_CORPUS"
    assert result["refusal_message"] == "요청한 회사는 현재 코퍼스에 없습니다."
    state, config = graph.invocations[0]
    assert "review_target" not in state and "review_id" not in state
    assert config["configurable"]["thread_id"].startswith("direct-")


def test_runner_without_review_refuses_to_leave_an_approval_nobody_can_resume():
    graph = FinalStateGraph({"outcome": "awaiting_approval", "draft_items": [{"item": "borrowings"}]})

    with pytest.raises(RuntimeError):
        LangGraphReviewRunner(graph).ask_without_review(user_id=USER, question="질문")


def test_runner_with_review_omits_the_target_for_a_direct_question():
    graph = FinalStateGraph({"outcome": "awaiting_approval", "draft_items": []})
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    review = ReviewRecord(id=NEW_REVIEW, user_id=USER, thread_id="t", corp_code="00126380", corp_name="테스트 주식회사",
                          fiscal_year=2025, status="open", query_count=0, created_at=now, updated_at=now)

    LangGraphReviewRunner(graph).ask(review=review, user_id=USER, question="질문", thread_id="t", review_target=None)

    state, _ = graph.invocations[0]
    assert "review_target" not in state
    assert state["review_id"] == NEW_REVIEW


@pytest.fixture(scope="module")
def resolve():
    return question_target_resolver(load_corpus_catalog(CORPUS_MANIFEST))


def test_resolver_finds_a_company_and_year_named_in_the_question(resolve):
    target = resolve("오뚜기의 2025년 말 단기차입금은 얼마인가요?")

    assert target is not None
    assert target["corp_name"] == "오뚜기" and target["fiscal_year"] == 2025
    assert len(target["corp_code"]) == 8


@pytest.mark.parametrize("question", [
    "단기차입금이 얼마인가요?",  # 회사 이름 없음
    "두 회사의 차입금 구성을 비교해 주세요.",  # 회사 이름 없는 비교
    "오뚜기의 2019년 매출액은 얼마인가요?",  # 코퍼스 밖 기간
])
def test_resolver_returns_none_when_the_graph_would_refuse_or_ask_back(resolve, question):
    assert resolve(question) is None


# 162번: 되물음에 대한 답을 원래 질문과 합쳐 다시 묻는다
ASKED_BACK = "두 회사의 차입금 구성을 비교해 주세요."


def test_clarification_reply_is_joined_to_the_question_that_was_asked_back():
    """회사 이름만 답하면 '답 + 원래 질문'으로 대상을 찾고 그래프에 넘긴다."""

    repository, runner, seen = Repository(), Runner({"outcome": "answered", "draft": []}), []

    def resolver(question):
        seen.append(question)
        return None

    response = _post(repository, runner, resolver, question="오뚜기와 대상, 2025 사업연도", clarifies=ASKED_BACK)

    assert response.status_code == 200, response.text
    joined = f"오뚜기와 대상, 2025 사업연도 {ASKED_BACK}"
    assert seen == [joined]
    assert runner.direct_calls == [{"user_id": USER, "question": joined}]


def test_clarification_reply_that_resolves_a_target_runs_in_a_review():
    repository = Repository()
    runner = Runner({"outcome": "awaiting_approval", "draft": [], "applied_rules": []})

    response = _post(repository, runner, lambda question: dict(TARGET), question="테스트 주식회사 2025", clarifies="차입금은 얼마야")

    assert response.status_code == 200, response.text
    assert response.json()["review"]["id"] == NEW_REVIEW
    assert [call["question"] for call in runner.review_calls] == ["테스트 주식회사 2025 차입금은 얼마야"]


def test_a_new_full_question_ignores_the_question_that_was_asked_back():
    """되물음을 받은 뒤 새 질문을 통째로 적으면(물음표가 있음) 합치지 않는다."""

    runner = Runner({"outcome": "answered", "draft": []})

    _post(Repository(), runner, lambda question: None, question="오뚜기의 2025년 단기차입금은 얼마인가요?", clarifies=ASKED_BACK)

    assert runner.direct_calls == [{"user_id": USER, "question": "오뚜기의 2025년 단기차입금은 얼마인가요?"}]


def test_joined_question_over_the_length_limit_is_rejected_before_the_graph():
    runner = Runner({"outcome": "answered", "draft": []})

    response = _post(Repository(), runner, lambda question: None, question="가" * 80, clarifies="나" * 1000)

    assert response.status_code == 422
    assert runner.direct_calls == [] and runner.review_calls == []


@pytest.mark.parametrize("original, reply, expected", [
    ("두 회사를 비교해 주세요.", "  오뚜기와   대상 ", "오뚜기와 대상 두 회사를 비교해 주세요."),
    ("두 회사를 비교해 주세요.", "오뚜기의 차입금은?", "오뚜기의 차입금은?"),
    ("두 회사를 비교해 주세요.", "가" * 81, "가" * 81),
    ("  ", "오뚜기", "오뚜기"),
])
def test_clarified_question_rule(original, reply, expected):
    from backend.graph.refusal import clarified_question

    assert clarified_question(original, reply) == expected
