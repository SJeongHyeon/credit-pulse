"""v1 평가셋 회사만 골라 데모 스냅샷을 만든다. Spec: docs/specs/w1-8-demo-snapshot.md

키 소유자가 로컬에서 실행한다. API 키는 필요 없다. 순서는 data/README.md의 '데모 스냅샷' 참고.
  select: 청크·manifest에서 대상 회사만 골라 작업 폴더에 쓴다(임베딩 입력).
  pack:   작업 폴더와 임베딩 산출물을 묶어 data/snapshot/을 만든다.
"""
import argparse
import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

# `python scripts/build_demo_snapshot.py`로 실행해도 backend를 찾도록 저장소 루트를 경로에 넣는다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.rag.snapshot import (  # noqa: E402
    CHUNKS_FILE,
    EMBEDDING_IDS_FILE,
    EMBEDDING_METADATA_FILE,
    EMBEDDING_VECTORS_FILE,
    MANIFEST_FILE,
    SNAPSHOT_INFO_FILE,
    sha256_bytes,
    write_deterministic_gzip,
)

SELECTION_FILE = "selection.json"
SUBSET_CHUNKS_FILE = "chunks.jsonl"
SUBSET_MANIFEST_FILE = "manifest.jsonl"


class SnapshotBuildError(ValueError):
    """입력 산출물이 서로 맞지 않아 스냅샷을 만들 수 없다."""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    lines = (
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for row in rows
    )
    return "".join(f"{line}\n" for line in lines).encode("utf-8")


def json_bytes(value: dict[str, Any]) -> bytes:
    return f"{json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)}\n".encode("utf-8")


def derive_corpus_id(source_corpus_id: str, corp_codes: list[str]) -> str:
    # 부분집합은 부모와 다른 코퍼스다. 같은 id면 전체 코퍼스 적재와 서로 덮어쓴다.
    payload = json.dumps(
        {"source_corpus_id": source_corpus_id, "corp_codes": sorted(corp_codes)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"corpus-{hashlib.sha256(payload.encode()).hexdigest()[:24]}"


def read_corp_codes(filters_path: Path) -> list[str]:
    codes = {str(row["corp_code"]) for row in read_jsonl(filters_path)}
    if not codes:
        raise SnapshotBuildError(f"필터에 corp_code가 없습니다: {filters_path}")
    return sorted(codes)


def select_subset(
    chunks_path: Path, manifest_path: Path, filters_path: Path, work_dir: Path
) -> dict[str, Any]:
    corp_codes = read_corp_codes(filters_path)
    targets = set(corp_codes)
    manifest = read_jsonl(manifest_path)
    chunks = read_jsonl(chunks_path)

    source_ids = {str(row["corpus_id"]) for row in [*manifest, *chunks]}
    if len(source_ids) != 1:
        raise SnapshotBuildError(f"manifest와 청크의 corpus_id가 다릅니다: {sorted(source_ids)}")
    source_corpus_id = next(iter(source_ids))
    corpus_id = derive_corpus_id(source_corpus_id, corp_codes)

    # chunk_id는 바꾸지 않는다. 평가셋 gold가 이 값으로 청크를 가리킨다.
    selected_manifest = [
        {**row, "corpus_id": corpus_id} for row in manifest if row["corp_code"] in targets
    ]
    selected_chunks = [
        {**row, "corpus_id": corpus_id} for row in chunks if row["corp_code"] in targets
    ]

    missing = targets - {row["corp_code"] for row in selected_manifest}
    if missing:
        raise SnapshotBuildError(f"manifest에 없는 회사: {sorted(missing)}")
    for row in selected_manifest:
        actual = sum(1 for chunk in selected_chunks if chunk["rcept_no"] == row["rcept_no"])
        if actual != row["chunks"]:
            raise SnapshotBuildError(
                f"{row['rcept_no']} 청크 수 불일치: manifest {row['chunks']}, 청크 {actual}"
            )

    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / SUBSET_MANIFEST_FILE).write_bytes(jsonl_bytes(selected_manifest))
    (work_dir / SUBSET_CHUNKS_FILE).write_bytes(jsonl_bytes(selected_chunks))
    selection = {
        "corpus_id": corpus_id,
        "source_corpus_id": source_corpus_id,
        "source_chunks_sha256": sha256_bytes(chunks_path.read_bytes()),
        "corp_codes": corp_codes,
    }
    (work_dir / SELECTION_FILE).write_bytes(json_bytes(selection))
    return selection


def subset_embeddings(
    chunks: list[dict[str, Any]], embedding_prefix: str
) -> tuple[list[dict[str, Any]], np.ndarray, dict[str, Any]]:
    """청크가 쓰는 본문의 벡터만 골라 0번부터 다시 번호를 매긴다."""

    source_metadata = json.loads(Path(f"{embedding_prefix}.metadata.json").read_text(encoding="utf-8"))
    if not source_metadata.get("complete"):
        raise SnapshotBuildError("임베딩 metadata가 완료 상태가 아닙니다")
    source_index = {
        row["content_sha256"]: int(row["index"])
        for row in read_jsonl(Path(f"{embedding_prefix}.ids.jsonl"))
    }
    source_vectors = np.load(f"{embedding_prefix}.vectors.npy", mmap_mode="r")

    # 청크에 처음 나온 순서로 고정해 결과가 재현되게 한다.
    content_hashes = list(dict.fromkeys(row["content_sha256"] for row in chunks))
    missing = [value for value in content_hashes if value not in source_index]
    if missing:
        raise SnapshotBuildError(f"임베딩이 없는 본문 {len(missing)}건: {missing[:3]}")

    ids = [
        {"index": index, "content_sha256": value} for index, value in enumerate(content_hashes)
    ]
    vectors = np.asarray(
        source_vectors[[source_index[value] for value in content_hashes]], dtype=np.float32
    )
    return ids, vectors, source_metadata


def pack_snapshot(work_dir: Path, embedding_prefix: str, output_dir: Path) -> dict[str, Any]:
    selection = json.loads((work_dir / SELECTION_FILE).read_text(encoding="utf-8"))
    manifest_bytes = (work_dir / SUBSET_MANIFEST_FILE).read_bytes()
    chunks_bytes = (work_dir / SUBSET_CHUNKS_FILE).read_bytes()
    chunks = read_jsonl(work_dir / SUBSET_CHUNKS_FILE)
    manifest = read_jsonl(work_dir / SUBSET_MANIFEST_FILE)

    ids, vectors, source_metadata = subset_embeddings(chunks, embedding_prefix)
    ids_bytes = jsonl_bytes(ids)
    vectors_buffer = io.BytesIO()
    np.save(vectors_buffer, vectors, allow_pickle=False)
    vectors_bytes = vectors_buffer.getvalue()

    # 원본 metadata에는 로컬 경로와 실행 시간이 있어 필요한 필드만 새로 만든다.
    metadata_bytes = json_bytes(
        {
            "complete": True,
            "model_name": source_metadata["model_name"],
            "dimension": int(vectors.shape[1]),
            "dtype": "float32",
            "rows": len(ids),
            "chunks_sha256": sha256_bytes(chunks_bytes),
            "ids_sha256": sha256_bytes(ids_bytes),
            "vectors_sha256": sha256_bytes(vectors_bytes),
        }
    )

    plain_files = {
        MANIFEST_FILE: manifest_bytes,
        CHUNKS_FILE: chunks_bytes,
        EMBEDDING_IDS_FILE: ids_bytes,
        EMBEDDING_VECTORS_FILE: vectors_bytes,
        EMBEDDING_METADATA_FILE: metadata_bytes,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict[str, Any]] = {}
    for name, content in plain_files.items():
        path = output_dir / name
        if name.endswith(".gz"):
            write_deterministic_gzip(path, content)
        else:
            path.write_bytes(content)
        stored = path.read_bytes()
        files[name] = {
            "sha256": sha256_bytes(stored),
            "bytes": len(stored),
            "uncompressed_sha256": sha256_bytes(content),
        }

    info = {
        "schema_version": 1,
        **selection,
        "embedding_model": source_metadata["model_name"],
        "dimension": int(vectors.shape[1]),
        "counts": {
            "documents": len(manifest),
            "chunks": len(chunks),
            "unique_contents": len(ids),
        },
        "files": files,
    }
    (output_dir / SNAPSHOT_INFO_FILE).write_bytes(json_bytes(info))
    return info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="데모 스냅샷 생성")
    commands = parser.add_subparsers(dest="command", required=True)

    select = commands.add_parser("select", help="대상 회사만 골라 작업 폴더에 쓴다")
    select.add_argument("--chunks", required=True, type=Path)
    select.add_argument("--manifest", required=True, type=Path)
    select.add_argument("--filters", default=Path("evals/filters_v1.jsonl"), type=Path)
    select.add_argument("--work-dir", required=True, type=Path)

    pack = commands.add_parser("pack", help="작업 폴더와 임베딩으로 스냅샷을 만든다")
    pack.add_argument("--work-dir", required=True, type=Path)
    pack.add_argument("--embedding-prefix", required=True)
    pack.add_argument("--output-dir", default=Path("data/snapshot"), type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "select":
            result = select_subset(args.chunks, args.manifest, args.filters, args.work_dir)
        else:
            result = pack_snapshot(args.work_dir, args.embedding_prefix, args.output_dir)
    except SnapshotBuildError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
