"""청크가 바뀌어도 동결 정답을 쓰도록 본문 포함 규칙으로 옮긴다.

정답을 번호가 아니라 규칙으로 정의해야 코퍼스가 바뀌어도 살아남는다.
동결 파일은 건드리지 않고 파생 평가 파일을 만든다.
Spec: docs/specs/d3-short-table-merge.md
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from backend.rag.chunking import chunk_body


class GoldMappingError(ValueError):
    """정답을 옮길 수 없다. 조용히 빼지 않고 측정을 멈춘다."""


def _body(row: Mapping[str, Any]) -> str:
    return chunk_body(str(row["content"]), row.get("table_title"))


def _by_document(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["rcept_no"])].append(row)
    for chunks in grouped.values():
        chunks.sort(key=lambda row: int(row["ordinal"]))
    return grouped


def _align_document(
    old_chunks: Sequence[Mapping[str, Any]],
    new_chunks: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """옛 청크를 문서 순서대로 따라가며 새 청크에 대응시킨다."""

    aligned: dict[str, str] = {}
    cursor = 0
    for old in old_chunks:
        body = _body(old)
        position = cursor
        while position < len(new_chunks) and body not in str(new_chunks[position]["content"]):
            position += 1
        if position < len(new_chunks):
            aligned[str(old["chunk_id"])] = str(new_chunks[position]["chunk_id"])
            cursor = position
    return aligned


def map_gold_chunks(
    dataset_rows: Sequence[Mapping[str, Any]],
    old_chunks: Sequence[Mapping[str, Any]],
    new_chunks: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    """옛 gold chunk_id마다 새 chunk_id와 판정 종류를 돌려준다."""

    old_by_id = {str(row["chunk_id"]): row for row in old_chunks}
    old_documents = _by_document(old_chunks)
    new_documents = _by_document(new_chunks)
    alignments: dict[str, dict[str, str]] = {}
    mapping: dict[str, dict[str, str]] = {}
    for item in dataset_rows:
        for gold_id in item.get("gold_chunks") or []:
            if gold_id in mapping:
                continue
            old = old_by_id.get(str(gold_id))
            if old is None:
                raise GoldMappingError(f"옛 청크에 없는 정답입니다: {gold_id}")
            rcept_no = str(old["rcept_no"])
            body = _body(old)
            candidates = [
                row
                for row in new_documents.get(rcept_no, [])
                if body in str(row["content"]) and row["section_path"] == old["section_path"]
            ]
            if not candidates:
                raise GoldMappingError(f"새 청크에서 정답 본문을 찾지 못했습니다: {gold_id}")
            if len(candidates) == 1:
                mapping[str(gold_id)] = {
                    "new_chunk_id": str(candidates[0]["chunk_id"]),
                    "match": "exact",
                }
                continue
            if rcept_no not in alignments:
                alignments[rcept_no] = _align_document(
                    old_documents[rcept_no], new_documents[rcept_no]
                )
            ordered = alignments[rcept_no].get(str(gold_id))
            if ordered not in {str(row["chunk_id"]) for row in candidates}:
                raise GoldMappingError(f"문서 순서로 정답을 정하지 못했습니다: {gold_id}")
            mapping[str(gold_id)] = {"new_chunk_id": str(ordered), "match": "ordered"}
    return mapping


def derive_dataset(
    dataset_rows: Sequence[Mapping[str, Any]],
    mapping: Mapping[str, Mapping[str, str]],
    *,
    corpus_id: str,
) -> list[dict[str, Any]]:
    """gold_chunks와 corpus_version만 바꾼 복사본. 입력은 수정하지 않는다."""

    derived: list[dict[str, Any]] = []
    for row in dataset_rows:
        gold = list(
            dict.fromkeys(mapping[str(value)]["new_chunk_id"] for value in row.get("gold_chunks") or [])
        )
        derived.append({**row, "gold_chunks": gold, "corpus_version": corpus_id})
    return derived
