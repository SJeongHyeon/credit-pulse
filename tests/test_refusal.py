from datetime import date

import pytest

from backend.graph.refusal import (
    CorpusCatalog,
    CorpusTarget,
    classify_question,
    load_corpus_catalog,
    normalize_fiscal_year_expressions,
    requested_fiscal_years,
    requires_filing_year_clarification,
)


def test_classify_question_refuses_company_not_in_catalog():
    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
    )

    reason = classify_question(
        "가상기업의 사업보고서에서 차입금 현황을 알려주세요.",
        catalog,
    )

    assert reason == "CORP_NOT_IN_CORPUS"

def test_classify_question_refuses_fiscal_year_not_in_catalog():
    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
    )

    reason = classify_question(
        "오뚜기의 FY2024 사업보고서에서 차입금 현황을 알려주세요.",
        catalog,
    )

    assert reason == "PERIOD_NOT_IN_CORPUS"

def test_classify_question_allows_company_and_year_in_catalog():
    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
    )

    reason = classify_question(
        "오뚜기의 FY2025 사업보고서에서 차입금 현황을 알려주세요.",
        catalog,
    )

    assert reason is None

def test_classify_question_refuses_unknown_company_with_fiscal_year():
    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
    )

    reason = classify_question(
        "가상기업의 FY2025 사업보고서에서 차입금 현황을 알려주세요.",
        catalog,
    )

    assert reason == "CORP_NOT_IN_CORPUS"

def test_load_corpus_catalog_reads_values_from_manifest(tmp_path):
    manifest_path = tmp_path / "manifest.jsonl"
    manifest_path.write_text(
        "\n".join(
            [
                '{"corp_name":"오뚜기","corp_code":"00000001","fiscal_year":2025,"report_type":"annual"}',
                '{"corp_name":"가상기업","fiscal_year":2026,"report_type":"quarterly"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    catalog = load_corpus_catalog(manifest_path)

    assert catalog.corp_names == frozenset({"오뚜기", "가상기업"})
    assert catalog.fiscal_years == frozenset({2025, 2026})
    assert catalog.report_types == frozenset({"annual", "quarterly"})
    assert catalog.targets == frozenset(
        {CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2025)}
    )


def test_catalog_resolves_manifest_company_and_year_to_required_search_target():
    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
        targets=frozenset(
            {CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2025)}
        ),
    )

    assert catalog.resolve_target("오뚜기의 FY2025 사업보고서 차입금 현황") == CorpusTarget(
        corp_name="오뚜기", corp_code="00000001", fiscal_year=2025
    )

def test_classify_question_refuses_quarterly_report_not_in_catalog():
    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
    )

    reason = classify_question(
        "오뚜기의 FY2025 1분기보고서에서 차입금 현황을 알려주세요.",
        catalog,
    )

    assert reason == "PERIOD_NOT_IN_CORPUS"

@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("주가 하락 시 전환사채 리픽싱 조건이 있나요?", None),
        ("주가연계증권 보유 내역", None),
        ("내일 주가 예측해 줘", None),
        ("환율 전망 알려줘", None),
        ("오늘 서울 날씨를 알려줘", None),
        ("삼성전자 차입금 만기 구조 알려줘", None),
        ("오뚜기 2024년 사업보고서 소송 현황", "PERIOD_NOT_IN_CORPUS"),
        ("가상기업 2025년 사업보고서 차입금 내역", "CORP_NOT_IN_CORPUS"),
        ("오뚜기 2025년 사업보고서에서 2024년 차입금 내역 확인", None),
        ("(주)오뚜기의 사업보고서 차입금 내역", None),
        ("주식회사 오 뚜 기의 사업보고서 차입금 내역", None),
        ("오뚜기㈜의 사업보고서 차입금 내역", None),
    ],
)
def test_classify_question_with_unseen_phrasing(question, expected):
    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
    )
    assert classify_question(question, catalog) == expected


def test_classify_question_normalizes_catalog_company():
    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기 주식회사"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
    )
    assert classify_question("오뚜기의 사업보고서 차입금 내역", catalog) is None

def test_classify_question_refuses_half_year_report_not_in_catalog():
    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
    )

    result = classify_question(
        "오뚜기의 FY2025 반기보고서에서 차입금 만기 구조를 알려주세요.",
        catalog,
    )

    assert result == "PERIOD_NOT_IN_CORPUS"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("올해", "2026년"),
        ("작년", "2025년"),
        ("재작년", "2024년"),
        ("내년", "2027년"),
        ("내후년", "2028년"),
        ("2025년 FY2025", "2025년 FY2025"),
    ],
)
def test_normalize_fiscal_year_expressions_with_fixed_date(question, expected):
    assert normalize_fiscal_year_expressions(
        question, reference_date=date(2026, 11, 5)
    ) == expected


def test_two_digit_year_only_when_fiscal_and_not_duration():
    reference_date = date(2026, 11, 5)
    assert normalize_fiscal_year_expressions(
        "오뚜기 25년 매출액", reference_date=reference_date,
        two_digit_year_is_fiscal=True,
    ) == "오뚜기 2025년 매출액"
    assert normalize_fiscal_year_expressions(
        "25년 만기 사채", reference_date=reference_date,
        two_digit_year_is_fiscal=True,
    ) == "25년 만기 사채"


@pytest.mark.parametrize(
    ("question", "reason", "expected_year"),
    [
        ("오뚜기 작년 차입금", None, 2025),
        ("오뚜기 25년 매출액", None, 2025),
        ("오뚜기 올해 차입금", "PERIOD_NOT_IN_CORPUS", None),
        ("오뚜기 2026년 차입금", "PERIOD_NOT_IN_CORPUS", None),
        ("오뚜기 재작년 차입금", "PERIOD_NOT_IN_CORPUS", None),
        ("오뚜기 작년과 재작년 차입금 비교", "PERIOD_NOT_IN_CORPUS", None),
        ("오뚜기 FY2025 사업보고서에서 2024년 차입금 확인", None, 2025),
    ],
)
def test_classify_and_resolve_share_fiscal_year(question, reason, expected_year):
    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
        targets=frozenset({
            CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2025)
        }),
    )
    reference_date = date(2026, 11, 5)
    assert classify_question(question, catalog, reference_date=reference_date) == reason
    target = catalog.resolve_target(question, reference_date=reference_date)
    assert (target.fiscal_year if target else None) == expected_year


def test_forecast_question_does_not_become_period_refusal():
    catalog = CorpusCatalog(
        corp_names=frozenset({"티엠씨"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
    )
    assert classify_question(
        "티엠씨 사업보고서를 바탕으로 내년 원/달러 환율을 예측해 주세요.",
        catalog, reference_date=date(2026, 11, 5),
    ) is None


@pytest.mark.parametrize(
    "question",
    [
        "오뚜기 올해 접수된 사업보고서 차입금",
        "오뚜기 2026년에 제출된 사업보고서 차입금",
        "오뚜기 올해 공시된 사업보고서 차입금",
    ],
)
def test_filing_year_question_requires_fiscal_year(question):
    catalog = CorpusCatalog(
        corp_names=frozenset({"오뚜기"}),
        fiscal_years=frozenset({2025}),
        report_types=frozenset({"annual"}),
        targets=frozenset({
            CorpusTarget(corp_name="오뚜기", corp_code="00000001", fiscal_year=2025)
        }),
    )
    reference_date = date(2026, 11, 5)
    assert requires_filing_year_clarification(question)
    assert classify_question(question, catalog, reference_date=reference_date) is None
    assert catalog.resolve_target(question, reference_date=reference_date) is None


def test_explicit_report_year_with_filing_date_question_keeps_target():
    question = "오뚜기 FY2025 사업보고서 접수일 알려줘"
    assert not requires_filing_year_clarification(question)
    assert requested_fiscal_years(
        question, reference_date=date(2026, 11, 5)
    ) == (2025,)


def test_supported_and_missing_comparison_years_are_distinct():
    question = "오뚜기 작년과 재작년 차입금 비교"
    assert requested_fiscal_years(
        question, reference_date=date(2026, 11, 5)
    ) == (2025, 2024)
