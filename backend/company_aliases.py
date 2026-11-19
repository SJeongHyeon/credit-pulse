"""공시 회사명을 유지하면서 사람이 입력하는 별칭을 고유번호에 연결한다."""

from __future__ import annotations

import json
import unicodedata
from functools import lru_cache
from pathlib import Path

ALIASES_PATH = Path(__file__).resolve().parents[1] / "data" / "company_aliases.json"


def normalize_company_query(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


@lru_cache(maxsize=1)
def company_aliases() -> dict[str, tuple[str, ...]]:
    entries = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    return {entry["corp_code"]: tuple(entry["aliases"]) for entry in entries}


def find_company_matches(rows: list[dict], query: str) -> list[dict]:
    """정식 회사명·고유번호, 정확한 별칭, 부분 검색 순으로 후보를 고른다."""

    needle = normalize_company_query(query)
    if not needle:
        return []
    official, alias_exact, partial = [], [], []
    aliases = company_aliases()
    for row in rows:
        code, name = str(row["corp_code"]), str(row["corp_name"])
        normalized_name = normalize_company_query(name)
        normalized_aliases = [normalize_company_query(alias) for alias in aliases.get(code, ())]
        if needle == code or needle == normalized_name:
            official.append(row)
        elif needle in normalized_aliases:
            alias_exact.append(row)
        elif len(needle) >= 2 and any(needle in value for value in (normalized_name, *normalized_aliases)):
            partial.append(row)
    return official or alias_exact or partial
