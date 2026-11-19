"""FY2025 100사 업종 층화 표본을 결정적으로 선정한다."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


DEFAULT_REQUIRED_COMPANIES = (
    "효성중공업",
    "한일철강",
    "한화오션",
    "대한조선",
)
FINANCIAL_INDUSTRY_PREFIXES = {"64", "65", "66"}
_CORP_CODE_RE = re.compile(r"^\d{8}$")
_RCEPT_NO_RE = re.compile(r"^\d{14}$")


class TargetSelectionError(ValueError):
    """후보·기업개황 또는 층화 선정 계약이 올바르지 않을 때 발생한다."""


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(Path(path).open(encoding="utf-8"), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TargetSelectionError(f"{path}:{line_number} JSON 형식 오류") from exc
        if not isinstance(value, dict):
            raise TargetSelectionError(f"{path}:{line_number} 객체가 아닙니다")
        rows.append(value)
    return rows


def latest_candidates(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """회사별 원본/기재정정 중 최신 1건만 남긴다."""

    latest: dict[str, tuple[tuple[int, str, str], dict[str, Any]]] = {}
    for index, source in enumerate(rows, start=1):
        row = dict(source)
        corp_code = str(row.get("corp_code", ""))
        rcept_no = str(row.get("rcept_no", ""))
        corp_name = str(row.get("corp_name", "")).strip()
        if not _CORP_CODE_RE.fullmatch(corp_code):
            raise TargetSelectionError(f"후보 {index} corp_code 형식 오류")
        if not _RCEPT_NO_RE.fullmatch(rcept_no):
            raise TargetSelectionError(f"후보 {index} rcept_no 형식 오류")
        if not corp_name:
            raise TargetSelectionError(f"후보 {index} corp_name이 비어 있습니다")
        report_name = str(row.get("report_nm", ""))
        filed_at = str(row.get("filed_at") or row.get("rcept_dt", ""))
        key = (int(report_name.startswith("[기재정정]")), filed_at, rcept_no)
        previous = latest.get(corp_code)
        if previous is None or key > previous[0]:
            latest[corp_code] = (key, row)
    return [latest[code][1] for code in sorted(latest)]


def _industry_group(induty_code: str) -> str:
    value = str(induty_code).strip()
    if len(value) < 2 or not value.isdigit():
        raise TargetSelectionError(f"업종코드 형식 오류: {induty_code!r}")
    return value[:2]


def _stable_sample_key(seed: int, corp_code: str) -> str:
    return hashlib.sha256(f"{seed}:{corp_code}".encode()).hexdigest()


def _quotas(
    group_sizes: dict[str, int],
    required_counts: Counter[str],
    count: int,
) -> dict[str, int]:
    total = sum(group_sizes.values())
    ideals = {group: count * size / total for group, size in group_sizes.items()}
    quotas = {group: math.floor(value) for group, value in ideals.items()}
    remaining = count - sum(quotas.values())
    for group in sorted(
        group_sizes,
        key=lambda value: (-(ideals[value] - quotas[value]), value),
    )[:remaining]:
        quotas[group] += 1

    for group, required in required_counts.items():
        quotas[group] = max(quotas.get(group, 0), required)
    excess = sum(quotas.values()) - count
    while excess:
        reducible = [
            group
            for group in quotas
            if quotas[group] > required_counts.get(group, 0)
        ]
        if not reducible:
            raise TargetSelectionError("필수 포함 기업 때문에 표본 수를 맞출 수 없습니다")
        group = max(reducible, key=lambda value: (quotas[value] - ideals[value], value))
        quotas[group] -= 1
        excess -= 1
    return quotas


def select_stratified_targets(
    candidates: Iterable[dict[str, Any]],
    profiles: Iterable[dict[str, Any]],
    *,
    count: int = 100,
    seed: int = 20261030,
    required_companies: Iterable[str] = DEFAULT_REQUIRED_COMPANIES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if count < 1:
        raise TargetSelectionError("표본 수는 1 이상이어야 합니다")
    latest = latest_candidates(candidates)
    profiles_by_code: dict[str, dict[str, Any]] = {}
    for profile in profiles:
        corp_code = str(profile.get("corp_code", ""))
        if not _CORP_CODE_RE.fullmatch(corp_code):
            raise TargetSelectionError("기업개황 corp_code 형식 오류")
        if corp_code in profiles_by_code:
            raise TargetSelectionError(f"기업개황 corp_code 중복: {corp_code}")
        profiles_by_code[corp_code] = dict(profile)

    missing = sorted(
        row["corp_code"] for row in latest if row["corp_code"] not in profiles_by_code
    )
    if missing:
        raise TargetSelectionError(
            f"기업개황이 없는 후보 {len(missing)}개: {', '.join(missing[:5])}"
        )

    eligible: list[dict[str, Any]] = []
    excluded_financial = 0
    for candidate in latest:
        profile = profiles_by_code[candidate["corp_code"]]
        industry_group = _industry_group(str(profile.get("induty_code", "")))
        if industry_group in FINANCIAL_INDUSTRY_PREFIXES:
            excluded_financial += 1
            continue
        row = {
            **candidate,
            "induty_code": str(profile["induty_code"]),
            "industry_group": industry_group,
        }
        eligible.append(row)
    if len(eligible) < count:
        raise TargetSelectionError(
            f"금융업 제외 후 후보 {len(eligible)}개로 {count}사를 뽑을 수 없습니다"
        )

    required_names = tuple(dict.fromkeys(str(value) for value in required_companies))
    by_name = {row["corp_name"]: row for row in eligible}
    missing_required = [name for name in required_names if name not in by_name]
    if missing_required:
        raise TargetSelectionError(
            f"필수 포함 기업이 후보에 없습니다: {', '.join(missing_required)}"
        )

    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        by_group[row["industry_group"]].append(row)
    required_codes = {by_name[name]["corp_code"] for name in required_names}
    required_counts = Counter(
        row["industry_group"] for row in eligible if row["corp_code"] in required_codes
    )
    quotas = _quotas(
        {group: len(rows) for group, rows in by_group.items()},
        required_counts,
        count,
    )

    selected: list[dict[str, Any]] = []
    for group in sorted(by_group):
        ordered = sorted(
            by_group[group],
            key=lambda row: (_stable_sample_key(seed, row["corp_code"]), row["corp_code"]),
        )
        required = [row for row in ordered if row["corp_code"] in required_codes]
        others = [row for row in ordered if row["corp_code"] not in required_codes]
        chosen = [*required, *others[: quotas[group] - len(required)]]
        selected.extend(chosen)
    if len(selected) != count or len({row["corp_code"] for row in selected}) != count:
        raise TargetSelectionError("선정 결과의 회사 수 또는 고유성이 올바르지 않습니다")

    selected = sorted(
        selected,
        key=lambda row: (row["industry_group"], row["corp_code"]),
    )
    output = []
    for rank, row in enumerate(selected, start=1):
        output.append(
            {
                "rcept_no": str(row["rcept_no"]),
                "corp_code": str(row["corp_code"]),
                "corp_name": str(row["corp_name"]),
                "fiscal_year": int(row.get("fiscal_year", 2025)),
                "report_type": str(row.get("report_type", "annual")),
                "filed_at": str(row.get("filed_at") or row.get("rcept_dt", "")),
                "superseded": False,
                "induty_code": str(row["induty_code"]),
                "industry_group": str(row["industry_group"]),
                "selection_rank": rank,
                "required": row["corp_code"] in required_codes,
            }
        )
    summary = {
        "seed": seed,
        "requested": count,
        "candidate_companies": len(latest),
        "excluded_financial_companies": excluded_financial,
        "eligible_companies": len(eligible),
        "selected_companies": len(output),
        "required_companies": list(required_names),
        "industry_quotas": dict(sorted(quotas.items())),
    }
    return output, summary


def write_targets_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise TargetSelectionError("저장할 대상이 없습니다")
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output)
