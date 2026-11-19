"""pilot 코퍼스와 로컬 E5 벡터를 pgvector에 멱등 적재한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from dotenv import load_dotenv

from backend.rag.retrieve import RetrievalBackend, ScoredChunk, SearchFilters


logger = logging.getLogger(__name__)


class VectorStoreError(ValueError):
    """벡터 산출물 또는 DB 적재 계약 위반."""


class QueryEmbedder:
    def encode_queries(self, texts: list[str]) -> np.ndarray: ...


_DIGIT_RE = re.compile(r"\d")
_TEXT_RE = re.compile(r"[^\W\d_]", re.UNICODE)
_VECTOR_TYPE_RE = re.compile(r"^vector\((\d+)\)$")
DEFAULT_EMBED_DIM = 1536  # SPEC §3.4: 채택 조합 OpenAI 3-small


def configured_embed_dim(value: str | int | None = None) -> int:
    """환경 설정의 임베딩 차원을 검증해 반환한다."""

    raw = os.getenv("EMBED_DIM", str(DEFAULT_EMBED_DIM)) if value is None else value
    try:
        dimension = int(raw)
    except (TypeError, ValueError) as exc:
        raise VectorStoreError("EMBED_DIM은 양의 정수여야 합니다") from exc
    if dimension < 1:
        raise VectorStoreError("EMBED_DIM은 양의 정수여야 합니다")
    return dimension


def ensure_embedding_dimension_matches(configured: int, database: int) -> None:
    if configured != database:
        raise VectorStoreError(
            "임베딩 차원 불일치: "
            f"EMBED_DIM={configured}, chunks.embedding=vector({database})"
        )


def validate_database_embedding_dimension(
    *,
    database_url: str | None = None,
    expected_dimension: int | None = None,
) -> dict[str, int]:
    """앱 기동 시 EMBED_DIM과 실제 pgvector 컬럼 차원을 대조한다."""

    dsn = database_url or os.getenv("DATABASE_URL", "")
    if not dsn:
        raise VectorStoreError("DATABASE_URL이 필요합니다")
    expected = configured_embed_dim(expected_dimension)
    try:
        import psycopg
    except ImportError as exc:
        raise VectorStoreError("psycopg Python 패키지가 필요합니다") from exc

    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT format_type(atttypid, atttypmod)
                FROM pg_attribute
                WHERE attrelid = 'chunks'::regclass
                  AND attname = 'embedding'
                  AND NOT attisdropped
                """
            )
            row = cursor.fetchone()
    if row is None:
        raise VectorStoreError("chunks.embedding 컬럼을 찾을 수 없습니다")
    match = _VECTOR_TYPE_RE.fullmatch(str(row[0]))
    if match is None:
        raise VectorStoreError(f"chunks.embedding 타입이 vector(n)이 아닙니다: {row[0]}")
    actual = int(match.group(1))
    ensure_embedding_dimension_matches(expected, actual)
    return {"configured_dimension": expected, "database_dimension": actual}


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def is_numeric_heavy(content: str) -> bool:
    digits = len(_DIGIT_RE.findall(content))
    letters = len(_TEXT_RE.findall(content))
    return digits > 0 and digits / max(digits + letters, 1) >= 0.5


def load_embedding_index(
    ids_path: str | Path,
    vectors_path: str | Path,
    metadata_path: str | Path,
) -> tuple[dict[str, int], np.ndarray, dict[str, Any]]:
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    if not metadata.get("complete"):
        raise VectorStoreError("임베딩 metadata가 완료 상태가 아닙니다")
    if metadata.get("ids_sha256") != _sha256(ids_path):
        raise VectorStoreError("ids SHA-256이 metadata와 다릅니다")
    if metadata.get("vectors_sha256") != _sha256(vectors_path):
        raise VectorStoreError("vectors SHA-256이 metadata와 다릅니다")

    indices: dict[str, int] = {}
    for line_number, line in enumerate(
        Path(ids_path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            index = int(row["index"])
            sha256 = str(row["content_sha256"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise VectorStoreError(f"ids JSONL {line_number}행 형식 오류") from exc
        if sha256 in indices:
            raise VectorStoreError(f"ids에 중복 content_sha256: {sha256}")
        indices[sha256] = index

    vectors = np.load(vectors_path, mmap_mode="r")
    expected_shape = (len(indices), int(metadata["dimension"]))
    if vectors.shape != expected_shape:
        raise VectorStoreError(
            f"벡터 shape 불일치: actual={vectors.shape}, expected={expected_shape}"
        )
    expected_dimension = configured_embed_dim()
    if int(metadata["dimension"]) != expected_dimension:
        raise VectorStoreError(
            "임베딩 산출물 차원 불일치: "
            f"metadata={metadata['dimension']}, EMBED_DIM={expected_dimension}"
        )
    return indices, vectors, metadata


def _load_manifest(path: str | Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    failed = [row["rcept_no"] for row in rows if row.get("status") != "success"]
    if failed:
        raise VectorStoreError(f"실패 manifest 문서는 적재할 수 없습니다: {failed}")
    return rows


def _chunk_corpus_ids(path: str | Path) -> set[str]:
    corpus_ids: set[str] = set()
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                corpus_ids.add(str(json.loads(line).get("corpus_id")))
    return corpus_ids


def load_pgvector(
    manifest_path: str | Path,
    chunks_path: str | Path,
    ids_path: str | Path,
    vectors_path: str | Path,
    metadata_path: str | Path,
    *,
    database_url: str | None = None,
    batch_size: int = 500,
) -> dict[str, Any]:
    if batch_size < 1:
        raise VectorStoreError("batch_size는 1 이상이어야 합니다")
    dsn = database_url or os.getenv("DATABASE_URL", "")
    if not dsn:
        raise VectorStoreError("DATABASE_URL이 필요합니다")
    try:
        import psycopg
        from pgvector.psycopg import register_vector
    except ImportError as exc:
        raise VectorStoreError("psycopg와 pgvector Python 패키지가 필요합니다") from exc

    manifest = _load_manifest(manifest_path)
    indices, vectors, embedding_metadata = load_embedding_index(
        ids_path, vectors_path, metadata_path
    )
    corpus_ids = {str(row["corpus_id"]) for row in manifest}
    if len(corpus_ids) != 1:
        raise VectorStoreError("manifest corpus_id가 하나가 아닙니다")
    corpus_id = next(iter(corpus_ids))
    chunk_file_sha256 = _sha256(chunks_path)
    if embedding_metadata.get("chunks_sha256") != chunk_file_sha256:
        raise VectorStoreError("임베딩 입력 chunks와 적재 chunks가 다릅니다")
    # 아래 COPY는 manifest의 corpus_id를 모든 청크에 쓴다. chunks 파일의 corpus_id가 다르면
    # DB(벡터)와 이미지 안 파일(BM25)이 다른 corpus_id를 갖게 되므로 DB에 닿기 전에 막는다.
    chunk_corpus_ids = _chunk_corpus_ids(chunks_path)
    if chunk_corpus_ids != {corpus_id}:
        raise VectorStoreError(
            f"chunks corpus_id {sorted(chunk_corpus_ids)}가 manifest corpus_id {corpus_id}와 다릅니다"
        )

    document_rows = []
    for row in manifest:
        document_rows.append(
            (
                row["rcept_no"],
                row["corp_code"],
                row["corp_name"],
                row["fiscal_year"],
                row["report_type"],
                datetime.strptime(str(row["filed_at"]), "%Y%m%d").date(),
                "superseded" if row.get("superseded") else "active",
                row["collected_at"],
                row["raw_sha256"],
            )
        )

    with psycopg.connect(dsn, prepare_threshold=None) as connection:
        register_vector(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO corpus_versions (
                    corpus_id, parser, embedding_model, vector_dimension,
                    manifest_sha256, chunks_sha256, embeddings_sha256,
                    documents_count, chunks_count
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (corpus_id) DO UPDATE SET
                    parser=EXCLUDED.parser,
                    embedding_model=EXCLUDED.embedding_model,
                    vector_dimension=EXCLUDED.vector_dimension,
                    manifest_sha256=EXCLUDED.manifest_sha256,
                    chunks_sha256=EXCLUDED.chunks_sha256,
                    embeddings_sha256=EXCLUDED.embeddings_sha256,
                    documents_count=EXCLUDED.documents_count,
                    chunks_count=EXCLUDED.chunks_count
                """,
                (
                    corpus_id,
                    manifest[0]["parser"],
                    embedding_metadata["model_name"],
                    embedding_metadata["dimension"],
                    _sha256(manifest_path),
                    chunk_file_sha256,
                    embedding_metadata["vectors_sha256"],
                    len(manifest),
                    sum(int(row["chunks"]) for row in manifest),
                ),
            )
            cursor.executemany(
                """
                INSERT INTO documents (
                    rcept_no, corp_code, corp_name, fiscal_year, report_type,
                    filed_at, status, collected_at, raw_sha256
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (rcept_no) DO UPDATE SET
                    corp_code=EXCLUDED.corp_code,
                    corp_name=EXCLUDED.corp_name,
                    fiscal_year=EXCLUDED.fiscal_year,
                    report_type=EXCLUDED.report_type,
                    filed_at=EXCLUDED.filed_at,
                    status=EXCLUDED.status,
                    collected_at=EXCLUDED.collected_at,
                    raw_sha256=EXCLUDED.raw_sha256
                """,
                document_rows,
            )

            cursor.execute(
                """
                CREATE TEMP TABLE stage_chunks (LIKE chunks INCLUDING DEFAULTS)
                ON COMMIT DROP
                """
            )
            columns = (
                "corpus_id,chunk_id,rcept_no,corp_code,corp_name,fiscal_year,"
                "chapter,section_path,note_no,table_title,ordinal,content,"
                "content_sha256,is_table,is_numeric_heavy,near_dup,superseded,"
                "bm25_tokens,embedding"
            )
            inserted = 0
            with cursor.copy(f"COPY stage_chunks ({columns}) FROM STDIN") as copy:
                for line_number, line in enumerate(
                    Path(chunks_path).open(encoding="utf-8"), start=1
                ):
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    index = indices.get(str(row["content_sha256"]))
                    if index is None:
                        if row.get("near_dup") or row.get("superseded"):
                            continue
                        raise VectorStoreError(
                            f"청크 {line_number}행의 임베딩이 없습니다"
                        )
                    copy.write_row(
                        (
                            corpus_id,
                            row["chunk_id"],
                            row["rcept_no"],
                            row["corp_code"],
                            row["corp_name"],
                            row["fiscal_year"],
                            row["chapter"],
                            row["section_path"],
                            row.get("note_no"),
                            row.get("table_title"),
                            row["ordinal"],
                            row["content"],
                            row["content_sha256"],
                            row["is_table"],
                            is_numeric_heavy(row["content"]),
                            row.get("near_dup", False),
                            row.get("superseded", False),
                            [],
                            vectors[index],
                        )
                    )
                    inserted += 1
            cursor.execute(
                """
                INSERT INTO chunks SELECT * FROM stage_chunks
                ON CONFLICT (corpus_id, chunk_id) DO UPDATE SET
                    rcept_no=EXCLUDED.rcept_no,
                    corp_code=EXCLUDED.corp_code,
                    corp_name=EXCLUDED.corp_name,
                    fiscal_year=EXCLUDED.fiscal_year,
                    chapter=EXCLUDED.chapter,
                    section_path=EXCLUDED.section_path,
                    note_no=EXCLUDED.note_no,
                    table_title=EXCLUDED.table_title,
                    ordinal=EXCLUDED.ordinal,
                    content=EXCLUDED.content,
                    content_sha256=EXCLUDED.content_sha256,
                    is_table=EXCLUDED.is_table,
                    is_numeric_heavy=EXCLUDED.is_numeric_heavy,
                    near_dup=EXCLUDED.near_dup,
                    superseded=EXCLUDED.superseded,
                    bm25_tokens=EXCLUDED.bm25_tokens,
                    embedding=EXCLUDED.embedding
                """
            )
            cursor.execute(
                "SELECT count(*) FROM chunks WHERE corpus_id = %s", (corpus_id,)
            )
            stored = int(cursor.fetchone()[0])
    return {
        "corpus_id": corpus_id,
        "documents": len(document_rows),
        "chunks_input": inserted,
        "chunks_stored": stored,
        "embedding_model": embedding_metadata["model_name"],
        "dimension": embedding_metadata["dimension"],
    }


class PgVectorBackend(RetrievalBackend):
    """회사·연도 필터를 SQL WHERE에서 먼저 적용하는 cosine **정확** 검색 백엔드.

    `chunks.embedding`에 HNSW 인덱스를 두지 않는다(ARCHITECTURE 결정 기록 11/7). 회사·연도 B-tree로
    좁힌 뒤 전수 계산하므로 결과가 메모리 백엔드(`InMemoryVectorBackend`)와 같다.

    연결은 둘 중 하나로 받는다: 서비스는 런타임 연결 풀(`pool`, Supabase 트랜잭션 풀러용
    `prepare_threshold=None`), 적재·비교 스크립트는 `database_url`(검색마다 새 연결).
    """

    def __init__(
        self,
        *,
        corpus_id: str,
        query_embedder: QueryEmbedder,
        retrieval_method: str,
        database_url: str | None = None,
        pool: Any = None,
        trace_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if (pool is None) == (not database_url):
            raise VectorStoreError("database_url과 pool 중 정확히 하나가 필요합니다")
        if not corpus_id:
            raise VectorStoreError("corpus_id가 필요합니다")
        if not retrieval_method:
            raise VectorStoreError("retrieval_method가 필요합니다")
        self.database_url = database_url
        self.pool = pool
        self.corpus_id = corpus_id
        self.query_embedder = query_embedder
        self.retrieval_method = retrieval_method
        self.trace_sink = trace_sink
        self.embedding_dimension = configured_embed_dim()

    def _connect(self):
        """풀이 있으면 빌리고, 없으면 새로 연다. 둘 다 with 문으로 쓰는 컨텍스트 매니저다."""

        if self.pool is not None:
            return self.pool.connection()
        try:
            import psycopg
        except ImportError as exc:
            raise VectorStoreError("psycopg Python 패키지가 필요합니다") from exc
        return psycopg.connect(self.database_url, prepare_threshold=None)

    def _fetchall(self, sql: str, params: list[Any]) -> list[tuple[Any, ...]]:
        # 런타임 풀은 dict_row라서, 위치로 읽는 이 클래스는 커서마다 tuple_row를 지정한다.
        from psycopg.rows import tuple_row

        with self._connect() as connection:
            with connection.cursor(row_factory=tuple_row) as cursor:
                cursor.execute(sql, params)
                return list(cursor.fetchall())

    def corpus_version(self) -> dict[str, Any] | None:
        """`corpus_versions`의 이 corpus_id 행. 없으면 None(적재 전)."""

        rows = self._fetchall(
            "SELECT embedding_model, vector_dimension, chunks_sha256, chunks_count "
            "FROM corpus_versions WHERE corpus_id = %s",
            [self.corpus_id],
        )
        if not rows:
            return None
        model, dimension, chunks_sha256, chunks_count = rows[0]
        return {
            "embedding_model": str(model),
            "vector_dimension": int(dimension),
            "chunks_sha256": str(chunks_sha256),
            "chunks_count": int(chunks_count),
        }

    def corp_codes(self) -> frozenset[str]:
        """이 corpus_id로 적재된 회사 코드(CorpNotIndexed 검사용)."""

        rows = self._fetchall(
            "SELECT DISTINCT corp_code FROM chunks WHERE corpus_id = %s", [self.corpus_id]
        )
        return frozenset(str(row[0]).strip() for row in rows)

    def _emit_search_trace(
        self,
        *,
        filters: SearchFilters,
        top_k: int,
        result_count: int,
    ) -> None:
        if self.trace_sink is None:
            return
        event = {
            "event": "pgvector_search",
            "corpus_id": self.corpus_id,
            "filters": {
                "corp_code": filters.corp_code,
                "fiscal_year": filters.fiscal_year,
                "chapter": filters.chapter,
                "note_no": filters.note_no,
            },
            "top_k": top_k,
            "result_count": result_count,
        }
        try:
            self.trace_sink(event)
        except Exception:
            logger.warning("pgvector 검색 trace 기록에 실패했습니다", exc_info=True)

    def search(
        self,
        question: str,
        *,
        filters: SearchFilters,
        top_k: int,
    ) -> list[ScoredChunk]:
        try:
            from pgvector import Vector
        except ImportError as exc:
            raise VectorStoreError("pgvector Python 패키지가 필요합니다") from exc

        vectors = np.asarray(
            self.query_embedder.encode_queries([question]), dtype=np.float32
        )
        if vectors.shape != (1, self.embedding_dimension):
            raise VectorStoreError(f"질의 벡터 shape 오류: {vectors.shape}")
        if not np.isfinite(vectors).all() or not np.allclose(
            np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5
        ):
            raise VectorStoreError("질의 벡터가 유한한 L2 정규화 벡터가 아닙니다")
        # register_vector는 연결마다 타입 조회를 4번 해서(풀에서 빌릴 때마다 왕복 증가) 텍스트로 보내고
        # SQL에서 ::vector로 바꾼다. float32 값은 텍스트에서 그대로 복원된다.
        query_vector = Vector(vectors[0]).to_text()

        where = [
            "corpus_id = %s",
            "corp_code = %s",
            "fiscal_year = %s",
            "near_dup = false",
            "superseded = false",
        ]
        params: list[Any] = [
            self.corpus_id,
            filters.corp_code,
            filters.fiscal_year,
        ]
        if filters.chapter is not None:
            where.append("chapter = %s")
            params.append(filters.chapter)
        if filters.note_no is not None:
            where.append("note_no = %s")
            params.append(filters.note_no)

        sql = f"""
            SELECT chunk_id, corpus_id, rcept_no, corp_code, corp_name,
                   fiscal_year, chapter, section_path, note_no, table_title,
                   ordinal, content, content_sha256,
                   1 - (embedding <=> %s::vector) AS score
            FROM chunks
            WHERE {' AND '.join(where)}
            ORDER BY embedding <=> %s::vector, chunk_id
            LIMIT %s
        """
        # 둘째 정렬 키 chunk_id: 본문이 같아 거리가 같은 청크가 LIMIT 경계에 걸려도
        # 메모리 백엔드와 같은 쪽(chunk_id 오름차순)을 남긴다.
        query_params = [query_vector, *params, query_vector, top_k]
        rows = self._fetchall(sql, query_params)

        ordered = sorted(rows, key=lambda row: (-float(row[13]), str(row[0])))
        results = [
            ScoredChunk(
                chunk_id=str(row[0]),
                corpus_id=str(row[1]),
                rcept_no=str(row[2]),
                corp_code=str(row[3]),
                corp_name=str(row[4]),
                fiscal_year=int(row[5]),
                chapter=str(row[6]),
                section_path=tuple(row[7]),
                note_no=str(row[8]) if row[8] is not None else None,
                table_title=str(row[9]) if row[9] is not None else None,
                ordinal=int(row[10]),
                content=str(row[11]),
                content_sha256=str(row[12]),
                score=float(row[13]),
                rank=rank,
                retrieval_method=self.retrieval_method,
            )
            for rank, row in enumerate(ordered, start=1)
        ]
        self._emit_search_trace(
            filters=filters,
            top_k=top_k,
            result_count=len(results),
        )
        return results


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="D2 pgvector 적재")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--chunks", required=True)
    parser.add_argument("--embedding-ids", required=True)
    parser.add_argument("--embedding-vectors", required=True)
    parser.add_argument("--embedding-metadata", required=True)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args(argv)
    result = load_pgvector(
        args.manifest,
        args.chunks,
        args.embedding_ids,
        args.embedding_vectors,
        args.embedding_metadata,
        batch_size=args.batch_size,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
