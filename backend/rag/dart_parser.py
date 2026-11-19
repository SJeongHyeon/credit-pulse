"""OpenDART 원본 XML에서 검색 가능한 구조 블록을 추출한다.

OpenDART XML은 보고서와 작성 도구에 따라 태그 구성이 조금씩 다르다.
이 파서는 SECTION/TITLE/P/TABLE 계열 태그를 느슨하게 받아들이되, 대상
장(II, IV, XI, 연결·별도 재무제표 주석) 밖의 본문은 내보내지 않는다.
"""

from __future__ import annotations

import copy
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree as ET

from backend.rag.models import ParsedBlock


class DartParseError(ValueError):
    """원본을 구조화할 수 없을 때 발생한다."""


_SECTION_RE = re.compile(r"^SECTION(?:[-_]?\d+)?$", re.IGNORECASE)
_TOP_LEVEL_CHAPTER_RE = re.compile(r"^[IVXLCDM]{1,6}\s*(?:\.|장|\s)", re.IGNORECASE)
_NOTES_SCOPE_END_RE = re.compile(
    # "4. 재무제표"처럼 제목이 재무제표로 끝날 때만 주석 범위의 끝으로 본다.
    # "2. 연결재무제표 작성기준 및 중요한 회계정책" 같은 주석 제목에서 멈추지 않게 한다.
    r"^\d{1,2}\s*[.)]\s*(?:(?:연결\s*)?재무제표\s*$|배당에\s*관한\s*사항|증권의\s*발행|기타\s*재무에\s*관한\s*사항)",
    re.IGNORECASE,
)
# 주석 제목은 "38. 위험관리"처럼 숫자 뒤에 마침표가 오는 형식만 받는다.
# "3) 유동성위험", "1 상각후원가측정금융자산"은 주석 안의 소제목이라 note_no를 바꾸지 않는다.
_NOTE_HEADING_RE = re.compile(
    r"^\s*(?:(?:주석|NOTE)\s*)?(\d{1,3})\s*\.\s*(.{1,100})$",
    re.IGNORECASE,
)
_SUB_HEADING_RE = re.compile(r"^\s*\d{1,3}(?:\s*\)|\s+(?=[^\d\s.]))")
# 원문에서 주석 제목과 본문이 한 문단에 붙은 경우("38. 위험관리(1) 자본위험관리...").
# 제목은 첫 소제목 표시 "(1)" 또는 "1)" 앞까지로 본다.
_LONG_NOTE_HEADING_RE = re.compile(r"^(\d{1,3})\s*\.\s*(?=[가-힣A-Za-z])")
_LONG_NOTE_TITLE_END_RE = re.compile(r"\(1\)|(?<![\d(])1\)")
# 140자 이하 제목 문단이 "순수 제목"인지 판단한다. 순수 제목으로 확정하지 못하면
# 본문이 붙은 것으로 보고 문단을 보존한다("3) 유동성위험: 2026년 6월 … 상환 예정").
# 번호 표시("3)", "5.", "2.2.1", "1 ")를 뗀 나머지에 숫자·콜론류·서술어 종결이 없고
# 30자 이하일 때만 순수 제목이다. 100개사 원문에서 남는 순수 제목은 모두 명사로 끝났다.
_HEADING_NUMBER_RE = re.compile(r"^\s*(?:(?:주석|NOTE)\s*)?\d{1,3}(?:\.\d{1,3})*\s*[.)]?\s*", re.IGNORECASE)
_HEADING_BODY_HINT_RE = re.compile(
    r"\d|[:：=~]|다\s*\.|(?:다|음|함|됨|임|예정|없음|있음)\s*[.。]?\s*$"
)
_PURE_HEADING_MAX = 30
# v10(11/16): 제목과 본문이 한 문단에 구분 없이 붙은 경우("9. 재고자산당기말 및 전기말 현재 … 다음과 같습니다.")
# 제목이 끝나는 곳을 찾는다. 소제목 표시 "(1)"·"1)"·"①"이나, 주석 본문이 흔히 시작하는 말 앞까지를 제목으로 본다.
# 원문 XML에 구분이 없어 추정이다. 찾지 못하면 None을 돌려주고 호출부가 문단 전체를 제목으로 둔다(v9 동작).
_HEADING_BODY_START_RE = re.compile(
    r"\(\s*1\s*\)|(?<![\d(])1\)|①|\(\s*가\s*\)"
    r"|당기\s*말|전기\s*말|당기\s*(?:및|와|중|의)|보고기간|당분기|연결실체|연결회사|연결기업|연결그룹"
    r"|당사[는의가와]|회사[는의가와]|지배기업|종속기업|다음과|다음의|다음은|아래와|상기|\(\s*단위"
)
_HEADING_TITLE_MAX = 40
_LONG_NOTE_TITLE_MAX = 40
_LONG_NOTE_MAX_STEP = 2
_SPACE_RE = re.compile(r"\s+")
_INVALID_XML_CHAR_RE = re.compile(
    "[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]"
)
_BARE_AMP_RE = re.compile(
    r"&(?!#\d+;|#x[0-9A-Fa-f]+;|amp;|lt;|gt;|quot;|apos;)"
)
# 표 셀의 "<당기말>"처럼 꺾쇠로 감싼 한글은 태그가 아니라 글자다.
# 태그로 읽히면 닫히지 않은 요소가 되어 뒤따르는 주석 제목과 표를 삼킨다.
_KOREAN_PSEUDO_TAG_RE = re.compile(r"<(?=[가-힣])")
_BLANK_LINE_RE = re.compile(r"(?:\r?\n)[ \t]*(?:\r?\n)")
_SENTENCE_MARK_RE = re.compile(r"[.!?。！？]")
_PARAGRAPH_TAGS = {"P", "PARA", "PARAGRAPH", "TEXT", "TP"}


@dataclass
class _ParserState:
    chapter: str | None = None
    section_path: tuple[str, ...] = ()
    note_no: str | None = None
    table_title: str | None = None


def normalize_text(value: str) -> str:
    """해시와 비교에 쓸 결정적 본문 정규화."""

    return _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", value)).strip()


def _tag(element: ET.Element) -> str:
    value = element.tag
    if not isinstance(value, str):
        return ""
    return value.rsplit("}", 1)[-1].upper()


def _element_text(element: ET.Element) -> str:
    return normalize_text(" ".join(part for part in element.itertext() if part))


def _paragraph_text_and_nested_sections(
    element: ET.Element,
) -> tuple[str, list[ET.Element]]:
    """복구 파싱으로 문단 아래 들어간 후속 SECTION을 본문과 분리한다."""

    parts: list[str] = []
    nested_sections: list[ET.Element] = []

    def collect(node: ET.Element, *, root: bool = False) -> None:
        if not root and _SECTION_RE.match(_tag(node)):
            nested_sections.append(node)
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            collect(child)
            if child.tail:
                parts.append(child.tail)

    collect(element, root=True)
    return normalize_text(" ".join(parts)), nested_sections


def _elements_without_nested_sections(element: ET.Element):
    """현재 요소를 순회하되 복구 파싱으로 들어온 후속 SECTION은 건너뛴다."""

    yield element
    for child in element:
        if _SECTION_RE.match(_tag(child)):
            continue
        yield from _elements_without_nested_sections(child)


def _top_level_nested_sections(element: ET.Element) -> list[ET.Element]:
    sections: list[ET.Element] = []

    def collect(node: ET.Element) -> None:
        for child in node:
            if _SECTION_RE.match(_tag(child)):
                sections.append(child)
            else:
                collect(child)

    collect(element)
    return sections


def _direct_title(section: ET.Element) -> tuple[ET.Element | None, str]:
    for child in section:
        if _tag(child).startswith("TITLE"):
            return child, _element_text(child)
    return None, ""


def _chapter_from_title(title: str) -> str | None:
    compact = normalize_text(title)
    upper = compact.upper()
    if re.match(r"^II(?:\.|\s|장)", upper) or "사업의 내용" in compact:
        return "II"
    if re.match(r"^IV(?:\.|\s|장)", upper) or "이사의 경영진단" in compact:
        return "IV"
    if re.match(r"^XI(?:\.|\s|장)", upper) or "투자자 보호" in compact:
        return "XI"
    if "재무제표 주석" in compact or "연결재무제표주석" in compact.replace(" ", ""):
        return "NOTES"
    return None


def _note_heading(text: str) -> tuple[str, str] | None:
    match = _NOTE_HEADING_RE.match(normalize_text(text))
    if not match:
        return None
    return match.group(1), normalize_text(match.group(2))


def _is_pure_heading(text: str) -> bool:
    """번호 문단이 본문 없이 제목만 있는지 판단한다. 애매하면 False(본문 보존)."""

    rest = _HEADING_NUMBER_RE.sub("", normalize_text(text), count=1)
    return len(rest) <= _PURE_HEADING_MAX and not _HEADING_BODY_HINT_RE.search(rest)


def split_heading_title(text: str) -> str | None:
    """번호가 붙은 제목 문단에 본문이 붙어 있으면 제목 부분만 돌려준다. 가르지 못하면 None."""

    normalized = normalize_text(text)
    number = _HEADING_NUMBER_RE.match(normalized)
    if not number:
        return None
    rest = normalized[number.end():]
    start = _HEADING_BODY_START_RE.search(rest)
    if start is None:
        return None
    title_body = rest[: start.start()].strip()
    if not 2 <= len(title_body) <= _HEADING_TITLE_MAX:
        return None
    return normalize_text(normalized[: number.end()] + title_body)


def heading_is_body_only(text: str) -> bool:
    """번호 뒤에 제목 없이 곧바로 본문이 시작하는 문장인지 본다(본문 시작 말이 번호 바로 뒤에 온다)."""

    normalized = normalize_text(text)
    number = _HEADING_NUMBER_RE.match(normalized)
    if not number:
        return False
    start = _HEADING_BODY_START_RE.search(normalized[number.end():])
    return start is not None and len(normalized[number.end(): number.end() + start.start()].strip()) < 2


def _long_note_title(text: str, current_note_no: str | None) -> str | None:
    """140자를 넘는 문단 앞에 붙은 다음 주석 제목을 잘라 낸다.

    긴 문단은 번호 목록 본문일 수도 있으므로, 번호가 현재 주석의 다음 번호
    (+1~+2)일 때만 제목으로 본다.
    """

    match = _LONG_NOTE_HEADING_RE.match(text)
    if not match:
        return None
    current = int(current_note_no) if current_note_no and current_note_no.isdigit() else 0
    number = int(match.group(1))
    if not current < number <= current + _LONG_NOTE_MAX_STEP:
        return None
    end = _LONG_NOTE_TITLE_END_RE.search(text, match.end())
    if end is not None and end.start() - match.end() <= _LONG_NOTE_TITLE_MAX:
        title = text[: end.start()]
    else:
        title = text[: match.end() + _LONG_NOTE_TITLE_MAX]
    return normalize_text(title)


def _decode_xml(raw: bytes, source: str) -> str:
    """선언을 믿지 않고 실제 바이트를 UTF-8, CP949 순으로 검사한다."""

    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DartParseError(f"XML 디코딩 실패(UTF-8/CP949): {source}")


def _parse_xml(raw: bytes, source: str) -> ET.Element:
    text = _decode_xml(raw, source)
    text = _INVALID_XML_CHAR_RE.sub("", text)
    text = _BARE_AMP_RE.sub("&amp;", text)
    text = _KOREAN_PSEUDO_TAG_RE.sub("&lt;", text)
    try:
        parser = ET.XMLParser(
            encoding="utf-8",
            recover=True,
            huge_tree=True,
            no_network=True,
            resolve_entities=False,
        )
        root = ET.fromstring(text.encode("utf-8"), parser=parser)
        if root is None:
            raise DartParseError(f"XML 복구 실패: {source}")
        return root
    except (ET.XMLSyntaxError, LookupError, ValueError) as exc:
        raise DartParseError(f"XML 파싱 실패: {source}: {exc}") from exc


def _separator_counts(raw: bytes, source: str) -> dict[str, int]:
    text = _decode_xml(raw, source)
    root = _parse_xml(raw, source)
    plain_text = " ".join(part for part in root.itertext() if part)
    elements = list(root.iter())
    return {
        "blank_lines": len(_BLANK_LINE_RE.findall(text)),
        "line_breaks": text.count("\n"),
        "sentence_marks": len(_SENTENCE_MARK_RE.findall(plain_text)),
        "paragraph_elements": sum(_tag(node) in _PARAGRAPH_TAGS for node in elements),
        "section_elements": sum(bool(_SECTION_RE.match(_tag(node))) for node in elements),
        "table_rows": sum(_tag(node) in {"TR", "ROW"} for node in elements),
    }


def document_separator_counts(path: str | Path) -> dict[str, int]:
    """청킹 전 원문 형식의 구분자 빈도를 문서 단위로 집계한다."""

    source_path = Path(path)
    totals = {
        "blank_lines": 0,
        "line_breaks": 0,
        "sentence_marks": 0,
        "paragraph_elements": 0,
        "section_elements": 0,
        "table_rows": 0,
    }

    def add(raw: bytes, source: str) -> None:
        for key, value in _separator_counts(raw, source).items():
            totals[key] += value

    if source_path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(source_path) as archive:
                names = sorted(
                    name
                    for name in archive.namelist()
                    if name.lower().endswith((".xml", ".xhtml", ".html", ".htm"))
                    and not name.endswith("/")
                )
                if not names:
                    raise DartParseError(f"ZIP 안에 XML 문서가 없습니다: {source_path.name}")
                parsed = 0
                first_error: DartParseError | None = None
                for name in names:
                    try:
                        add(archive.read(name), name)
                        parsed += 1
                    except DartParseError as exc:
                        first_error = first_error or exc
                if parsed == 0:
                    raise first_error or DartParseError(
                        f"ZIP 구분자 분석 실패: {source_path.name}"
                    )
        except zipfile.BadZipFile as exc:
            raise DartParseError(f"손상된 ZIP: {source_path.name}") from exc
        return totals

    add(source_path.read_bytes(), source_path.name)
    return totals


def _table_rows(table: ET.Element) -> list[str]:
    rows: list[str] = []
    row_nodes = [
        node
        for node in _elements_without_nested_sections(table)
        if _tag(node) in {"TR", "ROW"}
    ]
    if not row_nodes:
        row_nodes = [table]
    for row in row_nodes:
        cells = [
            _paragraph_text_and_nested_sections(cell)[0]
            for cell in _elements_without_nested_sections(row)
            if _tag(cell) in {"TD", "TH", "TE", "TU", "CELL"}
        ]
        cells = [cell for cell in cells if cell]
        if not cells and row is table:
            text = _element_text(table)
            if text:
                cells = [text]
        if cells:
            rows.append(" | ".join(cells))
    return rows


def parse_xml_bytes(raw: bytes, *, source: str = "document.xml", split_headings: bool = False) -> list[ParsedBlock]:
    """XML 한 파일에서 대상 장의 문단과 표 행을 순서대로 반환한다."""

    root = _parse_xml(raw, source)
    blocks: list[ParsedBlock] = []
    ordinal = 0
    table_ordinal = 0

    def emit(
        text: str,
        state: _ParserState,
        *,
        is_table: bool = False,
        table_group: str | None = None,
    ) -> None:
        nonlocal ordinal
        normalized = normalize_text(text)
        if not normalized or state.chapter is None:
            return
        ordinal += 1
        blocks.append(
            ParsedBlock(
                chapter=state.chapter,
                section_path=state.section_path,
                note_no=state.note_no,
                table_title=state.table_title if is_table else None,
                ordinal=ordinal,
                text=normalized,
                is_table=is_table,
                table_group=table_group,
            )
        )

    def apply_heading(state: _ParserState, title: str) -> _ParserState:
        title = normalize_text(title)
        if not title:
            return state
        updated = copy.copy(state)
        chapter = _chapter_from_title(title)
        if chapter is not None:
            updated.chapter = chapter
            updated.section_path = (title,)
            updated.note_no = None
            updated.table_title = title
            return updated
        if _TOP_LEVEL_CHAPTER_RE.match(title):
            updated.chapter = None
            updated.section_path = ()
            updated.note_no = None
            updated.table_title = None
            return updated
        if updated.chapter == "NOTES" and _NOTES_SCOPE_END_RE.match(title):
            updated.chapter = None
            updated.section_path = ()
            updated.note_no = None
            updated.table_title = None
            return updated
        if updated.chapter is None:
            return updated
        note = _note_heading(title) if updated.chapter == "NOTES" else None
        if note:
            updated.note_no = note[0]
            chapter_title = updated.section_path[:1]
            updated.section_path = (*chapter_title, title)
        elif (
            updated.chapter == "NOTES"
            and updated.note_no is not None
            and _SUB_HEADING_RE.match(title)
        ):
            # 소제목은 현재 주석 제목 아래 한 단계로만 둔다(형제 소제목끼리 쌓이지 않게).
            updated.section_path = (*updated.section_path[:2], title)
        else:
            updated.section_path = (*updated.section_path, title)
        updated.table_title = title
        return updated

    def walk(element: ET.Element, state: _ParserState) -> _ParserState:
        nonlocal table_ordinal
        tag = _tag(element)

        if _SECTION_RE.match(tag):
            local = copy.copy(state)
            title_node, title = _direct_title(element)
            if title:
                local = apply_heading(local, title)
            for child in element:
                if child is title_node:
                    continue
                local = walk(child, local)
            return state

        if tag.startswith("TITLE"):
            title_text = _element_text(element)
            if split_headings and state.chapter == "NOTES" and not _is_pure_heading(title_text):
                # v10: 제목 태그 안에 본문이 함께 든 경우. 제목 부분만 절 제목으로 쓰고 글 전체는 본문으로 남긴다.
                short = split_heading_title(title_text)
                if short:
                    state = apply_heading(state, short)
                    emit(title_text, state)
                    return state
            return apply_heading(state, title_text)

        if tag in {"TABLE", "TBL"}:
            table_ordinal += 1
            table_group = f"{source}#table-{table_ordinal}"
            local = copy.copy(state)
            nested_sections = _top_level_nested_sections(element)
            explicit_title = element.attrib.get("TITLE") or element.attrib.get("title")
            if explicit_title:
                local.table_title = normalize_text(explicit_title)
            elif local.section_path:
                local.table_title = local.section_path[-1]
            for row in _table_rows(element):
                emit(row, local, is_table=True, table_group=table_group)
            for section in nested_sections:
                walk(section, state)
            return state

        if tag in _PARAGRAPH_TAGS:
            text, nested_sections = _paragraph_text_and_nested_sections(element)
            long_title = (
                _long_note_title(text, state.note_no)
                if text and state.chapter == "NOTES" and len(text) > 140
                else None
            )
            if text and state.chapter == "NOTES" and len(text) <= 140:
                if _note_heading(text) or (
                    state.note_no is not None and _SUB_HEADING_RE.match(text)
                ):
                    pure = _is_pure_heading(text)
                    heading = text
                    if split_headings and not pure:
                        if heading_is_body_only(text):
                            # v10: 번호로 시작하지만 제목 없이 바로 본문인 문장("2) 당기 및 전기 중 … 다음과 같습니다.").
                            # 절 제목을 바꾸지 않고 본문으로만 낸다.
                            emit(text, state)
                            for section in nested_sections:
                                walk(section, state)
                            return state
                        heading = split_heading_title(text) or text
                    state = apply_heading(state, heading)
                    if not pure:
                        # 140자 초과 분기와 같이 제목은 경계로 쓰고, 붙은 본문은 문단 전체로 낸다.
                        emit(text, state)
                else:
                    emit(text, state)
            elif long_title:
                # 제목만 경계로 쓰고, 붙어 있던 본문은 잃지 않도록 문단 전체를 낸다.
                state = apply_heading(state, long_title)
                emit(text, state)
            else:
                emit(text, state)
            for section in nested_sections:
                walk(section, state)
            return state

        local = state
        for child in element:
            local = walk(child, local)
        return local

    walk(root, _ParserState())
    if not blocks:
        raise DartParseError("대상 장(II, IV, XI, 재무제표 주석)의 본문을 찾지 못했습니다")
    return blocks


def parse_document(path: str | Path, *, split_headings: bool = False) -> list[ParsedBlock]:
    """OpenDART ZIP 또는 단일 XML 파일을 파싱한다.

    split_headings(v10): 제목과 본문이 한 문단에 붙어 있으면 제목 부분만 절 제목으로 쓴다. 기본값은 v9 동작이다.
    """

    source_path = Path(path)
    if source_path.suffix.lower() == ".zip":
        blocks: list[ParsedBlock] = []
        try:
            with zipfile.ZipFile(source_path) as archive:
                xml_names = sorted(
                    name
                    for name in archive.namelist()
                    if name.lower().endswith((".xml", ".xhtml", ".html", ".htm"))
                    and not name.endswith("/")
                )
                if not xml_names:
                    raise DartParseError(f"ZIP 안에 XML 문서가 없습니다: {source_path.name}")
                errors: list[str] = []
                for name in xml_names:
                    try:
                        blocks.extend(parse_xml_bytes(archive.read(name), source=name, split_headings=split_headings))
                    except DartParseError as exc:
                        errors.append(str(exc))
                if not blocks:
                    detail = errors[0] if errors else "대상 본문 없음"
                    raise DartParseError(f"ZIP 파싱 실패: {source_path.name}: {detail}")
        except zipfile.BadZipFile as exc:
            raise DartParseError(f"손상된 ZIP: {source_path.name}") from exc
        return [
            ParsedBlock(
                chapter=block.chapter,
                section_path=block.section_path,
                note_no=block.note_no,
                table_title=block.table_title,
                ordinal=index,
                text=block.text,
                is_table=block.is_table,
                table_group=block.table_group,
            )
            for index, block in enumerate(blocks, start=1)
        ]

    return parse_xml_bytes(source_path.read_bytes(), source=source_path.name, split_headings=split_headings)


def extract_plain_text(path: str | Path) -> str:
    """원문 대조용으로 ZIP/XML의 태그를 제외한 전체 텍스트를 반환한다."""

    source_path = Path(path)
    parts: list[str] = []
    if source_path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(source_path) as archive:
                names = sorted(
                    name
                    for name in archive.namelist()
                    if name.lower().endswith((".xml", ".xhtml", ".html", ".htm"))
                    and not name.endswith("/")
                )
                for name in names:
                    try:
                        parts.append(_element_text(_parse_xml(archive.read(name), name)))
                    except DartParseError:
                        continue
        except zipfile.BadZipFile as exc:
            raise DartParseError(f"손상된 ZIP: {source_path.name}") from exc
    else:
        parts.append(_element_text(_parse_xml(source_path.read_bytes(), source_path.name)))
    text = normalize_text(" ".join(parts))
    if not text:
        raise DartParseError(f"원문 텍스트를 추출하지 못했습니다: {source_path.name}")
    return text
