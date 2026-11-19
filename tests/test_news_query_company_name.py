"""뉴스 검색어에서 코퍼스 회사명을 쪼개지 않는다 (docs/specs/news-query-company-name.md AC-Q1~Q5). 키·네트워크 없이 돈다."""
import pytest

from backend.services import news_search

NAMES = frozenset({"한화시스템", "한화", "SK이노베이션", "SK", "오뚜기", "현대글로비스"})


def _splitting_extractor(text):
    """회사명을 쪼개는 형태소 분석기를 흉내 낸다(실제 kiwipiepy가 '한화시스템'을 '한화','시스템'으로 쪼갠 것과 같다)."""
    table = {"한화시스템": ["한화", "시스템"], "SK이노베이션": ["SK", "이노베이션"], "현대글로비스": ["현대", "글로비스"]}
    out = []
    for word in text.replace("의 ", " ").replace("를 ", " ").split():
        out.extend(table.get(word, [word]))
    return out


def test_q1_company_name_stays_one_piece_and_comes_first():
    query = news_search.build_news_query(
        "한화시스템의 노조 파업 보도", noun_extractor=_splitting_extractor, company_names=NAMES)

    assert query == "한화시스템 노조 파업 보도"  # 회사명이 한 덩어리로 맨 앞


def test_q1_without_company_names_the_name_is_split_as_before():
    query = news_search.build_news_query("한화시스템의 노조 파업 보도", noun_extractor=_splitting_extractor)

    assert query.startswith("한화 시스템")


def test_q2_longest_name_wins_and_does_not_double_count_its_prefix():
    query = news_search.build_news_query(
        "한화시스템의 방산 수주", noun_extractor=_splitting_extractor, company_names=NAMES)

    assert query.split()[0] == "한화시스템"
    assert query.split().count("한화") == 0  # 짧은 이름 '한화'가 따로 또 들어가지 않는다


def test_q2_at_most_two_company_names():
    names = frozenset({"한화시스템", "오뚜기", "현대글로비스"})

    query = news_search.build_news_query(
        "한화시스템과 오뚜기와 현대글로비스 비교", noun_extractor=lambda t: [], company_names=names)

    assert len([t for t in query.split() if t in names]) == 2


def test_q3_question_without_company_name_is_unchanged():
    question = "토스의 최근 자본잠식 이슈를 알려주세요."

    assert news_search.build_news_query(question, noun_extractor=_splitting_extractor, company_names=NAMES) == \
        news_search.build_news_query(question, noun_extractor=_splitting_extractor)


def test_q4_query_limits_still_hold_with_company_name():
    def extractor(text):
        return ["가나다라", "마바사아", "자차카타", "파하거너", "더러머버"]

    query = news_search.build_news_query("오뚜기 질문", noun_extractor=extractor, company_names=NAMES)

    assert len(query.split()) <= 4 and len(query) <= 30
    assert query.split()[0] == "오뚜기"


def test_q5_default_is_unchanged_for_a_company_name_question():
    def extractor(text):
        return ["오뚜기", "소송"]

    assert news_search.build_news_query("오뚜기 소송", noun_extractor=extractor) == "오뚜기 소송"
    assert news_search.build_news_query("오뚜기 소송", noun_extractor=extractor, company_names=()) == "오뚜기 소송"


def test_company_name_is_not_removed_by_stopword_or_personal_data_rules():
    query = news_search.build_news_query(
        "kim@example.com 오뚜기 연락", noun_extractor=lambda t: ["연락"], company_names=NAMES)

    assert query.startswith("오뚜기")
    assert "@" not in query


def test_q1_real_morph_analyzer_keeps_hanwha_systems_whole():
    pytest.importorskip("kiwipiepy")

    query = news_search.build_news_query(
        "한화시스템의 FY2025 사업보고서를 바탕으로 최근 노조 파업 관련 보도를 알려주세요", company_names=NAMES)

    assert query.split()[0] == "한화시스템" and "노조" in query and "파업" in query
    assert "한화 시스템" not in query


# ── Q: 회사명을 큰따옴표로 감싸 앞에 둔다(docs/evidence/news_query_company_name_2026-11-12.md 2차 사전 등록) ──


def test_quoted_company_name_is_wrapped_in_double_quotes_and_first():
    query = news_search.build_news_query(
        "한화시스템의 노조 파업 보도", noun_extractor=_splitting_extractor, company_names=NAMES, quote_company_names=True)

    assert query == '"한화시스템" 노조 파업 보도'


def test_quote_flag_default_off_keeps_unquoted_query():
    query = news_search.build_news_query(
        "한화시스템의 노조 파업 보도", noun_extractor=_splitting_extractor, company_names=NAMES)

    assert '"' not in query


def test_quote_flag_without_company_in_question_changes_nothing():
    question = "토스의 최근 자본잠식 이슈를 알려주세요."

    assert news_search.build_news_query(question, noun_extractor=_splitting_extractor, company_names=NAMES,
                                        quote_company_names=True) == \
        news_search.build_news_query(question, noun_extractor=_splitting_extractor)


def test_quoted_query_keeps_token_and_length_limits():
    def extractor(text):
        return ["가나다라", "마바사아", "자차카타", "파하거너", "더러머버"]

    query = news_search.build_news_query("오뚜기 질문", noun_extractor=extractor, company_names=NAMES,
                                         quote_company_names=True)

    assert len(query.split()) <= 4 and len(query) <= 30 and query.startswith('"오뚜기"')


def test_quote_characters_in_the_question_never_reach_the_query():
    query = news_search.build_news_query(
        '오뚜기의 "주요" 소송', noun_extractor=lambda t: ["주요", "소송"], company_names=NAMES, quote_company_names=True)

    assert query == '"오뚜기" 소송'  # 질문의 따옴표(주요는 불용어)가 검색어 연산자로 새지 않는다
