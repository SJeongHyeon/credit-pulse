import hashlib
import json
import urllib.error
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from backend.rag.chunking import (
    build_chunks,
    chunks_preserve_blocks,
    remove_common_boilerplate,
)
from backend.rag.dart_client import DartClient, DartClientError
from backend.rag.dart_parser import (
    DartParseError,
    document_separator_counts,
    parse_document,
    parse_xml_bytes,
)
from backend.rag.ingest import build_corpus, main
from backend.rag.models import DocumentMetadata, ParsedBlock
from backend.rag.validation import (
    OutputValidationError,
    export_unique_chunk_hashes,
    validate_outputs,
)
from backend.rag.verification import _is_meaningful_evidence, build_verification_report


SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DOCUMENT>
  <SECTION-1>
    <TITLE>II. 사업의 내용</TITLE>
    <P>회사는 변압기와 전력기기를 생산합니다.</P>
  </SECTION-1>
  <SECTION-1>
    <TITLE>III. 재무에 관한 사항</TITLE>
    <P>대상 범위가 아닌 본문입니다.</P>
  </SECTION-1>
  <SECTION-1>
    <TITLE>IV. 이사의 경영진단 및 분석의견</TITLE>
    <P>원재료 가격과 환율 변동에 노출되어 있습니다.</P>
  </SECTION-1>
  <SECTION-1>
    <TITLE>XI. 그 밖에 투자자 보호를 위하여 필요한 사항</TITLE>
    <P>소송과 관련한 중요한 변동은 없습니다.</P>
  </SECTION-1>
  <SECTION-1>
    <TITLE>연결재무제표 주석</TITLE>
    <P>1. 중요한 회계정책</P>
    <P>재고자산은 원가와 순실현가능가치 중 낮은 금액으로 측정합니다.</P>
    <P>33. 우발부채 및 약정사항</P>
    <P>회사는 특수관계자에게 지급보증을 제공하고 있습니다.</P>
    <TITLE>보증 내역</TITLE>
    <TABLE>
      <TR><TH>상대방</TH><TH>금액</TH></TR>
      <TR><TD>관계회사 A</TD><TD>1,000백만원</TD></TR>
    </TABLE>
  </SECTION-1>
</DOCUMENT>
"""


def test_unique_chunk_hash_evidence_is_sorted_and_filters_ineligible(tmp_path: Path):
    first_content = "첫 번째 eligible 청크"
    second_content = "두 번째 eligible 청크"
    first_hash = hashlib.sha256(first_content.encode()).hexdigest()
    second_hash = hashlib.sha256(second_content.encode()).hexdigest()
    rows = [
        {
            "content": second_content,
            "content_sha256": second_hash,
            "near_dup": False,
            "superseded": False,
        },
        {
            "content": first_content,
            "content_sha256": first_hash,
            "near_dup": False,
            "superseded": False,
        },
        {
            "content": first_content,
            "content_sha256": first_hash,
            "near_dup": False,
            "superseded": False,
        },
        {
            "content": "제외할 근접 중복",
            "content_sha256": hashlib.sha256(
                "제외할 근접 중복".encode()
            ).hexdigest(),
            "near_dup": True,
            "superseded": False,
        },
    ]
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    output = tmp_path / "unique_hashes.txt"

    result = export_unique_chunk_hashes(chunks, output)

    assert output.read_text(encoding="utf-8").splitlines() == sorted(
        [first_hash, second_hash]
    )
    assert result["eligible_chunks"] == 3
    assert result["unique_hashes"] == 2
    assert result["duplicate_eligible_chunks"] == 1


def test_unique_chunk_hash_evidence_rejects_content_mismatch(tmp_path: Path):
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text(
        json.dumps(
            {
                "content": "본문",
                "content_sha256": "0" * 64,
                "near_dup": False,
                "superseded": False,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OutputValidationError, match="본문 SHA-256 불일치"):
        export_unique_chunk_hashes(chunks, tmp_path / "hashes.txt")


def metadata(
    rcept_no: str = "20260312000123",
    corp_code: str = "00126380",
    fiscal_year: int = 2025,
    superseded: bool = False,
) -> DocumentMetadata:
    return DocumentMetadata(
        rcept_no=rcept_no,
        corp_code=corp_code,
        corp_name=f"회사-{corp_code}",
        fiscal_year=fiscal_year,
        filed_at="20260312",
        collected_at="2026-11-02T00:00:00Z",
        source_url=f"https://opendart.fss.or.kr/api/document.xml?rcept_no={rcept_no}",
        raw_sha256="0" * 64,
        superseded=superseded,
    )


def test_parser_preserves_target_sections_note_and_table_rows():
    blocks = parse_xml_bytes(SAMPLE_XML.encode("utf-8"))

    assert {block.chapter for block in blocks} == {"II", "IV", "XI", "NOTES"}
    assert all("대상 범위가 아닌 본문" not in block.text for block in blocks)

    guarantee = next(block for block in blocks if "지급보증" in block.text)
    assert guarantee.note_no == "33"
    assert guarantee.section_path[-1] == "33. 우발부채 및 약정사항"

    table_rows = [block for block in blocks if block.is_table]
    assert [block.text for block in table_rows] == [
        "상대방 | 금액",
        "관계회사 A | 1,000백만원",
    ]
    assert all(block.table_title == "보증 내역" for block in table_rows)


def test_zip_parser_preserves_distinct_table_groups(tmp_path: Path):
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><SECTION-1><TITLE>II. 사업의 내용</TITLE>
    <TITLE>표 A</TITLE><TABLE>
      <TR><TH>구분</TH><TH>금액</TH></TR><TR><TD>A</TD><TD>100</TD></TR>
    </TABLE>
    <TITLE>표 B</TITLE><TABLE>
      <TR><TH>구분</TH><TH>비율</TH></TR><TR><TD>B</TD><TD>20%</TD></TR>
    </TABLE></SECTION-1></DOCUMENT>
    """
    source = tmp_path / "document.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("document.xml", xml.encode("utf-8"))

    blocks = parse_document(source)
    table_blocks = [block for block in blocks if block.is_table]
    groups = {block.table_group for block in table_blocks}

    assert None not in groups
    assert len(groups) == 2
    chunks = build_chunks(metadata(), blocks, table_mode="group")
    assert {chunk.table_title for chunk in chunks if chunk.is_table} == {"표 A", "표 B"}


def test_verification_rejects_placeholder_and_unit_only_evidence():
    placeholder = ParsedBlock(
        chapter="NOTES",
        section_path=("연결재무제표 주석",),
        note_no=None,
        table_title=None,
        ordinal=1,
        text="해당사항 없음",
    )
    unit_only = ParsedBlock(
        chapter="II",
        section_path=("II. 사업의 내용",),
        note_no=None,
        table_title=None,
        ordinal=2,
        text="(단위 : 백만원)",
        is_table=True,
    )
    meaningful = ParsedBlock(
        chapter="II",
        section_path=("II. 사업의 내용",),
        note_no=None,
        table_title=None,
        ordinal=3,
        text="제품 구분 | 2025년 매출액 | 전년 대비 증감률 15퍼센트로 주요 변동이 확인됨",
        is_table=True,
    )

    assert not _is_meaningful_evidence(placeholder, "note")
    assert not _is_meaningful_evidence(unit_only, "table")
    assert _is_meaningful_evidence(meaningful, "table")


def test_chunker_excludes_accounting_policy_and_is_deterministic():
    blocks = parse_xml_bytes(SAMPLE_XML.encode("utf-8"))
    first = build_chunks(metadata(), blocks)
    second = build_chunks(metadata(), blocks)

    assert [chunk.to_dict() for chunk in first] == [chunk.to_dict() for chunk in second]
    assert all("재고자산은 원가" not in chunk.content for chunk in first)
    assert any("지급보증" in chunk.content for chunk in first)
    assert len([chunk for chunk in first if chunk.is_table]) == 2
    assert all(len(chunk.content) <= 1000 for chunk in first)


def test_grouped_table_mode_repeats_title_and_header_within_limit():
    blocks = [
        ParsedBlock(
            chapter="II",
            section_path=("II. 사업의 내용",),
            note_no=None,
            table_title="매출 현황",
            ordinal=index,
            text=text,
            is_table=True,
            table_group="doc.xml#table-1",
        )
        for index, text in enumerate(
            ["구분 | 금액", "제품 A | 100", "제품 B | 200", "제품 C | 300"],
            start=1,
        )
    ]

    chunks = build_chunks(
        metadata(), blocks, max_chars=100, overlap_chars=10, table_mode="group"
    )

    assert all(chunk.is_table for chunk in chunks)
    assert all(len(chunk.content) <= 100 for chunk in chunks)
    assert all(chunk.content.startswith("매출 현황 구분 | 금액") for chunk in chunks)
    assert chunks_preserve_blocks(
        blocks, chunks, max_chars=100, overlap_chars=10, table_mode="group"
    )


def _long_row_table_blocks(long_row: str) -> list[ParsedBlock]:
    return [
        ParsedBlock(
            chapter="II",
            section_path=("II. 사업의 내용",),
            note_no=None,
            table_title="주요 제품",
            ordinal=index,
            text=text,
            is_table=True,
            table_group="doc.xml#table-1",
        )
        for index, text in enumerate(
            ["구분 | 내용", "B | 짧은 행", long_row, "C | 다음 짧은 행"], start=1
        )
    ]


def _spaced_long_row() -> str:
    words = ["매출", "부채", "약정", "담보", "보증", "차입금", "우발", "소송"]
    return "A | " + " ".join(f"{words[i % len(words)]}{i}" for i in range(400))


@pytest.mark.parametrize(
    "long_row",
    [_spaced_long_row(), "A|" + "가나다라마바사" * 360],
    ids=["with-spaces", "without-spaces"],
)
def test_grouped_table_long_row_split_with_header_is_preserved(long_row):
    blocks = _long_row_table_blocks(long_row)

    chunks = build_chunks(
        metadata(), blocks, max_chars=1000, overlap_chars=100, table_mode="group"
    )

    assert len(chunks) >= 3
    assert all(len(chunk.content) <= 1000 for chunk in chunks)
    assert chunks_preserve_blocks(
        blocks, chunks, max_chars=1000, overlap_chars=100, table_mode="group"
    )


def test_grouped_table_detects_missing_or_altered_long_row_piece():
    blocks = _long_row_table_blocks(_spaced_long_row())
    chunks = build_chunks(
        metadata(), blocks, max_chars=1000, overlap_chars=100, table_mode="group"
    )
    piece_index = next(
        index for index, chunk in enumerate(chunks) if "부채1 " in chunk.content
    )

    missing = chunks[:piece_index + 1] + chunks[piece_index + 2 :]
    altered = list(chunks)
    altered[piece_index] = replace(
        chunks[piece_index], content=chunks[piece_index].content.replace("부채1 ", "부채X ")
    )

    for broken in (missing, altered):
        assert not chunks_preserve_blocks(
            blocks, broken, max_chars=1000, overlap_chars=100, table_mode="group"
        )


def test_grouped_table_long_row_is_matched_at_nearest_position():
    """긴 행 뒷부분과 같은 문장이 뒤쪽 표에 또 있어도 가까운 위치부터 대조한다."""

    def table_block(ordinal, text, group, title):
        return ParsedBlock(
            chapter="NOTES",
            section_path=("3. 연결재무제표 주석", "14. 관계기업"),
            note_no="14",
            table_title=title,
            ordinal=ordinal,
            text=text,
            is_table=True,
            table_group=group,
        )

    words = ["지분율", "장부가액", "유동자산", "비유동자산", "매출", "순이익"]
    long_row = " ".join(f"{words[i % len(words)]}{i}" for i in range(100))
    title = "관계기업 및 공동기업 현황 " * 6
    first_table = [
        table_block(1, "구분 | 내용", "doc.xml#table-1", title),
        table_block(2, long_row, "doc.xml#table-1", title),
    ]
    first_chunks = build_chunks(
        metadata(), first_table, max_chars=400, overlap_chars=40, table_mode="group"
    )
    assert len(first_chunks) >= 3
    first_piece_end = long_row.index(first_chunks[0].content.split()[-1]) + len(
        first_chunks[0].content.split()[-1]
    )
    repeated_tail = long_row[first_piece_end:].strip()

    blocks = first_table + [
        table_block(3, "가 | 나", "doc.xml#table-2", None),
        table_block(4, repeated_tail, "doc.xml#table-2", None),
    ]
    chunks = build_chunks(
        metadata(), blocks, max_chars=400, overlap_chars=40, table_mode="group"
    )

    assert chunks_preserve_blocks(
        blocks, chunks, max_chars=400, overlap_chars=40, table_mode="group"
    )


def test_group_drop_numeric_excludes_numeric_heavy_rows():
    blocks = [
        ParsedBlock(
            chapter="II",
            section_path=("II. 사업의 내용",),
            note_no=None,
            table_title="요약",
            ordinal=index,
            text=text,
            is_table=True,
            table_group="doc.xml#table-1",
        )
        for index, text in enumerate(
            ["구분 | 값", "2025 | 1,234 | 5.6%", "설명 | 신규 계약 체결"],
            start=1,
        )
    ]

    chunks = build_chunks(
        metadata(),
        blocks,
        max_chars=1000,
        table_mode="group_drop_numeric",
    )

    assert len(chunks) == 1
    assert "2025 | 1,234 | 5.6%" not in chunks[0].content
    assert "설명 | 신규 계약 체결" in chunks[0].content
    assert chunks_preserve_blocks(
        blocks,
        chunks,
        max_chars=1000,
        table_mode="group_drop_numeric",
    )


def test_parser_recovers_cp949_and_bare_ampersand():
    xml = SAMPLE_XML.replace('encoding="UTF-8"', 'encoding="EUC-KR"').replace(
        "변압기와 전력기기", "변압기 & 전력기기"
    )

    blocks = parse_xml_bytes(xml.encode("cp949"))

    assert any("변압기 & 전력기기" in block.text for block in blocks)


def test_parser_uses_actual_utf8_before_misleading_declaration():
    xml = SAMPLE_XML.replace('encoding="UTF-8"', 'encoding="EUC-KR"')

    blocks = parse_xml_bytes(xml.encode("utf-8"))

    assert any("변압기와 전력기기" in block.text for block in blocks)
    assert all("�" not in block.text for block in blocks)


def test_parser_rejects_bytes_that_are_not_utf8_or_cp949():
    with pytest.raises(DartParseError, match="디코딩 실패"):
        parse_xml_bytes(b"\x81\x00")


def test_parser_accepts_multi_xml_zip_when_one_file_has_no_target(tmp_path: Path):
    source = tmp_path / "document.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("cover.xml", "<DOCUMENT><P>표지</P></DOCUMENT>")
        archive.writestr("report.xml", SAMPLE_XML)

    blocks = parse_document(source)

    assert any("변압기" in block.text for block in blocks)


def test_long_paragraph_splits_without_changing_identifiers():
    long_text = " ".join(["환율과 원재료 가격 변동 위험이 있습니다."] * 120)
    xml = f"<DOCUMENT><SECTION-1><TITLE>II. 사업의 내용</TITLE><P>{long_text}</P></SECTION-1></DOCUMENT>"
    blocks = parse_xml_bytes(xml.encode("utf-8"))

    first = build_chunks(metadata(), blocks, max_chars=1000)
    second = build_chunks(metadata(), blocks, max_chars=1000)

    assert len(first) >= 3
    assert all(len(chunk.content) <= 1000 for chunk in first)
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]


def test_boundaryless_text_uses_fixed_overlap_and_preserves_content():
    text = "가" * 2500
    block = ParsedBlock("II", ("II. 사업의 내용",), None, None, 1, text)

    chunks = build_chunks(
        metadata(), [block], max_chars=1000, overlap_chars=100
    )

    assert [len(chunk.content) for chunk in chunks] == [1000, 1000, 700]
    assert chunks[0].content[-100:] == chunks[1].content[:100]
    assert chunks[1].content[-100:] == chunks[2].content[:100]
    assert chunks_preserve_blocks(
        [block], chunks, max_chars=1000, overlap_chars=100
    )


def test_common_boilerplate_requires_three_distinct_companies():
    common = "이 문장은 서로 다른 세 회사에 반복되는 스무 글자 이상의 공통 문구입니다."
    documents = []
    for index in range(3):
        blocks = [
            ParsedBlock("II", ("II. 사업의 내용",), None, None, 1, common),
            ParsedBlock(
                "II",
                ("II. 사업의 내용",),
                None,
                None,
                2,
                f"회사 {index}만의 고유한 사업 설명입니다.",
            ),
        ]
        documents.append((metadata(corp_code=f"0000000{index}"), blocks))

    cleaned = remove_common_boilerplate(documents)

    assert all(common not in [block.text for block in blocks] for _, blocks in cleaned)
    assert all(len(blocks) == 1 for _, blocks in cleaned)


def _write_document(directory: Path, meta: DocumentMetadata, xml: str | None) -> None:
    directory.mkdir(parents=True)
    (directory / "metadata.json").write_text(
        json.dumps(meta.to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    if xml is None:
        (directory / "document.zip").write_bytes(b"not-a-zip")
        return
    with zipfile.ZipFile(directory / "document.zip", "w") as archive:
        archive.writestr("document.xml", xml)


def test_build_corpus_records_success_failure_and_matching_counts(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _write_document(corpus / "20260312000123", metadata(), SAMPLE_XML)
    _write_document(
        corpus / "20260312000124",
        metadata("20260312000124", "00126381"),
        SAMPLE_XML.replace("변압기", "차단기"),
    )
    _write_document(
        corpus / "20260312000125",
        metadata("20260312000125", "00126382"),
        None,
    )
    manifest = tmp_path / "manifest.jsonl"
    chunks = tmp_path / "chunks.jsonl"
    summary_path = tmp_path / "summary.json"

    summary = build_corpus(
        corpus,
        manifest_path=manifest,
        chunks_path=chunks,
        summary_path=summary_path,
    )

    first_manifest = manifest.read_bytes()
    first_chunks = chunks.read_bytes()
    first_summary = summary_path.read_bytes()
    second_summary = build_corpus(
        corpus,
        manifest_path=manifest,
        chunks_path=chunks,
        summary_path=summary_path,
    )
    assert manifest.read_bytes() == first_manifest
    assert chunks.read_bytes() == first_chunks
    assert summary_path.read_bytes() == first_summary
    assert second_summary == summary

    manifest_rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    chunk_rows = [json.loads(line) for line in chunks.read_text(encoding="utf-8").splitlines()]
    assert summary["documents_total"] == 3
    assert summary["documents_success"] == 2
    assert summary["documents_failed"] == 1
    assert summary["chunks_total"] == len(chunk_rows)
    assert summary["unique_chunks"] == len(
        {
            row["content_sha256"]
            for row in chunk_rows
            if not row["near_dup"] and not row["superseded"]
        }
    )
    assert summary["corpus_id"].startswith("corpus-")
    assert {row["corpus_id"] for row in manifest_rows} == {summary["corpus_id"]}
    assert all(row["truncated"] is False for row in manifest_rows if row["status"] == "success")
    assert all(
        row["separator_counts"]["paragraph_elements"] > 0
        for row in manifest_rows
        if row["status"] == "success"
    )
    assert {row["status"] for row in manifest_rows} == {"success", "failed"}
    assert json.loads(summary_path.read_text(encoding="utf-8")) == summary

    validated = validate_outputs(manifest, chunks, summary_path=summary_path)
    assert validated["documents"] == 3
    assert validated["chunks"] == len(chunk_rows)


def test_separator_counts_capture_source_structure(tmp_path: Path):
    source = tmp_path / "document.xml"
    source.write_text(SAMPLE_XML, encoding="utf-8")

    counts = document_separator_counts(source)

    assert counts["line_breaks"] > 0
    assert counts["paragraph_elements"] >= 6
    assert counts["section_elements"] == 5
    assert counts["table_rows"] == 2


def test_corpus_id_changes_with_build_settings(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _write_document(corpus / "20260312000123", metadata(), SAMPLE_XML)

    first = build_corpus(
        corpus,
        manifest_path=tmp_path / "manifest-1.jsonl",
        chunks_path=tmp_path / "chunks-1.jsonl",
        summary_path=tmp_path / "summary-1.json",
        overlap_chars=100,
        embedding_model="unselected",
    )
    same = build_corpus(
        corpus,
        manifest_path=tmp_path / "manifest-2.jsonl",
        chunks_path=tmp_path / "chunks-2.jsonl",
        summary_path=tmp_path / "summary-2.json",
        overlap_chars=100,
        embedding_model="unselected",
    )
    changed = build_corpus(
        corpus,
        manifest_path=tmp_path / "manifest-3.jsonl",
        chunks_path=tmp_path / "chunks-3.jsonl",
        summary_path=tmp_path / "summary-3.json",
        overlap_chars=50,
        embedding_model="bge-m3",
    )

    assert first["corpus_id"] == same["corpus_id"]
    assert changed["corpus_id"] != first["corpus_id"]


def test_superseded_chunks_are_kept_but_excluded_from_g3(tmp_path: Path):
    corpus = tmp_path / "corpus"
    active = metadata("20260312000123", "00126380")
    old = metadata("20260312000124", "00126381", superseded=True)
    _write_document(corpus / active.rcept_no, active, SAMPLE_XML)
    _write_document(
        corpus / old.rcept_no,
        old,
        SAMPLE_XML.replace("변압기", "폐기된 변압기"),
    )
    chunks_path = tmp_path / "chunks.jsonl"

    summary = build_corpus(
        corpus,
        manifest_path=tmp_path / "manifest.jsonl",
        chunks_path=chunks_path,
        summary_path=tmp_path / "summary.json",
    )
    rows = [
        json.loads(line)
        for line in chunks_path.read_text(encoding="utf-8").splitlines()
    ]

    assert any(row["superseded"] is True for row in rows)
    assert summary["documents_superseded"] == 1
    assert summary["chunks_superseded"] == sum(
        row["superseded"] is True for row in rows
    )
    assert summary["unique_chunks"] == len(
        {
            row["content_sha256"]
            for row in rows
            if not row["near_dup"] and not row["superseded"]
        }
    )
    validate_outputs(
        tmp_path / "manifest.jsonl",
        chunks_path,
        summary_path=tmp_path / "summary.json",
    )


def test_validation_detects_tampered_chunk_content(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _write_document(corpus / "20260312000123", metadata(), SAMPLE_XML)
    manifest = tmp_path / "manifest.jsonl"
    chunks = tmp_path / "chunks.jsonl"
    summary_path = tmp_path / "summary.json"
    build_corpus(
        corpus,
        manifest_path=manifest,
        chunks_path=chunks,
        summary_path=summary_path,
    )
    rows = [json.loads(line) for line in chunks.read_text(encoding="utf-8").splitlines()]
    rows[0]["content"] += " 변조"
    chunks.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OutputValidationError, match="SHA-256 불일치"):
        validate_outputs(manifest, chunks, summary_path=summary_path)


def test_verification_report_covers_three_documents(tmp_path: Path):
    corpus = tmp_path / "corpus"
    for index in range(3):
        xml = (
            SAMPLE_XML.replace("변압기와 전력기기", f"회사 {index}의 전력기기")
            .replace("회사는 특수관계자에게", f"회사 {index}는 관계회사에게")
            .replace("관계회사 A", f"관계회사 {index}")
            .replace("1,000백만원", f"{index + 1},000백만원")
        )
        rcept_no = f"2026031200012{index}"
        _write_document(
            corpus / rcept_no,
            metadata(rcept_no, f"0012638{index}"),
            xml,
        )
    chunks = tmp_path / "chunks.jsonl"
    build_corpus(
        corpus,
        manifest_path=tmp_path / "manifest.jsonl",
        chunks_path=chunks,
        summary_path=tmp_path / "summary.json",
    )
    report = tmp_path / "verification.md"

    result = build_verification_report(corpus, chunks, report)

    assert result["documents"] == 3
    assert result["raw_matches"] == 3
    assert result["chunk_matches"] == 3
    text = report.read_text(encoding="utf-8")
    assert "사람 확인" in text
    assert text.count("PASS") == 6


def test_build_validate_verify_cli_pipeline(tmp_path: Path):
    corpus = tmp_path / "corpus"
    for index in range(3):
        rcept_no = f"2026031300012{index}"
        xml = SAMPLE_XML.replace("변압기와 전력기기", f"고유 설비 {index}").replace(
            "관계회사 A", f"대상회사 {index}"
        )
        _write_document(
            corpus / rcept_no,
            metadata(rcept_no, f"0012640{index}"),
            xml,
        )
    manifest = tmp_path / "manifest.jsonl"
    chunks = tmp_path / "chunks.jsonl"
    summary = tmp_path / "summary.json"
    report = tmp_path / "verification.md"

    assert main(
        [
            "build",
            "--corpus-dir",
            str(corpus),
            "--manifest",
            str(manifest),
            "--chunks",
            str(chunks),
            "--summary",
            str(summary),
        ]
    ) == 0
    assert main(
        [
            "validate",
            "--manifest",
            str(manifest),
            "--chunks",
            str(chunks),
            "--summary",
            str(summary),
        ]
    ) == 0
    assert main(
        [
            "verify",
            "--corpus-dir",
            str(corpus),
            "--chunks",
            str(chunks),
            "--output",
            str(report),
        ]
    ) == 0
    assert report.exists()


class _FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self.payload


def test_dart_list_filters_quarterly_and_attachment_only_reports():
    payload = json.dumps(
        {
            "status": "000",
            "total_page": 1,
            "list": [
                {"report_nm": "사업보고서 (2025.12)", "rcept_no": "20260312000123"},
                {"report_nm": "[기재정정]사업보고서 (2025.12)", "rcept_no": "20260313000123"},
                {"report_nm": "[첨부정정]사업보고서 (2025.12)", "rcept_no": "20260314000123"},
                {"report_nm": "[첨부추가]사업보고서 (2025.12)", "rcept_no": "20260315000123"},
                {"report_nm": "분기보고서 (2026.03)", "rcept_no": "20260512000123"},
            ],
        }
    ).encode()
    client = DartClient(api_key="secret", opener=lambda *args, **kwargs: _FakeResponse(payload))

    reports = client.list_annual_reports("20260301", "20260430")

    assert [report["rcept_no"] for report in reports] == [
        "20260312000123",
        "20260313000123",
    ]


def test_dart_company_profile_returns_industry_fields():
    payload = json.dumps(
        {
            "status": "000",
            "corp_code": "01316245",
            "corp_name": "효성중공업",
            "corp_cls": "Y",
            "stock_code": "298040",
            "induty_code": "28111",
        }
    ).encode()
    client = DartClient(
        api_key="secret", opener=lambda *args, **kwargs: _FakeResponse(payload)
    )

    profile = client.company_profile("01316245")

    assert profile == {
        "corp_code": "01316245",
        "corp_name": "효성중공업",
        "corp_cls": "Y",
        "stock_code": "298040",
        "induty_code": "28111",
    }


def test_parser_recovers_malformed_opendart_xml():
    malformed = """<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><SECTION-1><TITLE>II. 사업의 내용</TITLE>
    <P>정상 문단<P>닫힘 태그가 누락된 문단</SECTION-1></DOCUMENT>
    """.encode("utf-8")

    blocks = parse_xml_bytes(malformed)

    assert [block.chapter for block in blocks] == ["II"]
    assert blocks[0].text == "정상 문단 닫힘 태그가 누락된 문단"


def test_parser_finds_section_nested_under_unclosed_prior_paragraph():
    malformed = """<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><SECTION-1><TITLE>I. 회사의 개요</TITLE>
    <P>닫히지 않은 앞 장의 문단
    <SECTION-1><TITLE>II. 사업의 내용</TITLE><P>검색 대상 본문</P></SECTION-1>
    </SECTION-1></DOCUMENT>
    """.encode("utf-8")

    blocks = parse_xml_bytes(malformed)

    assert [(block.chapter, block.text) for block in blocks] == [
        ("II", "검색 대상 본문")
    ]


def test_parser_finds_section_nested_under_unclosed_prior_table():
    malformed = """<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><TABLE><TR><TD>앞 장 표
    <SECTION-1><TITLE>II. 사업의 내용</TITLE><P>표 뒤 검색 대상</P></SECTION-1>
    </TD></TR></TABLE></DOCUMENT>
    """.encode("utf-8")

    blocks = parse_xml_bytes(malformed)

    assert [(block.chapter, block.text, block.is_table) for block in blocks] == [
        ("II", "표 뒤 검색 대상", False)
    ]


def test_parser_stops_target_scope_at_non_target_top_level_chapter():
    malformed = """<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><SECTION-1><TITLE>3. 연결재무제표 주석</TITLE>
    <P>33. 우발부채 및 약정사항</P><P>보증 관련 검색 대상 본문입니다.</P>
    <SECTION-1><TITLE>V. 회계감사인의 감사의견 등</TITLE>
    <P>대상 범위 밖의 배당 관련 본문입니다.</P></SECTION-1>
    </SECTION-1></DOCUMENT>
    """.encode("utf-8")

    blocks = parse_xml_bytes(malformed)

    assert any("보증 관련" in block.text for block in blocks)
    assert all("배당 관련" not in block.text for block in blocks)


def test_parser_stops_notes_at_financial_chapter_subsection_boundary():
    malformed = """<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><SECTION-2><TITLE>3. 연결재무제표 주석</TITLE>
    <P>33. 우발부채 및 약정사항</P><P>우발부채 검색 대상입니다.</P>
    <SECTION-2><TITLE>6. 배당에 관한 사항</TITLE>
    <P>주석이 아닌 배당 정책입니다.</P></SECTION-2>
    </SECTION-2></DOCUMENT>
    """.encode("utf-8")

    blocks = parse_xml_bytes(malformed)

    assert any("우발부채 검색" in block.text for block in blocks)
    assert all("배당 정책" not in block.text for block in blocks)


def test_parser_stops_notes_at_securities_issuance_subsection():
    malformed = """<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><SECTION-2><TITLE>5. 재무제표 주석</TITLE>
    <P>12. 우발상황</P><P>소송 관련 검색 대상입니다.</P>
    <SECTION-2><TITLE>7. 증권의 발행을 통한 자금조달에 관한 사항</TITLE>
    <P>주석이 아닌 자기주식 소각 내용입니다.</P></SECTION-2>
    </SECTION-2></DOCUMENT>
    """.encode("utf-8")

    blocks = parse_xml_bytes(malformed)

    assert any("소송 관련" in block.text for block in blocks)
    assert all("자기주식 소각" not in block.text for block in blocks)


def test_parser_keeps_notes_after_note_titled_with_consolidated_statements():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><SECTION-2><TITLE>3. 연결재무제표 주석</TITLE>
    <P>1. 일반 사항 (연결)</P><P>회사 개요 본문입니다.</P>
    <P>2. 연결재무제표 작성기준 및 중요한 회계정책 (연결)</P><P>작성기준 본문입니다.</P>
    <P>20. 차입금 (연결)</P><P>차입금 검색 대상입니다.</P>
    <SECTION-2><TITLE>4. 재무제표</TITLE><P>별도 재무상태표입니다.</P></SECTION-2>
    </SECTION-2></DOCUMENT>
    """.encode("utf-8")

    blocks = parse_xml_bytes(xml)
    borrowing = next(block for block in blocks if "차입금 검색" in block.text)

    assert borrowing.note_no == "20"
    assert borrowing.section_path == ("3. 연결재무제표 주석", "20. 차입금 (연결)")
    assert all("별도 재무상태표" not in block.text for block in blocks)


def test_parser_treats_bracketed_korean_cell_text_as_text_not_tag():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><SECTION-2><TITLE>3. 연결재무제표 주석</TITLE>
    <TABLE-GROUP><TITLE>11. 관계기업투자 (연결)</TITLE>
    <TABLE><TR><TD><당기말></TD></TR><TR><TD>관계기업</TD><TD>270,571</TD></TR></TABLE>
    </TABLE-GROUP>
    <TABLE-GROUP><TITLE>42. 담보제공자산 (연결)</TITLE>
    <TABLE><TR><TD>담보로 제공된 금융자산</TD><TD>1,767,562</TD></TR></TABLE>
    </TABLE-GROUP>
    </SECTION-2></DOCUMENT>
    """.encode("utf-8")

    blocks = parse_xml_bytes(xml)
    by_text = {block.text: block for block in blocks}

    assert "<당기말>" in by_text
    assert by_text["<당기말>"].note_no == "11"
    pledged = by_text["담보로 제공된 금융자산 | 1,767,562"]
    assert pledged.note_no == "42"
    assert pledged.section_path == ("3. 연결재무제표 주석", "42. 담보제공자산 (연결)")


# 우성머티리얼스 원문 형태: 주석 제목과 본문이 한 <P>에 붙어 140자를 넘는다.
_LONG_RISK_PARAGRAPH = (
    "38. 위험관리(1) 자본위험관리회사의 자본관리 목적은 계속기업으로서 주주 및 "
    "이해회사자들에게 이익을 지속적으로 제공할 수 있는 능력을 유지하고 자본비용을 "
    "절감하기 위해 최적의 자본구조를 유지하는 것입니다. 자본구조의 유지 또는 조정을 "
    "위하여 필요한 경우 회사는 배당을 조정합니다."
)


def test_parser_detects_long_note_heading_glued_to_body():
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><SECTION-2><TITLE>5. 재무제표 주석</TITLE>
    <P>37. 현금흐름표</P><P>현금흐름표 본문입니다.</P>
    <P>{_LONG_RISK_PARAGRAPH}</P>
    <TABLE><TR><TD>차입금총계</TD><TD>48,344,495,045</TD></TR></TABLE>
    <P>5) 유동성위험회사는 유동성위험을 관리하기 위하여 자금관리계획을 수립합니다.</P>
    <P>유동성위험 본문입니다.</P>
    </SECTION-2></DOCUMENT>
    """.encode("utf-8")

    blocks = parse_xml_bytes(xml)
    by_text = {block.text: block for block in blocks}

    assert len(_LONG_RISK_PARAGRAPH) > 140
    cash_flow = by_text["현금흐름표 본문입니다."]
    assert (cash_flow.note_no, cash_flow.section_path) == ("37", ("5. 재무제표 주석", "37. 현금흐름표"))
    # 제목은 "(1)" 앞까지로 자르고, 붙어 있던 본문은 잃지 않는다.
    risk = by_text[_LONG_RISK_PARAGRAPH]
    assert (risk.note_no, risk.section_path) == ("38", ("5. 재무제표 주석", "38. 위험관리"))
    table_row = by_text["차입금총계 | 48,344,495,045"]
    assert table_row.note_no == "38"
    assert table_row.table_title == "38. 위험관리"
    liquidity = by_text["유동성위험 본문입니다."]
    assert liquidity.note_no == "38"
    assert liquidity.section_path[:2] == ("5. 재무제표 주석", "38. 위험관리")


def test_parser_keeps_note_number_under_sub_headings():
    # 모나미 원문 형태: "36. 재무위험관리" 아래 "1) 시장위험", "3) 유동성위험" 소제목.
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><SECTION-2><TITLE>5. 재무제표 주석</TITLE>
    <P>36. 재무위험관리</P>
    <P>1) 시장위험</P><P>시장위험 본문입니다.</P>
    <P>3) 유동성위험</P><P>유동성위험 본문입니다.</P>
    <P>1 상각후원가측정금융자산</P><P>상각후원가 본문입니다.</P>
    <P>37. 사업결합</P><P>사업결합 본문입니다.</P>
    </SECTION-2></DOCUMENT>
    """.encode("utf-8")

    blocks = parse_xml_bytes(xml)
    by_text = {block.text: block for block in blocks}

    market = by_text["시장위험 본문입니다."]
    assert (market.note_no, market.section_path) == (
        "36",
        ("5. 재무제표 주석", "36. 재무위험관리", "1) 시장위험"),
    )
    # 형제 소제목은 쌓이지 않고 같은 깊이로 바뀐다.
    liquidity = by_text["유동성위험 본문입니다."]
    assert (liquidity.note_no, liquidity.section_path) == (
        "36",
        ("5. 재무제표 주석", "36. 재무위험관리", "3) 유동성위험"),
    )
    amortized = by_text["상각후원가 본문입니다."]
    assert amortized.note_no == "36"
    assert amortized.section_path[-1] == "1 상각후원가측정금융자산"
    merger = by_text["사업결합 본문입니다."]
    assert (merger.note_no, merger.section_path) == ("37", ("5. 재무제표 주석", "37. 사업결합"))


def test_parser_emits_body_glued_to_sub_heading_at_note_end():
    # 리뷰 지적(PR #67): 소제목 뒤에 본문이 붙은 짧은 문단이 주석의 마지막 문단이면
    # 뒤에 표·본문이 없어 상환 근거가 코퍼스에서 사라졌다.
    glued = "3) 유동성위험회사는 2026년 6월까지 차입금 300억원을 상환합니다."
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><SECTION-2><TITLE>5. 재무제표 주석</TITLE>
    <P>36. 재무위험관리</P>
    <P>1) 시장위험</P><P>시장위험 본문입니다.</P>
    <P>{glued}</P>
    <P>37. 사업결합</P><P>사업결합 본문입니다.</P>
    </SECTION-2></DOCUMENT>
    """.encode("utf-8")

    blocks = parse_xml_bytes(xml)
    by_text = {block.text: block for block in blocks}

    liquidity = by_text[glued]
    assert (liquidity.note_no, liquidity.section_path) == (
        "36",
        ("5. 재무제표 주석", "36. 재무위험관리", glued),
    )
    merger = by_text["사업결합 본문입니다."]
    assert (merger.note_no, merger.section_path) == ("37", ("5. 재무제표 주석", "37. 사업결합"))


def test_parser_emits_body_glued_to_short_note_heading():
    # 짧은 주석 제목 + 본문("5. 차입금회사는 …")도 문서 끝 마지막 문단이면 사라졌다.
    glued = "5. 차입금회사는 2026년 6월까지 차입금 300억원을 상환합니다."
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><SECTION-2><TITLE>5. 재무제표 주석</TITLE>
    <P>4. 재고자산</P><P>재고자산 본문입니다.</P>
    <P>{glued}</P>
    </SECTION-2></DOCUMENT>
    """.encode("utf-8")

    blocks = parse_xml_bytes(xml)
    by_text = {block.text: block for block in blocks}

    borrowings = by_text[glued]
    assert (borrowings.note_no, borrowings.section_path) == ("5", ("5. 재무제표 주석", glued))


@pytest.mark.parametrize(
    "glued",
    [
        # 리뷰 지적(PR #67 2차): 문장 종결이 없는 명사형 공시 문구도 본문이다.
        "3) 유동성위험: 2026년 6월 차입금 300억원 상환 예정",
        "3) 담보제공: 해당사항 없음",
    ],
)
def test_parser_emits_noun_style_body_at_note_end(glued: str):
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><SECTION-2><TITLE>5. 재무제표 주석</TITLE>
    <P>36. 재무위험관리</P>
    <P>1) 시장위험</P><P>시장위험 본문입니다.</P>
    <P>{glued}</P>
    <P>37. 사업결합</P><P>사업결합 본문입니다.</P>
    </SECTION-2></DOCUMENT>
    """.encode("utf-8")

    blocks = parse_xml_bytes(xml)
    by_text = {block.text: block for block in blocks}

    body = by_text[glued]
    assert (body.note_no, body.section_path) == (
        "36",
        ("5. 재무제표 주석", "36. 재무위험관리", glued),
    )
    merger = by_text["사업결합 본문입니다."]
    assert (merger.note_no, merger.section_path) == ("37", ("5. 재무제표 주석", "37. 사업결합"))


def test_parser_does_not_emit_pure_headings():
    # 본문 문장이 없는 순수 제목은 지금처럼 경계로만 쓰고 블록으로 내지 않는다.
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><SECTION-2><TITLE>5. 재무제표 주석</TITLE>
    <P>5. 차입금</P>
    <P>3) 유동성위험</P><P>유동성위험 본문입니다.</P>
    </SECTION-2></DOCUMENT>
    """.encode("utf-8")

    blocks = parse_xml_bytes(xml)
    texts = [block.text for block in blocks]

    assert "5. 차입금" not in texts
    assert "3) 유동성위험" not in texts
    liquidity = next(block for block in blocks if block.text == "유동성위험 본문입니다.")
    assert (liquidity.note_no, liquidity.section_path) == (
        "5",
        ("5. 재무제표 주석", "5. 차입금", "3) 유동성위험"),
    )


def test_parser_ignores_long_numbered_paragraph_that_is_not_next_note():
    # 주석 5 안의 긴 번호 목록 문단("1. ...")은 다음 주석 번호가 아니므로 제목이 아니다.
    long_list_item = "1. " + "계약상 현금흐름의 수취를 목적으로 하는 사업모형하에서 보유합니다. " * 4
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><SECTION-2><TITLE>3. 연결재무제표 주석</TITLE>
    <P>5. 금융상품</P><P>{long_list_item}</P>
    </SECTION-2></DOCUMENT>
    """.encode("utf-8")

    blocks = parse_xml_bytes(xml)
    item = next(block for block in blocks if block.text.startswith("1. 계약상"))

    assert len(item.text) > 140
    assert (item.note_no, item.section_path) == ("5", ("3. 연결재무제표 주석", "5. 금융상품"))


def test_dart_errors_do_not_expose_key():
    def fail(*args, **kwargs):
        raise urllib.error.URLError("secret-value")

    client = DartClient(
        api_key="secret-value",
        max_attempts=1,
        opener=fail,
        sleeper=lambda _: None,
    )

    with pytest.raises(DartClientError) as caught:
        client.list_annual_reports("20260301", "20260430")

    assert "secret-value" not in str(caught.value)


def test_collect_waits_between_documents_but_not_after_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    targets = tmp_path / "targets.jsonl"
    rows = [
        {
            "rcept_no": f"2026031200012{index}",
            "corp_code": f"0012638{index}",
            "corp_name": f"회사-{index}",
            "fiscal_year": 2025,
        }
        for index in range(2)
    ]
    targets.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    collected: list[str] = []
    sleeps: list[float] = []

    class FakeDartClient:
        def collect(self, target, corpus_dir):
            collected.append(str(target["rcept_no"]))

    monkeypatch.setattr("backend.rag.ingest.DartClient", FakeDartClient)
    monkeypatch.setattr("backend.rag.ingest.time.sleep", sleeps.append)

    result = main(
        [
            "collect",
            "--targets",
            str(targets),
            "--corpus-dir",
            str(tmp_path / "corpus"),
            "--request-interval-seconds",
            "1.25",
        ]
    )

    assert result == 0
    assert collected == [row["rcept_no"] for row in rows]
    assert sleeps == [1.25]
