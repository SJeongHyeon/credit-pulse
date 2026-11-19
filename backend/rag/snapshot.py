"""데모 스냅샷을 검증해 pgvector에 적재한다. Spec: docs/specs/w1-8-demo-snapshot.md

compose의 loader 서비스가 `python -m backend.rag.snapshot --dir data/snapshot`으로 실행한다.
적재 자체는 load_pgvector()를 그대로 쓰고, 여기서는 그 앞뒤만 맡는다:
손상 검사, gzip 해제, 이미 적재된 스냅샷 건너뛰기.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import psycopg

from backend.rag.vector_store import VectorStoreError, load_pgvector

SNAPSHOT_INFO_FILE = "snapshot.json"
MANIFEST_FILE = "manifest.jsonl.gz"
CHUNKS_FILE = "chunks.jsonl.gz"
EMBEDDING_IDS_FILE = "embeddings.ids.jsonl.gz"
EMBEDDING_VECTORS_FILE = "embeddings.vectors.npy.gz"
EMBEDDING_METADATA_FILE = "embeddings.metadata.json"
SNAPSHOT_FILES = (
    MANIFEST_FILE,
    CHUNKS_FILE,
    EMBEDDING_IDS_FILE,
    EMBEDDING_VECTORS_FILE,
    EMBEDDING_METADATA_FILE,
)
BUILD_HINT = "생성 방법: data/README.md의 '데모 스냅샷' 절을 보세요"
RESET_HINT = "`docker compose down -v`로 볼륨을 지운 뒤 다시 올리세요"


class SnapshotError(ValueError):
    """스냅샷을 적재할 수 없다. 메시지는 사람이 바로 조치할 수 있게 쓴다."""


@dataclass(frozen=True)
class LoadedState:
    manifest_sha256: str
    chunks_sha256: str
    embeddings_sha256: str
    chunks_count: int


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def write_deterministic_gzip(path: Path, content: bytes) -> None:
    # mtime=0, 빈 파일 이름: 같은 입력이 같은 바이트가 되어 sha256이 재현된다.
    with path.open("wb") as raw_file:
        with gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=raw_file, mtime=0) as gz:
            gz.write(content)


def read_snapshot_info(snapshot_dir: Path) -> dict[str, Any]:
    info_path = snapshot_dir / SNAPSHOT_INFO_FILE
    if not info_path.exists():
        raise SnapshotError(f"[snapshot] 없음: {info_path}. {BUILD_HINT}")
    try:
        return json.loads(info_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"[snapshot] {info_path}를 읽을 수 없습니다: {exc}") from exc


def _hash_mismatch(name: str, expected: str, actual: str) -> SnapshotError:
    return SnapshotError(
        f"[snapshot] sha256 불일치: {name} (기대 {expected[:12]}, 실제 {actual[:12]}). "
        "파일이 손상됐거나 snapshot.json과 다른 버전입니다"
    )


def extract_verified_files(
    snapshot_dir: Path, info: dict[str, Any], output_dir: Path
) -> dict[str, Path]:
    """모든 파일을 검증한 뒤 평문으로 푼다. 하나라도 틀리면 DB를 건드리기 전에 멈춘다."""

    extracted: dict[str, Path] = {}
    for name in SNAPSHOT_FILES:
        source = snapshot_dir / name
        if not source.exists():
            raise SnapshotError(f"[snapshot] 없음: {source}. {BUILD_HINT}")
        expected = info["files"][name]
        stored = source.read_bytes()
        actual = sha256_bytes(stored)
        if actual != expected["sha256"]:
            raise _hash_mismatch(name, expected["sha256"], actual)

        content = gzip.decompress(stored) if name.endswith(".gz") else stored
        actual_plain = sha256_bytes(content)
        if actual_plain != expected["uncompressed_sha256"]:
            raise _hash_mismatch(name, expected["uncompressed_sha256"], actual_plain)

        target = output_dir / name.removesuffix(".gz")
        target.write_bytes(content)
        extracted[name] = target
    return extracted


def fetch_loaded_state(database_url: str, corpus_id: str) -> LoadedState | None:
    """이 corpus_id가 이미 적재돼 있으면 그 상태를 돌려준다.

    load_pgvector()는 corpus_versions와 chunks를 한 트랜잭션으로 넣으므로,
    corpus_versions 행이 있으면 적재가 끝났다는 뜻이다.
    """

    try:
        with psycopg.connect(database_url, prepare_threshold=None) as connection:
            version = connection.execute(
                """
                SELECT manifest_sha256, chunks_sha256, embeddings_sha256
                FROM corpus_versions WHERE corpus_id = %s
                """,
                (corpus_id,),
            ).fetchone()
            if version is None:
                return None
            chunks_count = connection.execute(
                "SELECT count(*) FROM chunks WHERE corpus_id = %s", (corpus_id,)
            ).fetchone()[0]
    except psycopg.errors.UndefinedTable as exc:
        # db/ 아래 SQL은 볼륨을 처음 만들 때만 실행되므로 예전 볼륨에는 테이블이 없을 수 있다.
        raise SnapshotError(f"[snapshot] DB에 테이블이 없습니다. {RESET_HINT}") from exc
    return LoadedState(version[0], version[1], version[2], int(chunks_count))


def _expected_state(info: dict[str, Any]) -> LoadedState:
    files = info["files"]
    return LoadedState(
        manifest_sha256=files[MANIFEST_FILE]["uncompressed_sha256"],
        chunks_sha256=files[CHUNKS_FILE]["uncompressed_sha256"],
        embeddings_sha256=files[EMBEDDING_VECTORS_FILE]["uncompressed_sha256"],
        chunks_count=int(info["counts"]["chunks"]),
    )


def load_snapshot(
    snapshot_dir: str | Path,
    *,
    database_url: str,
    state_reader: Callable[[str, str], LoadedState | None] = fetch_loaded_state,
    loader: Callable[..., dict[str, Any]] = load_pgvector,
) -> str:
    """스냅샷을 적재하고 결과를 한 줄로 돌려준다. 실패하면 SnapshotError."""

    directory = Path(snapshot_dir)
    info = read_snapshot_info(directory)
    corpus_id = info["corpus_id"]

    with tempfile.TemporaryDirectory(prefix="snapshot-") as work_dir:
        extracted = extract_verified_files(directory, info, Path(work_dir))

        loaded = state_reader(database_url, corpus_id)
        if loaded == _expected_state(info):
            return f"[snapshot] 이미 적재됨, 건너뜀: {corpus_id}"
        if loaded is not None:
            # 다른 내용이 같은 id로 들어 있다. 조용히 덮어쓰면 평가 재현성이 깨진다.
            raise SnapshotError(
                f"[snapshot] {corpus_id}가 다른 내용으로 적재돼 있습니다. {RESET_HINT}"
            )

        result = loader(
            extracted[MANIFEST_FILE],
            extracted[CHUNKS_FILE],
            extracted[EMBEDDING_IDS_FILE],
            extracted[EMBEDDING_VECTORS_FILE],
            extracted[EMBEDDING_METADATA_FILE],
            database_url=database_url,
        )

    expected_chunks = int(info["counts"]["chunks"])
    if result["chunks_stored"] != expected_chunks:
        raise SnapshotError(
            f"[snapshot] 적재 행 수 불일치: 기대 {expected_chunks}, 실제 {result['chunks_stored']}"
        )
    return f"[snapshot] 적재 완료: {corpus_id}, 문서 {result['documents']}, 청크 {expected_chunks}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="데모 스냅샷 적재")
    parser.add_argument("--dir", default="data/snapshot")
    args = parser.parse_args(argv)

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        print("[snapshot] DATABASE_URL이 필요합니다", file=sys.stderr)
        return 1
    try:
        print(load_snapshot(args.dir, database_url=database_url))
    except (SnapshotError, VectorStoreError, psycopg.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
