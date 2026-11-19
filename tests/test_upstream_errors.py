"""외부 API 장애를 SPEC §6 응답으로 구분한다(11/7 팀 키 한도 초과 403이 500으로 보인 문제).

1) llm.py가 openai·anthropic·voyageai·httpx 예외를 자기 예외로 바꾼다(AGENTS.md 2번).
2) API가 그 예외를 503·502·504로 바꾼다. 실제 외부 호출은 하지 않는다.
"""
import types

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from backend.errors import upstream_api_error
from backend.services import llm

REQUEST = httpx.Request("POST", "https://example.invalid/v1")


def _response(status):
    return httpx.Response(status, request=REQUEST)


@pytest.mark.parametrize("exc, expected", [
    (openai.PermissionDeniedError("한도 초과", response=_response(403), body=None), (llm.ExternalApiStatusError, 403)),
    (openai.APIStatusError("크레딧 없음", response=_response(402), body=None), (llm.ExternalApiStatusError, 402)),
    (openai.APITimeoutError(request=REQUEST), (llm.ExternalApiTimeout, None)),
    (openai.APIConnectionError(request=REQUEST), (llm.ExternalApiStatusError, 503)),
    (httpx.ReadTimeout("느림"), (llm.ExternalApiTimeout, None)),
    (httpx.ConnectError("끊김"), (llm.ExternalApiStatusError, 503)),
])
def test_sdk_errors_become_llm_errors(exc, expected):
    mapped = llm._sdk_error(exc)
    assert isinstance(mapped, expected[0])
    if expected[1] is not None:
        assert mapped.status_code == expected[1]


def test_voyage_errors_become_llm_errors():
    import voyageai.error as voyage_errors

    assert llm._sdk_error(voyage_errors.RateLimitError("많음")).status_code == 429
    assert llm._sdk_error(voyage_errors.AuthenticationError("키")).status_code == 401
    assert llm._sdk_error(voyage_errors.APIError("크레딧", http_status=402)).status_code == 402
    assert isinstance(llm._sdk_error(voyage_errors.Timeout("느림")), llm.ExternalApiTimeout)
    assert llm._sdk_error(ValueError("우리 버그")) is None  # 외부 오류가 아니면 바꾸지 않는다


def test_embedding_permission_error_does_not_escape_llm(monkeypatch):
    """11/7: 임베딩만 openai 예외를 그대로 내보냈다(채팅은 감쌌음)."""

    class Embeddings:
        def create(self, **kwargs):
            raise openai.PermissionDeniedError("워크스페이스 한도", response=_response(403), body=None)

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.embeddings = Embeddings()

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    with pytest.raises(llm.ExternalApiStatusError) as error:
        llm.openai_embeddings(["질문"], model="openai/text-embedding-3-small", dimensions=1536)
    assert error.value.status_code == 403


@pytest.mark.parametrize("exc, status, code", [
    (llm.ExternalApiStatusError(403, "PermissionDeniedError"), 503, "SERVICE_UNAVAILABLE_UPSTREAM"),
    (llm.ExternalApiStatusError(402, "x"), 503, "SERVICE_UNAVAILABLE_UPSTREAM"),
    (llm.ExternalApiStatusError(429, "x"), 503, "SERVICE_UNAVAILABLE_UPSTREAM"),
    (llm.ExternalApiStatusError(502, "x"), 503, "SERVICE_UNAVAILABLE_UPSTREAM"),
    (llm.ExternalApiKeyMissing("키 없음"), 503, "SERVICE_UNAVAILABLE_UPSTREAM"),
    (llm.ExternalApiResponseError("형식"), 502, "UPSTREAM_LLM_BAD_OUTPUT"),
    (llm.ExternalApiTimeout("느림"), 504, "UPSTREAM_LLM_TIMEOUT"),
])
def test_upstream_errors_map_to_spec_codes(exc, status, code):
    mapped = upstream_api_error(exc)
    assert (mapped.status_code, mapped.code) == (status, code)
    assert "key" not in mapped.message.lower()


def test_our_request_bugs_stay_internal_errors():
    assert upstream_api_error(llm.ExternalApiStatusError(400, "x")) is None
    assert upstream_api_error(llm.ExternalApiStatusError(422, "x")) is None
    assert upstream_api_error(ValueError("x")) is None


def test_api_query_with_exhausted_key_is_503_and_releases_review():
    from backend.api.limits import QueryGuard
    from backend.api.reviews import get_query_guard, get_review_graph_runner, get_review_repository
    from backend.auth import current_user_id
    from backend.main import app
    from tests.test_reviews_api import FakeReviewRepository

    user = "00000000-0000-0000-0000-000000000001"

    class ExhaustedRunner:
        def ask(self, **_):
            raise llm.ExternalApiStatusError(403, "PermissionDeniedError")

    repository = FakeReviewRepository()
    review = repository.create_review(user, "테스트 주식회사", 2025)
    app.dependency_overrides[get_review_repository] = lambda: repository
    app.dependency_overrides[get_review_graph_runner] = lambda: ExhaustedRunner()
    app.dependency_overrides[current_user_id] = lambda: user
    app.dependency_overrides[get_query_guard] = lambda: QueryGuard()
    try:
        response = TestClient(app).post(f"/api/v1/reviews/{review.id}/queries", json={"question": "차입금은?"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE_UPSTREAM"
        assert repository.get_review(user, review.id).status == "open"  # 같은 심사 건으로 다시 물을 수 있다
    finally:
        app.dependency_overrides.clear()
