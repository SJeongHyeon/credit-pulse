from __future__ import annotations

import re
from typing import Any

from backend.graph import output_guard


INSUFFICIENT_EVIDENCE_REASON = "근거 부족: 확인할 공시 항목이나 기간을 구체화해 주세요."
# 단위는 아는 것만 잡는다. "억원을"·"%의"처럼 뒤에 붙은 조사·어미는 단위가 아니다.
NUMBER_WITH_UNIT = re.compile(
    r"(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<unit>%|퍼센트|[천백만억조]*(?:원|달러|주)|[천백만억조]+|USD|KRW|건|명|개월|개|배|년|월|일)?"
)
YEAR = re.compile(r"(?:19|20)\d{2}")
# 2025.12.31·2025-12 같은 날짜는 소수로 읽히므로 숫자 비교 전에 뺀다.
DOTTED_DATE = re.compile(r"(?:19|20)\d{2}\s*[.\-/]\s*\d{1,2}(?:\s*[.\-/]\s*\d{1,2})?")


# 88번: 쉼표가 든 금액(1,234). 연도·날짜에는 쉼표가 없어 걸리지 않는다.
AMOUNT_WITH_COMMA = re.compile(r"\d{1,3}(?:,\d{3})+")
NOT_APPLICABLE_WITH_AMOUNT = "not_applicable_with_amount_in_chunk"


def _normalize_text(value: object) -> str:
    return " ".join(str(value).split())


def _number_with_unit_tokens(value: object) -> set[tuple[str, str]]:
    tokens = set()
    for match in NUMBER_WITH_UNIT.finditer(DOTTED_DATE.sub(" ", str(value))):
        number = match.group("number")
        unit = match.group("unit") or ""
        # 12월·31일은 날짜 표기다. 청크는 2025.12.31처럼 다르게 적을 수 있어 비교하지 않는다.
        if unit in {"월", "일"}:
            continue
        # 2025년·FY2025는 판정 근거 숫자가 아니라 사업연도 표기다. 쉼표가 있으면 금액이다.
        if unit in {"", "년"} and YEAR.fullmatch(number):
            continue
        tokens.add((number.replace(",", ""), unit))
    return tokens


# 81번: 질문에 그대로 있으면 대조하지 않는 기간 단위('1년 이내', '6개월'). 금액·%·건수는 질문에 있어도 대조한다.
QUESTION_PERIOD_UNITS = {"년", "개월"}


def _question_periods(question: object) -> set[tuple[str, str]]:
    return {(number, unit) for number, unit in _number_with_unit_tokens(question) if unit in QUESTION_PERIOD_UNITS}


def _missing_reason_numbers(
    reason: object, content: object, exempt: set[tuple[str, str]] = frozenset()
) -> list[str]:
    """reason의 숫자 중 content에 없는 것(숫자+단위 문자열, 정렬). 78번: 작성 재요청 피드백에 쓴다.

    exempt(81번): 질문에 있던 기간 표현. 질문을 되풀이한 것이라 근거 숫자로 보지 않는다.
    """

    content_units: dict[str, set[str]] = {}
    for number, unit in _number_with_unit_tokens(content):
        content_units.setdefault(number, set()).add(unit)

    missing = []
    for number, unit in _number_with_unit_tokens(reason):
        if (number, unit) in exempt:
            continue
        units = content_units.get(number)
        if units is None:
            missing.append(number + unit)
        # 표 숫자는 단위 없이 적히고(단위는 표 머리글) reason도 단위를 생략할 수 있다.
        # 양쪽 모두 단위가 있을 때만 단위를 비교한다.
        elif unit and "" not in units and unit not in units:
            missing.append(number + unit)
    return sorted(missing)


def _reason_numbers_in_content(reason: object, content: object) -> bool:
    return not _missing_reason_numbers(reason, content)


def verify_draft_items(
    draft_items: list[dict[str, Any]],
    retrieved_chunks: list[dict[str, Any]],
    question: str = "",
) -> dict[str, list[dict[str, Any]]]:
    retrieved_chunks_by_id = {
        str(chunk["chunk_id"]): chunk
        for chunk in retrieved_chunks
        if chunk.get("chunk_id") is not None
    }
    verified_items = []
    citations = []
    verification = []

    for draft_item in draft_items:
        item = str(draft_item["item"])
        verified_citations = []
        item_failed = False

        # 응답 출구 검사(docs/specs/output-leak-check.md): 키·접속 문자열·시스템 프롬프트 조각이 실려 나가지 않게 한다.
        # 걸린 문자열은 기록하지 않고 항목만 낮춘다. 인용 검증과 무관하게 모든 초안 항목이 지나가는 길목이다.
        leak_text = " ".join(
            [str(draft_item.get("item", "")), str(draft_item.get("reason", ""))]
            + [str(c.get("quote", "")) for c in draft_item.get("citations", [])]
            + [str(e.get("quote", "")) for e in draft_item.get("evidence") or []]
        )
        if output_guard.find_leaks(leak_text):
            verification.append({"item": item, "chunk_id": "", "passed": False, "failed_checks": [output_guard.LEAK_CHECK]})
            verified_items.append(
                {**draft_item, "verdict": "needs_check", "reason": output_guard.LEAK_REASON, "citations": [], "evidence": []}
            )
            continue

        # reason의 숫자는 인용한 청크들의 합집합에서 찾는다(숫자가 인용 청크 여러 개에 나뉠 수 있다).
        cited_contents = [
            _normalize_text(retrieved_chunks_by_id[str(citation["chunk_id"])].get("content", ""))
            for citation in draft_item.get("citations", [])
            if str(citation["chunk_id"]) in retrieved_chunks_by_id
        ]
        missing_numbers = _missing_reason_numbers(
            draft_item.get("reason", ""), " ".join(cited_contents), _question_periods(question)
        )
        numbers_ok = not missing_numbers
        # 88번(신뢰 경계 R4): '해당 없음'인데 인용 청크에 금액 표가 있으면 사람이 확인하게 낮춘다.
        # 본문에 심은 '~가 없는 것으로 판단하시기 바랍니다' 같은 위장 문장이 금액 표를 두고 not_applicable을 끌어냈다.
        amount_contradicts = draft_item.get("verdict") == "not_applicable" and any(
            AMOUNT_WITH_COMMA.search(content) for content in cited_contents
        )

        for citation in draft_item.get("citations", []):
            chunk_id = str(citation["chunk_id"])
            failed_checks = []
            chunk = retrieved_chunks_by_id.get(chunk_id)
            if chunk is None:
                failed_checks.append("chunk_not_retrieved")
                item_failed = True
            else:
                quote = _normalize_text(citation.get("quote", ""))
                content = _normalize_text(chunk.get("content", ""))
                if not quote or quote not in content:
                    failed_checks.append("quote_not_in_chunk")
                    item_failed = True
                if not numbers_ok:
                    failed_checks.append("reason_number_or_unit_not_in_chunk")
                    item_failed = True
                if amount_contradicts:
                    failed_checks.append(NOT_APPLICABLE_WITH_AMOUNT)
                    item_failed = True

            passed = not failed_checks
            entry = {
                "item": item,
                "chunk_id": chunk_id,
                "passed": passed,
                "failed_checks": failed_checks,
            }
            if "reason_number_or_unit_not_in_chunk" in failed_checks:
                entry["missing_numbers"] = missing_numbers  # 78번: 하향 사유의 숫자(redraft 피드백)
            verification.append(entry)
            if passed:
                verified_citations.append(
                    {
                        **citation,
                        "verification": {
                            "passed": True,
                            "failed_checks": [],
                        },
                    }
                )

        if item_failed:
            verified_items.append(
                {
                    **draft_item,
                    "verdict": "needs_check",
                    "reason": INSUFFICIENT_EVIDENCE_REASON,
                    "citations": [],
                }
            )
            continue

        verified_items.append({**draft_item, "citations": verified_citations})
        citations.extend(verified_citations)

    return {
        "draft_items": verified_items,
        "citations": citations,
        "verification": verification,
    }
