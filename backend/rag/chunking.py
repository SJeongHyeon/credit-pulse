"""구조화된 DART 블록을 결정적인 검색 청크로 변환한다."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import replace
from difflib import SequenceMatcher
from typing import Iterable

from backend.rag.dart_parser import normalize_text
from backend.rag.models import ChunkRecord, DocumentMetadata, ParsedBlock
from backend.rag.vector_store import is_numeric_heavy


ACCOUNTING_POLICY_TITLES = (
    "중요한 회계정책",
    "주요 회계정책",
    "유의적인 회계정책",
    "회계정책의 변경",
)
DEFAULT_CORPUS_ID = "corpus-" + "0" * 24
MERGE_INTRO_MODE = "group_merge_short"
MERGE_TABLE_ONLY_MODE = "group_merge_short_table"
MERGE_MODES = (MERGE_INTRO_MODE, MERGE_TABLE_ONLY_MODE)
# 장·주석 안에서 표 묶음 청크를 이어 붙인 뒤 고정 길이 창으로 자른다(사전 등록 8번).
FIXED_MODE = "fixed_window"
# v10(11/16): 표 행을 묶음(제목·머리행 반복) 대신 행 단위로 이어 붙이고, 행·문단 경계를 줄바꿈으로 남긴다.
# v9의 두 결함(행 경계가 공백으로 뭉개짐, 표 앞에 절 제목이 끼어듦)을 없앤다. v9(fixed_window)는 그대로 재현된다.
FIXED_ROWS_MODE = "fixed_window_rows"
FIXED_MODES = (FIXED_MODE, FIXED_ROWS_MODE)
TABLE_MODES = ("row", "group", "group_drop_numeric", *MERGE_MODES, FIXED_MODE, FIXED_ROWS_MODE)
SHORT_CHUNK_CHARS = 200
_TWO_DIGITS_RE = re.compile(r"\d[\d,]*\d")


def content_sha256(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def is_accounting_policy(block: ParsedBlock) -> bool:
    path = " > ".join(block.section_path)
    return block.chapter == "NOTES" and any(
        title in path for title in ACCOUNTING_POLICY_TITLES
    )


def remove_common_boilerplate(
    documents: list[tuple[DocumentMetadata, list[ParsedBlock]]],
    *,
    min_companies: int = 3,
    min_chars: int = 20,
) -> list[tuple[DocumentMetadata, list[ParsedBlock]]]:
    """서로 다른 회사 3곳 이상에 같은 문단이 있으면 제거한다."""

    companies_by_text: dict[str, set[str]] = defaultdict(set)
    for metadata, blocks in documents:
        for block in blocks:
            text = normalize_text(block.text)
            if len(text) >= min_chars:
                companies_by_text[text].add(metadata.corp_code)

    boilerplate = {
        text
        for text, companies in companies_by_text.items()
        if len(companies) >= min_companies
    }
    return [
        (
            metadata,
            [block for block in blocks if normalize_text(block.text) not in boilerplate],
        )
        for metadata, blocks in documents
    ]


def _split_long_text(
    text: str, max_chars: int, overlap_chars: int = 100
) -> list[str]:
    text = normalize_text(text)
    if len(text) <= max_chars:
        return [text] if text else []

    parts: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        window = remaining[: max_chars + 1]
        boundaries = [
            window.rfind(token)
            for token in (". ", "다. ", "요. ", "; ", " ")
        ]
        split_at = max((value for value in boundaries if value >= max_chars // 2), default=-1)
        fixed_fallback = split_at < 0
        if split_at < 0:
            split_at = max_chars
        elif window[split_at : split_at + 2] in {". ", "; "}:
            split_at += 1
        part = normalize_text(remaining[:split_at])
        if part:
            parts.append(part)
        next_start = split_at
        if fixed_fallback and overlap_chars:
            next_start = max(1, split_at - overlap_chars)
        remaining = normalize_text(remaining[next_start:])
    if remaining:
        parts.append(remaining)
    return parts


def _chunk_id(rcept_no: str, ordinal: int, sha256: str) -> str:
    seed = f"{rcept_no}:{ordinal}:{sha256}".encode("utf-8")
    return f"chunk-{hashlib.sha256(seed).hexdigest()[:24]}"


def build_chunks(
    metadata: DocumentMetadata,
    blocks: Iterable[ParsedBlock],
    *,
    max_chars: int = 1000,
    overlap_chars: int = 100,
    corpus_id: str = DEFAULT_CORPUS_ID,
    table_mode: str = "row",
) -> list[ChunkRecord]:
    """문단과 표를 결정적으로 청킹한다.

    ``row``는 현재 A, ``group``은 표 제목·헤더·여러 행을 1,000자
    이내로 묶는 B, ``group_drop_numeric``은 숫자 위주 행을 제외하는 C다.
    """

    if max_chars < 100:
        raise ValueError("max_chars는 100 이상이어야 합니다")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars는 0 이상 max_chars 미만이어야 합니다")
    if table_mode not in TABLE_MODES:
        raise ValueError(f"지원하지 않는 table_mode: {table_mode}")
    if table_mode == FIXED_MODE:
        sources = build_chunks(
            metadata,
            list(blocks),
            max_chars=max_chars,
            overlap_chars=overlap_chars,
            corpus_id=corpus_id,
            table_mode="group",
        )
        return fixed_window_chunks(sources, max_chars=max_chars, overlap_chars=overlap_chars)
    if table_mode == FIXED_ROWS_MODE:
        sources = build_chunks(
            metadata,
            list(blocks),
            max_chars=max_chars,
            overlap_chars=overlap_chars,
            corpus_id=corpus_id,
            table_mode="row",
        )
        return fixed_window_chunks(sources, max_chars=max_chars, overlap_chars=overlap_chars, keep_lines=True)

    chunks: list[ChunkRecord] = []
    buffer: list[str] = []
    buffer_context: tuple[str, tuple[str, ...], str | None] | None = None

    def append_chunk(
        text: str,
        *,
        chapter: str,
        section_path: tuple[str, ...],
        note_no: str | None,
        table_title: str | None,
        is_table: bool,
    ) -> None:
        normalized = normalize_text(text)
        if not normalized:
            return
        ordinal = len(chunks) + 1
        sha256 = content_sha256(normalized)
        chunks.append(
            ChunkRecord(
                chunk_id=_chunk_id(metadata.rcept_no, ordinal, sha256),
                corpus_id=corpus_id,
                rcept_no=metadata.rcept_no,
                corp_code=metadata.corp_code,
                corp_name=metadata.corp_name,
                fiscal_year=metadata.fiscal_year,
                chapter=chapter,
                section_path=section_path,
                note_no=note_no,
                table_title=table_title,
                ordinal=ordinal,
                content=normalized,
                content_sha256=sha256,
                is_table=is_table,
                superseded=metadata.superseded,
            )
        )

    def flush() -> None:
        nonlocal buffer, buffer_context
        if not buffer or buffer_context is None:
            buffer = []
            buffer_context = None
            return
        chapter, section_path, note_no = buffer_context
        append_chunk(
            "\n".join(buffer),
            chapter=chapter,
            section_path=section_path,
            note_no=note_no,
            table_title=None,
            is_table=False,
        )
        buffer = []
        buffer_context = None

    def append_grouped_table(group: list[ParsedBlock]) -> None:
        """한 TABLE/TBL의 제목·첫 행(헤더)과 여러 행을 max_chars로 묶는다."""

        if not group:
            return
        first = group[0]
        title = normalize_text(first.table_title or "")
        header = normalize_text(first.text)
        title_prefix = [title] if title and len(title) <= max_chars // 4 else []
        full_prefix = [value for value in (title, header) if value]
        if len(full_prefix) == 2 and full_prefix[0] == full_prefix[1]:
            full_prefix = full_prefix[:1]

        # 정상적인 짧은 헤더만 각 묶음에 반복한다. 첫 행 자체가 매우 긴
        # 비정형 표는 헤더를 데이터 행으로 한 번만 처리해 폭증을 막는다.
        if len("\n".join(full_prefix)) <= max_chars // 2:
            prefix = full_prefix
            rows = [(block, True) for block in group[1:]]
        else:
            prefix = title_prefix
            rows = [(group[0], False), *[(block, True) for block in group[1:]]]

        groups: list[list[str]] = []
        current = list(prefix)
        for block, numeric_filter_allowed in rows:
            row = normalize_text(block.text)
            if not row:
                continue
            if (
                table_mode == "group_drop_numeric"
                and numeric_filter_allowed
                and is_numeric_heavy(row)
            ):
                continue
            candidate = "\n".join([*current, row])
            if current != prefix and len(candidate) > max_chars:
                groups.append(current)
                current = list(prefix)
            candidate = "\n".join([*current, row])
            if len(candidate) <= max_chars:
                current.append(row)
                continue

            # 제목·헤더와 합쳐도 1,000자를 넘는 비정상적으로 긴 행은
            # 기존 결정적 분할을 적용하고 각 조각에 문맥 접두사를 반복한다.
            prefix_chars = len("\n".join(prefix))
            available = max_chars - prefix_chars - (1 if prefix else 0)
            if available < 1:
                if prefix:
                    groups.append(prefix)
                prefix = []
                available = max_chars
            # 접두사(표 제목·헤더)를 매 조각에 반복하므로 행 자체에는 overlap을
            # 두지 않는다. available=100일 때 99자 overlap으로 1자씩 전진하는
            # 병적 성능도 피한다.
            for piece in _split_long_text(row, available, 0):
                groups.append([*prefix, piece])
            current = list(prefix)

        if current and (current != prefix or not groups):
            groups.append(current)

        for lines in groups:
            content = "\n".join(lines)
            append_chunk(
                content,
                chapter=first.chapter,
                section_path=first.section_path,
                note_no=first.note_no,
                table_title=first.table_title,
                is_table=True,
            )

    ordered_blocks = sorted(blocks, key=lambda value: value.ordinal)
    index = 0
    while index < len(ordered_blocks):
        block = ordered_blocks[index]
        if is_accounting_policy(block):
            index += 1
            continue
        pieces = _split_long_text(block.text, max_chars, overlap_chars)
        if block.is_table:
            flush()
            if table_mode == "row":
                for piece in pieces:
                    append_chunk(
                        piece,
                        chapter=block.chapter,
                        section_path=block.section_path,
                        note_no=block.note_no,
                        table_title=block.table_title,
                        is_table=True,
                    )
                index += 1
                continue

            table_group = block.table_group
            group = [block]
            index += 1
            while index < len(ordered_blocks):
                candidate = ordered_blocks[index]
                if not candidate.is_table or candidate.table_group != table_group:
                    break
                group.append(candidate)
                index += 1
            append_grouped_table(group)
            continue

        context = (block.chapter, block.section_path, block.note_no)
        for piece in pieces:
            if buffer_context != context:
                flush()
                buffer_context = context
            candidate = "\n".join([*buffer, piece])
            if buffer and len(candidate) > max_chars:
                flush()
                buffer_context = context
            buffer.append(piece)
        index += 1
    flush()
    if table_mode in MERGE_MODES:
        return merge_short_chunks(
            chunks, include_intro=table_mode == MERGE_INTRO_MODE, max_chars=max_chars
        )
    return chunks


def _section_key(chunk: ChunkRecord) -> tuple[str, str | None]:
    return (chunk.chapter, chunk.note_no)


def _sections(chunks: list[ChunkRecord]) -> list[list[ChunkRecord]]:
    """연속한 같은 장·주석 청크끼리 묶는다. 경계에서 고정 창을 새로 시작하기 위해서다."""

    sections: list[list[ChunkRecord]] = []
    for chunk in chunks:
        if sections and _section_key(sections[-1][-1]) == _section_key(chunk):
            sections[-1].append(chunk)
        else:
            sections.append([chunk])
    return sections


def _window_starts(length: int, max_chars: int, overlap_chars: int) -> list[int]:
    step = max_chars - overlap_chars
    starts = [0]
    while starts[-1] + max_chars < length:
        starts.append(starts[-1] + step)
    return starts


def _normalize_lines(text: str) -> str:
    """줄마다 정규화하고 빈 줄을 버린 뒤 줄바꿈으로 다시 잇는다(행·문단 경계를 남긴다)."""

    return "\n".join(line for line in (normalize_text(part) for part in text.split("\n")) if line)


def fixed_window_chunks(
    sources: list[ChunkRecord],
    *,
    max_chars: int = 1000,
    overlap_chars: int = 100,
    keep_lines: bool = False,
) -> list[ChunkRecord]:
    """장·주석마다 청크 본문을 한 줄로 잇고 max_chars 창을 overlap_chars씩 겹쳐 자른다.

    마지막 창만 짧을 수 있다. 창의 장·절·주석 메타데이터는 창이 시작하는 원본 청크를 따른다.
    keep_lines(v10): 원본 청크(표 행·문단) 사이를 공백 대신 줄바꿈으로 잇고, 창 본문에서도 줄바꿈을 남긴다.
    """

    if max_chars <= overlap_chars:
        raise ValueError("overlap_chars는 max_chars보다 작아야 합니다")
    output: list[ChunkRecord] = []
    for section in _sections(sources):
        offsets: list[int] = []
        parts: list[str] = []
        cursor = 0
        for chunk in section:
            offsets.append(cursor)
            parts.append(chunk.content)
            cursor += len(chunk.content) + 1
        stream = ("\n" if keep_lines else " ").join(parts)
        for start in _window_starts(len(stream), max_chars, overlap_chars):
            end = start + max_chars
            content = _normalize_lines(stream[start:end]) if keep_lines else normalize_text(stream[start:end])
            if not content:
                continue
            owner = max(i for i, offset in enumerate(offsets) if offset <= start)
            covered = [
                chunk
                for chunk, offset in zip(section, offsets)
                if offset < end and offset + len(chunk.content) > start
            ]
            ordinal = len(output) + 1
            # v10(keep_lines): 본문에 줄바꿈이 있으므로 저장하는 글자 그대로의 해시를 쓴다. content_sha256()은 공백을
            # 뭉갠 글의 해시라, 신뢰 경계의 무결성 검사(trust_boundary.content_intact: 본문 그대로의 해시)와 어긋나
            # 모든 청크가 변조로 버려진다(158번 측정에서 실제로 그랬다).
            sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest() if keep_lines else content_sha256(content)
            output.append(
                replace(
                    section[owner],
                    chunk_id=_chunk_id(section[owner].rcept_no, ordinal, sha256),
                    ordinal=ordinal,
                    content=content,
                    content_sha256=sha256,
                    is_table=any(chunk.is_table for chunk in covered),
                    table_title=None,
                )
            )
    return output


def fixed_chunks_preserve_sources(
    sources: list[ChunkRecord],
    chunks: list[ChunkRecord],
    *,
    max_chars: int = 1000,
    overlap_chars: int = 100,
    keep_lines: bool = False,
) -> bool:
    """고정 창이 장·주석별 원본 줄을 빠짐없이 덮는지 확인한다.

    창 시작점이 (max_chars - overlap_chars) 간격이고 마지막 창이 줄 끝까지 닿으면
    잘린 글자가 없다. 창 수와 본문이 같은 입력으로 다시 만든 결과와 같은지도 본다.
    """

    rebuilt = fixed_window_chunks(sources, max_chars=max_chars, overlap_chars=overlap_chars, keep_lines=keep_lines)
    if [c.content for c in rebuilt] != [c.content for c in chunks]:
        return False
    for section in _sections(sources):
        length = len(" ".join(chunk.content for chunk in section))
        starts = _window_starts(length, max_chars, overlap_chars)
        if starts[-1] + max_chars < length:
            return False
    return all(len(chunk.content) <= max_chars for chunk in chunks)


def chunk_body(content: str, table_title: str | None) -> str:
    """표 조각마다 반복되는 절 제목 접두사를 뺀 본문."""

    if table_title and content.startswith(table_title):
        return content[len(table_title) :].strip()
    return content


def _is_label(chunk: ChunkRecord) -> bool:
    return not _TWO_DIGITS_RE.search(chunk_body(chunk.content, chunk.table_title))


def _merge_key(chunk: ChunkRecord) -> tuple:
    return (chunk.chapter, chunk.section_path, chunk.note_no)


def _joined_content(group: list[ChunkRecord]) -> str:
    """첫 조각은 그대로, 뒤 조각은 제목을 빼고 잇는다. 직전 본문과 겹치면 한 번만 쓴다."""

    parts = [group[0].content]
    previous = chunk_body(group[0].content, group[0].table_title)
    for chunk in group[1:]:
        body = chunk_body(chunk.content, chunk.table_title)
        # 단어 경계에서만 겹침으로 본다. "변동내역"과 "변동내역에"는 겹침이 아니다.
        overlaps = body == previous or body.startswith(f"{previous} ")
        addition = body[len(previous) :].strip() if overlaps else body
        if addition:
            parts.append(addition)
        previous = body
    return normalize_text(" ".join(parts))


def merge_short_chunks(
    chunks: list[ChunkRecord],
    *,
    include_intro: bool,
    max_chars: int = 1000,
) -> list[ChunkRecord]:
    """같은 절의 짧은 소개 문장·라벨을 뒤따르는 데이터 표 조각 하나와 묶는다.

    파서는 제목, 단위 라벨, 숫자 표를 서로 다른 표로 내므로 표 묶음에서 합쳐지지
    않는다. 묶음마다 데이터는 하나만 두어 당기 표와 전기 표가 섞이지 않게 한다.
    """

    groups: list[list[ChunkRecord]] = []
    group: list[ChunkRecord] = []

    def close() -> None:
        nonlocal group
        if group:
            groups.append(group)
        group = []

    for index, chunk in enumerate(chunks):
        following = chunks[index + 1] if index + 1 < len(chunks) else None
        is_short = len(chunk.content) < SHORT_CHUNK_CHARS
        is_intro = (
            include_intro
            and not chunk.is_table
            and is_short
            and following is not None
            and following.is_table
        )
        if not (chunk.is_table and is_short) and not is_intro:
            close()
            groups.append([chunk])
            continue
        if group:
            has_data = any(item.is_table and not _is_label(item) for item in group)
            too_long = len(_joined_content([*group, chunk])) > max_chars
            if has_data or too_long or _merge_key(group[-1]) != _merge_key(chunk):
                close()
        group.append(chunk)
    close()

    merged: list[ChunkRecord] = []
    for ordinal, members in enumerate(groups, start=1):
        first = members[0]
        content = _joined_content(members) if len(members) > 1 else first.content
        sha256 = content_sha256(content)
        is_table = any(item.is_table for item in members)
        titles = [item.table_title for item in members if item.table_title]
        merged.append(
            replace(
                first,
                chunk_id=_chunk_id(first.rcept_no, ordinal, sha256),
                ordinal=ordinal,
                content=content,
                content_sha256=sha256,
                is_table=is_table,
                table_title=titles[0] if titles else None,
            )
        )
    return merged


def merged_chunks_preserve_sources(
    sources: Iterable[ChunkRecord], merged: Iterable[ChunkRecord]
) -> bool:
    """합치기 전 본문이 합친 청크에 순서대로 모두 들어 있는지 검사한다."""

    merged_contents = [chunk.content for chunk in merged]
    position = 0
    for source in sources:
        body = chunk_body(source.content, source.table_title)
        while position < len(merged_contents) and body not in merged_contents[position]:
            position += 1
        if position == len(merged_contents):
            return False
    return True


def _consume_table_row(stream: str, row: str, cursor: int, min_piece: int) -> int:
    """``row``가 ``stream``의 ``cursor`` 뒤에 순서대로 있으면 끝 위치, 없으면 -1.

    표 묶음 모드에서 ``max_chars``를 넘는 행은 조각마다 제목·헤더 접두사가
    붙은 채 여러 청크로 나뉜다. 그래서 행을 앞에서부터 조각 단위로 이어 찾는다.
    같은 문단이 문서 뒤쪽에 반복될 수 있으므로 가장 긴 일치가 아니라
    ``cursor``에서 가장 가까운 일치를 고르고, 그 자리에서 일치하는 만큼 늘린다.
    """

    remaining = row
    while remaining:
        head = remaining[:min_piece]
        position = stream.find(head, cursor)
        if position < 0:
            return -1
        length = len(head)
        limit = min(len(remaining), len(stream) - position)
        step = 256
        while length < limit:
            size = min(step, limit - length)
            if stream.startswith(remaining[length : length + size], position + length):
                length += size
            elif size > 1:
                step = max(1, size // 2)
            else:
                break
        cursor = position + length
        if length == len(remaining):
            return cursor
        remaining = remaining[length:].lstrip()
    return cursor


def chunks_preserve_blocks(
    blocks: Iterable[ParsedBlock],
    chunks: Iterable[ChunkRecord],
    *,
    max_chars: int = 1000,
    overlap_chars: int = 100,
    table_mode: str = "row",
) -> bool:
    """장·절별 원본 조각과 청크의 정규화 문자열·순서가 같은지 검사한다."""

    if table_mode not in {"row", "group", "group_drop_numeric"}:
        raise ValueError(f"지원하지 않는 table_mode: {table_mode}")

    expected: dict[tuple, list[str]] = defaultdict(list)
    expected_table_rows: list[str] = []
    for block in blocks:
        if is_accounting_policy(block):
            continue
        if block.is_table and table_mode != "row":
            if table_mode == "group_drop_numeric" and is_numeric_heavy(block.text):
                continue
            expected_table_rows.append(normalize_text(block.text))
            continue
        key = (
            block.chapter,
            block.section_path,
            block.note_no,
            block.is_table,
        )
        expected[key].extend(
            _split_long_text(block.text, max_chars, overlap_chars)
        )

    actual: dict[tuple, list[str]] = defaultdict(list)
    actual_table_text = "\n".join(
        chunk.content for chunk in chunks if chunk.is_table
    )
    for chunk in chunks:
        if chunk.is_table and table_mode != "row":
            continue
        key = (
            chunk.chapter,
            chunk.section_path,
            chunk.note_no,
            chunk.is_table,
        )
        actual[key].append(chunk.content)

    if expected.keys() != actual.keys():
        return False
    non_table_preserved = all(
        normalize_text(" ".join(parts))
        == normalize_text(" ".join(actual[key]))
        for key, parts in expected.items()
    )
    if not non_table_preserved or table_mode == "row":
        return non_table_preserved

    # B/C는 제목·헤더를 반복하고 긴 행을 조각낼 수 있으므로 원본 행이
    # (조각 단위로라도) 순서대로 모두 포함되는지만 확인한다.
    # C에서 제외한 숫자 행은 expected에 넣지 않는다.
    cursor = 0
    normalized_actual = normalize_text(actual_table_text)
    min_piece = max(1, max_chars // 8)
    for row in expected_table_rows:
        cursor = _consume_table_row(normalized_actual, row, cursor, min_piece)
        if cursor < 0:
            return False
    return True


def mark_prior_year_near_duplicates(
    chunks: list[ChunkRecord], *, threshold: float = 0.9
) -> list[ChunkRecord]:
    """같은 회사의 전년 문단과 90% 이상 같으면 near_dup를 표시한다."""

    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold는 0과 1 사이여야 합니다")
    by_company_year: dict[tuple[str, int], list[ChunkRecord]] = defaultdict(list)
    for chunk in chunks:
        by_company_year[(chunk.corp_code, chunk.fiscal_year)].append(chunk)

    output: list[ChunkRecord] = []
    for chunk in chunks:
        previous = by_company_year.get((chunk.corp_code, chunk.fiscal_year - 1), [])
        is_near_dup = False
        for candidate in previous:
            if candidate.chapter != chunk.chapter:
                continue
            length_ratio = min(len(candidate.content), len(chunk.content)) / max(
                len(candidate.content), len(chunk.content), 1
            )
            if length_ratio < threshold:
                continue
            if candidate.content_sha256 == chunk.content_sha256 or SequenceMatcher(
                None, candidate.content, chunk.content, autojunk=False
            ).ratio() >= threshold:
                is_near_dup = True
                break
        output.append(replace(chunk, near_dup=is_near_dup))
    return output
