"""OpenDART 수집·파싱·청킹·manifest CLI (G3, D1).

서비스 런타임에서는 이 모듈의 네트워크 수집 명령을 호출하지 않는다. 키 소유자가
로컬에서 ``list``와 ``collect``를 실행하고, 키가 없는 환경은 저장된 스냅샷에
``build``만 실행한다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.rag.chunking import (
    FIXED_MODE,
    FIXED_ROWS_MODE,
    MERGE_MODES,
    TABLE_MODES,
    build_chunks,
    chunks_preserve_blocks,
    fixed_chunks_preserve_sources,
    mark_prior_year_near_duplicates,
    merged_chunks_preserve_sources,
    remove_common_boilerplate,
)
from backend.rag.dart_client import DartClient, DartClientError
from backend.rag.dart_parser import (
    DartParseError,
    document_separator_counts,
    parse_document,
)
from backend.rag.models import (
    ChunkRecord,
    DocumentBuildResult,
    DocumentMetadata,
    ParsedBlock,
    parse_bool,
)
from backend.rag.target_selection import (
    TargetSelectionError,
    load_jsonl,
    select_stratified_targets,
    write_targets_csv,
)
from backend.rag.validation import (
    OutputValidationError,
    export_unique_chunk_hashes,
    validate_outputs,
)
from backend.rag.verification import build_verification_report


def _write_jsonl_atomic(path: str | Path, records: Iterable[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            stream.write("\n")
    temporary.replace(output)


def _write_json_atomic(path: str | Path, value: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)


def _load_metadata(directory: Path) -> DocumentMetadata:
    path = directory / "metadata.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return DocumentMetadata.from_dict(value)
    except FileNotFoundError as exc:
        raise ValueError(f"metadata.json 없음: {directory.name}") from exc
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"metadata.json 형식 오류: {directory.name}") from exc


def _source_file(directory: Path) -> Path:
    candidates = [directory / "document.zip", directory / "document.xml"]
    candidates.extend(sorted(directory.glob("*.xml")))
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    raise ValueError(f"document.zip 또는 XML 없음: {directory.name}")


def _corpus_id(
    metadata: Iterable[DocumentMetadata],
    *,
    max_chars: int,
    overlap_chars: int,
    embedding_model: str,
    table_mode: str = "group",
) -> str:
    payload = {
        "documents": [
            {
                "rcept_no": value.rcept_no,
                "raw_sha256": value.raw_sha256,
                "collected_at": value.collected_at,
                "superseded": value.superseded,
            }
            for value in sorted(metadata, key=lambda item: item.rcept_no)
        ],
        "settings": {
            # v8: 꺾쇠 한글(<당기말>) 이스케이프와 주석 범위 끝 정규식 수정(#52)
            "parser": "dart_xml_v10" if table_mode == FIXED_ROWS_MODE else "dart_xml_v9",
            "max_chars": max_chars,
            "overlap_chars": overlap_chars,
            "embedding_model": embedding_model,
            "boilerplate_min_companies": 3,
            "boilerplate_min_chars": 20,
            "near_duplicate_threshold": 0.9,
            "table_mode": table_mode,
        },
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"corpus-{hashlib.sha256(canonical).hexdigest()[:24]}"


def _preserves_blocks(
    metadata: DocumentMetadata,
    blocks: list[ParsedBlock],
    chunks: list[ChunkRecord],
    *,
    max_chars: int,
    overlap_chars: int,
    table_mode: str,
) -> bool:
    """합치는 모드는 표·문단이 섞이므로 합치기 전 청크를 거쳐 두 단계로 검사한다."""

    if table_mode == FIXED_MODE:
        sources = build_chunks(
            metadata, blocks, max_chars=max_chars, overlap_chars=overlap_chars, table_mode="group"
        )
        return chunks_preserve_blocks(
            blocks, sources, max_chars=max_chars, overlap_chars=overlap_chars, table_mode="group"
        ) and fixed_chunks_preserve_sources(
            sources, chunks, max_chars=max_chars, overlap_chars=overlap_chars
        )
    if table_mode == FIXED_ROWS_MODE:
        sources = build_chunks(
            metadata, blocks, max_chars=max_chars, overlap_chars=overlap_chars, table_mode="row"
        )
        return chunks_preserve_blocks(
            blocks, sources, max_chars=max_chars, overlap_chars=overlap_chars, table_mode="row"
        ) and fixed_chunks_preserve_sources(
            sources, chunks, max_chars=max_chars, overlap_chars=overlap_chars, keep_lines=True
        )
    if table_mode not in MERGE_MODES:
        return chunks_preserve_blocks(
            blocks, chunks, max_chars=max_chars, overlap_chars=overlap_chars, table_mode=table_mode
        )
    sources = build_chunks(
        metadata, blocks, max_chars=max_chars, overlap_chars=overlap_chars, table_mode="group"
    )
    return chunks_preserve_blocks(
        blocks, sources, max_chars=max_chars, overlap_chars=overlap_chars, table_mode="group"
    ) and merged_chunks_preserve_sources(sources, chunks)


def build_corpus(
    corpus_dir: str | Path = "data/corpus",
    *,
    manifest_path: str | Path = "data/manifest.jsonl",
    chunks_path: str | Path = "data/chunks.jsonl",
    summary_path: str | Path = "data/ingest_summary.json",
    max_chars: int = 1000,
    overlap_chars: int = 100,
    embedding_model: str = "unselected",
    table_mode: str = "group",
) -> dict[str, Any]:
    """로컬 스냅샷을 파싱해 manifest와 청크를 결정적으로 만든다."""

    root = Path(corpus_dir)
    if not root.exists():
        raise FileNotFoundError(f"코퍼스 디렉터리가 없습니다: {root}")

    parsed: list[tuple[DocumentMetadata, list[ParsedBlock]]] = []
    separators_by_rcept: dict[str, dict[str, int]] = {}
    failed: list[DocumentBuildResult] = []
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        metadata: DocumentMetadata | None = None
        try:
            metadata = _load_metadata(directory)
            source = _source_file(directory)
            blocks = parse_document(source, split_headings=table_mode == FIXED_ROWS_MODE)
            separators_by_rcept[metadata.rcept_no] = document_separator_counts(source)
            parsed.append((metadata, blocks))
        except (ValueError, DartParseError, OSError) as exc:
            if metadata is None:
                metadata = DocumentMetadata(
                    rcept_no=directory.name,
                    corp_code="",
                    corp_name="",
                    fiscal_year=0,
                )
            failed.append(
                DocumentBuildResult(
                    metadata=metadata,
                    status="failed",
                    error=str(exc)[:500],
                )
            )

    corpus_id = _corpus_id(
        [metadata for metadata, _ in parsed]
        + [result.metadata for result in failed],
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        embedding_model=embedding_model,
        table_mode=table_mode,
    )
    for result in failed:
        result.corpus_id = corpus_id

    cleaned = remove_common_boilerplate(parsed)
    original_by_rcept = {
        metadata.rcept_no: blocks for metadata, blocks in parsed
    }
    successful: list[DocumentBuildResult] = []
    for metadata, blocks in cleaned:
        original_blocks = original_by_rcept[metadata.rcept_no]
        chunks = build_chunks(
            metadata,
            blocks,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
            corpus_id=corpus_id,
            table_mode=table_mode,
        )
        if not chunks:
            failed.append(
                DocumentBuildResult(
                    metadata=metadata,
                    status="failed",
                    corpus_id=corpus_id,
                    chars_original=sum(len(block.text) for block in original_blocks),
                    separator_counts=separators_by_rcept.get(metadata.rcept_no, {}),
                    error="정제·청킹 후 남은 본문이 없습니다",
                )
            )
            continue
        successful.append(
            DocumentBuildResult(
                metadata=metadata,
                status="success",
                corpus_id=corpus_id,
                chunks=chunks,
                chars_original=sum(len(block.text) for block in original_blocks),
                chars_kept=sum(len(chunk.content) for chunk in chunks),
                separator_counts=separators_by_rcept.get(metadata.rcept_no, {}),
                truncated=not _preserves_blocks(
                    metadata,
                    blocks,
                    chunks,
                    max_chars=max_chars,
                    overlap_chars=overlap_chars,
                    table_mode=table_mode,
                ),
            )
        )

    all_chunks = mark_prior_year_near_duplicates(
        [chunk for result in successful for chunk in result.chunks]
    )
    chunks_by_document: dict[str, list] = {}
    for chunk in all_chunks:
        chunks_by_document.setdefault(chunk.rcept_no, []).append(chunk)
    for result in successful:
        result.chunks = chunks_by_document.get(result.metadata.rcept_no, [])
        result.chars_kept = sum(len(chunk.content) for chunk in result.chunks)

    results = sorted(
        [*successful, *failed], key=lambda result: result.metadata.rcept_no
    )
    ordered_chunks = sorted(
        all_chunks, key=lambda chunk: (chunk.rcept_no, chunk.ordinal)
    )
    _write_jsonl_atomic(
        manifest_path, (result.manifest_dict() for result in results)
    )
    _write_jsonl_atomic(chunks_path, (chunk.to_dict() for chunk in ordered_chunks))

    countable_chunks = [
        chunk
        for chunk in ordered_chunks
        if not chunk.near_dup and not chunk.superseded
    ]
    summary = {
        "corpus_id": corpus_id,
        "documents_total": len(results),
        "documents_success": len(successful),
        "documents_failed": len(failed),
        "documents_superseded": sum(
            result.metadata.superseded for result in results
        ),
        "chunks_total": len(ordered_chunks),
        "chunks_near_dup": sum(chunk.near_dup for chunk in ordered_chunks),
        "chunks_superseded": sum(chunk.superseded for chunk in ordered_chunks),
        "unique_chunks": len(
            {chunk.content_sha256 for chunk in countable_chunks}
        ),
        "manifest_path": str(manifest_path),
        "chunks_path": str(chunks_path),
        "max_chars": max_chars,
        "overlap_chars": overlap_chars,
        "embedding_model": embedding_model,
        "table_mode": table_mode,
    }
    _write_json_atomic(summary_path, summary)
    return summary


def ingest(corpus_dir: str = "data/corpus") -> None:
    """기존 scaffold의 공개 진입점을 유지한다."""

    build_corpus(corpus_dir)


def _load_targets(path: str | Path) -> list[dict[str, Any]]:
    target_path = Path(path)
    if target_path.suffix.lower() == ".jsonl":
        targets = [
            json.loads(line)
            for line in target_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        with target_path.open("r", encoding="utf-8-sig", newline="") as stream:
            targets = list(csv.DictReader(stream))
    required = {"rcept_no", "corp_code", "corp_name", "fiscal_year"}
    for index, target in enumerate(targets, start=1):
        missing = required - set(target)
        if missing:
            raise ValueError(f"대상 {index}에 필수 필드가 없습니다: {sorted(missing)}")
        if not re.fullmatch(r"\d{14}", str(target["rcept_no"])):
            raise ValueError(f"대상 {index}의 rcept_no는 14자리 숫자여야 합니다")
        if not re.fullmatch(r"\d{8}", str(target["corp_code"])):
            raise ValueError(f"대상 {index}의 corp_code는 8자리 숫자여야 합니다")
        if not str(target["corp_name"]).strip():
            raise ValueError(f"대상 {index}의 corp_name이 비어 있습니다")
        try:
            fiscal_year = int(target["fiscal_year"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"대상 {index}의 fiscal_year가 정수가 아닙니다") from exc
        if not 2020 <= fiscal_year <= 2030:
            raise ValueError(f"대상 {index}의 fiscal_year는 2020~2030이어야 합니다")
        try:
            target["superseded"] = parse_bool(target.get("superseded", False))
        except ValueError as exc:
            raise ValueError(f"대상 {index}의 superseded 형식 오류") from exc
    return targets


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenDART D1 ingest pipeline")
    commands = parser.add_subparsers(dest="command", required=True)

    list_command = commands.add_parser("list", help="사업보고서 후보 목록 조회")
    list_command.add_argument("--begin-date", required=True, help="YYYYMMDD")
    list_command.add_argument("--end-date", required=True, help="YYYYMMDD")
    list_command.add_argument("--fiscal-year", type=int, default=2025)
    list_command.add_argument("--corp-cls", default="Y")
    list_command.add_argument("--max-records", type=int)
    list_command.add_argument("--output", default="data/candidates.jsonl")

    profiles_command = commands.add_parser(
        "profiles", help="후보 회사의 기업개황·업종코드 캐시"
    )
    profiles_command.add_argument("--candidates", default="data/candidates.jsonl")
    profiles_command.add_argument("--output", default="data/company_profiles.jsonl")
    profiles_command.add_argument(
        "--request-interval-seconds",
        type=float,
        default=0.5,
        help="기업개황 요청 사이 대기 시간(초, 기본 0.5)",
    )

    select_command = commands.add_parser(
        "select", help="기업개황 업종 분포로 FY2025 100사 후보안 생성"
    )
    select_command.add_argument("--candidates", default="data/candidates.jsonl")
    select_command.add_argument("--profiles", default="data/company_profiles.jsonl")
    select_command.add_argument("--output", default="data/targets.proposed.100.csv")
    select_command.add_argument(
        "--summary", default="data/targets.proposed.100.summary.json"
    )
    select_command.add_argument("--count", type=int, default=100)
    select_command.add_argument("--seed", type=int, default=20261030)

    collect_command = commands.add_parser("collect", help="승인된 대상 원본 수집")
    collect_command.add_argument("--targets", required=True, help="CSV 또는 JSONL")
    collect_command.add_argument("--corpus-dir", default="data/corpus")
    collect_command.add_argument(
        "--request-interval-seconds",
        type=float,
        default=0.5,
        help="문서 요청 사이 대기 시간(초, 기본 0.5)",
    )

    build_command = commands.add_parser("build", help="로컬 스냅샷 파싱·청킹")
    build_command.add_argument("--corpus-dir", default="data/corpus")
    build_command.add_argument("--manifest", default="data/manifest.jsonl")
    build_command.add_argument("--chunks", default="data/chunks.jsonl")
    build_command.add_argument("--summary", default="data/ingest_summary.json")
    build_command.add_argument("--max-chars", type=int, default=1000)
    build_command.add_argument("--overlap-chars", type=int, default=100)
    build_command.add_argument("--embedding-model", default="unselected")
    build_command.add_argument(
        "--table-mode",
        choices=TABLE_MODES,
        default="group",
    )

    validate_command = commands.add_parser("validate", help="manifest/chunks 계약 검증")
    validate_command.add_argument("--manifest", default="data/manifest.jsonl")
    validate_command.add_argument("--chunks", default="data/chunks.jsonl")
    validate_command.add_argument("--summary", default="data/ingest_summary.json")

    hashes_command = commands.add_parser(
        "hashes", help="eligible 고유 청크 SHA-256 목록 생성"
    )
    hashes_command.add_argument("--chunks", default="data/chunks.jsonl")
    hashes_command.add_argument(
        "--output", default="data/unique_chunk_hashes.txt"
    )

    verify_command = commands.add_parser("verify", help="원문 대조 3건 리포트 생성")
    verify_command.add_argument("--corpus-dir", default="data/corpus")
    verify_command.add_argument("--chunks", default="data/chunks.jsonl")
    verify_command.add_argument("--output", default="docs/evidence/d1_source_verification.md")
    verify_command.add_argument("--rcept-no", action="append", dest="rcept_nos")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "list":
            client = DartClient()
            reports = client.list_annual_reports(
                args.begin_date,
                args.end_date,
                corp_cls=args.corp_cls,
                max_records=args.max_records,
            )
            records = []
            for report in reports:
                record = dict(report)
                record["fiscal_year"] = args.fiscal_year
                record["filed_at"] = record.get("rcept_dt", "")
                record["report_type"] = "annual"
                record["superseded"] = False
                records.append(record)
            _write_jsonl_atomic(args.output, records)
            print(
                json.dumps(
                    {"candidates": len(records), "output": args.output},
                    ensure_ascii=False,
                )
            )
            return 0

        if args.command == "profiles":
            if not 0 <= args.request_interval_seconds <= 60:
                raise ValueError("request interval은 0~60초여야 합니다")
            candidates = load_jsonl(args.candidates)
            corp_codes = sorted({str(row.get("corp_code", "")) for row in candidates})
            existing_rows = load_jsonl(args.output) if Path(args.output).exists() else []
            profiles = {str(row.get("corp_code", "")): row for row in existing_rows}
            remaining = [code for code in corp_codes if code not in profiles]
            if not remaining:
                print(
                    json.dumps(
                        {"profiles": len(profiles), "fetched": 0, "failed": 0},
                        ensure_ascii=False,
                    )
                )
                return 0
            client = DartClient()
            failures: list[dict[str, str]] = []
            fetched = 0
            for index, corp_code in enumerate(remaining):
                try:
                    profiles[corp_code] = client.company_profile(corp_code)
                    fetched += 1
                    _write_jsonl_atomic(
                        args.output,
                        (profiles[code] for code in sorted(profiles)),
                    )
                except (DartClientError, OSError, ValueError) as exc:
                    failures.append({"corp_code": corp_code, "error": str(exc)})
                if index + 1 < len(remaining) and args.request_interval_seconds:
                    time.sleep(args.request_interval_seconds)
            print(
                json.dumps(
                    {
                        "profiles": len(profiles),
                        "fetched": fetched,
                        "failed": len(failures),
                        "failures": failures,
                    },
                    ensure_ascii=False,
                )
            )
            return 1 if failures else 0

        if args.command == "select":
            rows, summary = select_stratified_targets(
                load_jsonl(args.candidates),
                load_jsonl(args.profiles),
                count=args.count,
                seed=args.seed,
            )
            write_targets_csv(args.output, rows)
            _write_json_atomic(args.summary, {**summary, "output": args.output})
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "collect":
            if not 0 <= args.request_interval_seconds <= 60:
                raise ValueError("request interval은 0~60초여야 합니다")
            client = DartClient()
            failures: list[dict[str, str]] = []
            collected = 0
            targets = _load_targets(args.targets)
            for index, target in enumerate(targets):
                try:
                    client.collect(target, args.corpus_dir)
                    collected += 1
                except (DartClientError, OSError, ValueError) as exc:
                    failures.append(
                        {"rcept_no": str(target.get("rcept_no", "")), "error": str(exc)}
                    )
                if index + 1 < len(targets) and args.request_interval_seconds:
                    time.sleep(args.request_interval_seconds)
            print(
                json.dumps(
                    {"collected": collected, "failed": len(failures), "failures": failures},
                    ensure_ascii=False,
                )
            )
            return 1 if failures else 0

        if args.command == "validate":
            result = validate_outputs(
                args.manifest,
                args.chunks,
                summary_path=args.summary,
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0

        if args.command == "hashes":
            result = export_unique_chunk_hashes(args.chunks, args.output)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "verify":
            result = build_verification_report(
                args.corpus_dir,
                args.chunks,
                args.output,
                sample_ids=args.rcept_nos,
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0

        summary = build_corpus(
            args.corpus_dir,
            manifest_path=args.manifest,
            chunks_path=args.chunks,
            summary_path=args.summary,
            max_chars=args.max_chars,
            overlap_chars=args.overlap_chars,
            embedding_model=args.embedding_model,
            table_mode=args.table_mode,
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 1 if summary["documents_failed"] else 0
    except (
        DartClientError,
        FileNotFoundError,
        OutputValidationError,
        TargetSelectionError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
