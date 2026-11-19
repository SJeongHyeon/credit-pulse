"""86번: anthropic/ 모델을 Anthropic Messages API로 직접 보내는 경로. 가짜 anthropic 모듈만 쓴다(실제 호출 없음)."""
import sys
import types

import pytest

from backend.services import llm


def _fake_anthropic(monkeypatch, texts, stop_reason="end_turn"):
    calls = []
    replies = iter(texts)

    class APIStatusError(Exception):
        def __init__(self, status_code):
            self.status_code = status_code

    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            usage = types.SimpleNamespace(input_tokens=1000, output_tokens=200)
            block = types.SimpleNamespace(type="text", text=next(replies))
            return types.SimpleNamespace(content=[block], usage=usage, stop_reason=stop_reason)

    class Anthropic:
        def __init__(self, **kwargs):
            self.messages = Messages()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Anthropic, APIStatusError=APIStatusError))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    return calls


MESSAGES = [{"role": "system", "content": "JSON만"}, {"role": "user", "content": "오뚜기 2025 차입금"}]


def test_anthropic_model_goes_direct_with_thinking_off_and_no_temperature(monkeypatch):
    calls = _fake_anthropic(monkeypatch, ['{"retrieval_query": "오뚜기 2025 단기차입금"}'])
    llm.drain_usage()
    result = llm.openrouter_structured(MESSAGES, model="anthropic/claude-haiku-5.5", response_model=llm._RewriteResponse)
    assert result.retrieval_query == "오뚜기 2025 단기차입금"
    sent = calls[0]
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["thinking"] == {"type": "disabled"}
    assert "temperature" not in sent
    assert sent["system"] == "JSON만" and sent["messages"] == [MESSAGES[1]]
    assert sent["max_tokens"] == llm.ANTHROPIC_DEFAULT_MAX_TOKENS
    usage = llm.drain_usage()
    assert usage[0]["cost_usd"] == pytest.approx((1000 * 0.10 + 200 * 0.50) / 1e6)


def test_format_failure_retries_with_note_then_fails_after_two(monkeypatch):
    calls = _fake_anthropic(monkeypatch, ["설명 문장", '{"retrieval_query": "x"}'])
    result = llm.openrouter_structured(MESSAGES, model="anthropic/claude-haiku-5.5", response_model=llm._RewriteResponse,
                                       format_retry_note=" 짧게.")
    assert result.retrieval_query == "x"
    assert calls[0]["system"] == "JSON만" and calls[1]["system"] == "JSON만 짧게."
    _fake_anthropic(monkeypatch, ["x", "y", "z"])
    with pytest.raises(llm.ExternalApiResponseError):
        llm.openrouter_structured(MESSAGES, model="anthropic/claude-haiku-5.5", response_model=llm._RewriteResponse)


def test_refusal_stop_reason_is_an_error(monkeypatch):
    _fake_anthropic(monkeypatch, ['{"retrieval_query": "x"}'], stop_reason="refusal")
    with pytest.raises(llm.ExternalApiResponseError):
        llm.openrouter_structured(MESSAGES, model="anthropic/claude-haiku-5.5", response_model=llm._RewriteResponse)


def test_without_anthropic_key_or_for_other_models_uses_openrouter(monkeypatch):
    calls = _fake_anthropic(monkeypatch, ['{"retrieval_query": "x"}'])
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(llm.ExternalApiKeyMissing):  # OpenRouter 키도 없으면 지금처럼 '키 없음'
        llm.openrouter_structured(MESSAGES, model="anthropic/claude-haiku-5.5", response_model=llm._RewriteResponse)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    with pytest.raises(llm.ExternalApiKeyMissing):  # gpt-4o-mini는 Anthropic 키가 있어도 OpenRouter 경로
        llm.openrouter_structured(MESSAGES, model="openai/gpt-4o-mini", response_model=llm._RewriteResponse)
    assert calls == []
    assert llm.anthropic_model_id("anthropic/claude-haiku-5.5") == "claude-haiku-5-5"
