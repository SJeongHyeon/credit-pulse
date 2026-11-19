"""데모 기록(data/demo/recordings.jsonl.gz)이 지금 코드와 맞는지 본다(리뷰 코멘트 11/13 ④).

기록은 외부 호출 입력의 해시로 재생한다. 모델·지시문·검색 구성이 바뀌면 해시가 달라져 재생되지 않고,
키 없는 새 clone의 데모 질문이 답하지 못한다(11/12 D10 점검에서 실제로 그랬다). 이 시험은 저장소에 든
스냅샷과 기록만으로(키·DB 없이) 데모 질문 7개를 실제 그래프로 돌려, 답할 질문은 인용과 함께 답하고
거절할 질문은 거절하는지 본다. 실패하면 `python -X utf8 scripts/record_demo.py`로 다시 녹화한다.
"""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend.api import runtime
from backend.services import demo_replay, llm
from scripts import record_demo

KEY_NAMES = ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "VOYAGE_API_KEY", "ANTHROPIC_API_KEY",
             "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")


@pytest.fixture(scope="module")
def replayed():
    patch = pytest.MonkeyPatch()
    for name in KEY_NAMES:
        patch.delenv(name, raising=False)
    patch.setattr(llm, "DEMO_MODE", True)
    patch.setattr(demo_replay, "_loaded", None)
    env = {"DEMO_MODE": "1", "DATABASE_URL": "unused"}
    try:
        yield record_demo._run_all(
            lambda: runtime.build_review_runtime(env, pool=record_demo._NoPool(), checkpointer=InMemorySaver())
        )
    finally:
        patch.setattr(demo_replay, "_enabled", False)  # 다른 시험에 재생이 끼어들지 않게 되돌린다
        patch.undo()
        demo_replay._enabled = False


def test_every_demo_question_is_replayed_from_the_recordings(replayed):
    assert len(replayed) == 7


def test_answerable_demo_questions_stop_for_approval_with_citations(replayed):
    """기록이 낡으면 작성 호출이 재생되지 않아 인용 없는 '확인 필요'나 거절로 내려간다."""

    for result in replayed[:5]:
        assert result["outcome"] == "awaiting_approval", result["question"]
        verdicts = [item["verdict"] for item in result["draft"]]
        assert "applicable" in verdicts, result["question"]
        assert all(item["citations"] for item in result["draft"] if item["verdict"] == "applicable"), result["question"]


def test_refusal_demo_questions_are_refused_for_the_recorded_reason(replayed):
    """주제 분류 지시문이 바뀌면 분류 호출이 재생되지 않아 사유가 달라진다."""

    assert (replayed[5]["outcome"], replayed[5]["refusal_reason"]) == ("refused", "ITEM_NOT_DISCLOSED")
    assert (replayed[6]["outcome"], replayed[6]["refusal_reason"]) == ("refused", "OUT_OF_DOMAIN")
