"""사전 등록 8번 고정 창 청킹(fixed_window) 회귀 테스트."""

from backend.rag.chunking import (
    FIXED_MODE,
    TABLE_MODES,
    fixed_chunks_preserve_sources,
    fixed_window_chunks,
)
from backend.rag.models import ChunkRecord


def _chunk(ordinal: int, content: str, *, chapter: str = "NOTES", note_no: str | None = "20", is_table: bool = False) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=f"src-{ordinal}",
        corpus_id="corpus-test",
        rcept_no="20260101000001",
        corp_code="00000001",
        corp_name="테스트",
        fiscal_year=2025,
        chapter=chapter,
        section_path=("주석", f"{note_no}. 차입금"),
        note_no=note_no,
        table_title="표" if is_table else None,
        ordinal=ordinal,
        content=content,
        content_sha256=f"sha-{ordinal}",
        is_table=is_table,
        superseded=False,
    )


def test_fixed_mode_is_registered():
    assert FIXED_MODE == "fixed_window"
    assert FIXED_MODE in TABLE_MODES


def test_windows_are_1000_chars_with_100_overlap_inside_one_note():
    # 2,500자 한 줄: 시작점 0, 900, 1800 -> 창 3개(1000, 1000, 700자)
    text = "".join(chr(0xAC00 + (i % 50)) for i in range(2500))
    chunks = fixed_window_chunks([_chunk(1, text)], max_chars=1000, overlap_chars=100)
    assert [len(c.content) for c in chunks] == [1000, 1000, 700]
    assert chunks[0].content[-100:] == chunks[1].content[:100]
    assert [c.ordinal for c in chunks] == [1, 2, 3]


def test_short_table_joins_previous_sentence_in_same_note():
    # 9번처럼 짧은 표가 같은 주석의 설명 문장과 한 창에 들어가야 한다.
    sentence = _chunk(1, "주요 고객 관련 매출액은 다음과 같습니다.")
    table = _chunk(2, "A사 | 46,589,675", is_table=True)
    chunks = fixed_window_chunks([sentence, table], max_chars=1000, overlap_chars=100)
    assert len(chunks) == 1
    assert chunks[0].content == "주요 고객 관련 매출액은 다음과 같습니다. A사 | 46,589,675"
    assert chunks[0].is_table is True
    assert chunks[0].table_title is None


def test_window_restarts_at_note_boundary():
    a = _chunk(1, "가" * 300, note_no="20")
    b = _chunk(2, "나" * 300, note_no="21")
    chunks = fixed_window_chunks([a, b], max_chars=1000, overlap_chars=100)
    assert [c.content for c in chunks] == ["가" * 300, "나" * 300]
    assert [c.note_no for c in chunks] == ["20", "21"]


def test_preserve_check_detects_dropped_window():
    text = "다" * 2500
    sources = [_chunk(1, text)]
    chunks = fixed_window_chunks(sources, max_chars=1000, overlap_chars=100)
    assert fixed_chunks_preserve_sources(sources, chunks, max_chars=1000, overlap_chars=100)
    assert not fixed_chunks_preserve_sources(sources, chunks[:-1], max_chars=1000, overlap_chars=100)
