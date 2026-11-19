"""D3 메타데이터 머리말 테스트. 실제 모델 없이 가짜 embedder로 검증한다."""
import hashlib
import json

import numpy as np
import pytest

from backend.rag.embedding import (
    EmbeddingBuildError,
    build_embeddings,
    embedding_input,
    embedding_key,
    load_unique_embedding_rows,
    main as embedding_main,
)
from backend.rag.offline_eval import (
    InMemoryVectorBackend,
    OfflineEvalError,
    load_embedding_artifacts,
)
from backend.rag.retrieve import SearchFilters
from backend.rag.vector_store import VectorStoreError, load_pgvector

HEADER = "v1"
BODY = "공통 본문"


def chunk(chunk_id="c1", **overrides):
    row = {
        "chunk_id": chunk_id,
        "corpus_id": "corpus-test",
        "rcept_no": "20260301000001",
        "corp_code": "00000001",
        "corp_name": "회사A",
        "fiscal_year": 2025,
        "chapter": "II",
        "section_path": ["II. 사업의 내용", "1. 사업의 개요", "가. 시장"],
        "note_no": None,
        "table_title": None,
        "ordinal": 1,
        "content": BODY,
        "content_sha256": hashlib.sha256(BODY.encode()).hexdigest(),
        "near_dup": False,
        "superseded": False,
    }
    return {**row, **overrides}


def write_chunks(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    return path


class MarkerEmbedder:
    """입력 텍스트에 따라 벡터가 달라져, 어떤 입력의 벡터인지 구분할 수 있다."""

    dimension = 2

    def __init__(self):
        self.texts = []

    def encode_passages(self, texts):
        self.texts.extend(texts)
        return np.asarray([self.vector(text) for text in texts], dtype=np.float32)

    @staticmethod
    def vector(text):
        return np.array([1.0, 0.0]) if "회사A" in text else np.array([0.0, 1.0])


class FixedQuery:
    dimension = 2

    def __init__(self, vector):
        self.vector = np.asarray(vector, dtype=np.float32)

    def encode_queries(self, texts):
        return np.asarray([self.vector for _ in texts], dtype=np.float32)


def test_header_uses_last_two_sections_and_keeps_body_after_newline():
    assert embedding_input(chunk(), HEADER) == (
        f"[회사A | FY2025 사업보고서 | 1. 사업의 개요 > 가. 시장]\n{BODY}"
    )


def test_header_shows_table_title_when_it_differs_from_section():
    text = embedding_input(chunk(table_title="차입금 내역"), HEADER)

    assert text.split("\n")[0] == (
        "[회사A | FY2025 사업보고서 | 1. 사업의 개요 > 가. 시장 | 표: 차입금 내역]"
    )


def test_header_omits_table_title_equal_to_section_name():
    text = embedding_input(chunk(table_title="가. 시장"), HEADER)

    assert "표:" not in text


def test_header_uses_single_section_and_omits_empty_section():
    assert embedding_input(chunk(section_path=["II. 사업의 내용"]), HEADER).split("\n")[0] == (
        "[회사A | FY2025 사업보고서 | II. 사업의 내용]"
    )
    assert embedding_input(chunk(section_path=[]), HEADER).split("\n")[0] == (
        "[회사A | FY2025 사업보고서]"
    )


def test_header_cuts_long_field_and_collapses_spaces():
    header = embedding_input(
        chunk(section_path=["상위   절", "가" * 41]), HEADER
    ).split("\n")[0]

    assert "상위 절 > " + "가" * 39 + "…]" in header


def test_header_field_of_exactly_40_chars_is_not_cut():
    header = embedding_input(chunk(section_path=["가" * 40]), HEADER).split("\n")[0]

    assert "…" not in header


def test_header_never_exceeds_120_chars():
    header = embedding_input(
        chunk(corp_name="회" * 60, section_path=["가" * 60, "나" * 60], table_title="다" * 60),
        HEADER,
    ).split("\n")[0]

    assert len(header) == 120
    assert header.endswith("…]")


def test_no_header_returns_content_unchanged():
    assert embedding_input(chunk(), None) == BODY


def test_unknown_header_version_is_rejected():
    with pytest.raises(EmbeddingBuildError, match="input_header"):
        embedding_input(chunk(), "v9")


def test_key_is_sha256_of_full_input_and_content_hash_when_off():
    row = chunk()

    assert embedding_key(row, HEADER) == hashlib.sha256(
        embedding_input(row, HEADER).encode("utf-8")
    ).hexdigest()
    assert embedding_key(row, None) == row["content_sha256"]


def test_off_build_is_byte_identical_to_build_without_argument(tmp_path):
    chunks = write_chunks(tmp_path / "chunks.jsonl", [chunk()])

    default = build_embeddings(chunks, tmp_path / "default", embedder=MarkerEmbedder())
    explicit = build_embeddings(
        chunks, tmp_path / "explicit", embedder=MarkerEmbedder(), input_header=None
    )

    assert "input_header" not in default
    assert default["config_sha256"] == explicit["config_sha256"]
    assert (tmp_path / "default.vectors.npy").read_bytes() == (
        tmp_path / "explicit.vectors.npy"
    ).read_bytes()
    assert (tmp_path / "default.ids.jsonl").read_bytes() == (
        tmp_path / "explicit.ids.jsonl"
    ).read_bytes()
    assert json.loads((tmp_path / "default.ids.jsonl").read_text(encoding="utf-8")) == {
        "index": 0,
        "content_sha256": chunk()["content_sha256"],
        "representative_chunk_id": "c1",
    }


def test_header_changes_config_hash_and_blocks_resume(tmp_path):
    chunks = write_chunks(tmp_path / "chunks.jsonl", [chunk()])
    off = build_embeddings(chunks, tmp_path / "off", embedder=MarkerEmbedder())
    on = build_embeddings(
        chunks, tmp_path / "on", embedder=MarkerEmbedder(), input_header=HEADER
    )

    assert on["input_header"] == HEADER
    assert on["config_sha256"] != off["config_sha256"]
    with pytest.raises(EmbeddingBuildError, match="progress"):
        build_embeddings(chunks, tmp_path / "off", embedder=MarkerEmbedder(), input_header=HEADER)


def test_embedder_receives_header_text(tmp_path):
    chunks = write_chunks(tmp_path / "chunks.jsonl", [chunk()])
    embedder = MarkerEmbedder()

    build_embeddings(chunks, tmp_path / "on", embedder=embedder, input_header=HEADER)

    assert embedder.texts == [embedding_input(chunk(), HEADER)]


def test_same_body_different_company_gets_separate_vectors(tmp_path):
    rows = [
        chunk("a"),
        chunk("b", corp_code="00000002", corp_name="회사B", rcept_no="20260301000002"),
    ]
    chunks = write_chunks(tmp_path / "chunks.jsonl", rows)

    build_embeddings(chunks, tmp_path / "on", embedder=MarkerEmbedder(), input_header=HEADER)
    metadata, vectors = load_embedding_artifacts(tmp_path / "on")

    assert metadata["rows"] == 2
    backend = InMemoryVectorBackend(
        chunks=rows,
        vectors_by_sha=vectors,
        query_embedder=FixedQuery([0.0, 1.0]),
        retrieval_method="test",
        input_header=HEADER,
    )
    found = backend.search(
        "질문", filters=SearchFilters(corp_code="00000002", fiscal_year=2025), top_k=1
    )
    assert found[0].chunk_id == "b"
    assert found[0].score == pytest.approx(1.0)


def test_same_input_is_deduplicated_and_shared(tmp_path):
    rows = [chunk("a", ordinal=1), chunk("b", ordinal=2)]
    chunks = write_chunks(tmp_path / "chunks.jsonl", rows)

    metadata = build_embeddings(
        chunks, tmp_path / "on", embedder=MarkerEmbedder(), input_header=HEADER
    )

    assert metadata["rows"] == 1
    assert [row["representative_chunk_id"] for row in load_unique_embedding_rows(
        chunks, input_header=HEADER
    )] == ["a"]


def test_near_dup_and_corp_filter_still_apply_with_header(tmp_path):
    rows = [
        chunk("a"),
        chunk("b", content="중복", near_dup=True),
        chunk("c", corp_code="00000002", corp_name="회사B"),
    ]
    chunks = write_chunks(tmp_path / "chunks.jsonl", rows)

    kept = load_unique_embedding_rows(chunks, corp_codes={"00000001"}, input_header=HEADER)

    assert [row["representative_chunk_id"] for row in kept] == ["a"]


def test_header_rows_sort_by_input_length_then_key(tmp_path):
    rows = [chunk("long", content="가" * 30), chunk("short", content="가")]
    chunks = write_chunks(tmp_path / "chunks.jsonl", rows)

    kept = load_unique_embedding_rows(chunks, input_header=HEADER)

    assert [row["representative_chunk_id"] for row in kept] == ["short", "long"]


def test_missing_metadata_field_is_rejected_with_header(tmp_path):
    row = chunk()
    del row["corp_name"]
    chunks = write_chunks(tmp_path / "chunks.jsonl", [row])

    with pytest.raises(EmbeddingBuildError, match="1행"):
        load_unique_embedding_rows(chunks, input_header=HEADER)


def test_header_ids_have_key_and_no_content_hash(tmp_path):
    chunks = write_chunks(tmp_path / "chunks.jsonl", [chunk()])

    build_embeddings(chunks, tmp_path / "on", embedder=MarkerEmbedder(), input_header=HEADER)

    assert json.loads((tmp_path / "on.ids.jsonl").read_text(encoding="utf-8")) == {
        "index": 0,
        "embedding_key": embedding_key(chunk(), HEADER),
        "representative_chunk_id": "c1",
    }


def test_header_artifacts_are_rejected_before_touching_database(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBED_DIM", "2")
    chunks = write_chunks(tmp_path / "chunks.jsonl", [chunk()])
    build_embeddings(chunks, tmp_path / "on", embedder=MarkerEmbedder(), input_header=HEADER)
    manifest = write_chunks(
        tmp_path / "manifest.jsonl",
        [{"status": "success", "corpus_id": "corpus-test", "rcept_no": "20260301000001"}],
    )

    with pytest.raises(VectorStoreError, match="형식 오류"):
        load_pgvector(
            manifest,
            chunks,
            tmp_path / "on.ids.jsonl",
            tmp_path / "on.vectors.npy",
            tmp_path / "on.metadata.json",
            database_url="postgresql://unused",
        )


def test_missing_vector_with_header_fails_loudly(tmp_path):
    rows = [chunk("a")]
    chunks = write_chunks(tmp_path / "chunks.jsonl", rows)
    build_embeddings(chunks, tmp_path / "on", embedder=MarkerEmbedder(), input_header=HEADER)
    _, vectors = load_embedding_artifacts(tmp_path / "on")
    other_section = chunk("b", section_path=["다른 절"], corp_code="00000001")

    backend = InMemoryVectorBackend(
        chunks=[*rows, other_section],
        vectors_by_sha=vectors,
        query_embedder=FixedQuery([1.0, 0.0]),
        retrieval_method="test",
        input_header=HEADER,
    )

    with pytest.raises(OfflineEvalError, match="벡터가 없습니다"):
        backend.search(
            "질문", filters=SearchFilters(corp_code="00000001", fiscal_year=2025), top_k=1
        )


def test_artifacts_without_header_still_use_content_hash(tmp_path):
    chunks = write_chunks(tmp_path / "chunks.jsonl", [chunk()])
    build_embeddings(chunks, tmp_path / "off", embedder=MarkerEmbedder())

    metadata, vectors = load_embedding_artifacts(tmp_path / "off")

    assert "input_header" not in metadata
    assert list(vectors) == [chunk()["content_sha256"]]


def test_cli_passes_input_header(tmp_path, monkeypatch):
    calls = []

    def fake_build(*args, **kwargs):
        calls.append(kwargs)
        return {}

    monkeypatch.setattr("backend.rag.embedding.build_embeddings", fake_build)
    base = ["build", "--chunks", "c.jsonl", "--output-prefix", "out"]

    embedding_main([*base, "--input-header", HEADER])
    embedding_main(base)

    assert calls[0]["input_header"] == HEADER
    assert calls[1]["input_header"] is None
