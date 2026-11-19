"""실제 원문과 파싱·청킹 결과를 사람이 대조할 증거 문서를 만든다."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from backend.rag.dart_parser import extract_plain_text, normalize_text, parse_document


def _load_chunks(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        output[str(row["rcept_no"])].append(row)
    return output


def _source_file(directory: Path) -> Path:
    for candidate in [directory / "document.zip", directory / "document.xml"]:
        if candidate.exists():
            return candidate
    xml_files = sorted(directory.glob("*.xml"))
    if xml_files:
        return xml_files[0]
    raise FileNotFoundError(f"원본 파일 없음: {directory.name}")


def _block_matches_chunk(block, chunks: list[dict[str, Any]]) -> bool:
    comparable = normalize_text(block.text.replace(" | ", " "))
    return any(
        normalize_text(block.text) in normalize_text(str(chunk["content"]))
        or comparable in normalize_text(str(chunk["content"]).replace(" | ", " "))
        for chunk in chunks
    )


def _is_meaningful_evidence(block, preference: str) -> bool:
    text = normalize_text(block.text)
    compact = text.replace(" ", "")
    if len(text) < 40 or compact in {"해당사항없음", "해당없음", "없음"}:
        return False
    if preference == "table":
        return text.count(" | ") >= 1
    return True


def _choose_block(blocks, chunks: list[dict[str, Any]], preference: str):
    if preference == "table":
        candidates = [block for block in blocks if block.is_table]
    elif preference == "note":
        candidates = [block for block in blocks if block.chapter == "NOTES" and not block.is_table]
    else:
        candidates = [block for block in blocks if not block.is_table]
    meaningful = [
        block for block in candidates if _is_meaningful_evidence(block, preference)
    ]
    for block in meaningful:
        if _block_matches_chunk(block, chunks):
            return block
    for block in candidates:
        if _block_matches_chunk(block, chunks):
            return block
    for block in blocks:
        if _block_matches_chunk(block, chunks):
            return block
    return (candidates or list(blocks))[0]


def _markdown_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def build_verification_report(
    corpus_dir: str | Path,
    chunks_path: str | Path,
    output_path: str | Path,
    *,
    sample_ids: list[str] | None = None,
    max_documents: int = 3,
) -> dict[str, Any]:
    """최대 3개 문서의 원문 포함 여부와 청크 연결을 Markdown으로 기록한다."""

    corpus = Path(corpus_dir)
    chunks_by_document = _load_chunks(chunks_path)
    available = sorted(
        directory.name
        for directory in corpus.iterdir()
        if directory.is_dir() and directory.name in chunks_by_document
    )
    selected = (sample_ids or available)[:max_documents]
    if len(selected) < max_documents:
        raise ValueError(
            f"원문 대조에는 {max_documents}개 문서가 필요하지만 {len(selected)}개만 있습니다"
        )

    preferences = ["paragraph", "note", "table"]
    evidence: list[dict[str, Any]] = []
    for index, rcept_no in enumerate(selected):
        directory = corpus / rcept_no
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        source = _source_file(directory)
        blocks = parse_document(source)
        block = _choose_block(
            blocks,
            chunks_by_document[rcept_no],
            preferences[index % len(preferences)],
        )
        raw_text = extract_plain_text(source)
        comparable = normalize_text(block.text.replace(" | ", " "))
        raw_match = comparable in raw_text

        matching_chunk = next(
            (
                chunk
                for chunk in chunks_by_document[rcept_no]
                if normalize_text(block.text) in normalize_text(str(chunk["content"]))
                or comparable in normalize_text(str(chunk["content"]).replace(" | ", " "))
            ),
            None,
        )
        evidence.append(
            {
                "rcept_no": rcept_no,
                "corp_name": str(metadata.get("corp_name", "")),
                "kind": "table" if block.is_table else ("note" if block.chapter == "NOTES" else "paragraph"),
                "chapter": block.chapter,
                "section_path": " > ".join(block.section_path),
                "note_no": block.note_no or "",
                "chunk_id": matching_chunk.get("chunk_id", "") if matching_chunk else "",
                "raw_match": raw_match,
                "chunk_match": matching_chunk is not None,
                "excerpt": block.text[:180],
            }
        )

    lines = [
        "# D1 원문 대조 기록",
        "",
        "자동 대조는 파싱 문장이 원문 텍스트와 생성 청크에 존재하는지 확인한다. 최종 제출 전 담당자가 DART 원문 화면과 직접 대조해 `사람 확인`을 체크한다.",
        "",
        "| rcept_no | 회사 | 유형 | 위치 | chunk_id | 원문 자동 대조 | 청크 대조 | 사람 확인 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in evidence:
        location = item["section_path"]
        if item["note_no"]:
            location += f" / 주석 {item['note_no']}"
        lines.append(
            "| {rcept_no} | {corp_name} | {kind} | {location} | {chunk_id} | {raw} | {chunk} | [ ] |".format(
                rcept_no=item["rcept_no"],
                corp_name=_markdown_escape(item["corp_name"]),
                kind=item["kind"],
                location=_markdown_escape(location),
                chunk_id=item["chunk_id"],
                raw="PASS" if item["raw_match"] else "FAIL",
                chunk="PASS" if item["chunk_match"] else "FAIL",
            )
        )
        lines.extend(["", f"> {_markdown_escape(item['excerpt'])}", ""])

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    temporary.replace(output)
    return {
        "documents": len(evidence),
        "raw_matches": sum(item["raw_match"] for item in evidence),
        "chunk_matches": sum(item["chunk_match"] for item in evidence),
        "output": str(output),
    }
