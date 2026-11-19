"""서비스 그래프의 기본 뉴스 검색이 질문 속 코퍼스 회사명으로 사후 필터를 거는지 시험
(docs/evidence/news_company_filter_2026-11-12.md 채택). 뉴스 HTTP만 가짜로 바꾸고 나머지는 실제 그래프 조립을 쓴다."""
import datetime as dt
import json

import pytest

from backend.graph.build import REFUSAL_MESSAGES_V2, build_supervisor_graph
from backend.graph.refusal import CorpusCatalog, CorpusTarget
from backend.services import news_search

NOW = dt.datetime(2026, 11, 12, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
CATALOG = CorpusCatalog(
    corp_names=frozenset({"한화시스템", "오뚜기"}),
    fiscal_years=frozenset({2025}),
    report_types=frozenset({"annual"}),
    targets=frozenset({CorpusTarget(corp_name="한화시스템", corp_code="00000001", fiscal_year=2025)}),
)
IN_CORPUS_NO_EVIDENCE = "한화시스템의 FY2025 사업보고서를 바탕으로 최근 노조 파업 관련 보도를 알려주세요."
CORP_OUT = "토스의 FY2025 사업보고서에서 차입금 현황을 알려주세요."


def _article(title, description="d", days_ago=1):
    when = NOW - dt.timedelta(days=days_ago)
    return {"title": title, "description": description, "link": "https://n/x", "originallink": "https://o.kr/x",
            "pubDate": when.strftime("%a, %d %b %Y %H:%M:%S +0900")}


@pytest.fixture
def hub(monkeypatch):
    monkeypatch.setenv("NAVER_HUB_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_HUB_CLIENT_SECRET", "secret")
    monkeypatch.setattr(news_search, "_now", lambda: NOW)
    state = {"articles": [], "queries": []}

    def fake_get(url, headers, timeout):
        state["queries"].append(url)
        return json.dumps({"items": state["articles"]}).encode()

    monkeypatch.setattr(news_search, "_default_http_get", fake_get)
    return state


def _graph():
    return build_supervisor_graph(
        retriever=lambda s: [], draft_writer=lambda s, c: [], memo_saver=lambda *a, **k: None,
        corpus_catalog=CATALOG, domain_classifier=lambda question: True,
        news_search_enabled=True, refusal_kind_classifier=lambda q: "work_related",
        speculative=False, search_grading_enabled=False,
    )


def test_w1_in_corpus_company_keeps_only_articles_that_mention_it(hub):
    hub["articles"] = [_article("한화오션 하청노조 불법 점거", days_ago=1), _article("HD현대중공업 소식", days_ago=2),
                       _article("KAI 노조 상경투쟁", "한화시스템 인수 언급", days_ago=3)]

    result = _graph().invoke({"question": IN_CORPUS_NO_EVIDENCE})

    assert result["refusal_reason"] == "ITEM_NOT_DISCLOSED"
    assert [i["title"] for i in result["news_items"]] == ["KAI 노조 상경투쟁"]
    assert result["refusal_message"] == REFUSAL_MESSAGES_V2["work_related_with_news"]
    assert len(hub["queries"]) == 1  # 호출은 그대로 1회


def test_w2_no_article_mentions_the_company_gives_unavailable_message(hub):
    hub["articles"] = [_article("한화오션 하청노조 불법 점거"), _article("한화세미텍 투자")]

    result = _graph().invoke({"question": IN_CORPUS_NO_EVIDENCE})

    assert result["news_items"] == [] and result["news_search"] == "ok" and result["news_calls"] == 1
    assert result["refusal_message"] == REFUSAL_MESSAGES_V2["work_related_news_unavailable"]


def test_w3_company_not_in_corpus_is_not_filtered(hub):
    hub["articles"] = [_article("토스플레이스 영구채"), _article("삼성카드 사장 인터뷰")]

    result = _graph().invoke({"question": CORP_OUT})

    assert result["refusal_reason"] == "CORP_NOT_IN_CORPUS"
    assert len(result["news_items"]) == 2  # 코퍼스 회사명이 없으면 필터를 걸지 않는다


def test_w4_default_news_search_finds_names_with_helper():
    assert news_search.find_company_names("한화시스템의 노조 파업", frozenset({"한화시스템", "한화"})) == ("한화시스템",)
    assert news_search.find_company_names("토스의 차입금", frozenset({"한화시스템"})) == ()
