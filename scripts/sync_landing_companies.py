"""서비스 manifest에서 첫 화면 기업 목록과 보유 기업 수를 동기화한다.

사용: python scripts/sync_landing_companies.py [--check]
--check는 파일을 고치지 않고 차이만 검사한다(CI용).
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/manifest.jsonl"
LANDING_DATA = ROOT / "backend/static/landing-data.js"
LOGIN_HTML = ROOT / "backend/templates/login.html"
ALIASES = ROOT / "data/company_aliases.json"


def load_companies(manifest_path: Path) -> list[dict]:
    """활성 사업보고서 중 회사별 최신 공시 하나를 고른다."""
    latest: dict[str, dict] = {}
    for number, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("status") != "success" or row.get("report_type") != "annual" or row.get("superseded"):
            continue
        code, name, year, filed = (row.get(key) for key in ("corp_code", "corp_name", "fiscal_year", "filed_at"))
        if not isinstance(code, str) or not re.fullmatch(r"\d{8}", code):
            raise ValueError(f"manifest {number}행의 corp_code가 올바르지 않습니다")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"manifest {number}행의 corp_name이 비어 있습니다")
        if not isinstance(year, int) or isinstance(year, bool) or year < 1900:
            raise ValueError(f"manifest {number}행의 fiscal_year가 올바르지 않습니다")
        if not isinstance(filed, str) or not re.fullmatch(r"\d{8}", filed):
            raise ValueError(f"manifest {number}행의 filed_at이 올바르지 않습니다")
        try:
            datetime.strptime(filed, "%Y%m%d")
        except ValueError as exc:
            raise ValueError(f"manifest {number}행의 filed_at이 실제 날짜가 아닙니다") from exc
        previous = latest.get(code)
        if previous is None or (year, filed, str(row.get("rcept_no", ""))) > (
            previous["fiscal_year"], previous["filed_raw"], previous["rcept_no"]
        ):
            latest[code] = {
                "name": name,
                "corp_code": code,
                "fiscal_year": year,
                "filed_at": f"{filed[:4]}-{filed[4:6]}-{filed[6:]}",
                "filed_raw": filed,
                "rcept_no": str(row.get("rcept_no", "")),
            }
    if not latest:
        raise ValueError("서비스 manifest에 사용 가능한 사업보고서가 없습니다")
    return [{key: row[key] for key in ("name", "corp_code", "fiscal_year", "filed_at")}
            for _, row in sorted(latest.items())]


def load_aliases(path: Path = ALIASES) -> dict[str, dict]:
    entries = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    for entry in entries:
        code = entry["corp_code"]
        if not re.fullmatch(r"\d{8}", code) or code in result:
            raise ValueError(f"별칭의 고유번호가 중복되거나 올바르지 않습니다: {code}")
        if not isinstance(entry.get("aliases"), list) or not entry["aliases"]:
            raise ValueError(f"별칭이 비어 있습니다: {code}")
        result[code] = entry
    return result


def render_landing_data(companies: list[dict], aliases: dict[str, dict] | None = None) -> str:
    aliases = load_aliases() if aliases is None else aliases
    lines = [
        "// data/manifest.jsonl의 활성 사업보고서 기업 목록. scripts/sync_landing_companies.py가 생성한다.",
        "// 키 없는 데모 검색 범위(data/snapshot)는 이 목록보다 작을 수 있다.",
        "export const COMPANIES = Object.freeze([",
    ]
    for company in companies:
        alias = aliases.get(company["corp_code"])
        if alias and alias["corp_name"] != company["name"]:
            raise ValueError(f"별칭의 회사명이 manifest와 다릅니다: {company['corp_code']}")
        line = (
            "  { name: " + json.dumps(company["name"], ensure_ascii=False)
            + ", corp_code: " + json.dumps(company["corp_code"])
            + f', fiscal_year: {company["fiscal_year"]}, filed_at: '
            + json.dumps(company["filed_at"])
        )
        if alias:
            line += ", aliases: " + json.dumps(alias["aliases"], ensure_ascii=False)
        lines.append(line + " },")
    return "\n".join([*lines, "]);", ""])


def render_login_html(original: str, companies: list[dict]) -> str:
    values = (
        (r'(<span id="stat-companies">)\d+(</span>)', len(companies)),
        (r'(<strong id="stat-year" class="stat-value">)\d+(</strong>)',
         max(company["fiscal_year"] for company in companies)),
    )
    for pattern, value in values:
        original, matches = re.subn(pattern, lambda match: f"{match[1]}{value}{match[2]}", original)
        if matches != 1:
            raise ValueError(f"login.html에 핵심 수치 자리 {pattern!r}가 정확히 하나 있어야 합니다")
    return original


def sync(manifest_path: Path = MANIFEST, data_path: Path = LANDING_DATA,
         html_path: Path = LOGIN_HTML, *, check: bool = False) -> int:
    companies = load_companies(manifest_path)
    outputs = {
        data_path: render_landing_data(companies),
        html_path: render_login_html(html_path.read_text(encoding="utf-8"), companies),
    }
    stale = [path for path, expected in outputs.items()
             if not path.exists() or path.read_text(encoding="utf-8") != expected]
    if check:
        if stale:
            raise ValueError("첫 화면 기업 목록이 manifest와 다릅니다: "
                             + ", ".join(str(path) for path in stale))
    else:
        for path, content in outputs.items():
            if path in stale:
                path.write_text(content, encoding="utf-8", newline="\n")
    return len(companies)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="파일을 수정하지 않고 manifest와 일치하는지만 확인")
    args = parser.parse_args()
    print(f"보유 기업 {sync(check=args.check)}곳 {'확인' if args.check else '동기화'}")
