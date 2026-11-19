"""뉴스 회사명 사후 필터: 질문의 코퍼스 회사명이 제목 또는 요약에 있는 기사만 남긴다
(docs/evidence/news_company_filter_2026-11-12.md). 키·네트워크 없이 가짜만 쓴다."""
import datetime as dt
import json

import pytest

from backend.services import news_search

NOW = dt.datetime(2026, 11, 12, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))


def _item(title="t", description="d", days_ago=1):
    when = NOW - dt.timedelta(days=days_ago)
    return {"title": title, "description": description, "link": "https://n/x", "originallink": "https://o.kr/x",
            "pub_date": when.strftime("%a, %d %b %Y %H:%M:%S +0900"), "source_domain": "o.kr"}


def test_filter_keeps_items_with_the_name_in_title_or_description_before_cutting_to_five():
    items = [_item(f"무관 {n}", days_ago=n) for n in range(1, 9)]  # 가장 최신 8건은 무관
    items += [_item("한화시스템 수주", days_ago=20), _item("다른", "한화시스템 언급", days_ago=21)]

    picked = news_search.select_recent_items(items, NOW, require_any=("한화시스템",))

    assert [i["title"] for i in picked] == ["한화시스템 수주", "다른"]  # 5건 상한 전에 걸러서 무관한 최신 기사에 밀리지 않는다


def test_filter_with_several_names_keeps_an_item_that_has_any_of_them():
    items = [_item("오뚜기 소식", days_ago=1), _item("한화시스템 소식", days_ago=2), _item("무관", days_ago=3)]

    picked = news_search.select_recent_items(items, NOW, require_any=("오뚜기", "한화시스템"))

    assert [i["title"] for i in picked] == ["오뚜기 소식", "한화시스템 소식"]


def test_filter_off_by_default_keeps_current_behaviour():
    items = [_item(f"무관 {n}", days_ago=n) for n in range(1, 9)]

    assert len(news_search.select_recent_items(items, NOW)) == 5
    assert len(news_search.select_recent_items(items, NOW, require_any=())) == 5


def test_filter_does_not_match_a_different_affiliate_name():
    items = [_item("한화오션 하청노조 불법 점거", days_ago=1), _item("한화세미텍 투자", days_ago=2)]

    assert news_search.select_recent_items(items, NOW, require_any=("한화시스템",)) == []


@pytest.fixture
def hub_keys(monkeypatch):
    monkeypatch.setenv("NAVER_HUB_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_HUB_CLIENT_SECRET", "secret")
    monkeypatch.setattr(news_search, "_now", lambda: NOW)


def _http(items):
    payload = {"items": [{"title": i["title"], "description": i["description"], "link": i["link"],
                          "originallink": i["originallink"], "pubDate": i["pub_date"]} for i in items]}
    return lambda url, headers, timeout: json.dumps(payload).encode()


def test_search_news_with_required_names_filters_and_still_calls_once(hub_keys):
    calls = []
    http = _http([_item("한화오션 노조", days_ago=1), _item("한화시스템 방산", days_ago=2)])

    def counting(url, headers, timeout):
        calls.append(url)
        return http(url, headers, timeout)

    result = news_search.search_news("한화시스템 방산", http_get=counting, require_company_names=("한화시스템",))

    assert [i["title"] for i in result["items"]] == ["한화시스템 방산"]
    assert result["status"] == "ok" and result["calls"] == 1 and len(calls) == 1


def test_search_news_zero_after_filter_is_ok_with_empty_items(hub_keys):
    http = _http([_item("한화오션 노조", days_ago=1), _item("한화세미텍", days_ago=2)])

    result = news_search.search_news("한화시스템 노조", http_get=http, require_company_names=("한화시스템",))

    assert result == {"status": "ok", "items": [], "calls": 1}


def test_search_news_without_required_names_is_unchanged(hub_keys):
    http = _http([_item("한화오션 노조", days_ago=1)])

    result = news_search.search_news("한화시스템 노조", http_get=http)

    assert [i["title"] for i in result["items"]] == ["한화오션 노조"]
