"""D1 산출물의 JSON Schema와 교차 참조를 검증한다."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from backend.rag.chunking import content_sha256


class OutputValidationError(ValueError):
    """manifest/chunks/summary가 계약을 위반할 때 발생한다."""


SCHEMA_DIR = Path(__file__).resolve().parents[2] / "data" / "schemas"
_SECRET_PATTERNS = (
    re.compile(r"crtfc_key\s*=", re.IGNORECASE),
    re.compile(r"DART_API_KEY\s*=\s*[^\s\"']+", re.IGNORECASE),
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export_unique_chunk_hashes(
    chunks_path: str | Path,
    output_path: str | Path,
) -> dict[str, int | str]:
    """eligible 청크의 고유 본문 SHA-256을 정렬해 재현 증거로 저장한다."""

    source = Path(chunks_path)
    output = Path(output_path)
    unique_hashes: set[str] = set()
    eligible_chunks = 0
    with source.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise OutputValidationError(
                    f"{source}:{line_number}: JSON 형식 오류"
                ) from exc
            if not isinstance(row, dict):
                raise OutputValidationError(
                    f"{source}:{line_number}: 객체가 아닙니다"
                )
            near_dup = row.get("near_dup")
            superseded = row.get("superseded")
            if not isinstance(near_dup, bool) or not isinstance(superseded, bool):
                raise OutputValidationError(
                    f"{source}:{line_number}: near_dup/superseded 형식 오류"
                )
            if near_dup or superseded:
                continue

            sha256 = row.get("content_sha256")
            content = row.get("content")
            if not isinstance(sha256, str) or not _SHA256_PATTERN.fullmatch(sha256):
                raise OutputValidationError(
                    f"{source}:{line_number}: content_sha256 형식 오류"
                )
            if not isinstance(content, str) or content_sha256(content) != sha256:
                raise OutputValidationError(
                    f"{source}:{line_number}: 본문 SHA-256 불일치"
                )
            eligible_chunks += 1
            unique_hashes.add(sha256)

    if not unique_hashes:
        raise OutputValidationError("eligible 고유 청크가 없습니다")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        "".join(f"{sha256}\n" for sha256 in sorted(unique_hashes)),
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(output)
    return {
        "chunks_sha256": _file_sha256(source),
        "eligible_chunks": eligible_chunks,
        "unique_hashes": len(unique_hashes),
        "duplicate_eligible_chunks": eligible_chunks - len(unique_hashes),
        "output_sha256": _file_sha256(output),
    }


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OutputValidationError(f"{source}:{line_number}: JSON 형식 오류") from exc
        if not isinstance(value, dict):
            raise OutputValidationError(f"{source}:{line_number}: 객체가 아닙니다")
        rows.append(value)
    return rows


def _load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _schema_errors(
    rows: list[dict[str, Any]], schema_name: str, label: str
) -> list[str]:
    validator = Draft202012Validator(_load_schema(schema_name))
    errors: list[str] = []
    for line_number, row in enumerate(rows, start=1):
        for error in sorted(validator.iter_errors(row), key=lambda item: list(item.path)):
            field = ".".join(str(part) for part in error.path) or "<row>"
            errors.append(f"{label}:{line_number}:{field}: {error.message}")
    return errors


def _scan_secrets(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
            errors.append(f"{path}: 키 또는 키 쿼리 패턴이 포함되어 있습니다")
    return errors


def validate_outputs(
    manifest_path: str | Path,
    chunks_path: str | Path,
    *,
    summary_path: str | Path | None = None,
) -> dict[str, int]:
    """행 스키마, 해시, 참조, 집계가 모두 일치하는지 확인한다."""

    manifest_file = Path(manifest_path)
    chunks_file = Path(chunks_path)
    manifest = _read_jsonl(manifest_file)
    chunks = _read_jsonl(chunks_file)
    errors = [
        *_schema_errors(manifest, "manifest.schema.json", "manifest"),
        *_schema_errors(chunks, "chunk.schema.json", "chunks"),
        *_scan_secrets([manifest_file, chunks_file]),
    ]

    manifest_by_rcept = {row.get("rcept_no"): row for row in manifest}
    if len(manifest_by_rcept) != len(manifest):
        errors.append("manifest:rcept_no가 중복되었습니다")

    chunk_ids: set[str] = set()
    ordinals: dict[str, list[int]] = defaultdict(list)
    chunks_per_document: Counter[str] = Counter()
    eligible_hashes_per_document: dict[str, set[str]] = defaultdict(set)
    for line_number, chunk in enumerate(chunks, start=1):
        chunk_id = str(chunk.get("chunk_id", ""))
        rcept_no = str(chunk.get("rcept_no", ""))
        if chunk_id in chunk_ids:
            errors.append(f"chunks:{line_number}:chunk_id 중복: {chunk_id}")
        chunk_ids.add(chunk_id)
        document = manifest_by_rcept.get(rcept_no)
        if document is None:
            errors.append(f"chunks:{line_number}:manifest에 없는 rcept_no: {rcept_no}")
        elif document.get("status") != "success":
            errors.append(f"chunks:{line_number}:실패 문서의 청크가 존재: {rcept_no}")
        else:
            if chunk.get("corpus_id") != document.get("corpus_id"):
                errors.append(f"chunks:{line_number}:manifest와 corpus_id가 다릅니다")
            if chunk.get("superseded") != document.get("superseded"):
                errors.append(f"chunks:{line_number}:manifest와 superseded가 다릅니다")
        expected_hash = content_sha256(str(chunk.get("content", "")))
        if chunk.get("content_sha256") != expected_hash:
            errors.append(f"chunks:{line_number}:본문 SHA-256 불일치")
        ordinal = chunk.get("ordinal")
        if isinstance(ordinal, int):
            ordinals[rcept_no].append(ordinal)
        chunks_per_document[rcept_no] += 1
        if chunk.get("near_dup") is False and chunk.get("superseded") is False:
            eligible_hashes_per_document[rcept_no].add(
                str(chunk.get("content_sha256", ""))
            )

    for rcept_no, values in ordinals.items():
        if sorted(values) != list(range(1, len(values) + 1)):
            errors.append(f"chunks:{rcept_no}:ordinal이 1부터 연속적이지 않습니다")

    for row in manifest:
        rcept_no = str(row.get("rcept_no", ""))
        if row.get("status") == "success":
            if row.get("chunks") != chunks_per_document[rcept_no]:
                errors.append(f"manifest:{rcept_no}:청크 수가 chunks.jsonl과 다릅니다")
            if row.get("unique_chunks") != len(eligible_hashes_per_document[rcept_no]):
                errors.append(f"manifest:{rcept_no}:고유 청크 수가 다릅니다")

    corpus_ids = {str(row.get("corpus_id", "")) for row in manifest}
    if len(corpus_ids) != 1:
        errors.append("manifest:corpus_id가 하나로 통일되지 않았습니다")

    unique_chunks = len(
        {
            str(row.get("content_sha256", ""))
            for row in chunks
            if row.get("near_dup") is False and row.get("superseded") is False
        }
    )
    if summary_path is not None:
        summary_file = Path(summary_path)
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        errors.extend(_scan_secrets([summary_file]))
        expected = {
            "corpus_id": next(iter(corpus_ids), ""),
            "documents_total": len(manifest),
            "documents_success": sum(row.get("status") == "success" for row in manifest),
            "documents_failed": sum(row.get("status") == "failed" for row in manifest),
            "documents_superseded": sum(
                row.get("superseded") is True for row in manifest
            ),
            "chunks_total": len(chunks),
            "chunks_near_dup": sum(row.get("near_dup") is True for row in chunks),
            "chunks_superseded": sum(
                row.get("superseded") is True for row in chunks
            ),
            "unique_chunks": unique_chunks,
        }
        for key, value in expected.items():
            if summary.get(key) != value:
                errors.append(f"summary:{key}: 기대 {value}, 실제 {summary.get(key)}")

    if errors:
        displayed = errors[:20]
        if len(errors) > 20:
            displayed.append(f"... 나머지 {len(errors) - 20}건")
        raise OutputValidationError("\n".join(displayed))

    return {
        "documents": len(manifest),
        "successful_documents": sum(row.get("status") == "success" for row in manifest),
        "chunks": len(chunks),
        "unique_chunks": unique_chunks,
    }
