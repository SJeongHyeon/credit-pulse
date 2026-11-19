"""v10 청킹(fixed_window_rows)과 제목·본문 분리: v9에서 찾은 파싱 결함 세 가지를 고친 동작을 고정한다.

1. 표 행 경계가 공백으로 뭉개짐 → 행·문단 사이에 줄바꿈이 남는다.
2. 제목 없는 표 앞에 절 제목이 본문 중간에 끼어듦 → 본문에 절 제목이 들어가지 않는다.
3. 제목과 본문이 한 문단에 붙으면 문단 전체가 절 제목이 됨 → 제목 부분만 절 제목이 된다.
v9(fixed_window)는 바뀌지 않아야 한다(G3 재현).
"""

import pytest

from backend.rag.chunking import FIXED_MODE, FIXED_ROWS_MODE, build_chunks
from backend.rag.dart_parser import parse_xml_bytes, split_heading_title
from backend.rag.models import DocumentMetadata

XML = """<?xml version="1.0" encoding="UTF-8"?>
<DOCUMENT>
<SECTION-1><TITLE>III. 재무에 관한 사항</TITLE>
<SECTION-2><TITLE>3. 연결재무제표 주석</TITLE>
<P>8. 유동금융자산</P>
<P>유동금융자산의 내역입니다.</P>
<P>9. 재고자산당기말 및 전기말 현재 재고자산 내역은 다음과 같습니다.</P>
<TABLE><TBODY>
<TR><TD>구분</TD><TD>당기말</TD><TD>전기말</TD></TR>
<TR><TD>제품</TD><TD>1,234,567</TD><TD>1,111,111</TD></TR>
<TR><TD>원재료</TD><TD>7,654,321</TD><TD>2,222,222</TD></TR>
</TBODY></TABLE>
<P>10. 차입금(1) 당기말 현재 차입금의 내역은 다음과 같습니다.</P>
<TABLE><TBODY>
<TR><TD>차입처</TD><TD>금액</TD></TR>
<TR><TD>가나은행</TD><TD>5,000,000</TD></TR>
</TBODY></TABLE>
</SECTION-2></SECTION-1>
</DOCUMENT>
"""


def _metadata() -> DocumentMetadata:
    return DocumentMetadata(
        rcept_no="20260312000123", corp_code="00126380", corp_name="회사", fiscal_year=2025, filed_at="20260312",
        collected_at="2026-11-02T00:00:00Z", source_url="https://opendart.fss.or.kr/api/document.xml?rcept_no=20260312000123",
        raw_sha256="0" * 64,
    )


def _chunks(mode: str):
    blocks = parse_xml_bytes(XML.encode("utf-8"), split_headings=mode == FIXED_ROWS_MODE)
    return build_chunks(_metadata(), blocks, max_chars=1000, overlap_chars=100, table_mode=mode)


@pytest.mark.parametrize("text, title", [
    ("9. 재고자산당기말 및 전기말 현재 재고자산 내역은 다음과 같습니다.", "9. 재고자산"),
    ("7. 매출채권및기타채권(1) 당기말 및 전기말 현재 내역은 다음과 같습니다.", "7. 매출채권및기타채권"),
    ("2) 금융자산의 만기구조연결실체는 현금유입을 통해 유동성을 관리하고 있습니다.", "2) 금융자산의 만기구조"),
])
def test_heading_title_is_cut_where_the_body_starts(text, title):
    assert split_heading_title(text) == title


@pytest.mark.parametrize("text", ["12. 차입금", "3) 유동성위험: 2026년 6월 상환 예정", "본문만 있는 문단입니다."])
def test_heading_without_a_recognized_body_start_is_left_alone(text):
    assert split_heading_title(text) is None


def test_v10_section_titles_do_not_contain_body_sentences():
    titles = {chunk.section_path[-1] for chunk in _chunks(FIXED_ROWS_MODE)}

    assert "9. 재고자산" in titles and "10. 차입금" in titles
    assert not any("다음과 같습니다" in title for title in titles)


def test_v9_section_titles_are_unchanged():
    titles = {chunk.section_path[-1] for chunk in _chunks(FIXED_MODE)}

    assert "9. 재고자산당기말 및 전기말 현재 재고자산 내역은 다음과 같습니다." in titles


def test_v10_keeps_each_table_row_on_its_own_line():
    inventory = next(c for c in _chunks(FIXED_ROWS_MODE) if "원재료" in c.content)
    lines = inventory.content.split("\n")

    assert "제품 | 1,234,567 | 1,111,111" in lines
    assert "원재료 | 7,654,321 | 2,222,222" in lines


def test_v9_joins_rows_with_spaces():
    inventory = next(c for c in _chunks(FIXED_MODE) if "원재료" in c.content)

    assert "\n" not in inventory.content
    assert "1,111,111 원재료" in inventory.content  # 앞 행의 끝과 다음 행의 시작이 공백으로만 이어진다


def test_v10_does_not_insert_the_section_title_before_untitled_table_rows():
    """v9는 제목 없는 표의 묶음마다 절 제목을 앞에 붙였다. v10 본문에는 원문에 있는 글자만 있다."""

    loans = next(c for c in _chunks(FIXED_ROWS_MODE) if "가나은행" in c.content)

    assert loans.content.count("10. 차입금") == 1  # 원문의 제목 문단 한 번뿐
    assert "차입처 | 금액\n가나은행 | 5,000,000" in loans.content


def test_v10_content_hash_matches_the_trust_boundary_integrity_check():
    """저장한 해시는 본문 글자 그대로의 해시여야 한다. 아니면 서비스의 무결성 검사가 모든 청크를 버린다(158번에서 찾음)."""

    from backend.graph.trust_boundary import content_intact

    chunks = _chunks(FIXED_ROWS_MODE)

    assert any("\n" in chunk.content for chunk in chunks)
    assert all(content_intact({"content": chunk.content, "content_sha256": chunk.content_sha256}) for chunk in chunks)


def test_v9_content_hash_still_matches_the_integrity_check():
    from backend.graph.trust_boundary import content_intact

    assert all(content_intact({"content": c.content, "content_sha256": c.content_sha256}) for c in _chunks(FIXED_MODE))
