"""pilot 50의 A/B/C 표 청킹 후보를 한 번의 원문 파싱으로 비교한다."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from backend.rag.chunking import (
    build_chunks,
    mark_prior_year_near_duplicates,
    remove_common_boilerplate,
)
from backend.rag.dart_parser import parse_document
from backend.rag.ingest import (
    _corpus_id,
    _load_metadata,
    _source_file,
    _write_json_atomic,
    _write_jsonl_atomic,
)
from backend.rag.models import ChunkRecord, DocumentMetadata, ParsedBlock
from backend.rag.verification import build_verification_report


MODES = {
    "A": "row",
    "B": "group",
    "C": "group_drop_numeric",
}
BASELINE_DB_MIB = 783.0
COMPANY_SCALE = 5


def _load_chunks(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _build_variant(
    label: str,
    mode: str,
    documents: list[tuple[DocumentMetadata, list[ParsedBlock]]],
    *,
    output_dir: Path,
    max_chars: int,
    overlap_chars: int,
    embedding_model: str,
) -> tuple[Path, dict[str, Any]]:
    corpus_id = _corpus_id(
        [metadata for metadata, _ in documents],
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        embedding_model=embedding_model,
        table_mode=mode,
    )
    chunks: list[ChunkRecord] = []
    for index, (metadata, blocks) in enumerate(documents, start=1):
        print(
            f"[{label}] {index:02d}/{len(documents)} {metadata.corp_name}",
            flush=True,
        )
        chunks.extend(
            build_chunks(
                metadata,
                blocks,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
                corpus_id=corpus_id,
                table_mode=mode,
            )
        )
    chunks = mark_prior_year_near_duplicates(chunks)
    chunks.sort(key=lambda chunk: (chunk.rcept_no, chunk.ordinal))
    path = output_dir / f"chunks.{label}.jsonl"
    _write_jsonl_atomic(path, (chunk.to_dict() for chunk in chunks))
    summary = {
        "candidate": label,
        "table_mode": mode,
        "corpus_id": corpus_id,
        "documents_success": len(documents),
        "chunks_total": len(chunks),
        "unique_chunks": len(
            {
                chunk.content_sha256
                for chunk in chunks
                if not chunk.near_dup and not chunk.superseded
            }
        ),
        "chunks_path": str(path),
    }
    _write_json_atomic(output_dir / f"summary.{label}.json", summary)
    return path, summary


def _metrics(
    label: str,
    chunks_path: Path,
    verification: dict[str, Any],
    *,
    fixed_bytes_per_row: float,
) -> dict[str, Any]:
    rows = _load_chunks(chunks_path)
    eligible = [
        row
        for row in rows
        if not row.get("near_dup", False) and not row.get("superseded", False)
    ]
    unique = {str(row["content_sha256"]) for row in eligible}
    content_bytes = sum(len(str(row["content"]).encode("utf-8")) for row in eligible)
    estimated_50_bytes = fixed_bytes_per_row * len(eligible) + content_bytes
    estimated_250_bytes = estimated_50_bytes * COMPANY_SCALE
    return {
        "candidate": label,
        "eligible_unique_chunks": len(unique),
        "g3_pass": len(unique) >= 10_000,
        "eligible_chunks": len(eligible),
        "median_chunk_chars": statistics.median(
            len(str(row["content"])) for row in eligible
        ),
        "estimated_250_chunks": len(eligible) * COMPANY_SCALE,
        "estimated_250_db_mib": round(estimated_250_bytes / (1024**2), 1),
        "source_verification": (
            f"{verification['raw_matches']}/{verification['documents']} raw, "
            f"{verification['chunk_matches']}/{verification['documents']} chunk"
        ),
        "source_verification_pass": (
            verification["raw_matches"] == verification["documents"] == 3
            and verification["chunk_matches"] == verification["documents"]
        ),
        "content_utf8_bytes": content_bytes,
    }


def _write_report(path: Path, metrics: list[dict[str, Any]]) -> None:
    lines = [
        "# Pilot 50 A/B/C 표 청킹 비교",
        "",
        "- A: 현재 방식(표 행 1개 = 청크 1개)",
        "- B: 표 제목·헤더·여러 행을 1,000자 이내로 묶음",
        "- C: B에서 `is_numeric_heavy`인 데이터 행을 색인 제외",
        "- 250사 청크 수는 동일 분포를 가정해 pilot 50의 5배로 추정",
        "- DB 용량은 A의 실측 783 MiB에서 본문 UTF-8 바이트를 제외한 행당 고정 비용을 보정한 뒤, 후보별 행 수·본문 바이트를 적용",
        "",
        "| 후보 | eligible 고유 청크 | G3 1만 | 길이 중앙값 | 250사 예상 청크 | 250사 예상 DB | 원문/청크 대조 3건 |",
        "|---|---:|:---:|---:|---:|---:|:---:|",
    ]
    for item in metrics:
        lines.append(
            "| {candidate} | {eligible_unique_chunks:,} | {g3} | {median_chunk_chars:g}자 | "
            "{estimated_250_chunks:,} | {estimated_250_db_mib:,.1f} MiB | {verify} |".format(
                candidate=item["candidate"],
                eligible_unique_chunks=item["eligible_unique_chunks"],
                g3="PASS" if item["g3_pass"] else "FAIL",
                median_chunk_chars=item["median_chunk_chars"],
                estimated_250_chunks=item["estimated_250_chunks"],
                estimated_250_db_mib=item["estimated_250_db_mib"],
                verify="PASS" if item["source_verification_pass"] else "FAIL",
            )
        )
    lines.extend(
        [
            "",
            "## 해석 주의",
            "",
            "- 용량은 실제 Supabase 적재값이 아니라 현재 로컬 pgvector 실측값을 보정한 추정치다.",
            "- 회사별 표 규모가 pilot과 다르면 250사 실제 값도 달라진다.",
            "- C는 원문을 삭제하지 않고 검색 색인 후보에서 숫자 위주 데이터 행을 제외하는 실험이다.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-dir", default="data/corpus")
    parser.add_argument(
        "--output-dir", default="data/experiments/table_chunking"
    )
    parser.add_argument(
        "--report", default="docs/evidence/table_chunking_abc_pilot50.md"
    )
    args = parser.parse_args()

    corpus_dir = Path(args.corpus_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    parsed: list[tuple[DocumentMetadata, list[ParsedBlock]]] = []
    directories = sorted(path for path in corpus_dir.iterdir() if path.is_dir())
    for index, directory in enumerate(directories, start=1):
        metadata = _load_metadata(directory)
        print(
            f"[parse] {index:02d}/{len(directories)} {metadata.corp_name}",
            flush=True,
        )
        parsed.append((metadata, parse_document(_source_file(directory))))
    cleaned = remove_common_boilerplate(parsed)

    a_path = output_dir / "chunks.A.jsonl"
    if not a_path.exists():
        _build_variant(
            "A",
            MODES["A"],
            cleaned,
            output_dir=output_dir,
            max_chars=1000,
            overlap_chars=100,
            embedding_model="intfloat/multilingual-e5-small",
        )
    for label in ("B", "C"):
        _build_variant(
            label,
            MODES[label],
            cleaned,
            output_dir=output_dir,
            max_chars=1000,
            overlap_chars=100,
            embedding_model="intfloat/multilingual-e5-small",
        )

    a_rows = _load_chunks(a_path)
    a_content_bytes = sum(
        len(str(row["content"]).encode("utf-8")) for row in a_rows
    )
    fixed_bytes_per_row = (
        BASELINE_DB_MIB * 1024**2 - a_content_bytes
    ) / len(a_rows)

    metrics: list[dict[str, Any]] = []
    for label in MODES:
        chunks_path = output_dir / f"chunks.{label}.jsonl"
        verification = build_verification_report(
            corpus_dir,
            chunks_path,
            output_dir / f"verification.{label}.md",
        )
        metrics.append(
            _metrics(
                label,
                chunks_path,
                verification,
                fixed_bytes_per_row=fixed_bytes_per_row,
            )
        )

    _write_json_atomic(output_dir / "comparison.json", {"candidates": metrics})
    _write_report(Path(args.report), metrics)
    print(json.dumps({"candidates": metrics, "report": args.report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
