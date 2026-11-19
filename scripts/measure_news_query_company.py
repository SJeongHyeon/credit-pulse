"""뉴스 검색어 회사명 보존 측정 (docs/evidence/news_query_company_name_2026-11-12.md 사전 등록).

B0(현재 검색어)와 Q(코퍼스 회사명을 큰따옴표로 감싸 앞에 둔 검색어; 1차 P는 미충족으로 기록 끝)로 HUB 뉴스를 검색해, 서비스와 같은 규칙
(관련도순 20건 -> 최근 90일 -> 날짜순 5건)으로 5건을 고른 결과를 비교한다.
HUB만 부른다(질문 14개 x 2방식 = 28회). OpenRouter는 쓰지 않는다. 키는 .env에서만 읽고 출력하지 않는다.
실행: python -X utf8 scripts/measure_news_query_company.py
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

CASES = [  # (질문, 회사명, 주제어). 마지막(통제)은 이미 쪼개지지 않는 이름.
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
    # 2차(Q) 사전 등록: 탐색에서 보지 않은 코퍼스 회사 4개
    ("코웨이의 최근 렌탈 이슈를 알려주세요.", "코웨이", "렌탈"),
    ("크래프톤의 최근 게임 이슈를 알려주세요.", "크래프톤", "게임"),
    ("신한지주의 최근 대출 이슈를 알려주세요.", "신한지주", "대출"),
    ("하나금융지주의 최근 배당 이슈를 알려주세요.", "하나금융지주", "배당"),
]
CONTROL_INDEX = 9  # 오뚜기: 이미 쪼개지지 않는 이름(통제)


def title_rate(items: list[dict], term: str) -> float:
    return sum(term in item["title"] for item in items) / len(items) if items else 0.0


def text_rate(items: list[dict], term: str) -> float:
    return sum(term in item["title"] + " " + item["description"] for item in items) / len(items) if items else 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def judge_adoption(base: dict[str, list], cand: dict[str, list], *, control_index: int) -> dict[str, bool]:
    """사전 등록한 채택 기준 4개. 값은 질문별 목록(company, topic, count, query)."""

    n = len(cand["count"])
    result = {
        "a": _mean(cand["company"]) - _mean(base["company"]) >= 0.15 - 1e-9,
        "b": _mean(base["topic"]) - _mean(cand["topic"]) <= 0.025 + 1e-9,
        "c": sum(c >= 1 for c in cand["count"]) >= 0.9 * n - 1e-9 and _mean(cand["count"]) >= 3.5,
        "d": cand["query"][control_index] == base["query"][control_index],
    }
    result["adopt"] = all(result.values())
    return result


def judge_quoted(base: dict[str, list], cand: dict[str, list], *, control_index: int) -> dict[str, bool]:
    """2차(Q) 사전 등록 채택 기준 4개. 값은 질문별 목록(company_text, topic, count)."""

    n = len(cand["count"])
    result = {
        "a": _mean(cand["company_text"]) - _mean(base["company_text"]) >= 0.20 - 1e-9,
        "b": _mean(base["topic"]) - _mean(cand["topic"]) <= 0.10 + 1e-9,
        "c": sum(c >= 1 for c in cand["count"]) >= 13 / 14 * n - 1e-9 and _mean(cand["count"]) >= 3.5,
        "d": cand["company_text"][control_index] >= base["company_text"][control_index] - 1e-9,
    }
    result["adopt"] = all(result.values())
    return result


def _search(url: str, headers: dict, query: str, now) -> list[dict]:
    from backend.services import news_search as news

    params = urllib.parse.urlencode({"query": query, "display": news.NEWS_FETCH, "start": 1, "sort": news.NEWS_SORT})
    body = news._default_http_get(f"{url}?{params}", headers, news.NEWS_TIMEOUT_SECONDS)
    items = news.parse_news_items(json.loads(body), limit=news.NEWS_FETCH)
    return news.select_recent_items(items, now)  # 서비스와 같은 규칙(90일, 날짜순, 최대 5건)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from dotenv import load_dotenv

    load_dotenv(root / ".env")
    from backend.graph.refusal import load_corpus_catalog
    from backend.services import news_search as news

    url, headers = news._endpoint_and_headers()
    if headers is None:
        print("뉴스 키가 없습니다(.env의 NAVER_HUB_CLIENT_ID/SECRET).")
        return 2
    names = load_corpus_catalog(root / "data" / "manifest.jsonl").corp_names
    now = news._now()
    print(f"측정 시각: {now:%Y-%m-%d %H:%M} KST, 코퍼스 회사 {len(names)}개\n")

    stats = {m: {"company": [], "company_text": [], "topic": [], "count": [], "query": []} for m in ("B0", "Q")}
    for question, company, topic in CASES:
        queries = {"B0": news.build_news_query(question),
                   "Q": news.build_news_query(question, company_names=names, quote_company_names=True)}
        print(f"## {company}/{topic}")
        for mode, query in queries.items():
            items = _search(url, headers, query, now)
            s = stats[mode]
            s["company"].append(title_rate(items, company))
            s["company_text"].append(text_rate(items, company))
            s["topic"].append(text_rate(items, topic))
            s["count"].append(len(items))
            s["query"].append(query)
            print(f"  [{mode}] 검색어={query!r} {len(items)}건 회사명제목 {s['company'][-1]:.0%} 제목·요약 {s['company_text'][-1]:.0%} 주제어 {s['topic'][-1]:.0%}")
            for item in items:
                print(f"       {item['pub_date'][5:16]} | {item['title'][:48]}")
        print()

    print("## 평균")
    n = len(CASES)
    for mode, s in stats.items():
        print(f"  [{mode}] 회사명제목 {_mean(s['company']):.1%} 제목·요약 {_mean(s['company_text']):.1%} 주제어 {_mean(s['topic']):.1%} 평균건수 {_mean(s['count']):.2f} "
              f"1건이상 {sum(c >= 1 for c in s['count'])}/{n} 0건 {sum(c == 0 for c in s['count'])}/{n}")
    verdict = judge_quoted(stats["B0"], stats["Q"], control_index=CONTROL_INDEX)
    print("\n## Q 채택 기준 판정:", json.dumps(verdict, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
