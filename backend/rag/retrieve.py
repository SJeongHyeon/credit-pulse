"""검색 백엔드와 에이전트 사이의 D2 계약.

실제 벡터·BM25 구현은 이 모듈의 ``RetrievalBackend``를 구현한다. 이 계층은
후처리 필터로 결과를 굶기지 않도록 백엔드 결과가 요청 필터와 일치하는지
검증하고, 계약 오류를 빈 결과로 숨기지 않는다.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Protocol, Sequence


_CORP_CODE_RE = re.compile(r"^\d{8}$")
_CHAPTERS = {"II", "IV", "XI", "NOTES"}


class RetrievalContractError(ValueError):
    """검색 입력 또는 백엔드 출력이 팀 계약과 다를 때 발생한다."""


class RetrieverNotConfigured(RuntimeError):
    """실제 검색 백엔드가 주입되지 않았을 때 발생한다."""


@dataclass(frozen=True)
class SearchFilters:
    corp_code: str
    fiscal_year: int
    chapter: str | None = None
    note_no: str | None = None

    def __post_init__(self) -> None:
        if not _CORP_CODE_RE.fullmatch(self.corp_code):
            raise RetrievalContractError("corp_code는 8자리 숫자여야 합니다")
        if not 2000 <= self.fiscal_year <= 2100:
            raise RetrievalContractError("fiscal_year는 2000~2100이어야 합니다")
        if self.chapter is not None and self.chapter not in _CHAPTERS:
            raise RetrievalContractError(
                "chapter는 II, IV, XI, NOTES 중 하나여야 합니다"
            )
        if self.note_no is not None and not str(self.note_no).strip():
            raise RetrievalContractError("note_no는 공백일 수 없습니다")


@dataclass(frozen=True)
class ScoredChunk:
    chunk_id: str
    corpus_id: str
    rcept_no: str
    corp_code: str
    corp_name: str
    fiscal_year: int
    chapter: str
    section_path: tuple[str, ...]
    note_no: str | None
    table_title: str | None
    ordinal: int
    content: str
    content_sha256: str
    score: float
    rank: int
    retrieval_method: str

    def __post_init__(self) -> None:
        if not self.chunk_id or not self.corpus_id:
            raise RetrievalContractError("chunk_id와 corpus_id가 필요합니다")
        if not _CORP_CODE_RE.fullmatch(self.corp_code):
            raise RetrievalContractError("결과 corp_code는 8자리 숫자여야 합니다")
        if self.chapter not in _CHAPTERS:
            raise RetrievalContractError("결과 chapter가 허용 범위가 아닙니다")
        if self.ordinal < 1 or self.rank < 1:
            raise RetrievalContractError("ordinal과 rank는 1 이상이어야 합니다")
        if not self.content.strip():
            raise RetrievalContractError("결과 content는 비어 있을 수 없습니다")
        if not math.isfinite(self.score):
            raise RetrievalContractError("score는 유한한 숫자여야 합니다")
        if not self.retrieval_method.strip():
            raise RetrievalContractError("retrieval_method가 필요합니다")

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "corpus_id": self.corpus_id,
            "rcept_no": self.rcept_no,
            "corp_code": self.corp_code,
            "corp_name": self.corp_name,
            "fiscal_year": self.fiscal_year,
            "chapter": self.chapter,
            "section_path": list(self.section_path),
            "note_no": self.note_no,
            "table_title": self.table_title,
            "ordinal": self.ordinal,
            "content": self.content,
            "content_sha256": self.content_sha256,
            "score": self.score,
            "rank": self.rank,
            "retrieval_method": self.retrieval_method,
        }


class RetrievalBackend(Protocol):
    def search(
        self,
        question: str,
        *,
        filters: SearchFilters,
        top_k: int,
    ) -> Sequence[ScoredChunk]: ...


def _matches_filters(chunk: ScoredChunk, filters: SearchFilters) -> bool:
    return (
        chunk.corp_code == filters.corp_code
        and chunk.fiscal_year == filters.fiscal_year
        and (filters.chapter is None or chunk.chapter == filters.chapter)
        and (filters.note_no is None or chunk.note_no == filters.note_no)
    )


def retrieve(
    question: str,
    *,
    filters: SearchFilters,
    top_k: int = 10,
    backend: RetrievalBackend | None = None,
) -> list[dict]:
    """주입된 백엔드를 호출하고 검색 계약을 검증한 dict 결과를 반환한다."""

    normalized_question = " ".join(question.split())
    if not normalized_question:
        raise RetrievalContractError("question은 공백일 수 없습니다")
    if not 1 <= top_k <= 100:
        raise RetrievalContractError("top_k는 1~100이어야 합니다")
    if backend is None:
        raise RetrieverNotConfigured("검색 백엔드가 연결되지 않았습니다")

    results = list(
        backend.search(
            normalized_question,
            filters=filters,
            top_k=top_k,
        )
    )
    if len(results) > top_k:
        raise RetrievalContractError("백엔드 결과가 top_k를 초과했습니다")
    if len({chunk.chunk_id for chunk in results}) != len(results):
        raise RetrievalContractError("백엔드 결과에 중복 chunk_id가 있습니다")

    for expected_rank, chunk in enumerate(results, start=1):
        if chunk.rank != expected_rank:
            raise RetrievalContractError("rank는 1부터 빈틈없이 증가해야 합니다")
        if not _matches_filters(chunk, filters):
            raise RetrievalContractError(
                "백엔드가 검색 전 필터와 일치하지 않는 결과를 반환했습니다"
            )
    return [chunk.to_dict() for chunk in results]
