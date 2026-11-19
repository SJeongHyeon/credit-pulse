"""뉴스 회사명 사후 필터 측정 (docs/evidence/news_company_filter_2026-11-12.md 사전 등록 3차).

현재(C: 관련도순 20건 -> 최근 90일 -> 날짜순 5건)와, 그 결과를 '제목 또는 요약에 정식 회사명이 있는 기사만'으로 거른 F를 같은 후보
20건에 적용해 비교한다. HUB만 부른다(질문 18개 x 1회 = 18회). OpenRouter는 쓰지 않는다. 키는 .env에서만 읽고 출력하지 않는다.
실행: python -X utf8 scripts/measure_news_company_filter.py
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

CASES = [  # (질문, 회사명, 주제어)
    ("한화시스템의 최근 방산 수주 이슈를 알려주세요.", "한화시스템", "수주"),
    ("한화시스템의 최근 노조 파업 이슈를 알려주세요.", "한화시스템", "파업"),
    ("한화투자증권의 최근 실적 이슈를 알려주세요.", "한화투자증권", "실적"),
    ("현대글로비스의 최근 물류 이슈를 알려주세요.", "현대글로비스", "물류"),
    ("롯데칠성음료의 최근 가격 이슈를 알려주세요.", "롯데칠성음료", "가격"),
    ("SK이노베이션의 최근 배터리 이슈를 알려주세요.", "SK이노베이션", "배터리"),
    ("LG유플러스의 최근 통신 이슈를 알려주세요.", "LG유플러스", "통신"),
    ("CJ대한통운의 최근 택배 이슈를 알려주세요.", "CJ대한통운", "택배"),
    ("POSCO홀딩스의 최근 철강 이슈를 알려주세요.", "POSCO홀딩스", "철강"),
    ("오뚜기의 최근 소송 이슈를 알려주세요.", "오뚜기", "소송"),
    ("코웨이의 최근 렌탈 이슈를 알려주세요.", "코웨이", "렌탈"),
    ("크래프톤의 최근 게임 이슈를 알려주세요.", "크래프톤", "게임"),
    ("신한지주의 최근 대출 이슈를 알려주세요.", "신한지주", "대출"),
    ("하나금융지주의 최근 배당 이슈를 알려주세요.", "하나금융지주", "배당"),
    # 3차 사전 등록: 처음 보는 코퍼스 회사 4개
    ("동원산업의 최근 수산 이슈를 알려주세요.", "동원산업", "수산"),
    ("한샘의 최근 인테리어 이슈를 알려주세요.", "한샘", "인테리어"),
    ("종근당의 최근 신약 이슈를 알려주세요.", "종근당", "신약"),
    ("한섬의 최근 패션 이슈를 알려주세요.", "한섬", "패션"),
]
HANWHA_STRIKE_INDEX = 1


def all_mention(items: list[dict], company: str) -> bool:
    """모든 기사의 제목 또는 요약에 회사명이 있는가. 0건이면 위배된 기사가 없으므로 True."""

    return all(company in item["title"] + " " + item["description"] for item in items)


def count_title(items: list[dict], term: str) -> int:
    return sum(term in item["title"] for item in items)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def judge_filter(f: dict) -> dict[str, bool]:
    """사전 등록한 채택 기준 4개. f: count(질문별 건수), all_mention(질문별 bool), ocean(한화시스템 파업의 한화오션 기사 수)."""

    result = {
        "a": all(f["all_mention"]),
        "b": _mean(f["count"]) >= 2.5,
        "c": sum(c == 0 for c in f["count"]) <= 4,
        "d": f["ocean"] == 0,
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

    c_stats = {"count": [], "text": []}
    f_stats = {"count": [], "all_mention": [], "ocean": 0}
    for index, (question, company, topic) in enumerate(CASES):
        query = news.build_news_query(question)  # 검색어는 두 방식이 같다
        candidates = _fetch(url, headers, query)
        c_items = news.select_recent_items(candidates, now)
        f_items = news.select_recent_items(candidates, now, require_any=(company,))
        dropped = [i for i in c_items if i not in f_items]
        c_stats["count"].append(len(c_items))
        c_stats["text"].append(sum(company in i["title"] + " " + i["description"] for i in c_items) / len(c_items) if c_items else 0.0)
        f_stats["count"].append(len(f_items))
        f_stats["all_mention"].append(all_mention(f_items, company))
        if index == HANWHA_STRIKE_INDEX:
            f_stats["ocean"] = count_title(f_items, "한화오션")
            print(f"[한화오션 혼입] C={count_title(c_items, '한화오션')}건, F={f_stats['ocean']}건")
        print(f"## {company}/{topic} 검색어={query!r}: C {len(c_items)}건(회사명 {c_stats['text'][-1]:.0%}) -> F {len(f_items)}건, 버려진 C 기사 {len(dropped)}건")
        for item in f_items:
            print(f"   [F] {item['pub_date'][5:16]} | {item['title'][:48]}")
        for item in dropped[:3]:
            print(f"   [버림] {item['pub_date'][5:16]} | {item['title'][:48]}")
        print()

    n = len(CASES)
    print("## 평균")
    print(f"  [C] 평균건수 {_mean(c_stats['count']):.2f} 0건 {sum(c == 0 for c in c_stats['count'])}/{n} 제목·요약 회사명 {_mean(c_stats['text']):.1%}")
    print(f"  [F] 평균건수 {_mean(f_stats['count']):.2f} 0건 {sum(c == 0 for c in f_stats['count'])}/{n} 전부 회사명 포함 {all(f_stats['all_mention'])}")
    print("\n## F 채택 기준 판정:", json.dumps(judge_filter(f_stats), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
