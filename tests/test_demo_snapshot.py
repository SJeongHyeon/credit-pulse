"""W1.8 데모 스냅샷 테스트. 실제 임베딩·DB 없이 작은 fixture로 검증한다."""
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from backend.rag.snapshot import (
    CHUNKS_FILE,
    EMBEDDING_IDS_FILE,
    EMBEDDING_METADATA_FILE,
    EMBEDDING_VECTORS_FILE,
    MANIFEST_FILE,
    SNAPSHOT_FILES,
    SNAPSHOT_INFO_FILE,
    LoadedState,
    SnapshotError,
    extract_verified_files,
    load_snapshot,
    main as load_main,
)
from backend.rag.vector_store import load_embedding_index
from scripts.build_demo_snapshot import (
    SnapshotBuildError,
    derive_corpus_id,
    pack_snapshot,
    select_subset,
)

SOURCE_CORPUS_ID = "corpus-" + "a" * 24
DATABASE_URL = "postgresql://unused"
KEPT_CORPS = ["00000001", "00000002"]
DROPPED_CORP = "00000003"


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def chunk_row(chunk_id: str, corp_code: str, content: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "corpus_id": SOURCE_CORPUS_ID,
        "corp_code": corp_code,
        "rcept_no": f"202601{corp_code}",
        "content": content,
        "content_sha256": content_hash(content),
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


@pytest.fixture
def source(tmp_path) -> dict:
    """회사 3개의 청크·manifest·임베딩. 앞 2개사만 필터에 있다."""

    chunks = [
        chunk_row("chunk-1", KEPT_CORPS[0], "본문 가"),
        chunk_row("chunk-2", DROPPED_CORP, "본문 나"),
        chunk_row("chunk-3", KEPT_CORPS[1], "본문 다"),
        chunk_row("chunk-4", KEPT_CORPS[1], "본문 가"),  # 같은 본문은 벡터 하나를 공유한다
    ]
    corps = {*KEPT_CORPS, DROPPED_CORP}
    manifest = [
        {
            "corpus_id": SOURCE_CORPUS_ID,
            "corp_code": corp,
            "rcept_no": f"202601{corp}",
            "chunks": sum(1 for row in chunks if row["corp_code"] == corp),
        }
        for corp in sorted(corps)
    ]
    paths = {
        "chunks": tmp_path / "source_chunks.jsonl",
        "manifest": tmp_path / "source_manifest.jsonl",
        "filters": tmp_path / "filters.jsonl",
    }
    write_jsonl(paths["chunks"], chunks)
    write_jsonl(paths["manifest"], manifest)
    # 한 회사가 여러 문항에 나와도 한 번만 세야 한다.
    write_jsonl(
        paths["filters"],
        [{"id": index, "corp_code": corp} for index, corp in enumerate([*KEPT_CORPS, KEPT_CORPS[0]])],
    )

    # 원본 순서를 일부러 섞어, 재번호가 틀리면 다른 벡터가 붙게 한다.
    source_vectors = {
        content_hash("본문 다"): [0.0, 0.0, 1.0, 0.0],
        content_hash("본문 나"): [0.0, 1.0, 0.0, 0.0],
        content_hash("본문 가"): [1.0, 0.0, 0.0, 0.0],
    }
    prefix = tmp_path / "source_embedding"
    write_jsonl(
        Path(f"{prefix}.ids.jsonl"),
        [{"index": index, "content_sha256": key} for index, key in enumerate(source_vectors)],
    )
    np.save(f"{prefix}.vectors.npy", np.array(list(source_vectors.values()), dtype=np.float32))
    Path(f"{prefix}.metadata.json").write_text(
        json.dumps({"complete": True, "model_name": "test-model", "chunks_path": "C:/private/path"}),
        encoding="utf-8",
    )
    return {**paths, "embedding_prefix": str(prefix), "vectors": source_vectors}


def build_snapshot(source: dict, root: Path) -> Path:
    work_dir = root / "work"
    output_dir = root / "snapshot"
    select_subset(source["chunks"], source["manifest"], source["filters"], work_dir)
    pack_snapshot(work_dir, source["embedding_prefix"], output_dir)
    return output_dir


@pytest.fixture
def snapshot_dir(source, tmp_path) -> Path:
    return build_snapshot(source, tmp_path)


def read_gz_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).decode().splitlines()]


def read_info(snapshot_dir: Path) -> dict:
    return json.loads((snapshot_dir / SNAPSHOT_INFO_FILE).read_text(encoding="utf-8"))


def expected_state(snapshot_dir: Path) -> LoadedState:
    info = read_info(snapshot_dir)
    return LoadedState(
        info["files"][MANIFEST_FILE]["uncompressed_sha256"],
        info["files"][CHUNKS_FILE]["uncompressed_sha256"],
        info["files"][EMBEDDING_VECTORS_FILE]["uncompressed_sha256"],
        info["counts"]["chunks"],
    )


class RecordingLoader:
    def __init__(self, chunks_stored: int = 3) -> None:
        self.calls: list[tuple] = []
        self.chunks_stored = chunks_stored

    def __call__(self, *paths, database_url: str) -> dict:
        # 임시 폴더가 살아 있는 동안 호출되는지, 평문을 넘기는지 함께 확인한다.
        assert all(Path(path).exists() and not str(path).endswith(".gz") for path in paths)
        self.calls.append(paths)
        return {"documents": 2, "chunks_stored": self.chunks_stored}


def no_state(_database_url: str, _corpus_id: str) -> None:
    return None


def test_select_keeps_only_filtered_companies_and_chunk_ids(snapshot_dir):
    chunks = read_gz_jsonl(snapshot_dir / CHUNKS_FILE)
    manifest = read_gz_jsonl(snapshot_dir / MANIFEST_FILE)

    assert [row["chunk_id"] for row in chunks] == ["chunk-1", "chunk-3", "chunk-4"]
    assert sorted(row["corp_code"] for row in manifest) == KEPT_CORPS
    assert sum(row["chunks"] for row in manifest) == len(chunks)


def test_vectors_are_renumbered_to_the_same_content(source, snapshot_dir):
    ids = read_gz_jsonl(snapshot_dir / EMBEDDING_IDS_FILE)
    vectors_path = snapshot_dir.parent / "vectors.npy"
    vectors_path.write_bytes(gzip.decompress((snapshot_dir / EMBEDDING_VECTORS_FILE).read_bytes()))
    vectors = np.load(vectors_path)

    assert [row["index"] for row in ids] == [0, 1]
    assert vectors.dtype == np.float32
    for row in ids:
        assert vectors[row["index"]].tolist() == source["vectors"][row["content_sha256"]]


def test_subset_gets_new_corpus_id_and_records_source(snapshot_dir):
    info = read_info(snapshot_dir)

    assert info["corpus_id"] == derive_corpus_id(SOURCE_CORPUS_ID, KEPT_CORPS)
    assert info["corpus_id"] != SOURCE_CORPUS_ID
    assert info["source_corpus_id"] == SOURCE_CORPUS_ID
    assert info["counts"] == {"documents": 2, "chunks": 3, "unique_contents": 2}
    rows = read_gz_jsonl(snapshot_dir / CHUNKS_FILE) + read_gz_jsonl(snapshot_dir / MANIFEST_FILE)
    assert {row["corpus_id"] for row in rows} == {info["corpus_id"]}


def test_metadata_passes_existing_loader_check_without_local_paths(
    snapshot_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv("EMBED_DIM", "4")
    output_dir = tmp_path / "plain"
    output_dir.mkdir()
    extracted = extract_verified_files(snapshot_dir, read_info(snapshot_dir), output_dir)

    _, vectors, metadata = load_embedding_index(
        extracted[EMBEDDING_IDS_FILE],
        extracted[EMBEDDING_VECTORS_FILE],
        extracted[EMBEDDING_METADATA_FILE],
    )

    assert vectors.shape == (2, 4)
    assert metadata["model_name"] == "test-model"
    published = (snapshot_dir / EMBEDDING_METADATA_FILE).read_text(encoding="utf-8")
    assert "private" not in published and "chunks_path" not in published


def test_build_is_deterministic(source, tmp_path):
    first = build_snapshot(source, tmp_path / "first")
    second = build_snapshot(source, tmp_path / "second")

    for name in [*SNAPSHOT_FILES, SNAPSHOT_INFO_FILE]:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_select_rejects_manifest_and_chunks_from_different_corpus(source, tmp_path):
    rows = [json.loads(line) for line in source["manifest"].read_text(encoding="utf-8").splitlines()]
    write_jsonl(source["manifest"], [{**row, "corpus_id": "corpus-" + "b" * 24} for row in rows])

    with pytest.raises(SnapshotBuildError, match="corpus_id"):
        select_subset(source["chunks"], source["manifest"], source["filters"], tmp_path / "work")


def test_select_rejects_manifest_chunk_count_mismatch(source, tmp_path):
    rows = [json.loads(line) for line in source["manifest"].read_text(encoding="utf-8").splitlines()]
    write_jsonl(source["manifest"], [{**row, "chunks": 99} for row in rows])

    with pytest.raises(SnapshotBuildError, match="청크 수 불일치"):
        select_subset(source["chunks"], source["manifest"], source["filters"], tmp_path / "work")


def test_select_rejects_company_missing_from_manifest(source, tmp_path):
    write_jsonl(source["filters"], [{"id": 1, "corp_code": "99999999"}])

    with pytest.raises(SnapshotBuildError, match="99999999"):
        select_subset(source["chunks"], source["manifest"], source["filters"], tmp_path / "work")


def test_pack_rejects_missing_embedding(source, tmp_path):
    work_dir = tmp_path / "work"
    select_subset(source["chunks"], source["manifest"], source["filters"], work_dir)
    write_jsonl(Path(f"{source['embedding_prefix']}.ids.jsonl"), [])

    with pytest.raises(SnapshotBuildError, match="임베딩이 없는 본문"):
        pack_snapshot(work_dir, source["embedding_prefix"], tmp_path / "out")


def test_empty_database_is_loaded(snapshot_dir):
    loader = RecordingLoader()

    message = load_snapshot(
        snapshot_dir, database_url=DATABASE_URL, state_reader=no_state, loader=loader
    )

    assert len(loader.calls) == 1
    assert "적재 완료" in message


def test_already_loaded_snapshot_is_skipped(snapshot_dir):
    loader = RecordingLoader()

    message = load_snapshot(
        snapshot_dir,
        database_url=DATABASE_URL,
        state_reader=lambda *_: expected_state(snapshot_dir),
        loader=loader,
    )

    assert loader.calls == []
    assert "건너뜀" in message


@pytest.mark.parametrize(
    "difference",
    [
        {"chunks_sha256": "0" * 64},
        {"manifest_sha256": "0" * 64},
        {"embeddings_sha256": "0" * 64},
        {"chunks_count": 1},
    ],
)
def test_conflicting_state_fails_without_overwriting(snapshot_dir, difference):
    loader = RecordingLoader()
    state = LoadedState(**{**expected_state(snapshot_dir).__dict__, **difference})

    with pytest.raises(SnapshotError, match="docker compose down -v"):
        load_snapshot(
            snapshot_dir,
            database_url=DATABASE_URL,
            state_reader=lambda *_: state,
            loader=loader,
        )

    assert loader.calls == []


def test_missing_snapshot_info_explains_how_to_build(tmp_path):
    loader = RecordingLoader()

    with pytest.raises(SnapshotError, match=r"\[snapshot\] 없음.*data/README.md"):
        load_snapshot(
            tmp_path / "nothing", database_url=DATABASE_URL, state_reader=no_state, loader=loader
        )

    assert loader.calls == []


@pytest.mark.parametrize("name", SNAPSHOT_FILES)
def test_missing_file_is_named_and_database_untouched(snapshot_dir, name):
    def fail_if_read(*_):
        raise AssertionError("검증 실패 시 DB를 읽지 않아야 한다")

    loader = RecordingLoader()
    (snapshot_dir / name).unlink()

    with pytest.raises(SnapshotError, match=name):
        load_snapshot(
            snapshot_dir, database_url=DATABASE_URL, state_reader=fail_if_read, loader=loader
        )

    assert loader.calls == []


def test_one_changed_byte_is_reported_with_both_hashes(snapshot_dir):
    path = snapshot_dir / CHUNKS_FILE
    content = bytearray(path.read_bytes())
    content[-1] ^= 0xFF
    path.write_bytes(bytes(content))
    expected = read_info(snapshot_dir)["files"][CHUNKS_FILE]["sha256"]
    loader = RecordingLoader()

    with pytest.raises(SnapshotError, match=CHUNKS_FILE) as error:
        load_snapshot(snapshot_dir, database_url=DATABASE_URL, state_reader=no_state, loader=loader)

    assert expected[:12] in str(error.value)
    assert hashlib.sha256(bytes(content)).hexdigest()[:12] in str(error.value)
    assert loader.calls == []


def test_uncompressed_hash_mismatch_is_detected(snapshot_dir):
    info_path = snapshot_dir / SNAPSHOT_INFO_FILE
    info = read_info(snapshot_dir)
    info["files"][CHUNKS_FILE]["uncompressed_sha256"] = "0" * 64
    info_path.write_text(json.dumps(info), encoding="utf-8")

    with pytest.raises(SnapshotError, match="sha256 불일치"):
        load_snapshot(
            snapshot_dir, database_url=DATABASE_URL, state_reader=no_state, loader=RecordingLoader()
        )


def test_stored_row_count_mismatch_fails(snapshot_dir):
    with pytest.raises(SnapshotError, match="행 수 불일치"):
        load_snapshot(
            snapshot_dir,
            database_url=DATABASE_URL,
            state_reader=no_state,
            loader=RecordingLoader(chunks_stored=1),
        )


def test_cli_exits_1_with_message_when_snapshot_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)

    assert load_main(["--dir", str(tmp_path / "nothing")]) == 1
    assert "[snapshot] 없음" in capsys.readouterr().err


def test_cli_requires_database_url(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert load_main(["--dir", str(tmp_path)]) == 1
    assert "DATABASE_URL" in capsys.readouterr().err


def compose_service_block(name: str) -> str:
    text = Path("docker-compose.yml").read_text(encoding="utf-8")
    start = text.index(f"\n  {name}:\n")
    following = [
        text.find(f"\n  {other}:\n", start + 1) for other in ("loader", "api", "db")
    ] + [text.find("\nvolumes:\n", start + 1)]
    end = min(position for position in following if position > start)
    return text[start:end]


def test_compose_loader_targets_only_the_compose_database():
    # environment가 env_file을 덮어쓰므로 .env의 운영 DB 주소로 가지 않는다.
    block = compose_service_block("loader")

    assert "DATABASE_URL: postgresql://app:app@db:5432/app" in block
    assert "env_file" not in block
    assert "restart: \"no\"" in block


def test_compose_api_waits_for_loader():
    block = compose_service_block("api")

    assert "loader:" in block
    assert "condition: service_completed_successfully" in block
