import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from backend.rag.retrieve import SearchFilters
from backend.rag.vector_store import (
    PgVectorBackend,
    VectorStoreError,
    configured_embed_dim,
    ensure_embedding_dimension_matches,
    is_numeric_heavy,
    load_embedding_index,
    load_pgvector,
)

ROOT = Path(__file__).resolve().parents[1]


class FixedQueryEmbedder:
    def encode_queries(self, texts):
        assert texts == ["질문"]
        return np.asarray([[1.0, 0.0]], dtype=np.float32)


TUPLE_ROW = object()  # 가짜 psycopg.rows.tuple_row(커서에 그대로 넘어가는지만 본다)
METHOD = "pgvector_cosine:openai/text-embedding-3-small"


def fake_connection(rows):
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = cursor
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor_context
    return connection, cursor


def install_fake_pgvector(monkeypatch, rows):
    connection, cursor = fake_connection(rows)
    connect = MagicMock(return_value=connection)
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connect))
    monkeypatch.setitem(sys.modules, "psycopg.rows", SimpleNamespace(tuple_row=TUPLE_ROW))
    return cursor, connect


class FakePool:
    """런타임 연결 풀처럼 connection()이 with 문용 연결을 빌려준다."""

    def __init__(self, connection):
        self._connection = connection
        self.borrowed = 0

    def connection(self):
        self.borrowed += 1
        return self._connection


def pg_row():
    return (
        "chunk-1",
        "corpus-v7",
        "20260301000001",
        "00126380",
        "회사A",
        2025,
        "NOTES",
        ["재무제표 주석"],
        None,
        None,
        1,
        "근거 본문",
        "a" * 64,
        0.9,
    )


def test_numeric_heavy_uses_digits_vs_letters():
    assert is_numeric_heavy("2025 | 1,234 | 5.6%")
    assert not is_numeric_heavy("회사는 2025년에 신규 계약을 체결했습니다")
    assert not is_numeric_heavy("해당사항 없음")


def test_embedding_dimension_configuration_and_mismatch():
    assert configured_embed_dim("384") == 384
    ensure_embedding_dimension_matches(384, 384)

    with pytest.raises(VectorStoreError, match="EMBED_DIM은 양의 정수"):
        configured_embed_dim("invalid")
    with pytest.raises(VectorStoreError, match="임베딩 차원 불일치"):
        ensure_embedding_dimension_matches(384, 1024)


def test_load_embedding_index_checks_hashes_and_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBED_DIM", "384")  # 해시·모양 검사 시험이라 차원은 시험 값으로 고정(기본값은 1536)
    ids = tmp_path / "ids.jsonl"
    vectors = tmp_path / "vectors.npy"
    metadata = tmp_path / "metadata.json"
    ids.write_text(
        json.dumps({"index": 0, "content_sha256": "a" * 64}) + "\n",
        encoding="utf-8",
    )
    np.save(vectors, np.ones((1, 384), dtype=np.float32))

    import hashlib

    metadata.write_text(
        json.dumps(
            {
                "complete": True,
                "dimension": 384,
                "ids_sha256": hashlib.sha256(ids.read_bytes()).hexdigest(),
                "vectors_sha256": hashlib.sha256(vectors.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    index, matrix, info = load_embedding_index(ids, vectors, metadata)

    assert index == {"a" * 64: 0}
    assert matrix.shape == (1, 384)
    assert info["complete"] is True

    ids.write_text("corrupted\n", encoding="utf-8")
    with pytest.raises(VectorStoreError, match="SHA-256"):
        load_embedding_index(ids, vectors, metadata)


@pytest.mark.parametrize("rows, expected_count", [([pg_row()], 1), ([], 0)])
def test_pgvector_search_traces_filters_and_result_count_without_hnsw_settings(
    monkeypatch, rows, expected_count
):
    monkeypatch.setenv("EMBED_DIM", "2")
    cursor, _ = install_fake_pgvector(monkeypatch, rows)
    events = []
    backend = PgVectorBackend(
        database_url="postgresql://test",
        corpus_id="corpus-v7",
        query_embedder=FixedQueryEmbedder(),
        retrieval_method=METHOD,
        trace_sink=events.append,
    )
    filters = SearchFilters(corp_code="00126380", fiscal_year=2025, chapter="NOTES")

    found = backend.search("질문", filters=filters, top_k=10)

    assert len(found) == expected_count
    assert events == [
        {
            "event": "pgvector_search",
            "corpus_id": "corpus-v7",
            "filters": {
                "corp_code": "00126380",
                "fiscal_year": 2025,
                "chapter": "NOTES",
                "note_no": None,
            },
            "top_k": 10,
            "result_count": expected_count,
        }
    ]
    # 정확 검색: HNSW 설정(SET LOCAL) 없이 검색 SQL 한 번. 질의 벡터는 텍스트로 보내 ::vector로 바꾼다.
    calls = cursor.execute.call_args_list
    assert len(calls) == 1
    sql, params = calls[0].args
    assert "SET LOCAL" not in sql
    assert "ORDER BY embedding <=> %s::vector, chunk_id" in sql
    assert params[0] == "[1.0,0.0]"
    assert params[1:4] == ["corpus-v7", "00126380", 2025]
    assert params[-1] == 10
    assert all(chunk.retrieval_method == "pgvector_cosine:openai/text-embedding-3-small" for chunk in found)


def test_pgvector_backend_borrows_from_pool_with_tuple_rows(monkeypatch):
    """서비스는 런타임 풀(dict_row)을 넘긴다. 새 연결을 열지 않고, 위치로 읽도록 커서에 tuple_row를 준다."""

    monkeypatch.setenv("EMBED_DIM", "2")
    _, connect = install_fake_pgvector(monkeypatch, [])
    connection, _ = fake_connection([pg_row()])
    pool = FakePool(connection)
    backend = PgVectorBackend(
        pool=pool, corpus_id="corpus-v7", query_embedder=FixedQueryEmbedder(), retrieval_method=METHOD
    )

    found = backend.search("질문", filters=SearchFilters(corp_code="00126380", fiscal_year=2025), top_k=5)

    assert [chunk.chunk_id for chunk in found] == ["chunk-1"]
    assert pool.borrowed == 1
    connect.assert_not_called()
    connection.cursor.assert_called_once_with(row_factory=TUPLE_ROW)


def test_pgvector_backend_needs_exactly_one_connection_source_and_method():
    common = {"corpus_id": "corpus-v7", "query_embedder": FixedQueryEmbedder()}
    with pytest.raises(VectorStoreError, match="정확히 하나"):
        PgVectorBackend(retrieval_method=METHOD, **common)
    with pytest.raises(VectorStoreError, match="정확히 하나"):
        PgVectorBackend(retrieval_method=METHOD, database_url="postgresql://x", pool=object(), **common)
    with pytest.raises(VectorStoreError, match="retrieval_method"):
        PgVectorBackend(retrieval_method="", database_url="postgresql://x", **common)


def test_pgvector_trace_failure_does_not_break_search(monkeypatch, caplog):
    monkeypatch.setenv("EMBED_DIM", "2")
    install_fake_pgvector(monkeypatch, [pg_row()])

    def broken_trace_sink(_event):
        raise RuntimeError("trace unavailable")

    backend = PgVectorBackend(
        database_url="postgresql://test",
        corpus_id="corpus-v7",
        query_embedder=FixedQueryEmbedder(),
        retrieval_method=METHOD,
        trace_sink=broken_trace_sink,
    )

    found = backend.search(
        "질문",
        filters=SearchFilters(corp_code="00126380", fiscal_year=2025),
        top_k=10,
    )

    assert [chunk.chunk_id for chunk in found] == ["chunk-1"]
    assert "pgvector 검색 trace 기록에 실패했습니다" in caplog.text


# ---- 적재 corpus_id 검사와 실제 pgvector 동치(사전 등록 90번) ----

def _write_load_inputs(tmp_path, chunks, vectors_by_content, *, manifest_corpus_id, dimension):
    """load_pgvector 입력 5개(manifest·chunks·ids·vectors·metadata)를 만든다."""

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    rcepts = {}
    for chunk in chunks:
        rcepts.setdefault(chunk["rcept_no"], {"corp_code": chunk["corp_code"], "chunks": 0})["chunks"] += 1
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("".join(json.dumps({
        "status": "success", "corpus_id": manifest_corpus_id, "parser": "test", "rcept_no": rcept_no,
        "corp_code": info["corp_code"], "corp_name": "테스트", "fiscal_year": 2025, "report_type": "annual",
        "filed_at": "20260301", "superseded": False, "collected_at": "2026-03-01T00:00:00Z",
        "raw_sha256": "f" * 64, "chunks": info["chunks"],
    }) + "\n" for rcept_no, info in rcepts.items()), encoding="utf-8")
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text("".join(json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks), encoding="utf-8")
    contents = sorted(vectors_by_content)
    ids = tmp_path / "emb.ids.jsonl"
    ids.write_text("".join(json.dumps({
        "index": index, "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }) + "\n" for index, content in enumerate(contents)), encoding="utf-8")
    vectors = tmp_path / "emb.vectors.npy"
    np.save(vectors, np.stack([vectors_by_content[content] for content in contents]).astype(np.float32))
    metadata = tmp_path / "emb.metadata.json"
    metadata.write_text(json.dumps({
        "complete": True, "model_name": "openai/text-embedding-3-small", "dimension": dimension,
        "ids_sha256": sha(ids), "vectors_sha256": sha(vectors), "chunks_sha256": sha(chunks_path),
    }), encoding="utf-8")
    return manifest, chunks_path, ids, vectors, metadata


def _chunk_row(chunk_id, corp_code, rcept_no, ordinal, content, *, corpus_id, chapter="NOTES", near_dup=False):
    return {
        "corpus_id": corpus_id, "chunk_id": chunk_id, "rcept_no": rcept_no, "corp_code": corp_code,
        "corp_name": "테스트", "fiscal_year": 2025, "chapter": chapter, "section_path": ["주석"],
        "note_no": None, "table_title": None, "ordinal": ordinal, "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(), "is_table": False,
        "near_dup": near_dup, "superseded": False,
    }


def test_load_pgvector_rejects_chunks_from_another_corpus_before_connecting(tmp_path, monkeypatch):
    """manifest와 chunks의 corpus_id가 다르면 DB에 닿기 전에 멈춘다(그대로 두면 manifest 값으로 덮어써짐)."""

    monkeypatch.setenv("EMBED_DIM", "2")
    chunks = [_chunk_row("c-1", "00000001", "90000000000001", 1, "본문", corpus_id="corpus-ffc1")]
    inputs = _write_load_inputs(tmp_path, chunks, {"본문": np.asarray([1.0, 0.0])},
                                manifest_corpus_id="corpus-4867", dimension=2)
    _, connect = install_fake_pgvector(monkeypatch, [])
    monkeypatch.setitem(sys.modules, "pgvector.psycopg", SimpleNamespace(register_vector=MagicMock()))

    with pytest.raises(VectorStoreError, match="corpus-ffc1.*corpus-4867"):
        load_pgvector(*inputs, database_url="postgresql://unused")
    connect.assert_not_called()


PGVECTOR_TEST_URL = os.getenv("PGVECTOR_TEST_DATABASE_URL", "").strip()
needs_pgvector = pytest.mark.skipif(
    not PGVECTOR_TEST_URL, reason="PGVECTOR_TEST_DATABASE_URL 없음: 실제 pgvector가 필요한 시험이라 건너뜀"
)


def _unit(vector):
    vector = np.asarray(vector, dtype=np.float32)
    return vector / np.linalg.norm(vector)


class ArrayQueryEmbedder:
    def __init__(self, vector):
        self.vector = vector

    def encode_queries(self, texts):
        return np.stack([self.vector for _ in texts])


@pytest.fixture
def loaded_pgvector(tmp_path, monkeypatch):
    """실제 pgvector에 두 회사 고정 코퍼스를 적재하고, 끝나면 그 코퍼스·문서 행을 지운다."""

    import psycopg

    from backend.api.limits import sql_statements

    monkeypatch.setenv("EMBED_DIM", "1536")
    with psycopg.connect(PGVECTOR_TEST_URL, autocommit=True) as connection:
        for name in ("01_init.sql", "02_schema.sql"):
            for statement in sql_statements(ROOT / "db" / name):
                connection.execute(statement)

    rng = np.random.default_rng(90)
    query = _unit(rng.standard_normal(1536))
    vectors = {name: _unit(rng.standard_normal(1536)) for name in ("본문1", "본문2", "본문3")}
    vectors["같은 본문"] = query  # 두 청크가 같은 본문이라 점수가 같다(동점 → chunk_id 오름차순)
    vectors["근접중복"] = query  # near_dup이라 점수가 가장 높아도 나오면 안 된다
    vectors["다른 회사"] = query  # 회사 필터로 빠져야 한다
    suffix = f"{uuid.uuid4().int % 10**12:012d}"
    corpus_id = f"corpus-pytest-{suffix}"
    rcept_a, rcept_b = f"91{suffix}", f"92{suffix}"  # CHAR(14)
    chunks = [
        # 물리 순서가 chunk_id 순서와 반대가 되도록 a-z를 a-a보다 먼저 넣는다.
        _chunk_row("a-z", "00000001", rcept_a, 1, "같은 본문", corpus_id=corpus_id),
        _chunk_row("a-a", "00000001", rcept_a, 2, "같은 본문", corpus_id=corpus_id),
        _chunk_row("a-1", "00000001", rcept_a, 3, "본문1", corpus_id=corpus_id),
        _chunk_row("a-2", "00000001", rcept_a, 4, "본문2", corpus_id=corpus_id),
        _chunk_row("a-3", "00000001", rcept_a, 5, "본문3", corpus_id=corpus_id, chapter="XI"),
        _chunk_row("a-nd", "00000001", rcept_a, 6, "근접중복", corpus_id=corpus_id, near_dup=True),
        _chunk_row("b-1", "00000002", rcept_b, 1, "다른 회사", corpus_id=corpus_id),
    ]
    inputs = _write_load_inputs(tmp_path, chunks, vectors, manifest_corpus_id=corpus_id, dimension=1536)
    load_pgvector(*inputs, database_url=PGVECTOR_TEST_URL)
    try:
        yield {"corpus_id": corpus_id, "chunks": chunks, "query": query, "vectors": vectors}
    finally:
        with psycopg.connect(PGVECTOR_TEST_URL, autocommit=True) as connection:
            connection.execute("DELETE FROM corpus_versions WHERE corpus_id = %s", (corpus_id,))
            connection.execute("DELETE FROM documents WHERE rcept_no IN (%s, %s)", (rcept_a, rcept_b))


def _backends(loaded):
    from backend.rag.offline_eval import InMemoryVectorBackend

    embedder = ArrayQueryEmbedder(loaded["query"])
    by_sha = {hashlib.sha256(content.encode("utf-8")).hexdigest(): vector
              for content, vector in loaded["vectors"].items()}
    memory = InMemoryVectorBackend(chunks=loaded["chunks"], vectors_by_sha=by_sha, query_embedder=embedder,
                                   retrieval_method="offline_cosine:test")
    pg = PgVectorBackend(database_url=PGVECTOR_TEST_URL, corpus_id=loaded["corpus_id"], query_embedder=embedder,
                         retrieval_method=METHOD)
    return memory, pg


@needs_pgvector
def test_pgvector_results_keep_retrieve_contract_and_filters(loaded_pgvector):
    """AC-05: retrieve() 계약(ScoredChunk·rank)과 필터(회사·장·near_dup)를 지키고, 동점은 chunk_id 오름차순."""

    from backend.rag.retrieve import retrieve

    _, pg = _backends(loaded_pgvector)
    filters = SearchFilters(corp_code="00000001", fiscal_year=2025, chapter="NOTES")

    found = retrieve("질문", filters=filters, top_k=5, backend=pg)

    assert [item["chunk_id"] for item in found[:2]] == ["a-a", "a-z"]
    assert {item["chunk_id"] for item in found} == {"a-a", "a-z", "a-1", "a-2"}  # a-3(XI)·a-nd·b-1 제외
    assert [item["rank"] for item in found] == [1, 2, 3, 4]
    assert all(item["retrieval_method"] == "pgvector_cosine:openai/text-embedding-3-small" for item in found)
    # LIMIT 경계가 동점 사이에 걸려도 chunk_id가 작은 쪽을 남긴다.
    assert [chunk.chunk_id for chunk in pg.search("질문", filters=filters, top_k=1)] == ["a-a"]


@needs_pgvector
def test_pgvector_matches_memory_backend(loaded_pgvector):
    """AC-06: 같은 질의 벡터·필터면 메모리 백엔드와 id 순서가 같고 점수 차가 1e-6 미만."""

    memory, pg = _backends(loaded_pgvector)
    for filters in (SearchFilters(corp_code="00000001", fiscal_year=2025),
                    SearchFilters(corp_code="00000001", fiscal_year=2025, chapter="NOTES"),
                    SearchFilters(corp_code="00000002", fiscal_year=2025)):
        for top_k in (1, 3, 50):
            expected = memory.search("질문", filters=filters, top_k=top_k)
            actual = pg.search("질문", filters=filters, top_k=top_k)
            assert [c.chunk_id for c in actual] == [c.chunk_id for c in expected]
            assert max((abs(a.score - e.score) for a, e in zip(actual, expected)), default=0.0) < 1e-6


@needs_pgvector
def test_pgvector_reports_corpus_version_and_corp_codes(loaded_pgvector):
    """build_retriever 기동 검사가 쓰는 두 조회: corpus_versions 행과 적재 회사 목록."""

    _, pg = _backends(loaded_pgvector)
    version = pg.corpus_version()
    assert version["embedding_model"] == "openai/text-embedding-3-small"
    assert version["vector_dimension"] == 1536
    assert pg.corp_codes() == frozenset({"00000001", "00000002"})
