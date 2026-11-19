"""거절 질문 뉴스 참고의 실호출 검증 (docs/specs/refusal-news-search.md).

공용 OpenRouter 키를 아끼려고 질문 3개만 돌린다. 검색기·작성기는 가짜(호출되면 실패)라 Voyage는 쓰지 않는다.
  - corp_out : 코퍼스에 없는 회사 -> LLM 0회, 네이버 HUB 1회
  - weather  : 무관 질문         -> OpenRouter 1회(거절 종류 분류), 네이버 0회
  - work_ood : 업무 관련·코퍼스 밖 -> OpenRouter 1회, 네이버 1회
키는 .env에서만 읽고 출력하지 않는다. 실행: python -X utf8 scripts/verify_refusal_news_live.py [--case corp_out]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

CASES = {
    "corp_out": "삼성전자의 사업보고서에서 타인 채무보증 내역을 확인해주세요.",
    "weather": "오늘 서울 날씨 어때?",
    "work_ood": "오뚜기의 FY2025 사업보고서를 바탕으로 최근 공급망 이슈를 알려주세요.",
}
# 호출 횟수 기대: (OpenRouter 최대, 네이버 정확히)
EXPECTED_KIND = {"corp_out": "work_related", "weather": "unrelated", "work_ood": "work_related"}


def judge(case: str, result: dict) -> list[str]:
    """결과가 기대와 다른 점을 한국어 문장 목록으로 돌려준다. 비어 있으면 합격. 모르는 case는 KeyError."""

    expected_kind = EXPECTED_KIND[case]
    failures: list[str] = []
    if result.get("outcome") != "refused":
        failures.append(f"outcome이 refused가 아님: {result.get('outcome')}")
    if result.get("citations") or result.get("draft_items"):
        failures.append("거절인데 citations 또는 draft_items가 있음")
    if result.get("refusal_kind") != expected_kind:
        failures.append(f"refusal_kind가 {expected_kind}가 아님: {result.get('refusal_kind')}")
    items = result.get("news_items") or []
    if expected_kind == "unrelated":
        if result.get("news_calls") != 0 or items:
            failures.append(f"무관 질문인데 뉴스를 호출함: news_calls={result.get('news_calls')}, items={len(items)}")
        return failures
    if result.get("news_search") != "ok":
        failures.append(f"news_search가 ok가 아님: {result.get('news_search')}")
    if result.get("news_calls") != 1:
        failures.append(f"뉴스 호출이 1회가 아님: {result.get('news_calls')}")
    if not 1 <= len(items) <= 5:
        failures.append(f"뉴스 항목이 1~5건이 아님: {len(items)}")
    return failures


def _build_graph():
    from backend.graph.build import build_supervisor_graph
    from backend.graph.refusal import CorpusCatalog, CorpusTarget

    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
        targets=frozenset({CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2025)}),
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("거절 질문에서 검색·작성·메모 저장이 호출됨")

    return build_supervisor_graph(
        retriever=forbidden, draft_writer=forbidden, memo_saver=forbidden,
        corpus_catalog=catalog,
        domain_classifier=lambda question: False,  # 주제 판정은 가짜(LLM 절약): 업무 범위 밖으로 본다
        news_search_enabled=True,  # 거절 종류 분류는 실제 LLM, 뉴스는 실제 HUB
        speculative=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", choices=sorted(CASES), help="하나만 실행(기본: 3개 전부)")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    graph = _build_graph()
    failed = False
    for name in ([args.case] if args.case else list(CASES)):
        result = graph.invoke({"question": CASES[name]})
        problems = judge(name, result)
        failed = failed or bool(problems)
        print(f"[{'PASS' if not problems else 'FAIL'}] {name}: kind={result.get('refusal_kind')} "
              f"news={result.get('news_search')} calls={result.get('news_calls')} items={len(result.get('news_items') or [])}")
        for item in (result.get("news_items") or [])[:3]:
            print(f"    - {item['title'][:50]} | {item['source_domain']} | {item['pub_date']}")
        for problem in problems:
            print(f"    ! {problem}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
