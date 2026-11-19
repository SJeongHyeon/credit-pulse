"""메모리 벡터 검색과 pgvector 정확 검색이 같은 결과를 내는지 비교한다(사전 등록 90번).

리랭커·임베딩 API를 부르지 않는다. 질의 벡터는 평가 때 저장한 캐시만 쓰고, 캐시에 없으면 멈춘다.
DB는 **로컬** pgvector를 쓴다(운영 Supabase 주소를 넣지 않는다). 주소는 저장소에 적지 않고 셸 환경변수로 준다.

  $env:COMPARE_DATABASE_URL = "postgresql://postgres:postgres@localhost:55432/postgres"
  python -X utf8 scripts/compare_vector_backends.py --load   # 스키마 적용 + 서비스 100사 적재 후 비교
  python -X utf8 scripts/compare_vector_backends.py          # 이미 적재돼 있으면 비교만

결과: evals/results/vector_backend_parity_v2.jsonl(문항별), .summary.json(요약·용량·환경)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from backend.rag.bm25 import create_tokenizer  # noqa: E402
from backend.rag.offline_eval import (  # noqa: E402
    CachedQueryEmbedder,
    InMemoryBM25Backend,
    InMemoryVectorBackend,
    RRFBackend,
    load_embedding_artifacts,
)
from backend.rag.retrieve import SearchFilters  # noqa: E402
from backend.rag.vector_store import PgVectorBackend, load_pgvector  # noqa: E402
from backend.retrieval_combo import get_combo  # noqa: E402

COMBO = get_combo("d3-55")
CHUNKS = Path("data/service/chunks.jsonl")
PREFIX = "data/service/embeddings"
MANIFEST = Path("data/experiments/b100-v9-fixed_window/manifest.jsonl")
DATASET = Path("evals/dataset_v2.jsonl")
FILTERS = Path("evals/filters_v2.jsonl")
QUERY_VECTORS = Path("evals/results/v2_query_vectors_oai_small.npz")
OUTPUT = Path("evals/results/vector_backend_parity_v2.jsonl")
TOP_K = 50  # d3-55 rrf_candidates(검색기별 후보 수)
SWAP_TOLERANCE = 1e-6  # 사전 등록 90번: 이보다 작은 점수 차의 자리바꿈만 허용


class NoApiEmbedder:
    """캐시에 없는 질의가 나오면 API를 부르지 않고 멈추게 하는 자리 채움."""

    dimension = COMBO.embedding_dim

    def encode_queries(self, texts: list[str]):
        raise RuntimeError(f"질의 벡터 캐시에 없는 질의 {len(texts)}개(API는 부르지 않는다)")


def _sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _load(url: str) -> dict[str, Any]:
    import psycopg

    from backend.api.limits import sql_statements

    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as connection:
        for name in ("01_init.sql", "02_schema.sql"):
            for statement in sql_statements(ROOT / "db" / name):
                connection.execute(statement)
    return load_pgvector(MANIFEST, CHUNKS, f"{PREFIX}.ids.jsonl", f"{PREFIX}.vectors.npy", f"{PREFIX}.metadata.json",
                         database_url=url)


def _db_facts(url: str, corpus_id: str) -> dict[str, Any]:
    import psycopg

    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as connection:
        def one(sql: str, params: tuple = ()) -> Any:
            return connection.execute(sql, params).fetchone()[0]

        indexes = [row[0] for row in connection.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'chunks' ORDER BY indexname").fetchall()]
        return {
            "pgvector_version": one("SELECT extversion FROM pg_extension WHERE extname = 'vector'"),
            "server_version": one("SHOW server_version"),
            "chunks_rows": one("SELECT count(*) FROM chunks WHERE corpus_id = %s", (corpus_id,)),
            "chunks_total_bytes": one("SELECT pg_total_relation_size('chunks')"),
            "chunks_indexes_bytes": one("SELECT pg_indexes_size('chunks')"),
            "database_bytes": one("SELECT pg_database_size(current_database())"),
            "chunks_indexes": indexes,
            "hnsw_index_present": "idx_chunks_embedding" in indexes,
        }


def _compare(memory: list[Any], pg: list[Any]) -> dict[str, Any]:
    memory_ids = [chunk.chunk_id for chunk in memory]
    pg_ids = [chunk.chunk_id for chunk in pg]
    memory_scores = {chunk.chunk_id: chunk.score for chunk in memory}
    pg_scores = {chunk.chunk_id: chunk.score for chunk in pg}
    common = set(memory_ids) & set(pg_ids)
    union = set(memory_ids) | set(pg_ids)
    # 자리가 다른 위치마다 그 자리 두 청크의 메모리 점수 차를 적는다(작으면 부동소수 차이로 본다).
    swaps = [
        {"rank": rank, "memory": a, "pg": b,
         "memory_score_gap": abs(memory_scores[a] - memory_scores.get(b, float("nan")))}
        for rank, (a, b) in enumerate(zip(memory_ids, pg_ids), start=1) if a != b
    ]
    return {
        "memory_count": len(memory_ids),
        "pg_count": len(pg_ids),
        "set_equal": set(memory_ids) == set(pg_ids),
        "jaccard": len(common) / len(union) if union else 1.0,
        "order_equal": memory_ids == pg_ids,
        "only_memory": sorted(set(memory_ids) - set(pg_ids)),
        "only_pg": sorted(set(pg_ids) - set(memory_ids)),
        "max_abs_score_diff": max((abs(memory_scores[cid] - pg_scores[cid]) for cid in common), default=0.0),
        "swaps": swaps,
        "swaps_within_tolerance": all(swap["memory_score_gap"] < SWAP_TOLERANCE for swap in swaps),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="메모리 vs pgvector 벡터 검색 비교(사전 등록 90번)")
    parser.add_argument("--load", action="store_true", help="비교 전에 스키마 적용 + 서비스 100사 적재")
    args = parser.parse_args(argv)
    url = os.getenv("COMPARE_DATABASE_URL", "").strip()
    if not url:
        print("COMPARE_DATABASE_URL(로컬 pgvector 주소)이 필요합니다", file=sys.stderr)
        return 2
    if "supabase" in url:
        print("운영 Supabase가 아니라 로컬 DB로 비교합니다", file=sys.stderr)
        return 2

    chunks = _read_jsonl(CHUNKS)
    corpus_ids = sorted({str(chunk["corpus_id"]) for chunk in chunks})
    if len(corpus_ids) != 1:
        print(f"chunks corpus_id가 하나가 아닙니다: {corpus_ids}", file=sys.stderr)
        return 1
    corpus_id = corpus_ids[0]
    load_result = _load(url) if args.load else None
    if load_result:
        print(f"적재: {json.dumps(load_result, ensure_ascii=False)}")

    embedder = CachedQueryEmbedder(NoApiEmbedder(), QUERY_VECTORS, model_name=COMBO.embedding_model)
    if not embedder.read_only:
        print(f"질의 벡터 캐시가 없습니다: {QUERY_VECTORS}", file=sys.stderr)
        return 1
    metadata, vectors_by_sha = load_embedding_artifacts(PREFIX)
    memory_backend = InMemoryVectorBackend(
        chunks=chunks, vectors_by_sha=vectors_by_sha, query_embedder=embedder,
        retrieval_method=f"offline_cosine:{COMBO.embedding_model}", input_header=metadata.get("input_header"),
    )
    pg_backend = PgVectorBackend(
        database_url=url, corpus_id=corpus_id, query_embedder=embedder,
        retrieval_method=f"pgvector_cosine:{COMBO.embedding_model}",
    )
    keyword_backend = InMemoryBM25Backend(
        chunks=chunks, tokenizer=create_tokenizer(COMBO.tokenizer), retrieval_method=f"bm25:{COMBO.tokenizer}"
    )

    def rrf(vector_backend: Any) -> RRFBackend:
        return RRFBackend(vector_backend=vector_backend, keyword_backend=keyword_backend, rrf_k=COMBO.rrf_k,
                          candidate_k=COMBO.rrf_candidates, retrieval_method="rrf")

    memory_rrf, pg_rrf = rrf(memory_backend), rrf(pg_backend)
    questions = {row["id"]: row["input"] for row in _read_jsonl(DATASET)}
    rows: list[dict[str, Any]] = []
    for item in _read_jsonl(FILTERS):
        filters = SearchFilters(corp_code=str(item["corp_code"]), fiscal_year=int(item["fiscal_year"]),
                                chapter=item.get("chapter"), note_no=item.get("note_no"))
        question = questions[item["id"]]
        vector = _compare(memory_backend.search(question, filters=filters, top_k=TOP_K),
                          pg_backend.search(question, filters=filters, top_k=TOP_K))
        fused = _compare(memory_rrf.search(question, filters=filters, top_k=TOP_K),
                         pg_rrf.search(question, filters=filters, top_k=TOP_K))
        rows.append({"id": item["id"], "corp_code": filters.corp_code, "fiscal_year": filters.fiscal_year,
                     "vector_top50": vector, "rrf_top50": {key: fused[key] for key in
                                                           ("set_equal", "order_equal", "only_memory", "only_pg")}})
        print(f"id={item['id']:>3} 벡터 집합={'같음' if vector['set_equal'] else '다름'} "
              f"순서={'같음' if vector['order_equal'] else '다름'} 최대점수차={vector['max_abs_score_diff']:.2e} "
              f"RRF 순서={'같음' if fused['order_equal'] else '다름'}")

    count = len(rows)
    summary = {
        "preregister_no": "90",
        "questions": count,
        "top_k": TOP_K,
        "vector_set_equal": sum(row["vector_top50"]["set_equal"] for row in rows),
        "vector_order_equal": sum(row["vector_top50"]["order_equal"] for row in rows),
        "vector_swaps_within_tolerance": sum(row["vector_top50"]["swaps_within_tolerance"] for row in rows),
        "vector_max_abs_score_diff": max(row["vector_top50"]["max_abs_score_diff"] for row in rows),
        "rrf_set_equal": sum(row["rrf_top50"]["set_equal"] for row in rows),
        "rrf_order_equal": sum(row["rrf_top50"]["order_equal"] for row in rows),
        "order_changed_ids": [row["id"] for row in rows if not row["vector_top50"]["order_equal"]],
        "set_changed_ids": [row["id"] for row in rows if not row["vector_top50"]["set_equal"]],
        "swap_tolerance": SWAP_TOLERANCE,
        "corpus_id": corpus_id,
        "chunks_sha256": _sha256(CHUNKS),
        "query_vectors_sha256": _sha256(QUERY_VECTORS),
        "embedding_model": COMBO.embedding_model,
        "load_result": load_result,
        "database": _db_facts(url, corpus_id),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    OUTPUT.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                                   encoding="utf-8")
    print(json.dumps({key: summary[key] for key in summary if key not in ("load_result",)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
