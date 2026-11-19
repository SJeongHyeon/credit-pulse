"""목차 어휘 기반 질의 번역(사전 등록 152번).

일상어 질문은 공시의 말(항목 이름)과 달라 첫 검색이 정답 청크를 놓친다(EVAL_REPORT 4-47).
추측으로 용어를 바꾸게 하면 효과가 없었으므로(4-48) 이 모듈은 **그 회사 보고서에 실제로 있는
항목 이름 목록 안에서만** 고르게 한다.

- 어휘는 청크 메타데이터(section_path·table_title)에서 코드가 만든다. 질문별로 손대지 않는다.
- 선택기(LLM)는 질문과 이 목록만 본다. 목록에 없는 이름은 코드가 버린다.
- 선택기가 실패하거나 아무것도 고르지 못하면 원래 질의로 검색한다(기존 동작).
- 덧붙이는 곳은 첫 검색 질의뿐이다. 리랭커와 작성기에는 원래 질문을 준다(호출하는 쪽의 책임).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

MAX_TERMS = 3
# 아라비아 번호(14.)와 로마 숫자 장 번호(XI.) 둘 다. 번호 뒤에 마침표나 괄호가 있어야 번호로 본다(2025년 같은 말은 그대로).
_NUMBERING = re.compile(r"^\s*(?:\d+(?:\.\d+)*|[IVX]+)\s*[.)]\s*")

# 선택기: (질문, 항목 이름 목록) -> 고른 이름들. 목록 밖 이름은 호출하는 쪽이 버린다.
Selector = Callable[[str, Sequence[str]], Sequence[str]]


def _normalize(text: object) -> str:
    return " ".join(str(text).split())


def display_name(heading: str) -> str:
    """'14. 차입금 (연결)' → '차입금 (연결)'. 번호는 질의에 넣어도 검색에 도움이 되지 않는다."""

    stripped = _NUMBERING.sub("", _normalize(heading))
    return stripped or _normalize(heading)


def heading_vocabulary(chunks: Iterable[Mapping[str, Any]], *, corp_code: str, fiscal_year: int) -> list[str]:
    """그 회사·사업연도 청크에 실제로 있는 항목 이름. 첫 등장 순서(ordinal), 중복 없음.

    대체된(superseded) 청크는 보지 않는다. 번호를 뗀 이름이 같으면 하나로 본다.
    """

    rows = [
        row
        for row in chunks
        if str(row.get("corp_code")) == str(corp_code)
        and int(row.get("fiscal_year", 0)) == int(fiscal_year)
        and not row.get("superseded")
    ]
    rows.sort(key=lambda row: int(row.get("ordinal", 0)))
    seen: set[str] = set()
    vocabulary: list[str] = []
    for row in rows:
        names = [*(row.get("section_path") or ()), row.get("table_title")]
        for raw in names:
            if not raw:
                continue
            name = display_name(raw)
            if name and name not in seen:
                seen.add(name)
                vocabulary.append(name)
    return vocabulary


def accept_terms(proposed: Iterable[object], vocabulary: Sequence[str], *, limit: int = MAX_TERMS) -> list[str]:
    """목록에 정확히 있는 이름만, 중복 없이 limit개까지. 번호가 붙어 와도 번호를 뗀 이름으로 비교한다."""

    allowed = set(vocabulary)
    accepted: list[str] = []
    for item in proposed:
        name = display_name(str(item))
        if name in allowed and name not in accepted:
            accepted.append(name)
        if len(accepted) >= limit:
            break
    return accepted


@dataclass(frozen=True)
class BridgeResult:
    query: str  # 첫 검색에 쓸 질의(덧붙인 것). 고른 것이 없으면 원래 질의 그대로
    terms: list[str] = field(default_factory=list)
    vocabulary_size: int = 0
    error: str | None = None  # 선택기 실패 사유(검색은 원래 질의로 계속한다)


class TermBridge:
    """선택기를 감싸 첫 검색 질의에 항목 이름을 덧붙인다. 같은 (회사·연도·질문)은 한 번만 묻는다."""

    def __init__(self, chunks: Iterable[Mapping[str, Any]], selector: Selector, *, limit: int = MAX_TERMS) -> None:
        self._chunks = list(chunks)
        self._selector = selector
        self._limit = limit
        self._vocabularies: dict[tuple[str, int], list[str]] = {}
        self._selected: dict[tuple[str, int, str], tuple[list[str], str | None]] = {}

    def vocabulary(self, corp_code: str, fiscal_year: int) -> list[str]:
        key = (str(corp_code), int(fiscal_year))
        if key not in self._vocabularies:
            self._vocabularies[key] = heading_vocabulary(self._chunks, corp_code=key[0], fiscal_year=key[1])
        return self._vocabularies[key]

    def bridge(self, question: str, query: str, *, corp_code: str, fiscal_year: int) -> BridgeResult:
        vocabulary = self.vocabulary(corp_code, fiscal_year)
        question = _normalize(question)
        query = _normalize(query)
        if not vocabulary or not question:
            return BridgeResult(query=query, vocabulary_size=len(vocabulary))
        key = (str(corp_code), int(fiscal_year), question)
        if key not in self._selected:
            try:
                proposed = self._selector(question, vocabulary)
                self._selected[key] = (accept_terms(proposed, vocabulary, limit=self._limit), None)
            except Exception as exc:  # 선택기 실패가 검색 실패로 번지면 안 된다(사전 등록 152번 회귀 위험 ④)
                self._selected[key] = ([], f"{type(exc).__name__}: {exc}"[:200])
        terms, error = self._selected[key]
        bridged = " ".join([query, *terms]) if terms else query
        return BridgeResult(query=bridged, terms=list(terms), vocabulary_size=len(vocabulary), error=error)
