"""목차 어휘 기반 질의 번역(사전 등록 152번): 목록 안에서만 고르고, 실패해도 검색은 계속한다."""

import pytest

from backend.rag.term_bridge import TermBridge, accept_terms, display_name, heading_vocabulary


def row(ordinal, *sections, corp="00000001", year=2025, title=None, superseded=False):
    return {
        "corp_code": corp, "fiscal_year": year, "ordinal": ordinal,
        "section_path": list(sections), "table_title": title, "superseded": superseded,
    }


ROWS = [
    row(3, "3. 연결재무제표 주석", "14. 차입금 (연결)"),
    row(1, "1. 사업의 개요"),
    row(2, "3. 연결재무제표 주석", "13. 리스 (연결)", title="리스부채 만기"),
    row(4, "3. 연결재무제표 주석", "14. 차입금 (연결)"),  # 같은 이름은 한 번만
    row(5, "3. 연결재무제표 주석", "99. 다른 회사 항목", corp="00000002"),
    row(6, "3. 연결재무제표 주석", "98. 지난해 항목", year=2024),
    row(7, "3. 연결재무제표 주석", "97. 대체된 항목", superseded=True),
]


def test_display_name_drops_numbering_only():
    assert display_name("14. 차입금 (연결)") == "차입금 (연결)"
    assert display_name("  3.1. 주석   요약 ") == "주석 요약"
    assert display_name("차입금 (연결)") == "차입금 (연결)"
    assert display_name("2025년 사업") == "2025년 사업"  # 번호 형식이 아니면 그대로
    assert display_name("XI. 그 밖에 투자자 보호를 위하여 필요한 사항") == "그 밖에 투자자 보호를 위하여 필요한 사항"


def test_vocabulary_is_company_year_scoped_ordered_and_deduplicated():
    vocabulary = heading_vocabulary(ROWS, corp_code="00000001", fiscal_year=2025)

    assert vocabulary == ["사업의 개요", "연결재무제표 주석", "리스 (연결)", "리스부채 만기", "차입금 (연결)"]
    assert "다른 회사 항목" not in vocabulary and "지난해 항목" not in vocabulary
    assert "대체된 항목" not in vocabulary


def test_accept_terms_drops_names_outside_the_list_and_caps_at_three():
    vocabulary = ["차입금 (연결)", "리스 (연결)", "충당부채", "자본"]

    assert accept_terms(["차입금", "리스 (연결)", "14. 차입금 (연결)", "충당부채", "자본", "리스 (연결)"],
                        vocabulary) == ["리스 (연결)", "차입금 (연결)", "충당부채"]
    assert accept_terms([], vocabulary) == []
    assert accept_terms([None, 3], vocabulary) == []  # 이름이 아닌 값은 버린다


def test_bridge_appends_terms_and_keeps_original_when_nothing_chosen():
    asked = []

    def selector(question, vocabulary):
        asked.append((question, list(vocabulary)))
        return ["차입금 (연결)", "없는 항목"] if "빚" in question else []

    bridge = TermBridge(ROWS, selector)
    chosen = bridge.bridge("빚이 얼마인가요", "빚이 얼마인가요", corp_code="00000001", fiscal_year=2025)
    none = bridge.bridge("매출은", "매출은", corp_code="00000001", fiscal_year=2025)

    assert chosen.terms == ["차입금 (연결)"] and chosen.query == "빚이 얼마인가요 차입금 (연결)"
    assert none.terms == [] and none.query == "매출은"
    assert asked[0][1] == heading_vocabulary(ROWS, corp_code="00000001", fiscal_year=2025)


def test_bridge_asks_once_per_company_year_question():
    calls = []
    bridge = TermBridge(ROWS, lambda q, v: calls.append(q) or ["차입금 (연결)"])

    bridge.bridge("질문", "질문", corp_code="00000001", fiscal_year=2025)
    again = bridge.bridge("질문", "다시 쓴 질의", corp_code="00000001", fiscal_year=2025)

    assert calls == ["질문"]
    assert again.query == "다시 쓴 질의 차입금 (연결)"  # 재검색 질의에도 같은 이름을 붙인다


def test_selector_failure_falls_back_to_original_query_and_records_error():
    def failing(question, vocabulary):
        raise TimeoutError("상류 시간 초과")

    result = TermBridge(ROWS, failing).bridge("질문", "질문", corp_code="00000001", fiscal_year=2025)

    assert result.query == "질문" and result.terms == []
    assert result.error and "TimeoutError" in result.error


def test_company_without_vocabulary_never_calls_selector():
    def selector(question, vocabulary):
        raise AssertionError("부르면 안 된다")

    result = TermBridge(ROWS, selector).bridge("질문", "질문", corp_code="99999999", fiscal_year=2025)

    assert result.query == "질문" and result.vocabulary_size == 0 and result.error is None


def test_select_headings_sends_only_question_and_list_and_returns_cleaned_names(monkeypatch):
    from backend.services import llm

    captured = {}

    def fake_structured(messages, *, model, response_model, **kwargs):
        captured.update(messages=messages, model=model)
        return response_model(headings=["  차입금   (연결) ", "리스 (연결)"])

    monkeypatch.setattr(llm, "openrouter_structured", fake_structured)

    names = llm.select_headings("빚이 얼마인가요", ["차입금 (연결)", "리스 (연결)"])

    assert names == ["차입금 (연결)", "리스 (연결)"]
    assert captured["model"] == llm.TERM_BRIDGE_MODEL == "openai/gpt-4.1"
    user = captured["messages"][1]["content"]
    assert "빚이 얼마인가요" in user and "차입금 (연결)" in user
    assert len(captured["messages"]) == 2
