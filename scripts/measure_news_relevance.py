"""뉴스 관련 기사 선별 측정 (docs/evidence/news_relevance_filter_2026-11-12.md 사전 등록).

C(현재: 관련도순 20건 -> 최근 90일 -> 날짜순 5건)에 'D1: 검색어 첫 낱말이 제목 또는 요약에 있는 기사만',
'D2: 제목에 있는 기사만'을 더해 비교한다. 같은 후보 20건에 세 방식을 적용한다.
HUB만 부른다(질문 10개 x 1회 = 10회). OpenRouter는 쓰지 않는다. 키는 .env에서만 읽고 출력하지 않는다.
실행: python -X utf8 scripts/measure_news_relevance.py
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

CASES = [  # (질문, 회사명, 주제어)
    ("삼성전자의 최근 반도체 투자 이슈를 알려주세요.", "삼성전자", "반도체"),
    ("삼성전자의 사업보고서에서 타인 채무보증 내역을 확인해주세요.", "삼성전자", "보증"),
    ("현대자동차의 최근 리콜 이슈를 알려주세요.", "현대자동차", "리콜"),
    ("SK하이닉스의 HBM 공급 이슈를 알려주세요.", "SK하이닉스", "HBM"),
    ("카카오의 최근 소송 이슈를 알려주세요.", "카카오", "소송"),
    ("네이버의 최근 AI 투자 이슈를 알려주세요.", "네이버", "AI"),
    ("오뚜기의 FY2025 사업보고서를 바탕으로 최근 공급망 이슈를 알려주세요.", "오뚜기", "공급망"),
    ("LG에너지솔루션의 최근 배터리 공급 계약 이슈를 알려주세요.", "LG에너지솔루션", "공급"),
    ('오뚜기의 FY2024 사업보고서에서 주요 소송 현황을 알려주세요."', "오뚜기", "소송"),
    ("한화시스템의 최근 방산 수주 이슈를 알려주세요.", "한화시스템", "수주"),
]


def first_term(query: str) -> str:
    """검색어의 첫 낱말(서비스는 회사명을 첫째로 둔다고 가정한다). 비어 있으면 빈 문자열."""

    parts = query.split()
    return parts[0] if parts else ""


def keep_mentioning(items: list[dict], term: str, *, where: str, limit: int) -> list[dict]:
    """term이 title 또는 (title+description)에 들어 있는 기사만 입력 순서대로 limit건."""

    if where not in ("title", "title_or_description"):
        raise ValueError(f"where는 title 또는 title_or_description: {where}")
    kept = []
    for item in items:
        haystack = item["title"] if where == "title" else item["title"] + " " + item["description"]
        if term in haystack:
            kept.append(item)
    return kept[:limit]


def title_rate(items: list[dict], term: str) -> float:
    return sum(term in item["title"] for item in items) / len(items) if items else 0.0


def text_rate(items: list[dict], term: str) -> float:
    return sum(term in item["title"] + " " + item["description"] for item in items) / len(items) if items else 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def judge_adoption(base: dict[str, list], candidate: dict[str, list]) -> dict[str, bool]:
    """사전 등록한 채택 기준 4개. base=C, candidate=D1. 값은 질문별 목록(company, topic, count)."""

    n = len(candidate["count"])
    result = {
        "a": _mean(candidate["company"]) - _mean(base["company"]) >= 0.10 - 1e-9,
        "b": _mean(candidate["count"]) >= 3.5,
        "c": sum(c >= 1 for c in candidate["count"]) >= 0.9 * n - 1e-9,
        "d": _mean(base["topic"]) - _mean(candidate["topic"]) <= 0.025 + 1e-9,
    }
    result["adopt"] = all(result.values())
    return result


def _fetch(url: str, headers: dict, query: str) -> list[dict]:
    from backend.services import news_search as news

    params = urllib.parse.urlencode({"query": query, "display": news.NEWS_FETCH, "start": 1, "sort": news.NEWS_SORT})
    body = news._default_http_get(f"{url}?{params}", headers, news.NEWS_TIMEOUT_SECONDS)
    return news.parse_news_items(json.loads(body), limit=news.NEWS_FETCH)


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
    now = news._now()
    print(f"측정 시각: {now:%Y-%m-%d %H:%M} KST\n")

    stats = {m: {"company": [], "topic": [], "count": []} for m in ("C", "D1", "D2")}
    for question, company, topic in CASES:
        query = news.build_news_query(question)
        term = first_term(query)
        candidates = _fetch(url, headers, query)  # 관련도순 후보 20건(질문당 HUB 1회)
        recent_pool = _recent_pool(candidates, now)
        results = {
            "C": recent_pool[:5],
            "D1": keep_mentioning(recent_pool, term, where="title_or_description", limit=5),
            "D2": keep_mentioning(recent_pool, term, where="title", limit=5),
        }
        print(f"## {company}/{topic}  검색어={query!r} 첫낱말={term!r} 90일풀={len(recent_pool)}건")
        for mode, items in results.items():
            stats[mode]["company"].append(title_rate(items, company))
            stats[mode]["topic"].append(text_rate(items, topic))
            stats[mode]["count"].append(len(items))
            print(f"  [{mode}] {len(items)}건 회사명제목 {stats[mode]['company'][-1]:.0%} 주제어 {stats[mode]['topic'][-1]:.0%}")
            for item in items:
                print(f"       {item['pub_date'][5:16]} | {item['title'][:48]}")
        print()

    print("## 평균")
    n = len(CASES)
    for mode, s in stats.items():
        print(f"  [{mode}] 회사명제목 {_mean(s['company']):.1%} 주제어 {_mean(s['topic']):.1%} 평균건수 {_mean(s['count']):.2f} "
              f"1건이상 {sum(c >= 1 for c in s['count'])}/{n} 0건 {sum(c == 0 for c in s['count'])}/{n}")
    verdict = judge_adoption(stats["C"], stats["D1"])
    print("\n## D1 채택 기준 판정:", json.dumps(verdict, ensure_ascii=False))
    return 0


def _recent_pool(candidates: list[dict], now) -> list[dict]:
    """후보 전체에서 최근 90일 안 기사를 날짜 내림차순으로(5건 상한 없이). C·D가 같은 풀을 쓴다."""

    import datetime as dt
    import email.utils

    from backend.services import news_search as news

    dated = []
    for item in candidates:
        try:
            when = email.utils.parsedate_to_datetime(item.get("pub_date", ""))
        except (TypeError, ValueError):
            continue
        if when is not None and now - when <= dt.timedelta(days=news.NEWS_WINDOW_DAYS):
            dated.append((when, item))
    dated.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in dated]


if __name__ == "__main__":
    raise SystemExit(main())
