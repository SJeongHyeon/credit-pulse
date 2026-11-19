"""D3 짧은 표 합치기 테스트. 임베딩과 원문 없이 작은 fixture로 검증한다."""
import json
from dataclasses import replace

import pytest

from backend.rag.chunking import (
    MERGE_INTRO_MODE,
    MERGE_TABLE_ONLY_MODE,
    build_chunks,
    content_sha256,
    merge_short_chunks,
    merged_chunks_preserve_sources,
)
from backend.rag.gold_mapping import GoldMappingError, derive_dataset, map_gold_chunks
from backend.rag.ingest import _build_parser, _corpus_id, _preserves_blocks
from backend.rag.models import ChunkRecord, DocumentMetadata, ParsedBlock

SECTION = ("5. 재무제표 주석", "25. 수익")
TITLE = "25. 수익"
DOC = "20260312000123"


def chunk(ordinal, content, *, table=True, title=TITLE, section=SECTION, note="25", chapter="NOTES", rcept_no=DOC):
    text = f"{title} {content}" if title and table else content
    return ChunkRecord(
        chunk_id=f"old-{ordinal}",
        corpus_id="corpus-" + "a" * 24,
        rcept_no=rcept_no,
        corp_code="00000001",
        corp_name="회사",
        fiscal_year=2025,
        chapter=chapter,
        section_path=section,
        note_no=note,
        table_title=title if table else None,
        ordinal=ordinal,
        content=text,
        content_sha256=content_sha256(text),
        is_table=table,
    )


def merge(chunks, *, intro=False, max_chars=1000):
    return merge_short_chunks(chunks, include_intro=intro, max_chars=max_chars)


def texts(chunks):
    return [item.content for item in chunks]


def test_label_and_data_become_one_chunk_with_single_title():
    rows = [
        chunk(1, "주요 고객에 대한 정보의 공시"),
        chunk(2, "주요 고객에 대한 정보의 공시 당기 | (단위 : 천원)"),
        chunk(3, "A사 수익 | 46,589,675"),
    ]

    assert texts(merge(rows)) == [
        "25. 수익 주요 고객에 대한 정보의 공시 당기 | (단위 : 천원) A사 수익 | 46,589,675"
    ]


def test_overlap_is_removed_only_at_word_boundary():
    rows = [
        chunk(1, "기타금융자산 변동내역"),
        chunk(2, "기타금융자산 변동내역에 대한 공시 당기 | (단위 : 백만원)"),
        chunk(3, "A | 12,345"),
    ]

    merged = merge(rows)

    assert "변동내역에 대한 공시" in merged[0].content
    assert merged_chunks_preserve_sources(rows, merged) is True


def test_next_period_label_starts_new_chunk():
    rows = [
        chunk(1, "당기 | (단위 : 천원)"),
        chunk(2, "A사 수익 | 46,589,675"),
        chunk(3, "전기 | (단위 : 천원)"),
        chunk(4, "A사 수익 | 47,017,529"),
    ]

    merged = merge(rows)

    assert len(merged) == 2
    assert "전기" not in merged[0].content
    assert "47,017,529" in merged[1].content


def test_two_data_chunks_are_never_merged():
    rows = [chunk(1, "A사 | 46,589,675"), chunk(2, "B사 | 12,345,678")]

    assert len(merge(rows)) == 2


@pytest.mark.parametrize(
    "other",
    [
        {"section": ("5. 재무제표 주석", "26. 비용")},
        {"note": "26"},
        {"chapter": "II"},
    ],
)
def test_boundary_change_stops_merge(other):
    rows = [chunk(1, "당기 | (단위 : 천원)"), chunk(2, "A사 | 46,589,675", **other)]

    assert len(merge(rows)) == 2


def test_long_table_is_not_a_target():
    rows = [chunk(1, "당기 | (단위 : 천원)"), chunk(2, "가 12,345 " * 30)]

    assert len(merge(rows)) == 2


def test_table_of_199_chars_is_target_and_200_is_not():
    label = chunk(1, "(단위 : 천원)")
    for size, expected in ((199, 1), (200, 2)):
        body = "9" * (size - len(TITLE) - 1)
        assert len(merge([label, chunk(2, body)])) == expected


def test_merged_result_never_exceeds_max_chars():
    rows = [chunk(1, "라벨 " * 20), chunk(2, "12,345 " * 8)]  # 각각은 상한 안, 합치면 초과
    limit = len(rows[0].content) + 5

    merged = merge(rows, max_chars=limit)

    assert len(merged) == 2
    assert all(len(item.content) <= limit for item in merged)


def test_merge_at_exact_limit_is_allowed():
    rows = [chunk(1, "라벨"), chunk(2, "12,345")]
    limit = len("25. 수익 라벨 12,345")

    assert len(merge(rows, max_chars=limit)) == 1
    assert len(merge(rows, max_chars=limit - 1)) == 2


def test_intro_is_merged_only_in_intro_mode():
    rows = [
        chunk(1, "환율 10% 변동 시 영향은 다음과 같습니다.", table=False, title=None),
        chunk(2, "(단위 : 백만원)"),
        chunk(3, "USD | 16 | (16)"),
    ]

    with_intro = merge(rows, intro=True)
    without = merge(rows, intro=False)

    assert len(with_intro) == 1
    assert with_intro[0].content.startswith("환율 10% 변동")
    assert with_intro[0].is_table is True
    assert with_intro[0].table_title == TITLE
    assert len(without) == 2
    assert without[0].is_table is False


def test_intro_followed_by_long_table_stays_alone():
    rows = [
        chunk(1, "소개 문장입니다.", table=False, title=None),
        chunk(2, "가 12,345 " * 30),
    ]

    assert texts(merge(rows, intro=True)) == [rows[0].content, rows[1].content]


def test_long_intro_is_not_a_target():
    rows = [chunk(1, "설명 " * 80, table=False, title=None), chunk(2, "(단위 : 원)")]

    assert len(merge(rows, intro=True)) == 2


def test_intro_after_data_starts_new_group():
    rows = [
        chunk(1, "A사 | 46,589,675"),
        chunk(2, "다음 표는 전기입니다.", table=False, title=None),
        chunk(3, "B사 | 47,017,529"),
    ]

    merged = merge(rows, intro=True)

    assert len(merged) == 2
    assert merged[1].content.startswith("다음 표는")


def test_unmerged_chunk_keeps_content_but_gets_new_ordinal_and_id():
    rows = [chunk(1, "(단위 : 원)"), chunk(2, "A | 12,345"), chunk(3, "가 12,345 " * 30)]

    merged = merge(rows)

    assert [item.ordinal for item in merged] == [1, 2]
    assert merged[1].content == rows[2].content
    assert merged[1].chunk_id != rows[2].chunk_id
    assert merged[1].content_sha256 == rows[2].content_sha256


def test_merged_chunk_hash_matches_content():
    merged = merge([chunk(1, "(단위 : 원)"), chunk(2, "A | 12,345")])

    assert merged[0].content_sha256 == content_sha256(merged[0].content)


def test_preserve_check_detects_lost_chunk():
    rows = [chunk(1, "(단위 : 원)"), chunk(2, "A | 12,345"), chunk(3, "가 12,345 " * 30)]
    merged = merge(rows)

    assert merged_chunks_preserve_sources(rows, merged) is True
    assert merged_chunks_preserve_sources(rows, merged[:-1]) is False


def metadata():
    return DocumentMetadata(
        rcept_no=DOC,
        corp_code="00000001",
        corp_name="회사",
        fiscal_year=2025,
        filed_at="20260312",
        collected_at="2026-11-02T00:00:00Z",
        source_url="https://example.invalid/doc",
        raw_sha256="0" * 64,
    )


def blocks():
    def table(ordinal, text, group):
        return ParsedBlock("NOTES", SECTION, "25", TITLE, ordinal, text, True, group)

    return [
        table(1, "당기 | (단위 : 천원)", "t1"),
        table(2, "A사 수익 | 46,589,675", "t2"),
    ]


@pytest.mark.parametrize("mode", [MERGE_INTRO_MODE, MERGE_TABLE_ONLY_MODE])
def test_build_chunks_applies_merge_modes(mode):
    merged = build_chunks(metadata(), blocks(), table_mode=mode)

    assert len(merged) == 1
    assert "당기" in merged[0].content and "46,589,675" in merged[0].content


def test_group_mode_is_unchanged():
    assert len(build_chunks(metadata(), blocks(), table_mode="group")) == 2


def test_intro_must_be_directly_before_table():
    rows = [
        chunk(1, "첫 문단입니다.", table=False, title=None),
        chunk(2, "소개 문장입니다.", table=False, title=None),
        chunk(3, "(단위 : 원)"),
        chunk(4, "A | 12,345"),
    ]

    merged = merge(rows, intro=True)

    assert merged[0].content == "첫 문단입니다."
    assert merged[1].content.startswith("소개 문장입니다.")


def test_table_only_mode_leaves_intro_alone():
    intro = ParsedBlock("NOTES", SECTION, "25", None, 0, "소개 문장입니다.", False, None)

    merged = build_chunks(metadata(), [intro, *blocks()], table_mode=MERGE_TABLE_ONLY_MODE)
    with_intro = build_chunks(metadata(), [intro, *blocks()], table_mode=MERGE_INTRO_MODE)

    assert [item.content for item in merged][0] == "소개 문장입니다."
    assert len(merged) == 2
    assert len(with_intro) == 1


@pytest.mark.parametrize("mode", [MERGE_INTRO_MODE, MERGE_TABLE_ONLY_MODE])
def test_ingest_check_catches_lost_merged_chunk(mode):
    merged = build_chunks(metadata(), blocks(), table_mode=mode)
    options = dict(max_chars=1000, overlap_chars=100, table_mode=mode)

    assert _preserves_blocks(metadata(), blocks(), merged, **options) is True
    tampered = [replace(merged[0], content="다른 본문")]
    assert _preserves_blocks(metadata(), blocks(), tampered, **options) is False


def test_cli_accepts_merge_modes():
    args = _build_parser().parse_args(["build", "--table-mode", MERGE_INTRO_MODE])

    assert args.table_mode == MERGE_INTRO_MODE


def test_table_mode_changes_corpus_id():
    base = dict(metadata=[metadata()], max_chars=1000, overlap_chars=100, embedding_model="m")
    ids = {
        _corpus_id(base["metadata"], max_chars=1000, overlap_chars=100, embedding_model="m", table_mode=mode)
        for mode in ("group", MERGE_INTRO_MODE, MERGE_TABLE_ONLY_MODE)
    }

    assert len(ids) == 3


def dicts(chunks):
    return [item.to_dict() for item in chunks]


def gold_dataset(*gold):
    return [{"id": 1, "kind": "relevant", "input": "질문", "gold_chunks": list(gold), "corpus_version": "old"}]


def test_merged_gold_maps_exact():
    rows = [chunk(1, "(단위 : 원)"), chunk(2, "A | 12,345")]
    new = merge(rows)

    result = map_gold_chunks(gold_dataset("old-2"), dicts(rows), dicts(new))

    assert result["old-2"] == {"new_chunk_id": new[0].chunk_id, "match": "exact"}


def test_repeated_body_uses_document_order():
    rows = [
        chunk(1, "(단위 : 원)"),
        chunk(2, "A | 12,345"),
        chunk(3, "(단위 : 달러)"),
        chunk(4, "A | 12,345"),
    ]
    new = merge(rows)

    result = map_gold_chunks(gold_dataset("old-4"), dicts(rows), dicts(new))

    assert result["old-4"] == {"new_chunk_id": new[1].chunk_id, "match": "ordered"}


def test_missing_gold_stops_mapping():
    rows = [chunk(1, "A | 12,345")]
    other = [chunk(1, "B | 99,999")]

    with pytest.raises(GoldMappingError, match="old-1"):
        map_gold_chunks(gold_dataset("old-1"), dicts(rows), dicts(other))


def test_gold_absent_from_old_chunks_stops_mapping():
    rows = [chunk(1, "A | 12,345")]

    with pytest.raises(GoldMappingError, match="ghost"):
        map_gold_chunks(gold_dataset("ghost"), dicts(rows), dicts(rows))


def test_candidates_are_limited_to_same_section():
    rows = [chunk(1, "A | 12,345")]
    other_section = [chunk(1, "A | 12,345", section=("5. 재무제표 주석", "26. 비용"))]

    with pytest.raises(GoldMappingError):
        map_gold_chunks(gold_dataset("old-1"), dicts(rows), dicts(other_section))


def test_derived_dataset_changes_only_gold_and_version():
    rows = gold_dataset("a", "b")
    rows.append({"id": 2, "kind": "irrelevant", "input": "무관", "gold_chunks": [], "corpus_version": "old"})
    mapping = {
        "a": {"new_chunk_id": "n1", "match": "exact"},
        "b": {"new_chunk_id": "n1", "match": "exact"},
    }

    derived = derive_dataset(rows, mapping, corpus_id="corpus-new")

    assert derived[0] == {**rows[0], "gold_chunks": ["n1"], "corpus_version": "corpus-new"}
    assert derived[1] == {**rows[1], "corpus_version": "corpus-new"}
    assert rows[0]["gold_chunks"] == ["a", "b"]


def test_derived_row_serializes():
    assert json.dumps(derive_dataset(gold_dataset(), {}, corpus_id="c")[0], ensure_ascii=False)
