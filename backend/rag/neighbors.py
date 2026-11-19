"""검색된 조각의 이웃을 함께 돌려주는 후처리. Spec: docs/specs/d3-neighbor-expansion.md

확장 함수는 ``(rcept_no, ordinal)`` 조회만 쓰므로 저장소와 무관하다. 지금은 메모리
lookup으로 평가하고, 서비스 검색에서는 같은 함수에 DB lookup을 넘기면 된다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from backend.rag.retrieve import SearchFilters


class NeighborExpansionError(ValueError):
    """이웃 확장 입력이 잘못됐다."""


class NeighborLookup(Protocol):
    def get(self, rcept_no: str, ordinal: int) -> Mapping[str, Any] | None: ...


class InMemoryNeighborLookup:
    """청크 목록으로 만든 lookup. 적재·검색에서 빠지는 조각은 이웃으로도 쓰지 않는다."""

    def __init__(self, chunks: Iterable[Mapping[str, Any]]) -> None:
        self._chunks = {
            (str(chunk["rcept_no"]), int(chunk["ordinal"])): chunk
            for chunk in chunks
            if not chunk.get("near_dup") and not chunk.get("superseded")
        }

    def get(self, rcept_no: str, ordinal: int) -> Mapping[str, Any] | None:
        return self._chunks.get((rcept_no, ordinal))


@dataclass(frozen=True)
class ExpandedChunk:
    chunk: Mapping[str, Any]
    seed_rank: int  # 이 조각을 처음 끌고 온 seed의 순위(1부터)
    is_seed: bool


def _passes_filters(chunk: Mapping[str, Any], filters: SearchFilters | None) -> bool:
    if filters is None:
        return True
    return (
        str(chunk.get("corp_code")) == filters.corp_code
        and int(chunk.get("fiscal_year", 0)) == filters.fiscal_year
        and (filters.chapter is None or chunk.get("chapter") == filters.chapter)
        and (filters.note_no is None or chunk.get("note_no") == filters.note_no)
    )


def expand_neighbors(
    seeds: Sequence[Mapping[str, Any]],
    lookup: NeighborLookup,
    *,
    before: int,
    after: int,
    filters: SearchFilters | None = None,
) -> list[ExpandedChunk]:
    """seed를 순위대로 돌며 같은 문서·같은 절의 이웃을 붙인 평평한 목록을 만든다.

    묶음 안은 ordinal 오름차순이다. 같은 조각이 또 나오면 앞 위치만 남긴다.
    """

    if before < 0 or after < 0:
        raise NeighborExpansionError("before와 after는 0 이상이어야 합니다")
    expanded: list[ExpandedChunk] = []
    seen: set[str] = set()
    for seed_rank, seed in enumerate(seeds, start=1):
        rcept_no = str(seed["rcept_no"])
        seed_ordinal = int(seed["ordinal"])
        for ordinal in range(seed_ordinal - before, seed_ordinal + after + 1):
            is_seed = ordinal == seed_ordinal
            chunk = seed if is_seed else lookup.get(rcept_no, ordinal)
            if chunk is None or str(chunk["chunk_id"]) in seen:
                continue
            if not is_seed and (
                chunk.get("section_path") != seed.get("section_path")
                or not _passes_filters(chunk, filters)
            ):
                continue
            seen.add(str(chunk["chunk_id"]))
            expanded.append(ExpandedChunk(chunk, seed_rank, is_seed))
    return expanded


def budget_n_by_chars(lengths: Sequence[int], budget: int) -> int:
    """누적 글자 수가 예산을 넘지 않는 가장 긴 앞부분의 길이."""

    used = 0
    for count, length in enumerate(lengths, start=1):
        used += length
        if used > budget:
            return count - 1
    return len(lengths)


def _first_rank(chunk_ids: Sequence[str], gold: set[str]) -> int | None:
    return next((rank for rank, chunk_id in enumerate(chunk_ids, start=1) if chunk_id in gold), None)


def _reciprocal(rank: int | None) -> float:
    return 0.0 if rank is None else 1.0 / rank


def _change(flat_rank: int | None, baseline_rank: int | None) -> str:
    if flat_rank == baseline_rank:
        return "same"
    if flat_rank is None:
        return "worsened"
    if baseline_rank is None or flat_rank < baseline_rank:
        return "improved"
    return "worsened"


def expansion_metrics(
    dataset_rows: Sequence[Mapping[str, Any]],
    result_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """확장 결과를 같은 글자 수·같은 조각 수의 베이스라인과 비교한다.

    판정용은 평평한 목록의 위치다. seed 순위는 묶음을 한 칸으로 쳐서 조각 단위인
    베이스라인과 단위가 다르므로 함께 기록만 한다.
    """

    results = {int(row["id"]): row for row in result_rows}
    questions: list[dict[str, Any]] = []
    for item in sorted(dataset_rows, key=lambda row: int(row["id"])):
        if item.get("kind") != "relevant":
            continue
        row = results[int(item["id"])]
        gold = set(item["gold_chunks"])
        expansion = row["expansion"]
        flat = [str(entry["chunk_id"]) for entry in expansion["items"]]
        flat_rank = _first_rank(flat, gold)
        seed_rank = None if flat_rank is None else int(expansion["items"][flat_rank - 1]["seed_rank"])
        budget = row["baseline_budget"]
        baseline = row["baseline_chunks"]
        by_chars = _first_rank(baseline[: budget["by_chars_n"]], gold)
        by_chunks = _first_rank(baseline[: budget["by_chunks_n"]], gold)
        questions.append(
            {
                "id": int(item["id"]),
                "flat_rank": flat_rank,
                "seed_rank": seed_rank,
                "baseline_chars_rank": by_chars,
                "baseline_chunks_rank": by_chunks,
                "by_chars_n": budget["by_chars_n"],
                "total_chunks": expansion["total_chunks"],
                "total_chars": expansion["total_chars"],
                "change": _change(flat_rank, by_chars),
            }
        )
    if not questions:
        raise NeighborExpansionError("관련 문항이 없어 지표를 계산할 수 없습니다")

    count = len(questions)

    def hit(field: str) -> float:
        return sum(question[field] is not None for question in questions) / count

    def mrr(field: str) -> float:
        return sum(_reciprocal(question[field]) for question in questions) / count

    changes = [question["change"] for question in questions]
    return {
        "relevant_questions": count,
        "hit": hit("flat_rank"),
        "mrr_flat_rank": mrr("flat_rank"),
        "mrr_seed_rank": mrr("seed_rank"),
        "baseline_by_chars": {"hit": hit("baseline_chars_rank"), "mrr": mrr("baseline_chars_rank")},
        "baseline_by_chunks": {"hit": hit("baseline_chunks_rank"), "mrr": mrr("baseline_chunks_rank")},
        "improved": changes.count("improved"),
        "worsened": changes.count("worsened"),
        "same": changes.count("same"),
        "mean_total_chunks": sum(q["total_chunks"] for q in questions) / count,
        "mean_total_chars": sum(q["total_chars"] for q in questions) / count,
        "max_total_chars": max(q["total_chars"] for q in questions),
        "per_question": questions,
    }
