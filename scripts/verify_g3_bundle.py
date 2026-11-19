"""배포된 100사 G3 청크를 압축 해제 파일 없이 독립 재집계한다.

실행: python -X utf8 scripts/verify_g3_bundle.py
표준 라이브러리만 사용하며 API 키·DB·원본 ZIP은 필요하지 않다.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "manifest.jsonl"
SUMMARY = ROOT / "data" / "ingest_summary.100-b-v9.json"
HASHES = ROOT / "data" / "unique_chunk_hashes.100-b-v9.txt"
BUNDLE_DIR = ROOT / "data" / "g3"
CHUNKS = BUNDLE_DIR / "chunks.100-b-v9.jsonl.gz"
CHECKSUMS = BUNDLE_DIR / "checksums.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def content_sha256(value: str) -> str:
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def verify() -> dict[str, int | str]:
    expected = json.loads(CHECKSUMS.read_text(encoding="utf-8"))
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    manifest_bytes = MANIFEST.read_bytes().replace(b"\r\n", b"\n")
    require(hashlib.sha256(manifest_bytes).hexdigest() == expected["manifest_sha256"], "manifest SHA-256 불일치")
    require(file_sha256(HASHES) == expected["unique_hashes_sha256"], "고유 해시 목록 SHA-256 불일치")
    require(file_sha256(CHUNKS) == expected["compressed_sha256"], "압축 청크 SHA-256 불일치")

    manifest = [json.loads(line) for line in MANIFEST.open(encoding="utf-8") if line.strip()]
    documents = {str(row["rcept_no"]): row for row in manifest}
    require(len(documents) == len(manifest) == summary["documents_success"], "manifest 문서 수 불일치")
    corpus_id = str(summary["corpus_id"])
    require(corpus_id == expected["corpus_id"], "검증 기록 corpus_id 불일치")
    require(all(row["corpus_id"] == corpus_id for row in manifest), "manifest corpus_id 불일치")

    uncompressed = hashlib.sha256()
    counts: Counter[str] = Counter()
    unique_hashes: set[str] = set()
    chunk_ids: set[str] = set()
    total = 0
    with gzip.open(CHUNKS, "rb") as stream:
        for line_number, line in enumerate(stream, start=1):
            uncompressed.update(line)
            row = json.loads(line)
            rcept_no = str(row.get("rcept_no", ""))
            require(rcept_no in documents, f"{line_number}: manifest에 없는 접수번호")
            require(row.get("corpus_id") == corpus_id, f"{line_number}: corpus_id 불일치")
            chunk_id = str(row.get("chunk_id", ""))
            require(chunk_id and chunk_id not in chunk_ids, f"{line_number}: 중복/빈 chunk_id")
            chunk_ids.add(chunk_id)
            content = row.get("content")
            require(isinstance(content, str) and content_sha256(content) == row.get("content_sha256"),
                    f"{line_number}: 본문 SHA-256 불일치")
            near_dup, superseded = row.get("near_dup"), row.get("superseded")
            require(type(near_dup) is bool and type(superseded) is bool, f"{line_number}: 제외 플래그 형식 오류")
            if not near_dup and not superseded:
                unique_hashes.add(row["content_sha256"])
            counts[rcept_no] += 1
            total += 1

    require(uncompressed.hexdigest() == expected["jsonl_sha256"], "압축 해제 청크 SHA-256 불일치")
    for rcept_no, row in documents.items():
        require(counts[rcept_no] == row["chunks"], f"{rcept_no}: manifest 청크 수 불일치")
    require(total == summary["chunks_total"], "전체 청크 수 불일치")
    require(len(unique_hashes) == summary["unique_chunks"], "고유 청크 수 불일치")
    require((len(documents), total, len(unique_hashes)) ==
            (expected["documents"], expected["chunks"], expected["unique_chunks"]), "검증 기록 수치 불일치")
    listed_hashes = {line.strip() for line in HASHES.open(encoding="utf-8") if line.strip()}
    require(unique_hashes == listed_hashes, "고유 청크 해시 집합 불일치")
    return {"corpus_id": corpus_id, "documents": len(documents), "chunks": total,
            "unique_chunks": len(unique_hashes), "compressed_sha256": expected["compressed_sha256"]}


if __name__ == "__main__":
    try:
        print(json.dumps(verify(), ensure_ascii=False, sort_keys=True))
    except (OSError, ValueError, EOFError, KeyError, TypeError) as exc:
        raise SystemExit(f"G3 검증 실패: {exc}") from exc
