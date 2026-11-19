"""OpenRouter 리랭커 어댑터 테스트(외부 호출 없이 가짜 응답)."""

import pytest

from backend.rag.rerank import OpenRouterReranker, RerankError, create_reranker
from backend.services import llm


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = "err"

    def json(self):
        return self._payload


def test_openrouter_rerank_restores_input_order(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen.update(url=url, json=json, auth=headers["Authorization"])
        return _Resp({"results": [
            {"index": 2, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.5},
            {"index": 1, "relevance_score": 0.1},
        ]})

    scores = llm.openrouter_rerank("q", ["a", "b", "c"], model="voyageai/rerank-3", post=fake_post)
    assert scores == [0.5, 0.1, 0.9]
    assert seen["url"] == "https://openrouter.ai/api/v1/rerank"
    assert seen["json"] == {"model": "voyageai/rerank-3", "query": "q", "documents": ["a", "b", "c"], "top_n": 3}
    assert seen["auth"] == "Bearer not-a-real-key"


def test_openrouter_rerank_rejects_missing_index(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    fake = lambda *a, **k: _Resp({"results": [{"index": 0, "relevance_score": 0.5}]})
    with pytest.raises(llm.ExternalApiResponseError):
        llm.openrouter_rerank("q", ["a", "b"], model="voyageai/rerank-3", post=fake)


def test_openrouter_rerank_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    # HTTP 오류는 응답 형식 오류가 아니라 상태 오류다(429면 API 503 SERVICE_UNAVAILABLE_UPSTREAM). 이전엔 형식 오류로 셌다.
    with pytest.raises(llm.ExternalApiStatusError) as error:
        llm.openrouter_rerank("q", ["a"], model="voyageai/rerank-3", post=lambda *a, **k: _Resp({}, 429))
    assert error.value.status_code == 429


def test_openrouter_reranker_maps_model_name():
    calls = {}

    def fake(query, texts, *, model):
        calls["model"] = model
        return [0.0] * len(texts)

    reranker = OpenRouterReranker("openrouter/voyageai/rerank-2.5", score_fn=fake)
    assert reranker.score("q", ["x", "y"]) == [0.0, 0.0]
    assert calls["model"] == "voyageai/rerank-2.5"
    assert isinstance(create_reranker.__call__, object)
    with pytest.raises(RerankError):
        OpenRouterReranker("openrouter/rerank-3", score_fn=fake)
