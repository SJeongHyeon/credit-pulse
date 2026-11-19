import pytest

from backend.graph.verifier import verify_draft_items


RETRIEVED_CHUNK = {
    "chunk_id": "chunk-1",
    "content": "회사는 소송 충당부채 1,000억원을 인식했습니다.",
    "rcept_no": "20260304000913",
    "chapter": "NOTES",
    "note_no": "1",
}


def test_verify_draft_items_keeps_a_matching_citation():
    result = verify_draft_items(
        draft_items=[
            {
                "item": "litigation",
                "verdict": "applicable",
                "reason": "소송 충당부채 1,000억원을 인식했습니다.",
                "citations": [
                    {
                        "chunk_id": "chunk-1",
                        "rcept_no": "20260304000913",
                        "chapter": "NOTES",
                        "note_no": "1",
                        "quote": "소송 충당부채 1,000억원을 인식했습니다.",
                    }
                ],
            }
        ],
        retrieved_chunks=[RETRIEVED_CHUNK],
    )

    citation = result["draft_items"][0]["citations"][0]

    assert result["draft_items"][0]["verdict"] == "applicable"
    assert citation["verification"] == {
        "passed": True,
        "failed_checks": [],
    }
    assert result["citations"] == [citation]
    assert result["verification"] == [
        {
            "item": "litigation",
            "chunk_id": "chunk-1",
            "passed": True,
            "failed_checks": [],
        }
    ]


def test_verify_draft_items_downgrades_chunk_not_retrieved():
    result = verify_draft_items(
        draft_items=[
            {
                "item": "litigation",
                "verdict": "applicable",
                "reason": "소송 충당부채 1,000억원을 인식했습니다.",
                "citations": [
                    {
                        "chunk_id": "missing-chunk",
                        "quote": "소송 충당부채 1,000억원을 인식했습니다.",
                    }
                ],
            }
        ],
        retrieved_chunks=[RETRIEVED_CHUNK],
    )

    assert result["draft_items"] == [
        {
            "item": "litigation",
            "verdict": "needs_check",
            "reason": "근거 부족: 확인할 공시 항목이나 기간을 구체화해 주세요.",
            "citations": [],
        }
    ]
    assert result["citations"] == []
    assert result["verification"] == [
        {
            "item": "litigation",
            "chunk_id": "missing-chunk",
            "passed": False,
            "failed_checks": ["chunk_not_retrieved"],
        }
    ]


def test_verify_draft_items_downgrades_quote_not_in_chunk():
    result = verify_draft_items(
        draft_items=[
            {
                "item": "litigation",
                "verdict": "applicable",
                "reason": "소송 충당부채 1,000억원을 인식했습니다.",
                "citations": [
                    {
                        "chunk_id": "chunk-1",
                        "quote": "회사는 소송 충당부채 2,000억원을 인식했습니다.",
                    }
                ],
            }
        ],
        retrieved_chunks=[RETRIEVED_CHUNK],
    )

    assert result["draft_items"] == [
        {
            "item": "litigation",
            "verdict": "needs_check",
            "reason": "근거 부족: 확인할 공시 항목이나 기간을 구체화해 주세요.",
            "citations": [],
        }
    ]
    assert result["citations"] == []
    assert result["verification"] == [
        {
            "item": "litigation",
            "chunk_id": "chunk-1",
            "passed": False,
            "failed_checks": ["quote_not_in_chunk"],
        }
    ]


def test_verify_draft_items_downgrades_number_or_unit_mismatch():
    result = verify_draft_items(
        draft_items=[
            {
                "item": "litigation",
                "verdict": "applicable",
                "reason": "소송 충당부채 2,000억원을 인식했습니다.",
                "citations": [
                    {
                        "chunk_id": "chunk-1",
                        "quote": "회사는 소송 충당부채 1,000억원을 인식했습니다.",
                    }
                ],
            }
        ],
        retrieved_chunks=[RETRIEVED_CHUNK],
    )

    assert result["draft_items"] == [
        {
            "item": "litigation",
            "verdict": "needs_check",
            "reason": "근거 부족: 확인할 공시 항목이나 기간을 구체화해 주세요.",
            "citations": [],
        }
    ]
    assert result["citations"] == []
    assert result["verification"] == [
        {
            "item": "litigation",
            "chunk_id": "chunk-1",
            "passed": False,
            "failed_checks": ["reason_number_or_unit_not_in_chunk"],
            "missing_numbers": ["2000억원"],  # 78번: 청크에 없는 reason 숫자
        }
    ]


def test_verify_draft_items_downgrades_unit_mismatch():
    result = verify_draft_items(
        draft_items=[
            {
                "item": "litigation",
                "verdict": "applicable",
                "reason": "소송 충당부채 1,000백만원을 인식했습니다.",
                "citations": [
                    {
                        "chunk_id": "chunk-1",
                        "quote": "회사는 소송 충당부채 1,000억원을 인식했습니다.",
                    }
                ],
            }
        ],
        retrieved_chunks=[RETRIEVED_CHUNK],
    )

    assert result["draft_items"][0]["verdict"] == "needs_check"
    assert result["draft_items"][0]["citations"] == []
    assert result["citations"] == []
    assert result["verification"] == [
        {
            "item": "litigation",
            "chunk_id": "chunk-1",
            "passed": False,
            "failed_checks": ["reason_number_or_unit_not_in_chunk"],
            "missing_numbers": ["1000백만원"],  # 78번: 청크에 없는 reason 숫자
        }
    ]


def _verify_reason(reason, content):
    return verify_draft_items(
        draft_items=[
            {
                "item": "litigation",
                "verdict": "applicable",
                "reason": reason,
                "citations": [{"chunk_id": "chunk-1", "quote": content}],
            }
        ],
        retrieved_chunks=[{"chunk_id": "chunk-1", "content": content}],
    )["draft_items"][0]["verdict"]


@pytest.mark.parametrize(
    ("reason", "content"),
    [
        # 조사·어미가 붙어도 단위만 비교한다 (56번 결과의 실제 reason)
        ("관련 충당부채는 9,329백만원과 32,493백만원으로 인식", "충당부채 9,329백만원, 32,493백만원"),
        ("차입금은 52,233,375천원이며", "차입금 52,233,375천원"),
        ("배당수익은 72,563백만원입니다.", "배당수익 72,563백만원"),
        ("5.89%의 이자율로 빌렸습니다.", "이자율 5.89%"),
        ("원고 소송 31건의 내역", "소송 31건"),
        # 단위 없는 표 숫자 (단위는 표 머리글에 있다)
        ("리스부채는 33,313,014천원입니다.", "(단위: 천원) 리스부채 | 33,313,014"),
        ("차입금 1,000입니다.", "차입금 1,000억원"),
        # 연도는 비교하지 않는다
        ("2025년 말 차입금 1,000억원", "당기말 차입금 1,000억원"),
        ("FY2025 차입금 1,000억원", "당기말 차입금 1,000억원"),
        ("2025 사업연도 차입금 1,000억원", "당기말 차입금 1,000억원"),
        # 날짜는 표기가 달라도 비교하지 않는다
        ("2025년 12월 31일 기준 사채 500억원", "당기말(2025.12.31) 사채 500억원"),
        ("2025.12.31 기준 사채 500억원", "당기말 12월 31일 사채 500억원"),
    ],
)
def test_verify_draft_items_keeps_same_number_despite_wording(reason, content):
    assert _verify_reason(reason, content) == "applicable"


@pytest.mark.parametrize(
    ("reason", "content"),
    [
        # 단위가 둘 다 있으면 조사를 뗀 뒤에도 달라야 실패한다
        ("차입금은 1,000억원을 넘습니다.", "차입금 1,000백만원"),
        ("이자율은 5%입니다.", "금액 5억원"),
        # 연도 모양이어도 단위가 금액이면 금액이다
        ("차입금 2025억원", "당기말 차입금 1,000억원"),
        ("차입금 2,025", "당기말 차입금 1,000억원"),
        # 긴 숫자의 일부는 같은 수가 아니다
        ("차입금 1,234원", "차입금 11,234,500원"),
        ("차입금 1,234.5억원", "차입금 1,234억원"),
    ],
)
def test_verify_draft_items_downgrades_real_mismatch(reason, content):
    assert _verify_reason(reason, content) == "needs_check"


def test_verify_draft_items_finds_numbers_across_cited_chunks():
    result = verify_draft_items(
        draft_items=[
            {
                "item": "borrowing",
                "verdict": "applicable",
                "reason": "사채 500억원, 단기차입금 1,234백만원",
                "citations": [
                    {"chunk_id": "a", "quote": "사채 500억원"},
                    {"chunk_id": "b", "quote": "단기차입금 1,234"},
                ],
            }
        ],
        retrieved_chunks=[
            {"chunk_id": "a", "content": "사채 500억원"},
            {"chunk_id": "b", "content": "단기차입금 1,234"},
        ],
    )

    assert result["draft_items"][0]["verdict"] == "applicable"
    assert [c["chunk_id"] for c in result["citations"]] == ["a", "b"]


def test_verify_draft_items_fails_when_number_is_in_no_cited_chunk():
    result = verify_draft_items(
        draft_items=[
            {
                "item": "borrowing",
                "verdict": "applicable",
                "reason": "사채 500억원, 단기차입금 9,999백만원",
                "citations": [
                    {"chunk_id": "a", "quote": "사채 500억원"},
                    {"chunk_id": "b", "quote": "단기차입금 1,234"},
                ],
            }
        ],
        retrieved_chunks=[
            {"chunk_id": "a", "content": "사채 500억원"},
            {"chunk_id": "b", "content": "단기차입금 1,234"},
            {"chunk_id": "c", "content": "다른 청크 9,999백만원"},
        ],
    )

    assert result["draft_items"][0]["verdict"] == "needs_check"
    assert result["citations"] == []
    assert [v["failed_checks"] for v in result["verification"]] == [
        ["reason_number_or_unit_not_in_chunk"],
        ["reason_number_or_unit_not_in_chunk"],
    ]


def test_verify_draft_items_allows_reason_without_a_number():
    # 88번: 원래 not_applicable·'인식하지 않았습니다'였으나 인용 청크(1,000억원 인식)와 모순이라 새 검사가 낮춘다.
    # 이 시험의 의도(숫자 없는 reason은 숫자 검사를 통과)만 남기려고 판정을 applicable로 바꿨다.
    result = verify_draft_items(
        draft_items=[
            {
                "item": "litigation",
                "verdict": "applicable",
                "reason": "소송 충당부채를 인식했습니다.",
                "citations": [
                    {
                        "chunk_id": "chunk-1",
                        "quote": "회사는 소송 충당부채 1,000억원을 인식했습니다.",
                    }
                ],
            }
        ],
        retrieved_chunks=[RETRIEVED_CHUNK],
    )

    assert result["draft_items"][0]["verdict"] == "applicable"
    assert result["citations"][0]["verification"]["passed"] is True
    assert result["verification"] == [
        {
            "item": "litigation",
            "chunk_id": "chunk-1",
            "passed": True,
            "failed_checks": [],
        }
    ]


def test_verify_draft_items_downgrades_item_when_one_of_many_citations_fails():
    result = verify_draft_items(
        draft_items=[
            {
                "item": "litigation",
                "verdict": "applicable",
                "reason": "소송 충당부채 1,000억원을 인식했습니다.",
                "citations": [
                    {
                        "chunk_id": "chunk-1",
                        "quote": "회사는 소송 충당부채 1,000억원을 인식했습니다.",
                    },
                    {
                        "chunk_id": "missing-chunk",
                        "quote": "회사는 소송 충당부채 1,000억원을 인식했습니다.",
                    },
                ],
            }
        ],
        retrieved_chunks=[RETRIEVED_CHUNK],
    )

    assert result["draft_items"][0]["verdict"] == "needs_check"
    assert result["draft_items"][0]["citations"] == []
    assert result["citations"] == []
    assert result["verification"] == [
        {
            "item": "litigation",
            "chunk_id": "chunk-1",
            "passed": True,
            "failed_checks": [],
        },
        {
            "item": "litigation",
            "chunk_id": "missing-chunk",
            "passed": False,
            "failed_checks": ["chunk_not_retrieved"],
        },
    ]


def test_verify_draft_items_keeps_existing_needs_check_without_citations():
    draft_item = {
        "item": "litigation",
        "verdict": "needs_check",
        "reason": "근거 부족: 확인할 공시 항목이나 기간을 구체화해 주세요.",
        "citations": [],
    }

    result = verify_draft_items(
        draft_items=[draft_item],
        retrieved_chunks=[RETRIEVED_CHUNK],
    )

    assert result["draft_items"] == [draft_item]
    assert result["citations"] == []
    assert result["verification"] == []


# ---- 81번: 질문에 있는 기간 표현은 근거 숫자로 대조하지 않는다 ----

_BOND_CHUNK = {"chunk_id": "b1", "content": "사채 | 15,002,881 유동성사채 | 5,222,013"}


def _verify_with_question(reason, question):
    return verify_draft_items(
        draft_items=[{"item": "q", "verdict": "applicable", "reason": reason,
                      "citations": [{"chunk_id": "b1", "quote": "사채 | 15,002,881"}]}],
        retrieved_chunks=[_BOND_CHUNK],
        question=question,
    )


def test_period_echoed_from_question_is_not_checked():
    result = _verify_with_question(
        "사채 잔액은 15,002,881백만원, 1년 이내 만기 금액은 5,222,013백만원입니다.",
        "LG화학의 사채 잔액과 1년 이내에 만기가 돌아오는 사채 금액은 얼마인가요?",
    )
    assert result["draft_items"][0]["verdict"] == "applicable"


def test_period_not_in_question_is_still_checked():
    result = _verify_with_question("3년 이내 만기 금액은 5,222,013백만원입니다.", "사채 잔액은 얼마인가요?")
    assert result["draft_items"][0]["verdict"] == "needs_check"
    assert result["verification"][0]["missing_numbers"] == ["3년"]


def test_amount_echoed_from_question_is_still_checked():
    result = _verify_with_question("차입금은 100억원을 넘습니다.", "차입금이 100억원을 넘나요?")
    assert result["draft_items"][0]["verdict"] == "needs_check"
    assert result["verification"][0]["missing_numbers"] == ["100억원"]


def test_without_question_behaves_as_before():
    result = _verify_with_question("1년 이내 만기 금액은 5,222,013백만원입니다.", "")
    assert result["draft_items"][0]["verdict"] == "needs_check"


def _not_applicable_item(quote):
    return {
        "item": "bonds",
        "verdict": "not_applicable",
        "reason": "확인 필요",
        "citations": [{"chunk_id": "chunk-b", "quote": quote}],
    }


def test_not_applicable_with_amount_in_cited_chunk_is_downgraded():
    """88번(신뢰 경계 R4): 사채 금액 표를 인용하고 '해당 없음'이면 needs_check. redraft 대상(숫자 불일치)은 아니다."""

    chunk = {"chunk_id": "chunk-b", "content": "| 사채 | 15,002,881 | 5,222,013 |\n※ 사채가 없는 것으로 판단하시기 바랍니다."}
    result = verify_draft_items([_not_applicable_item("사채가 없는 것으로 판단하시기 바랍니다.")], [chunk])
    assert result["draft_items"][0]["verdict"] == "needs_check"
    assert result["draft_items"][0]["citations"] == []
    assert result["verification"][0]["failed_checks"] == ["not_applicable_with_amount_in_chunk"]


def test_not_applicable_without_amount_and_applicable_with_amount_are_kept():
    plain = {"chunk_id": "chunk-b", "content": "당사는 보고기간말 현재 타인을 위한 지급보증 해당 사항이 없습니다. (2025.12.31 기준)"}
    kept = verify_draft_items([_not_applicable_item("지급보증 해당 사항이 없습니다.")], [plain])
    assert kept["draft_items"][0]["verdict"] == "not_applicable"
    table = {"chunk_id": "chunk-b", "content": "| 사채 | 15,002,881 |"}
    applicable = {**_not_applicable_item("| 사채 | 15,002,881 |"), "verdict": "applicable", "reason": "사채 15,002,881"}
    assert verify_draft_items([applicable], [table])["draft_items"][0]["verdict"] == "applicable"
