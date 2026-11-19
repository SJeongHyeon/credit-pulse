"""Verified disclosure metadata gives each chunk bounded, source-linked neighbors."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from backend.rag.retrieve import SearchFilters


class DocumentGraph:
    def __init__(self, chunks: Sequence[Mapping[str, Any]]) -> None:
        self.chunks: dict[str, dict[str, Any]] = {}
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for source in chunks:
            if source.get("superseded"):
                continue
            row = dict(source)
            chunk_id = str(row["chunk_id"])
            if chunk_id in self.chunks:
                raise ValueError(f"duplicate graph chunk_id: {chunk_id}")
            self.chunks[chunk_id] = row
            group = (
                row["corpus_id"], row["rcept_no"], row["corp_code"], row["fiscal_year"],
                row["chapter"], tuple(row["section_path"]), row.get("note_no"),
            )
            groups[group].append(row)

        self.edges: dict[str, set[str]] = defaultdict(set)
        for rows in groups.values():
            ordered = sorted(rows, key=lambda row: (int(row["ordinal"]), str(row["chunk_id"])))
            for left, right in zip(ordered, ordered[1:]):
                if int(right["ordinal"]) - int(left["ordinal"]) != 1:
                    continue
                left_id, right_id = str(left["chunk_id"]), str(right["chunk_id"])
                self.edges[left_id].add(right_id)
                self.edges[right_id].add(left_id)

    def expand(
        self,
        seed_ids: Sequence[str],
        filters: SearchFilters,
        *,
        max_hops: int = 2,
        limit: int = 10,
        exclude_ids: Collection[str] = (),
    ) -> list[dict[str, Any]]:
        if max_hops < 0 or limit < 0:
            raise ValueError("max_hops and limit must be nonnegative")
        if not max_hops or not limit:
            return []
        excluded = set(exclude_ids)
        found: list[dict[str, Any]] = []
        for seed_id in seed_ids:
            if seed_id not in self.chunks:
                continue
            visited = {seed_id}
            frontier = [seed_id]
            for _hop in range(max_hops):
                next_frontier: list[str] = []
                for current in frontier:
                    neighbors = sorted(
                        self.edges[current],
                        key=lambda cid: (int(self.chunks[cid]["ordinal"]), cid),
                    )
                    for candidate_id in neighbors:
                        if candidate_id in visited:
                            continue
                        visited.add(candidate_id)
                        next_frontier.append(candidate_id)
                        row = self.chunks[candidate_id]
                        if candidate_id in excluded or not self._matches(row, filters):
                            continue
                        excluded.add(candidate_id)
                        found.append(dict(row))
                        if len(found) >= limit:
                            return found
                frontier = next_frontier
                if not frontier:
                    break
        return found

    @staticmethod
    def _matches(row: Mapping[str, Any], filters: SearchFilters) -> bool:
        return (
            row["corp_code"] == filters.corp_code
            and row["fiscal_year"] == filters.fiscal_year
            and (filters.chapter is None or row["chapter"] == filters.chapter)
            and (filters.note_no is None or row.get("note_no") == filters.note_no)
        )
