"""뉴스 정렬 비교 측정 스크립트(scripts/measure_news_sort.py)의 계산 규칙 시험. 키·네트워크 없이 가짜 기사만 쓴다."""
import datetime as dt
import importlib.util
import json
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "measure_news_sort.py"
NOW = dt.datetime(2026, 11, 12, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))


def _load():
    spec = importlib.util.spec_from_file_location("measure_news_sort", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _item(title="t", description="d", days_ago=1):
    when = NOW - dt.timedelta(days=days_ago)
    return {"title": title, "description": description, "pub_date": when.strftime("%a, %d %b %Y %H:%M:%S +0900")}


def test_select_recent_keeps_only_window_and_sorts_by_date_desc():
    items = [_item("old", days_ago=200), _item("b", days_ago=30), _item("a", days_ago=3), _item("edge", days_ago=91)]

    picked = _load().select_recent(items, now=NOW, days=90, limit=5)

    assert [i["title"] for i in picked] == ["a", "b"]  # 200일·91일 전은 빠지고 최신이 먼저


def test_select_recent_cuts_to_limit():
    items = [_item(f"t{n}", days_ago=n) for n in range(1, 12)]

    picked = _load().select_recent(items, now=NOW, days=90, limit=5)

    assert [i["title"] for i in picked] == ["t1", "t2", "t3", "t4", "t5"]


def test_select_recent_skips_items_with_unparsable_dates():
    items = [{"title": "bad", "description": "", "pub_date": "???"}, _item("ok", days_ago=2)]

    assert [i["title"] for i in _load().select_recent(items, now=NOW, days=90, limit=5)] == ["ok"]


def test_company_title_rate_counts_titles_only():
    items = [_item("삼성전자 투자"), _item("다른 기사", description="삼성전자 언급"), _item("삼성전자 실적"), _item("무관")]

    assert _load().company_title_rate(items, "삼성전자") == 0.5


def test_topic_rate_counts_title_or_description():
    items = [_item("반도체 투자"), _item("무관", description="반도체 설명"), _item("무관2")]

    assert abs(_load().topic_rate(items, "반도체") - 2 / 3) < 1e-9


def test_recent_rate_uses_window_from_now():
    items = [_item(days_ago=10), _item(days_ago=100), _item(days_ago=89), _item(days_ago=400)]

    assert _load().recent_rate(items, now=NOW, days=90) == 0.5


def test_rates_are_zero_for_empty_results():
    mod = _load()

    assert mod.company_title_rate([], "x") == 0.0
    assert mod.topic_rate([], "x") == 0.0
    assert mod.recent_rate([], now=NOW, days=90) == 0.0


def test_fetch_returns_all_requested_candidates_not_only_first_five(monkeypatch):
    """C안의 후보 20건이 앞 5건으로 잘리면 측정이 B안의 필터 결과가 되어 버린다(최초 측정의 결함)."""
    from backend.services import news_search

    payload = {"items": [{"title": f"t{n}", "link": "https://n", "originallink": "https://o.kr/x",
                          "description": "d", "pubDate": "Mon, 10 Nov 2026 09:00:00 +0900"} for n in range(20)]}
    monkeypatch.setattr(news_search, "_default_http_get", lambda url, headers, timeout: json.dumps(payload).encode())

    assert len(_load()._fetch("https://x", {}, "q", "sim", 20)) == 20
