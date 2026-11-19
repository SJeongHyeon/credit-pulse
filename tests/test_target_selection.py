import pytest

from backend.rag.target_selection import (
    TargetSelectionError,
    select_stratified_targets,
)


def candidate(index: int, name: str, *, revision: bool = False) -> dict:
    return {
        "rcept_no": f"20260312{index:06d}",
        "corp_code": f"{index:08d}",
        "corp_name": name,
        "fiscal_year": 2025,
        "report_type": "annual",
        "filed_at": "20260312",
        "report_nm": (
            "[기재정정]사업보고서 (2025.12)"
            if revision
            else "사업보고서 (2025.12)"
        ),
    }


def profile(index: int, industry: str) -> dict:
    return {
        "corp_code": f"{index:08d}",
        "corp_name": f"회사-{index}",
        "corp_cls": "Y",
        "stock_code": f"{index:06d}",
        "induty_code": industry,
    }


def test_stratified_selection_is_deterministic_and_keeps_required_companies():
    names = [
        "효성중공업",
        "한일철강",
        "한화오션",
        "대한조선",
        "일반A",
        "일반B",
        "일반C",
        "일반D",
        "일반E",
        "금융사",
    ]
    candidates = [candidate(index, name) for index, name in enumerate(names, start=1)]
    candidates.append(
        {
            **candidate(1, "효성중공업", revision=True),
            "rcept_no": "20260323000001",
            "filed_at": "20260323",
        }
    )
    profiles = [
        profile(index, "10100" if index <= 5 else "20100")
        for index in range(1, 10)
    ]
    profiles.append(profile(10, "64100"))

    first, first_summary = select_stratified_targets(
        candidates, profiles, count=6, seed=20261030
    )
    second, second_summary = select_stratified_targets(
        reversed(candidates), reversed(profiles), count=6, seed=20261030
    )

    assert first == second
    assert first_summary == second_summary
    assert len(first) == 6
    assert {row["corp_name"] for row in first}.issuperset(names[:4])
    assert all(row["industry_group"] != "64" for row in first)
    hyosung = next(row for row in first if row["corp_name"] == "효성중공업")
    assert hyosung["rcept_no"] == "20260323000001"
    assert first_summary["excluded_financial_companies"] == 1


def test_stratified_selection_requires_complete_company_profiles():
    candidates = [
        candidate(index, name)
        for index, name in enumerate(
            ["효성중공업", "한일철강", "한화오션", "대한조선"], start=1
        )
    ]
    profiles = [profile(index, "10100") for index in range(1, 4)]

    with pytest.raises(TargetSelectionError, match="기업개황이 없는 후보"):
        select_stratified_targets(candidates, profiles, count=4)
