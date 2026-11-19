"""원문 구조 그래프는 실제 청크 연결만 후보로 돌려준다."""

import hashlib

import pytest

from backend.rag.document_graph import DocumentGraph
from backend.rag.retrieve import SearchFilters


def chunk(chunk_id, ordinal, *, corpus="corpus-test", corp="00000001", receipt="20260301000001", year=2025,
          chapter="NOTES", section=("연결재무제표 주석", "지급보증"), note="12", superseded=False):
    content = f"원문 {chunk_id}"
    return {
        "chunk_id": chunk_id,
        "corpus_id": corpus,
        "rcept_no": receipt,
        "corp_code": corp,
        "corp_name": "테스트 회사",
        "fiscal_year": year,
        "chapter": chapter,
        "section_path": list(section),
        "note_no": note,
        "table_title": None,
        "ordinal": ordinal,
        "content": content,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "superseded": superseded,
    }


def test_expands_only_adjacent_chunks_within_same_document_section():
    graph = DocumentGraph([
        chunk("a", 1), chunk("b", 2), chunk("c", 3),
        chunk("other-receipt", 2, receipt="20260301000002"),
        chunk("other-section", 2, section=("연결재무제표 주석", "차입금")),
    ])

    found = graph.expand(
        ["a"], SearchFilters(corp_code="00000001", fiscal_year=2025),
        max_hops=2, limit=10, exclude_ids={"a"},
    )

    assert [row["chunk_id"] for row in found] == ["b", "c"]


def test_zero_candidate_budget_does_not_return_a_neighbor():
    graph = DocumentGraph([chunk("a", 1), chunk("b", 2)])

    found = graph.expand(
        ["a"], SearchFilters(corp_code="00000001", fiscal_year=2025), limit=0,
    )

    assert found == []


def test_graph_keeps_company_year_chapter_note_and_ordinal_gaps_separate():
    graph = DocumentGraph([
        chunk("seed", 1), chunk("valid", 2),
        chunk("different-company", 2, corp="00000002"),
        chunk("different-year", 2, year=2024),
        chunk("different-corpus", 2, corpus="corpus-other"),
        chunk("different-chapter", 2, chapter="XI"),
        chunk("different-note", 2, note="13"),
        chunk("gap", 4),
    ])

    found = graph.expand(
        ["seed"], SearchFilters(corp_code="00000001", fiscal_year=2025, chapter="NOTES", note_no="12"),
        max_hops=2, limit=10,
    )

    assert [row["chunk_id"] for row in found] == ["valid"]


def test_superseded_chunk_is_not_a_graph_node_or_bridge():
    graph = DocumentGraph([chunk("seed", 1), chunk("old", 2, superseded=True), chunk("third", 3)])

    found = graph.expand(["seed"], SearchFilters(corp_code="00000001", fiscal_year=2025))

    assert found == []


def test_duplicate_active_source_id_is_rejected():
    with pytest.raises(ValueError, match="duplicate graph chunk_id"):
        DocumentGraph([chunk("same", 1), chunk("same", 2)])
