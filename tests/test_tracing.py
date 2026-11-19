"""G6 Langfuse 어댑터(backend/services/tracing.py). 실제 Langfuse로는 보내지 않고 가짜 클라이언트만 쓴다."""
import sys
import types
from contextlib import contextmanager

import pytest

from backend.services import llm, tracing
from evals.response_metrics import calculate_response_metrics, per_question_flags


class FakeObservation:
    def __init__(self, record):
        self.record = record
        self.trace_id = "trace-1"

    def update(self, **kwargs):
        self.record.setdefault("updates", []).append(kwargs)


class FakeLangfuse:
    def __init__(self):
        self.observations = []
        self.scores = []
        self.flushed = 0

    @contextmanager
    def _manager(self, record):
        yield FakeObservation(record)

    def start_as_current_observation(self, **kwargs):
        record = dict(kwargs)
        self.observations.append(record)
        return self._manager(record)

    def create_score(self, **kwargs):
        self.scores.append(kwargs)

    def flush(self):
        self.flushed += 1


@pytest.fixture
def fake_langfuse(monkeypatch):
    client = FakeLangfuse()
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setattr(tracing, "_client", client)
    return client


def test_disabled_without_keys_does_nothing():
    assert not tracing.enabled()
    with tracing.question("q", question="질문") as trace:
        trace.finish({"outcome": "answered"})
    assert trace.trace_id is None
    with tracing.external_call("chat", model="m", input={}) as call:
        call.done(input_tokens=1)
    tracing.score(None, "x", 1)
    tracing.flush()


def test_question_and_score_reach_langfuse(fake_langfuse):
    with tracing.question("eval", question="오뚜기 차입금", metadata={"row_id": 1}) as trace:
        trace.finish({"outcome": "answered", "citation_ids": ["c1"]})
    assert trace.trace_id == "trace-1"
    tracing.score(trace.trace_id, "correct_citation", 1)
    tracing.flush()
    first = fake_langfuse.observations[0]
    assert first["name"] == "eval" and first["input"] == {"question": "오뚜기 차입금"}
    assert first["updates"][-1]["output"]["citation_ids"] == ["c1"]
    assert fake_langfuse.scores == [{"trace_id": "trace-1", "name": "correct_citation", "value": 1.0,
                                     "data_type": "NUMERIC", "comment": None}]
    assert fake_langfuse.flushed == 1


def test_chat_attempt_is_one_generation_without_prompt_text(fake_langfuse, monkeypatch):
    """채팅 시도 한 번 = 관측 하나. 토큰·공급자 비용을 남기고 프롬프트 본문은 남기지 않는다(P19 R4·R7)."""

    class FakeCompletions:
        def create(self, **kwargs):
            usage = types.SimpleNamespace(prompt_tokens=120, completion_tokens=30, cost=0.0004)
            choice = types.SimpleNamespace(message=types.SimpleNamespace(content='{"retrieval_query": "x"}'), finish_reason="stop")
            return types.SimpleNamespace(choices=[choice], usage=usage)

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI, APIStatusError=RuntimeError))
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    secret_prompt = "비밀 프롬프트 본문 청크 내용"
    llm.openrouter_structured([{"role": "system", "content": secret_prompt}, {"role": "user", "content": "q"}],
                              model="openai/gpt-4o-mini", response_model=llm._RewriteResponse)
    chats = [o for o in fake_langfuse.observations if o["name"] == "chat"]
    assert len(chats) == 1
    assert chats[0]["as_type"] == "generation" and chats[0]["model"] == "openai/gpt-4o-mini"
    assert secret_prompt not in str(chats[0])
    update = chats[0]["updates"][-1]
    assert update["usage_details"] == {"input": 120, "output": 30}
    assert update["cost_details"] == {"total": 0.0004}
    assert update["output"] == {"finish_reason": "stop"}


def test_per_question_flags_add_up_to_aggregate_metrics():
    dataset = [
        {"id": 1, "kind": "relevant", "gold_chunks": ["g1"]},
        {"id": 2, "kind": "relevant", "gold_chunks": ["g2"]},
        {"id": 3, "kind": "relevant", "gold_chunks": ["g3"]},
        {"id": 4, "kind": "irrelevant", "irrelevant_type": "out_of_domain"},
        {"id": 5, "kind": "irrelevant", "irrelevant_type": "missing_attribute"},
    ]
    responses = [
        {"id": 1, "outcome": "answered", "refusal_reason": None, "citations": [{"chunk_id": "g1"}], "draft_items": [{}]},
        {"id": 2, "outcome": "answered", "refusal_reason": None, "citations": [{"chunk_id": "x"}], "draft_items": [{}]},
        {"id": 3, "outcome": "refused", "refusal_reason": "ITEM_NOT_DISCLOSED", "citations": [], "draft_items": []},
        {"id": 4, "outcome": "refused", "refusal_reason": "OUT_OF_DOMAIN", "citations": [], "draft_items": []},
        {"id": 5, "outcome": "answered", "refusal_reason": None, "citations": [], "draft_items": [{}]},
    ]
    aggregate = calculate_response_metrics(dataset, responses)
    by_id = {r["id"]: r for r in responses}
    totals: dict[str, int] = {}
    for row in dataset:
        for name, value in per_question_flags(row, by_id[row["id"]]).items():
            totals[name] = totals.get(name, 0) + value
    for name, value in totals.items():
        assert value == aggregate[name]["numerator"], name


def test_trace_level_user_session_and_tags_are_propagated(fake_langfuse, monkeypatch):
    """심사 건 질의·승인은 session_id(심사 건 id)로 묶이고, user_id는 내부 UUID만 붙는다(EVAL_REPORT 7장)."""

    import langfuse

    seen = []

    @contextmanager
    def fake_propagate(**kwargs):
        seen.append(kwargs)
        yield

    monkeypatch.setattr(langfuse, "propagate_attributes", fake_propagate)
    with tracing.question("review-query", question="q", user_id="user-uuid", session_id="review-1", tags=["service", "query"]):
        pass
    assert seen == [{"user_id": "user-uuid", "session_id": "review-1", "tags": ["service", "query"]}]
    assert fake_langfuse.observations[0]["name"] == "review-query"
