"""회사 별칭은 화면과 심사 건 생성에서 같은 고유번호를 가리킨다."""

import asyncio
import json
from pathlib import Path

import pytest

from backend.api.reviews import PostgresReviewRepository
from backend.company_aliases import find_company_matches
from backend.errors import ApiError, _handle_api_error

ROOT = Path(__file__).resolve().parents[1]


def test_alias_catalog_matches_active_manifest():
    active = {
        row["corp_code"]: row["corp_name"]
        for line in (ROOT / "data/manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if (row := json.loads(line)).get("status") == "success"
        and row.get("report_type") == "annual" and not row.get("superseded")
    }
    entries = json.loads((ROOT / "data/company_aliases.json").read_text(encoding="utf-8"))
    assert active
    assert len({entry["corp_code"] for entry in entries}) == len(entries)
    assert all(active.get(entry["corp_code"]) == entry["corp_name"] for entry in entries)
    rows = [{"corp_code": code, "corp_name": name} for code, name in active.items()]
    for entry in entries:
        for alias in entry["aliases"]:
            assert [row["corp_code"] for row in find_company_matches(rows, alias)] == [entry["corp_code"]]


class CompanyConnection:
    def __init__(self, rows):
        self.rows = rows
        self.year = None

    def execute(self, _sql, params):
        self.year = params[0]
        return self

    def fetchall(self):
        return [row for row in self.rows if row["fiscal_year"] == self.year]


ROWS = [
    {"corp_code": "00103042", "corp_name": "케이씨티시", "fiscal_year": 2025},
    {"corp_code": "00108038", "corp_name": "엔피씨", "fiscal_year": 2025},
    {"corp_code": "00231363", "corp_name": "LG유플러스", "fiscal_year": 2025},
    {"corp_code": "00266961", "corp_name": "NAVER", "fiscal_year": 2025},
    {"corp_code": "00356361", "corp_name": "LG화학", "fiscal_year": 2025},
    {"corp_code": "00540605", "corp_name": "이월드", "fiscal_year": 2025},
]


@pytest.mark.parametrize(("query", "expected"), [
    (" kctc ", "00103042"), ("ＮＰＣ", "00108038"),
    ("엘지화학", "00356361"), ("LG 화학", "00356361"),
    ("네이버", "00266961"), ("nAvEr", "00266961"),
    ("e-world", "00540605"),
    ("00103042", "00103042"), ("케이씨티시", "00103042"),
])
def test_review_create_resolves_canonical_name_alias_and_code(query, expected):
    connection = CompanyConnection(ROWS)
    code, _name = PostgresReviewRepository._find_corp(None, connection, query, 2025)
    assert code == expected
    assert connection.year == 2025


def test_short_prefix_returns_two_lg_candidates_instead_of_guessing():
    with pytest.raises(ApiError) as exc:
        PostgresReviewRepository._find_corp(None, CompanyConnection(ROWS), "엘지", 2025)
    assert exc.value.code == "VALIDATION_AMBIGUOUS_CORP"
    assert {item["corp_code"] for item in exc.value.details} == {"00231363", "00356361"}
    response = asyncio.run(_handle_api_error(None, exc.value))
    payload = json.loads(response.body)["error"]
    assert payload["code"] == "VALIDATION_AMBIGUOUS_CORP"
    assert payload["details"] == exc.value.details


def test_alias_cannot_create_a_review_without_document_for_selected_year():
    with pytest.raises(ApiError) as exc:
        PostgresReviewRepository._find_corp(None, CompanyConnection(ROWS), "KCTC", 2024)
    assert exc.value.code == "VALIDATION_CORP_NOT_FOUND"


def test_unregistered_phonetic_guess_does_not_select_a_company():
    assert find_company_matches(ROWS, "엘쥐화학") == []
