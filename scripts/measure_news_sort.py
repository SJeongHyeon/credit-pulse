"""뉴스 정렬 방식 비교 측정 (docs/evidence/news_sort_measure_2026-11-12.md 사전 등록).

A 최신순(date, 5건) / B 관련도순(sim, 5건) / C 절충(sim 20건 -> 최근 90일 -> 날짜순 5건)을 질문 8개로 비교한다.
HUB만 부른다(질문 8개 x 3방식 = 24회). OpenRouter는 쓰지 않는다. 키는 .env에서만 읽고 출력하지 않는다.
실행: python -X utf8 scripts/measure_news_sort.py
"""
from __future__ import annotations

import datetime as dt
import email.utils
import json
import sys
import urllib.parse
from pathlib import Path

WINDOW_DAYS = 90
CASES = [  # (질문, 회사명, 주제어)
    ("삼성전자의 최근 반도체 투자 이슈를 알려주세요.", "삼성전자", "반도체"),
    ("삼성전자의 사업보고서에서 타인 채무보증 내역을 확인해주세요.", "삼성전자", "보증"),
    ("현대자동차의 최근 리콜 이슈를 알려주세요.", "현대자동차", "리콜"),
    ("SK하이닉스의 HBM 공급 이슈를 알려주세요.", "SK하이닉스", "HBM"),
    ("카카오의 최근 소송 이슈를 알려주세요.", "카카오", "소송"),
    ("네이버의 최근 AI 투자 이슈를 알려주세요.", "네이버", "AI"),
    ("오뚜기의 FY2025 사업보고서를 바탕으로 최근 공급망 이슈를 알려주세요.", "오뚜기", "공급망"),
    ("LG에너지솔루션의 최근 배터리 공급 계약 이슈를 알려주세요.", "LG에너지솔루션", "공급"),
]


def _parse(pub_date: str) -> dt.datetime | None:
    try:
        return email.utils.parsedate_to_datetime(pub_date)
    except (TypeError, ValueError):
        return None


def select_recent(items: list[dict], *, now: dt.datetime, days: int, limit: int) -> list[dict]:
    """발행일이 now 기준 days일 이내인 기사만 남겨 날짜 내림차순으로 limit건. 날짜를 못 읽는 기사는 뺀다."""

    dated = []
    for item in items:
        when = _parse(item.get("pub_date", ""))
        if when is not None and now - when <= dt.timedelta(days=days):
            dated.append((when, item))
    dated.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in dated[:limit]]


def company_title_rate(items: list[dict], company: str) -> float:
    return sum(company in item["title"] for item in items) / len(items) if items else 0.0


def topic_rate(items: list[dict], topic: str) -> float:
    return sum(topic in item["title"] + item["description"] for item in items) / len(items) if items else 0.0


def recent_rate(items: list[dict], *, now: dt.datetime, days: int) -> float:
    if not items:
        return 0.0
    ok = 0
    for item in items:
        when = _parse(item.get("pub_date", ""))
        ok += when is not None and now - when <= dt.timedelta(days=days)
    return ok / len(items)


def _fetch(url: str, headers: dict, query: str, sort: str, display: int) -> list[dict]:
    from backend.services import news_search as news

    params = urllib.parse.urlencode({"query": query, "display": display, "start": 1, "sort": sort})
    body = news._default_http_get(f"{url}?{params}", headers, news.NEWS_TIMEOUT_SECONDS)
    return news.parse_news_items(json.loads(body), limit=display)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from dotenv import load_dotenv

    load_dotenv(root / ".env")
    from backend.services import news_search as news

    url, headers = news._endpoint_and_headers()
    if headers is None:
        print("뉴스 키가 없습니다(.env의 NAVER_HUB_CLIENT_ID/SECRET).")
        return 2
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    print(f"측정 시각: {now:%Y-%m-%d %H:%M} KST, 창: 최근 {WINDOW_DAYS}일\n")

    totals = {m: {"company": [], "topic": [], "recent": [], "count": []} for m in "ABC"}
    for question, company, topic in CASES:
        query = news.build_news_query(question)
        results = {
            "A": _fetch(url, headers, query, "date", 5),
            "B": _fetch(url, headers, query, "sim", 5),
            "C": select_recent(_fetch(url, headers, query, "sim", 20), now=now, days=WINDOW_DAYS, limit=5),
        }
        print(f"## {company}/{topic}  검색어={query!r}")
        for mode, items in results.items():
            stats = totals[mode]
            stats["company"].append(company_title_rate(items, company))
            stats["topic"].append(topic_rate(items, topic))
            stats["recent"].append(recent_rate(items, now=now, days=WINDOW_DAYS))
            stats["count"].append(len(items))
            print(f"  [{mode}] {len(items)}건 회사명제목 {stats['company'][-1]:.0%} 주제어 {stats['topic'][-1]:.0%} "
                  f"최근90일 {stats['recent'][-1]:.0%}")
            for item in items:
                print(f"       {item['pub_date'][5:16]} | {item['title'][:48]}")
        print()

    print("## 평균")
    for mode, stats in totals.items():
        n = len(CASES)
        print(f"  [{mode}] 회사명제목 {sum(stats['company']) / n:.1%} 주제어 {sum(stats['topic']) / n:.1%} "
              f"최근90일 {sum(stats['recent']) / n:.1%} 평균건수 {sum(stats['count']) / n:.2f} "
              f"1건이상 질문수 {sum(c >= 1 for c in stats['count'])}/{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
