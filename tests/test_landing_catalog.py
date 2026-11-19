"""서비스 manifest를 첫 화면 데이터로 옮길 때의 증분·중복 처리."""
import json
from pathlib import Path

import pytest

from scripts.sync_landing_companies import load_companies, sync


ROOT = Path(__file__).resolve().parents[1]


def test_one_new_company_updates_the_list_and_html_without_manual_count(tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    original = (ROOT / "data/manifest.jsonl").read_text(encoding="utf-8")
    added = {
        "corp_code": "99999999", "corp_name": "시험기업", "fiscal_year": 2025,
        "filed_at": "20260331", "rcept_no": "20260331000001",
        "status": "success", "report_type": "annual", "superseded": False,
    }
    manifest.write_text(original.rstrip() + "\n" + json.dumps(added, ensure_ascii=False) + "\n", encoding="utf-8")
    data = tmp_path / "landing-data.js"
    html = tmp_path / "login.html"
    data.write_text((ROOT / "backend/static/landing-data.js").read_text(encoding="utf-8"), encoding="utf-8")
    html.write_text((ROOT / "backend/templates/login.html").read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest와 다릅니다"):
        sync(manifest, data, html, check=True)
    assert sync(manifest, data, html) == 101
    assert 'id="stat-companies">101<' in html.read_text(encoding="utf-8")
    assert 'name: "시험기업", corp_code: "99999999"' in data.read_text(encoding="utf-8")
    assert sync(manifest, data, html, check=True) == 101


def test_corrections_and_new_year_do_not_double_count_a_company(tmp_path):
    rows = [
        {"corp_code": "00000001", "corp_name": "가", "fiscal_year": 2025, "filed_at": "20260301",
         "rcept_no": "1", "status": "success", "report_type": "annual", "superseded": False},
        {"corp_code": "00000001", "corp_name": "가", "fiscal_year": 2026, "filed_at": "20270301",
         "rcept_no": "2", "status": "success", "report_type": "annual", "superseded": False},
        {"corp_code": "00000002", "corp_name": "나", "fiscal_year": 2025, "filed_at": "20260302",
         "rcept_no": "3", "status": "success", "report_type": "annual", "superseded": True},
    ]
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    assert load_companies(manifest) == [
        {"name": "가", "corp_code": "00000001", "fiscal_year": 2026, "filed_at": "2027-03-01"}
    ]
