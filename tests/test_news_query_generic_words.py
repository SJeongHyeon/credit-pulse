"""뉴스 검색어에서 '주요'·'현황' 같은 범용 낱말을 뺀다. 서비스 화면 검증에서 '오뚜기 주요 소송 현황'이
무관한 기사(인물 소개)만 돌려준 문제(2026-11-12)의 재발 방지. 키·네트워크 없이 돈다."""
import pytest

from backend.services import news_search


def test_generic_words_are_dropped_with_injected_extractor():
    def extractor(text):
        return ["오뚜기", "주요", "소송", "현황"]

    assert news_search.build_news_query("질문", noun_extractor=extractor) == "오뚜기 소송"


def test_company_and_topic_survive_when_only_generic_words_are_removed():
    def extractor(text):
        return ["삼성전자", "차입금", "현황", "주요", "내용"]

    assert news_search.build_news_query("질문", noun_extractor=extractor) == "삼성전자 차입금"


def test_real_morph_analyzer_on_the_screen_check_question():
    pytest.importorskip("kiwipiepy")

    query = news_search.build_news_query('오뚜기의 FY2024 사업보고서에서 주요 소송 현황을 알려주세요."')

    assert query == "오뚜기 소송"
