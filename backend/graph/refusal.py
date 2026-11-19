from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def normalize_fiscal_year_expressions(
    question: str,
    *,
    reference_date: date | None = None,
    two_digit_year_is_fiscal: bool = False,
) -> str:
    """질문의 시간 표현을 절대 사업연도로 바꾼다.

    두 자리 연도는 사업연도 문맥이 확인된 경우에만 변환한다.
    """
    if reference_date is None:
        reference_date = kst_today()

    offsets = {"올해": 0, "작년": -1, "재작년": -2, "내년": 1, "내후년": 2}
    normalized = re.sub(
        r"재작년|내후년|올해|작년|내년",
        lambda match: f"{reference_date.year + offsets[match.group(0)]}년",
        question,
    )

    if two_digit_year_is_fiscal:
        century = reference_date.year // 100 * 100
        def rewrite_short_year(match: re.Match[str]) -> str:
            # `25년 만기`처럼 기간의 길이를 뜻하면 사업연도로 해석하지 않는다.
            following = normalized[match.end():]
            if re.match(r"\s*(?:간|동안|만기|이상|이하|초과|미만|후|전|째|차|경력|근속|치)", following):
                return match.group(0)
            return f"{century + int(match.group(1))}년"

        normalized = re.sub(r"(?<![\dA-Za-z])'?(\d{2})\s*년", rewrite_short_year, normalized)
    return normalized


@dataclass(frozen=True)
class CorpusTarget:
    corp_name: str
    corp_code: str
    fiscal_year: int


@dataclass(frozen=True)
class CorpusCatalog:
    corp_names: frozenset[str]
    fiscal_years: frozenset[int]
    report_types: frozenset[str]
    targets: frozenset[CorpusTarget] = field(default_factory=frozenset)

    def resolve_target(
        self, question: str, *, reference_date: date | None = None
    ) -> CorpusTarget | None:
        if requires_filing_year_clarification(question):
            return None
        normalized = _normalized_question(question, reference_date)
        corp_name, _ = _extract_target_parts(normalized)
        if not corp_name:
            corp_name = _matching_company(normalized, self.targets)
        if not corp_name:
            return None

        years = _requested_years(normalized)
        if len(years) > 1:
            return None
        fiscal_year = years[0] if years else None
        matches = [
            target
            for target in self.targets
            if _normalize_company(target.corp_name) == corp_name
            and (fiscal_year is None or target.fiscal_year == fiscal_year)
        ]
        return matches[0] if len(matches) == 1 else None


    def resolve_comparison_targets(
        self, question: str, *, reference_date: date | None = None
    ) -> list[CorpusTarget]:
        """질문에 코퍼스 회사 이름이 2~3개 있으면 질문에 나온 순서대로 돌려준다(사전 등록 63번).

        다른 이름에 포함되는 짧은 이름은 뺀다. 사업연도가 둘 이상이거나 회사별 대상이 하나로 정해지지
        않으면 빈 목록(비교로 보지 않음).
        """

        normalized = _normalized_question(question, reference_date)
        text = _normalize_company(normalized)
        names = {
            name for target in self.targets
            if (name := _normalize_company(target.corp_name)) and name in text
        }
        names = {name for name in names if not any(name != other and name in other for other in names)}
        if not 2 <= len(names) <= 3:
            return []
        years = _requested_years(normalized)
        if len(years) > 1:
            return []
        fiscal_year = years[0] if years else None
        resolved = []
        for name in sorted(names, key=text.index):
            matches = [
                target for target in self.targets
                if _normalize_company(target.corp_name) == name
                and (fiscal_year is None or target.fiscal_year == fiscal_year)
            ]
            if len(matches) != 1:
                return []
            resolved.append(matches[0])
        return resolved


def load_corpus_catalog(path: str | Path) -> CorpusCatalog:
    corp_names: set[str] = set()
    fiscal_years: set[int] = set()
    report_types: set[str] = set()
    targets: set[CorpusTarget] = set()

    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue

        row = json.loads(line)
        corp_names.add(str(row["corp_name"]))
        fiscal_years.add(int(row["fiscal_year"]))
        report_types.add(str(row["report_type"]))
        if row.get("corp_code"):
            targets.add(
                CorpusTarget(
                    corp_name=str(row["corp_name"]),
                    corp_code=str(row["corp_code"]),
                    fiscal_year=int(row["fiscal_year"]),
                )
            )

    return CorpusCatalog(
        corp_names=frozenset(corp_names),
        fiscal_years=frozenset(fiscal_years),
        report_types=frozenset(report_types),
        targets=frozenset(targets),
    )


def kst_today() -> date:
    """현재 한국 시간의 날짜를 반환한다."""
    return datetime.now(timezone(timedelta(hours=9))).date()


def _normalize_company(name: str) -> str:
    compact = "".join(name.split())
    legal_marker = r"(?:\(주\)|㈜|주식회사)"
    return re.sub(rf"^{legal_marker}|{legal_marker}$", "", compact)


def _matching_company(question: str, targets: frozenset[CorpusTarget]) -> str | None:
    question_text = _normalize_company(question)
    names = {
        name for target in targets
        if (name := _normalize_company(target.corp_name)) and name in question_text
    }
    longest = max(map(len, names), default=0)
    best_names = [name for name in names if len(name) == longest]
    return best_names[0] if len(best_names) == 1 else None


def _normalized_question(question: str, reference_date: date | None) -> str:
    return " ".join(
        normalize_fiscal_year_expressions(
            question,
            reference_date=reference_date,
            two_digit_year_is_fiscal=True,
        ).split()
    )


def _extract_target_parts(question: str) -> tuple[str | None, int | None]:
    report_match = re.search(
        r"^(?P<corp_name>.+?)(?:의(?=\s)|(?=\s+(?:FY\d{4}|\d{4}\s*년)\s+))"
        r"(?:\s+(?:FY(?P<fy_year>\d{4})|(?P<ko_year>\d{4})\s*년))?\s+사업보고서",
        question,
    )
    if report_match is None:
        return None, None
    year = report_match.group("fy_year") or report_match.group("ko_year")
    return _normalize_company(report_match.group("corp_name")), int(year) if year else None


CLARIFICATION_REPLY_MAX_CHARS = 80


def clarified_question(original: str, reply: str) -> str:
    """되물음에 대한 답을 원래 질문과 합친다(162번).

    회사 이름이 없어 되물은 질문(original)에 사용자가 회사·사업연도만 답하면(reply) "답 + 원래 질문"으로 다시 묻는다.
    답이 그 자체로 새 질문이면(물음표가 있거나 80자를 넘음) 합치지 않고 답만 쓴다: 되물음을 무시하고 새로 물은 것이다.
    """

    original, reply = " ".join(original.split()), " ".join(reply.split())
    if not original or "?" in reply or len(reply) > CLARIFICATION_REPLY_MAX_CHARS:
        return reply
    return f"{reply} {original}"


def requires_filing_year_clarification(question: str) -> bool:
    """접수·제출·공시연도를 사업연도로 자동 해석하지 않는다."""
    if "사업보고서" not in question:
        return False
    year = r"(?:재작년|내후년|올해|작년|내년|(?<!\d)\d{2,4}\s*년)"
    return bool(re.search(year + r"(?:에|의)?\s*(?:접수|제출|공시)", question))


def requested_fiscal_years(
    question: str, *, reference_date: date | None = None
) -> tuple[int, ...]:
    """질문의 사업연도 후보를 보고서 연도 우선 규칙으로 반환한다."""
    return _requested_years(_normalized_question(question, reference_date))


def _requested_years(question: str) -> tuple[int, ...]:
    _, report_year = _extract_target_parts(question)
    if report_year is not None:
        # 보고서의 사업연도가 본문 안에서 비교하는 연도보다 우선한다.
        return (report_year,)
    if re.search(r"예측|전망", question):
        # 주제 밖 예측 질문은 기간 거절로 선점하지 않는다.
        return ()
    year_pattern = re.compile(
        r"(?<![A-Za-z0-9])(?:FY(?P<fy>\d{4})(?!\d)|(?P<ko>\d{4})\s*년)",
        re.IGNORECASE,
    )
    return tuple(dict.fromkeys(
        int(match.group("fy") or match.group("ko"))
        for match in year_pattern.finditer(question)
    ))


def classify_question(
    question: str,
    catalog: CorpusCatalog,
    *,
    reference_date: date | None = None,
) -> str | None:
    if requires_filing_year_clarification(question):
        return None
    normalized = _normalized_question(question, reference_date)
    report_name, _ = _extract_target_parts(normalized)
    if report_name and report_name not in {
        _normalize_company(name) for name in catalog.corp_names
    }:
        return "CORP_NOT_IN_CORPUS"

    years = _requested_years(normalized)
    if any(year not in catalog.fiscal_years for year in years):
        return "PERIOD_NOT_IN_CORPUS"

    corp_name = report_name or _matching_company(normalized, catalog.targets)
    if corp_name and catalog.targets:
        for year in years:
            if not any(
                _normalize_company(target.corp_name) == corp_name
                and target.fiscal_year == year
                for target in catalog.targets
            ):
                return "PERIOD_NOT_IN_CORPUS"

    if "분기보고서" in normalized and "quarterly" not in catalog.report_types:
        return "PERIOD_NOT_IN_CORPUS"

    if "반기보고서" in normalized and "half" not in catalog.report_types:
        return "PERIOD_NOT_IN_CORPUS"

    return None
