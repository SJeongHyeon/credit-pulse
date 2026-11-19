"""승인 직전 항목 코드 정리: 작성기·데모 기록 입력은 그대로 둔다."""
import pytest

from backend.graph.approval import approval_prepare_node
from backend.graph.checklist import infer_checklist_item


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("오뚜기의 진행 중인 소송은 몇 건인가요?", "litigation"),
        ("LG화학이 제공한 지급보증과 담보는 무엇인가요?", "guarantee_collateral"),
        ("케이씨티시의 연결 기준 매출액 상위 고객과 비중은?", "customer_concentration"),
        ("우성머티리얼스의 환율·원재료 가격 위험은?", "fx_raw_material"),
        ("케이씨티시의 2025년 말 단기차입금은 얼마인가요?", "borrowing_structure"),
        ("오뚜기의 우발부채 내역은 무엇인가요?", "contingent_liability"),
    ],
)
def test_six_checklist_topics_reach_approval_as_codes(question, expected):
    draft = {"item": question, "verdict": "applicable", "reason": "근거", "citations": []}
    state = {"user_id": "u", "review_id": "r", "question": question, "draft_items": [draft]}

    update = approval_prepare_node(True)(state, {"configurable": {"thread_id": "u:r"}})

    assert infer_checklist_item(question) == expected
    assert update["draft_items"] == [{**draft, "item": expected}]
    assert state["draft_items"] == [draft]  # 작성기·검증 결과는 변경하지 않음


@pytest.mark.parametrize("question", ["올해 매출액은?", "소송과 담보 내역을 함께 알려줘", "차입금과 담보 금액은?"])
def test_unclear_or_combined_question_is_not_forced_into_one_topic(question):
    state = {"user_id": "u", "review_id": "r", "question": question,
             "draft_items": [{"item": question, "verdict": "needs_check", "reason": "근거 부족"}]}

    update = approval_prepare_node(True)(state, {"configurable": {"thread_id": "u:r"}})

    assert infer_checklist_item(question) is None
    assert "draft_items" not in update


def test_explicit_checklist_items_and_multiple_drafts_keep_their_codes():
    question = "소송과 차입금을 확인해줘"
    drafts = [{"item": "litigation"}, {"item": "borrowing_structure"}]
    state = {"user_id": "u", "review_id": "r", "question": question, "draft_items": drafts}

    update = approval_prepare_node(True)(state, {"configurable": {"thread_id": "u:r"}})

    assert "draft_items" not in update
    assert state["draft_items"] == drafts
