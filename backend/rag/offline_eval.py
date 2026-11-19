"""DB 없이 임베딩 모델을 비교하는 오프라인 검색 평가 실행기.

임베딩 산출물(``.ids.jsonl``·``.vectors.npy``·``.metadata.json``)과 chunks JSONL을
메모리에 올려, 평가셋 질문마다 회사·연도(선택: 장·주석) 필터를 먼저 건 뒤
코사인 유사도로 검색한다. 결과는 ``evaluation.py``가 읽는 원시 결과 형식으로
쓰고 Recall@k·MRR까지 계산한다.

필터를 검색 전에 거는 규칙(SPEC §5.2)과 ``(score DESC, chunk_id ASC)`` 정렬은
pgvector 백엔드와 같다. 검색 결과는 ``retrieve()`` 계약 검증을 그대로 통과해야 한다.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol, Sequence

import numpy as np
from dotenv import find_dotenv, load_dotenv

from backend.rag.bm25 import (
    DEFAULT_RRF_K,
    BM25Error,
    BM25Index,
    Tokenizer,
    create_tokenizer,
    rrf_fuse,
)
from backend.rag.bm25_tokens import content_key
from backend.rag.embedding import embedding_key
from backend.rag.evaluation import (
    _span_rcept_nos,
    compute_retrieval_metrics,
    evaluate_files,
    load_chunk_contents,
)
from backend.rag.neighbors import (
    InMemoryNeighborLookup,
    budget_n_by_chars,
    expand_neighbors,
    expansion_metrics,
)
from backend.rag.rerank import (
    DEFAULT_RERANK_BATCH_SIZE,
    Reranker,
    RerankError,
    latency_summary,
    rerank_candidates,
    rerank_document_with_header,
    reranked_chunk_ids,
)
from backend.rag.retrieve import (
    RetrievalBackend,
    ScoredChunk,
    SearchFilters,
    retrieve,
)


DEFAULT_RRF_CANDIDATES = 50
MAX_RETRIEVE_TOP_K = 100  # retrieve() 계약의 top_k 상한
REFERENCE_CANDIDATE_N = 30  # Spec: 후보 50개 결과에서 재계산 없이 뽑는 참고 값
MAX_BASELINE_CHUNKS = 100  # backend/rag/retrieve.py의 top_k 상한
VOYAGE_PRICE_PER_MILLION_TOKENS = {"rerank-3": 0.05, "rerank-2.5": 0.05}
VOYAGE_PRICING_SOURCE = "https://docs.voyageai.com/docs/pricing"
VOYAGE_PRICING_CHECKED_AT = "2026-11-03"
TEAM_API_BUDGET_USD = 30.0


class OfflineEvalError(ValueError):
    """오프라인 평가 입력이 계약과 다를 때 발생한다."""


class QueryEmbedder(Protocol):
    dimension: int

    def encode_queries(self, texts: list[str]) -> np.ndarray: ...


class CachedQueryEmbedder:
    """질의 벡터를 파일에 저장해 재측정에서 그대로 다시 쓴다.

    API 임베딩은 실행 시각에 따라 질의 벡터가 달라질 수 있어(EVAL_REPORT 5장 재현성 관찰)
    같은 설정을 다시 재도 후보가 바뀐다. 파일이 없으면 임베딩하고 저장한다(만들기). 파일이
    있으면 저장된 벡터만 쓰고, 없는 질의는 API를 부르지 않고 오류로 멈춘다(재사용).
    키는 sha256(모델 이름 + 줄바꿈 + 질의 문장)이다.
    """

    def __init__(self, embedder: QueryEmbedder, path: str | Path, *, model_name: str) -> None:
        self.embedder = embedder
        self.dimension = embedder.dimension
        self.path = Path(path)
        self.model_name = model_name
        self.read_only = self.path.exists()
        self.vectors: dict[str, np.ndarray] = {}
        if self.read_only:
            with np.load(self.path, allow_pickle=False) as stored:
                keys, vectors = stored["keys"], stored["vectors"]
            if vectors.ndim != 2 or vectors.shape[0] != keys.shape[0]:
                raise OfflineEvalError(f"질의 벡터 파일 형식 오류: {self.path}")
            self.vectors = {str(key): vectors[i] for i, key in enumerate(keys)}

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model_name}\n{text}".encode("utf-8")).hexdigest()

    def encode_queries(self, texts: list[str]) -> np.ndarray:
        keys = [self._key(text) for text in texts]
        missing = [text for text, key in zip(texts, keys) if key not in self.vectors]
        if missing and self.read_only:
            raise OfflineEvalError(
                f"질의 벡터 파일에 없는 질의 {len(missing)}개(재사용 모드라 새로 임베딩하지 않음): {self.path}"
            )
        if missing:
            fresh = np.asarray(self.embedder.encode_queries(missing), dtype=np.float32)
            for text, vector in zip(missing, fresh):
                self.vectors[self._key(text)] = vector
            self._save()
        return np.stack([self.vectors[key] for key in keys]).astype(np.float32)

    def _save(self) -> None:
        keys = sorted(self.vectors)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp.npz")
        np.savez(temporary, keys=np.array(keys), vectors=np.stack([self.vectors[k] for k in keys]))
        temporary.replace(self.path)


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(Path(path).open(encoding="utf-8"), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise OfflineEvalError(f"{path} {line_number}행 JSON 오류") from exc
    return rows


def load_embedding_artifacts(embedding_prefix: str | Path) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """완료된 임베딩 산출물을 검증하고 content_sha256 → 벡터 사전을 만든다."""

    prefix = Path(embedding_prefix)
    ids_path = Path(f"{prefix}.ids.jsonl")
    vectors_path = Path(f"{prefix}.vectors.npy")
    metadata_path = Path(f"{prefix}.metadata.json")
    for path in (ids_path, vectors_path, metadata_path):
        if not path.exists():
            raise OfflineEvalError(f"임베딩 산출물이 없습니다: {path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not metadata.get("complete"):
        raise OfflineEvalError("임베딩 metadata가 완료 상태가 아닙니다")
    if metadata.get("ids_sha256") != _sha256(ids_path):
        raise OfflineEvalError("ids SHA-256이 metadata와 다릅니다")
    if metadata.get("vectors_sha256") != _sha256(vectors_path):
        raise OfflineEvalError("vectors SHA-256이 metadata와 다릅니다")
    vectors = np.load(vectors_path, mmap_mode="r")
    ids = _read_jsonl(ids_path)
    if vectors.shape != (len(ids), int(metadata["dimension"])):
        raise OfflineEvalError("벡터 shape가 ids·metadata와 다릅니다")
    key_field = "embedding_key" if metadata.get("input_header") else "content_sha256"
    by_sha: dict[str, np.ndarray] = {}
    for row in ids:
        by_sha[str(row[key_field])] = np.asarray(
            vectors[int(row["index"])], dtype=np.float32
        )
    return metadata, by_sha


def _searchable_chunks(chunks: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        chunk
        for chunk in chunks
        if not chunk.get("near_dup") and not chunk.get("superseded")
    ]


def _chunks_matching(
    chunks: Sequence[dict[str, Any]], filters: SearchFilters
) -> list[dict[str, Any]]:
    return [
        chunk
        for chunk in chunks
        if str(chunk.get("corp_code")) == filters.corp_code
        and int(chunk.get("fiscal_year", 0)) == filters.fiscal_year
        and (filters.chapter is None or chunk.get("chapter") == filters.chapter)
        and (filters.note_no is None or chunk.get("note_no") == filters.note_no)
    ]


def _scored_chunks(
    ordered: Iterable[tuple[dict[str, Any], float]], retrieval_method: str
) -> list[ScoredChunk]:
    return [
        ScoredChunk(
            chunk_id=str(chunk["chunk_id"]),
            corpus_id=str(chunk["corpus_id"]),
            rcept_no=str(chunk["rcept_no"]),
            corp_code=str(chunk["corp_code"]),
            corp_name=str(chunk.get("corp_name", "")),
            fiscal_year=int(chunk["fiscal_year"]),
            chapter=str(chunk["chapter"]),
            section_path=tuple(chunk.get("section_path") or ()),
            note_no=chunk.get("note_no"),
            table_title=chunk.get("table_title"),
            ordinal=int(chunk["ordinal"]),
            content=str(chunk["content"]),
            content_sha256=str(chunk["content_sha256"]),
            score=float(score),
            rank=rank,
            retrieval_method=retrieval_method,
        )
        for rank, (chunk, score) in enumerate(ordered, start=1)
    ]


class InMemoryVectorBackend(RetrievalBackend):
    """chunks JSONL과 임베딩 산출물로 만든 메모리 코사인 검색 백엔드."""

    def __init__(
        self,
        *,
        chunks: Sequence[dict[str, Any]],
        vectors_by_sha: dict[str, np.ndarray],
        query_embedder: QueryEmbedder,
        retrieval_method: str,
        input_header: str | None = None,
    ) -> None:
        self.chunks = _searchable_chunks(chunks)
        self.vectors_by_sha = vectors_by_sha
        self.query_embedder = query_embedder
        self.retrieval_method = retrieval_method
        self.input_header = input_header

    def _vector_key(self, chunk: dict[str, Any]) -> str:
        return embedding_key(chunk, self.input_header)

    def _candidates(self, filters: SearchFilters) -> list[dict[str, Any]]:
        return _chunks_matching(self.chunks, filters)

    def search(
        self,
        question: str,
        *,
        filters: SearchFilters,
        top_k: int,
    ) -> list[ScoredChunk]:
        candidates = self._candidates(filters)
        if not candidates:
            return []
        missing = sorted(
            {self._vector_key(c) for c in candidates}
            - self.vectors_by_sha.keys()
        )
        if missing:
            raise OfflineEvalError(
                f"corp_code={filters.corp_code} 청크 {len(missing)}개의 벡터가 없습니다. "
                "임베딩할 때 이 회사를 --corp-codes에 넣었는지 확인하세요"
            )
        query = np.asarray(self.query_embedder.encode_queries([question]), dtype=np.float32)
        if query.ndim != 2 or query.shape[0] != 1:
            raise OfflineEvalError(f"질의 벡터 shape 오류: {query.shape}")
        if not np.isfinite(query).all() or not np.allclose(
            np.linalg.norm(query, axis=1), 1.0, atol=1e-5
        ):
            raise OfflineEvalError("질의 벡터가 유한한 L2 정규화 벡터가 아닙니다")
        matrix = np.stack([self.vectors_by_sha[self._vector_key(c)] for c in candidates])
        if matrix.shape[1] != query.shape[1]:
            raise OfflineEvalError("질의 벡터와 문서 벡터의 차원이 다릅니다")
        scores = matrix @ query[0]
        ordered = sorted(
            zip(candidates, scores.tolist()),
            key=lambda pair: (-pair[1], str(pair[0]["chunk_id"])),
        )[:top_k]
        return _scored_chunks(ordered, self.retrieval_method)


class InMemoryBM25Backend(RetrievalBackend):
    """필터로 좁힌 청크 안에서 BM25로 검색한다. IDF도 그 범위에서 계산한다."""

    def __init__(
        self,
        *,
        chunks: Sequence[dict[str, Any]],
        tokenizer: Tokenizer,
        retrieval_method: str,
        prebuilt_tokens: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self.chunks = _searchable_chunks(chunks)
        self.tokenizer = tokenizer
        self.retrieval_method = retrieval_method
        # 99번: 본문 sha256 → 미리 계산한 토큰(backend/rag/bm25_tokens.py). None이면 이전과 같다.
        self.prebuilt_tokens = prebuilt_tokens
        self._indexes: dict[SearchFilters, tuple[list[dict[str, Any]], BM25Index]] = {}

    def _chunk_tokens(self, chunk: dict[str, Any]) -> Sequence[str]:
        text = str(chunk["content"])
        if self.prebuilt_tokens is not None:
            tokens = self.prebuilt_tokens.get(content_key(text))
            if tokens is not None:
                return tokens
        # 표가 없거나 표에 없는 청크만 그 자리에서 토큰화한다.
        # 141번: 지시형 문장과 표를 뒤집는 주장을 뺀 글로 토큰화한다(공격 문장이 키워드 점수를 바꾸지 못하게).
        # 미리 만든 표는 원문 그대로의 토큰이지만 실제 코퍼스에는 걸리는 문장이 0건이라 같다.
        from backend.graph.trust_boundary import sanitized_for_index

        return self.tokenizer(sanitized_for_index(text))

    def _index_for(self, filters: SearchFilters) -> tuple[list[dict[str, Any]], BM25Index]:
        if filters not in self._indexes:
            candidates = _chunks_matching(self.chunks, filters)
            index = BM25Index([self._chunk_tokens(chunk) for chunk in candidates])
            self._indexes[filters] = (candidates, index)
        return self._indexes[filters]

    def search(
        self,
        question: str,
        *,
        filters: SearchFilters,
        top_k: int,
    ) -> list[ScoredChunk]:
        candidates, index = self._index_for(filters)
        # 토큰이 없거나 후보가 없으면 맞는 청크도 없어 빈 결과가 된다.
        matched = [
            (chunk, score)
            for chunk, score in zip(candidates, index.score(self.tokenizer(question)))
            if score > 0
        ]
        ordered = sorted(matched, key=lambda pair: (-pair[1], str(pair[0]["chunk_id"])))[:top_k]
        return _scored_chunks(ordered, self.retrieval_method)


class RRFBackend(RetrievalBackend):
    """벡터와 키워드 검색의 상위 후보를 RRF로 합친다."""

    def __init__(
        self,
        *,
        vector_backend: RetrievalBackend,
        keyword_backend: RetrievalBackend,
        rrf_k: int,
        candidate_k: int,
        retrieval_method: str,
    ) -> None:
        if rrf_k < 1:
            raise BM25Error("RRF k는 1 이상이어야 합니다")
        if not 1 <= candidate_k <= MAX_RETRIEVE_TOP_K:
            raise BM25Error(f"RRF 후보 수는 1~{MAX_RETRIEVE_TOP_K}이어야 합니다")
        self.vector_backend = vector_backend
        self.keyword_backend = keyword_backend
        self.rrf_k = rrf_k
        self.candidate_k = candidate_k
        self.retrieval_method = retrieval_method

    @property
    def chunks(self) -> list[dict[str, Any]]:
        """재정렬이 후보 본문을 찾을 수 있게 벡터 백엔드의 청크 목록을 그대로 보여 준다."""

        return self.vector_backend.chunks  # type: ignore[attr-defined]

    def search(
        self,
        question: str,
        *,
        filters: SearchFilters,
        top_k: int,
    ) -> list[ScoredChunk]:
        if top_k > self.candidate_k:
            raise BM25Error("top_k는 RRF 후보 수보다 클 수 없습니다")
        rankings = [
            backend.search(question, filters=filters, top_k=self.candidate_k)
            for backend in (self.vector_backend, self.keyword_backend)
        ]
        by_id = {chunk.chunk_id: chunk for ranking in rankings for chunk in ranking}
        fused = rrf_fuse(
            [[chunk.chunk_id for chunk in ranking] for ranking in rankings],
            k=self.rrf_k,
        )
        return [
            dataclasses.replace(
                by_id[chunk_id],
                score=score,
                rank=rank,
                retrieval_method=self.retrieval_method,
            )
            for rank, (chunk_id, score) in enumerate(fused[:top_k], start=1)
        ]


def _filters_for(row: dict[str, Any], filters_by_id: dict[int, dict[str, Any]]) -> SearchFilters | None:
    raw = row.get("filters") or filters_by_id.get(int(row["id"]))
    if not raw:
        return None
    return SearchFilters(
        corp_code=str(raw["corp_code"]),
        fiscal_year=int(raw["fiscal_year"]),
        chapter=raw.get("chapter"),
        note_no=raw.get("note_no"),
    )


def run_retrieval(
    dataset_rows: Sequence[dict[str, Any]],
    backend: RetrievalBackend,
    *,
    top_k: int = 10,
    filters_by_id: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """평가셋 질문마다 검색해 evaluation.py 원시 결과 행을 만든다.

    필터는 문항의 ``filters`` 필드나 ``filters_by_id``에서 읽는다. 관련 문항에
    필터가 없으면 오류다. 필터가 없는 비관련 문항은 빈 결과로 둔다(Recall·MRR
    계산에서 비관련 문항은 쓰지 않는다).
    """

    filters_by_id = filters_by_id or {}
    results: list[dict[str, Any]] = []
    for row in dataset_rows:
        row_id = int(row["id"])
        filters = _filters_for(row, filters_by_id)
        if filters is None:
            if row.get("kind") == "relevant":
                raise OfflineEvalError(f"관련 문항 id={row_id}에 검색 필터(corp_code, fiscal_year)가 없습니다")
            results.append({"id": row_id, "retrieved_chunks": []})
            continue
        found = retrieve(str(row["input"]), filters=filters, top_k=top_k, backend=backend)
        results.append({"id": row_id, "retrieved_chunks": [c["chunk_id"] for c in found]})
    return results


def run_reranked_retrieval(
    dataset_rows: Sequence[dict[str, Any]],
    backend: InMemoryVectorBackend,
    reranker: Reranker,
    *,
    top_k: int,
    candidate_k: int,
    filters_by_id: dict[int, dict[str, Any]] | None = None,
    clock: Callable[[], float] = time.perf_counter,
    rerank_instruction: str | None = None,
    subqueries_by_id: dict[int, list[str]] | None = None,
    rerank_header: bool = False,
    second_reranker: Reranker | None = None,
    second_k: int = 10,
) -> list[dict[str, Any]]:
    """1차 상위 candidate_k개를 재정렬해 top_k를 남기고 후보 전체의 원시 점수를 기록한다.

    second_reranker가 있으면 첫 리랭커 상위 second_k개만 두 번째 리랭커로 다시 정렬한다
    (예: 빠른 Voyage로 50→10을 고르고 LLM이 10개의 순서만 정함).

    rerank_header가 참이면 리랭커에 넘기는 문서 앞에만 '회사명 | 절 경로' 머리말을 붙인다
    (1차 검색과 임베딩은 그대로).

    rerank_instruction이 있으면 리랭커에 넘기는 질의 앞에만 붙인다(1차 검색 질의는 그대로).
    subqueries_by_id에 하위 질의가 2개 이상인 문항은 하위 질의마다 따로 검색·재정렬하고
    (각자 자기 몫의 후보 candidate_k개), 하위 질의별 순위를 한 칸씩 번갈아 담아 top_k를 채운다
    (2주19강 질의 분리). 하위 질의가 1개이거나 없으면 원래 질문 그대로 처리한다.
    """

    if top_k > candidate_k:
        raise RerankError("top_k는 candidate_k보다 클 수 없습니다")
    if second_reranker is not None and not top_k <= second_k <= candidate_k:
        raise RerankError("2단계는 top_k ≤ second_k ≤ candidate_k 이어야 합니다")
    filters_by_id = filters_by_id or {}
    chunks_by_id = {str(chunk["chunk_id"]): chunk for chunk in backend.chunks}
    results: list[dict[str, Any]] = []
    for row in dataset_rows:
        row_id = int(row["id"])
        filters = _filters_for(row, filters_by_id)
        if filters is None:
            if row.get("kind") == "relevant":
                raise OfflineEvalError(f"관련 문항 id={row_id}에 검색 필터(corp_code, fiscal_year)가 없습니다")
            results.append({"id": row_id, "retrieved_chunks": []})
            continue
        query = str(row["input"])
        subqueries = (subqueries_by_id or {}).get(row_id) or []
        if len(subqueries) >= 2:
            results.append(
                _decomposed_row(
                    row_id, subqueries, filters, backend, reranker, chunks_by_id,
                    top_k=top_k, candidate_k=candidate_k, clock=clock,
                )
            )
            continue
        found = retrieve(query, filters=filters, top_k=candidate_k, backend=backend)
        candidates = [chunks_by_id[str(item["chunk_id"])] for item in found]
        started = clock()
        rerank_query = f"{rerank_instruction}\n{query}" if rerank_instruction else query
        document_text = rerank_document_with_header if rerank_header else None
        ranked = rerank_candidates(rerank_query, candidates, reranker, document_text=document_text)
        retrieved = reranked_chunk_ids(ranked, top_k)
        second: dict[str, Any] | None = None
        if second_reranker is not None:
            # 2단계: 1단계 상위 second_k개의 순서만 다시 매긴다. 집합이 같으므로 top_k ≤ second_k이면
            # Hit·Recall은 1단계와 같고 순서(MRR·nDCG)만 바뀐다.
            head = [chunks_by_id[item["chunk_id"]] for item in ranked[:second_k]]
            second_ranked = rerank_candidates(rerank_query, head, second_reranker, document_text=document_text)
            order = [item["chunk_id"] for item in second_ranked]
            order += [item["chunk_id"] for item in ranked[second_k:]]
            retrieved = order[:top_k]
            second = {"model": second_reranker.model_name, "second_k": second_k, "candidates": second_ranked}
        latency_ms = (clock() - started) * 1000  # 2단계가 있으면 두 단계 합
        results.append(
            {
                "id": row_id,
                "retrieved_chunks": retrieved,
                "rerank": {
                    "model": reranker.model_name,
                    "candidate_k": candidate_k,
                    "latency_ms": latency_ms,
                    "candidates": ranked,
                    **({"instruction": rerank_instruction} if rerank_instruction else {}),
                    **({"second_stage": second} if second else {}),
                },
            }
        )
    return results


def _decomposed_row(
    row_id: int,
    subqueries: Sequence[str],
    filters: SearchFilters,
    backend: Any,
    reranker: Reranker,
    chunks_by_id: dict[str, dict[str, Any]],
    *,
    top_k: int,
    candidate_k: int,
    clock: Callable[[], float],
) -> dict[str, Any]:
    """하위 질의마다 검색·재정렬하고 순위를 번갈아 합친다. 지연은 재정렬 시간의 합이다."""

    per_query: list[dict[str, Any]] = []
    latency_ms = 0.0
    for sub in subqueries:
        found = retrieve(str(sub), filters=filters, top_k=candidate_k, backend=backend)
        candidates = [chunks_by_id[str(item["chunk_id"])] for item in found]
        started = clock()
        ranked = rerank_candidates(str(sub), candidates, reranker)
        latency_ms += (clock() - started) * 1000
        per_query.append({"query": str(sub), "candidates": ranked})
    merged: list[str] = []
    depth = 0
    while len(merged) < top_k and any(depth < len(q["candidates"]) for q in per_query):
        for q in per_query:
            if depth < len(q["candidates"]):
                chunk_id = q["candidates"][depth]["chunk_id"]
                if chunk_id not in merged and len(merged) < top_k:
                    merged.append(chunk_id)
        depth += 1
    # 참고 지표 계산이 같은 형식을 읽도록, 합친 순서를 점수(높을수록 앞)로 적는다.
    merged_candidates = [
        {"chunk_id": chunk_id, "first_stage_rank": rank, "score": float(-rank)}
        for rank, chunk_id in enumerate(merged, start=1)
    ]
    return {
        "id": row_id,
        "retrieved_chunks": merged,
        "rerank": {
            "model": reranker.model_name,
            "candidate_k": candidate_k,
            "latency_ms": latency_ms,
            "candidates": merged_candidates,
            "subqueries": per_query,
        },
    }


def _span_chunks_path(dataset_path: str | Path, chunks_path: str | Path) -> str | Path | None:
    """평가셋에 gold_spans(v2 span 정본)가 있으면 검색에 쓴 청크 파일로 정답 청크를 다시 만든다.

    gold_spans가 없는 v1은 None을 돌려줘 예전 채점과 바이트 단위로 같게 둔다.
    """

    return chunks_path if any(row.get("gold_spans") for row in _read_jsonl(dataset_path)) else None


def _write_rerank_metrics(
    dataset_path: str | Path,
    results_path: Path,
    results: Sequence[dict[str, Any]],
    *,
    top_k: int,
    candidate_k: int,
    model_name: str,
    rerank_instruction: str | None = None,
    chunks_path: str | Path | None = None,
    retrieval_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """기존 지표에 지연 시간과 후보 30개 참고 결과를 더한다."""

    metrics_path = results_path.with_suffix(".metrics.json")
    payload = evaluate_files(dataset_path, results_path, metrics_path, k=top_k, chunks_path=chunks_path)
    scored = [row for row in results if "rerank" in row]
    narrowed = [
        {
            "id": row["id"],
            "retrieved_chunks": (
                reranked_chunk_ids(row["rerank"]["candidates"], top_k, candidate_n=REFERENCE_CANDIDATE_N)
                if "rerank" in row
                else []
            ),
        }
        for row in results
    ]
    dataset_rows = _read_jsonl(dataset_path)
    chunks_by_rcept = (
        load_chunk_contents(chunks_path, set().union(*(_span_rcept_nos(r) for r in dataset_rows)))
        if chunks_path is not None
        else None
    )
    reference = compute_retrieval_metrics(dataset_rows, narrowed, k=top_k, chunks_by_rcept=chunks_by_rcept)
    payload.update(
        {
            "reranker": model_name,
            "candidate_k": candidate_k,
            "latency": latency_summary([row["rerank"]["latency_ms"] for row in scored]),
            f"reference_candidate_{REFERENCE_CANDIDATE_N}": {
                key: value for key, value in reference.items() if key != "per_question"
            },
        }
    )
    if rerank_instruction:
        payload["rerank_instruction"] = rerank_instruction
    if retrieval_settings:  # 리랭커 경로에도 1차 검색 설정을 남긴다(재현 근거, P17 R8)
        payload.update(retrieval_settings)
    metrics_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return payload


def run_expanded_retrieval(
    dataset_rows: Sequence[dict[str, Any]],
    backend: InMemoryVectorBackend,
    *,
    seed_k: int,
    before: int,
    after: int,
    filters_by_id: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """seed 상위 k개를 이웃으로 확장하고, 같은 예산의 베이스라인 목록을 함께 남긴다."""

    filters_by_id = filters_by_id or {}
    chunks_by_id = {str(chunk["chunk_id"]): chunk for chunk in backend.chunks}
    lookup = InMemoryNeighborLookup(backend.chunks)
    results: list[dict[str, Any]] = []
    for row in dataset_rows:
        row_id = int(row["id"])
        filters = _filters_for(row, filters_by_id)
        if filters is None:
            if row.get("kind") == "relevant":
                raise OfflineEvalError(f"관련 문항 id={row_id}에 검색 필터(corp_code, fiscal_year)가 없습니다")
            results.append({"id": row_id, "retrieved_chunks": []})
            continue
        found = retrieve(
            str(row["input"]), filters=filters, top_k=MAX_BASELINE_CHUNKS, backend=backend
        )
        ranked = [chunks_by_id[str(item["chunk_id"])] for item in found]
        items = expand_neighbors(
            ranked[:seed_k], lookup, before=before, after=after, filters=filters
        )
        total_chars = sum(len(str(item.chunk["content"])) for item in items)
        by_chars_n = budget_n_by_chars([len(str(chunk["content"])) for chunk in ranked], total_chars)
        if by_chars_n == MAX_BASELINE_CHUNKS:
            raise OfflineEvalError(
                f"id={row_id}의 글자 예산이 검색 계약 상한 100개를 넘을 수 있습니다"
            )
        by_chunks_n = min(len(items), len(ranked))
        results.append(
            {
                "id": row_id,
                "retrieved_chunks": [str(item.chunk["chunk_id"]) for item in items],
                "expansion": {
                    "seed_k": seed_k,
                    "before": before,
                    "after": after,
                    "items": [
                        {
                            "chunk_id": str(item.chunk["chunk_id"]),
                            "seed_rank": item.seed_rank,
                            "is_seed": item.is_seed,
                        }
                        for item in items
                    ],
                    "total_chunks": len(items),
                    "total_chars": total_chars,
                },
                "baseline_budget": {"by_chars_n": by_chars_n, "by_chunks_n": by_chunks_n},
                "baseline_chunks": [
                    str(chunk["chunk_id"]) for chunk in ranked[: max(by_chars_n, by_chunks_n)]
                ],
            }
        )
    return results


def _write_expansion_metrics(
    dataset_path: str | Path,
    results_path: Path,
    output_path: Path,
    *,
    seed_k: int,
    before: int,
    after: int,
) -> dict[str, Any]:
    payload = {
        "dataset_sha256": _sha256(dataset_path),
        "results_sha256": _sha256(results_path),
        "seed_k": seed_k,
        "before": before,
        "after": after,
        **expansion_metrics(_read_jsonl(dataset_path), _read_jsonl(results_path)),
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return payload


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def estimate_voyage_rerank_run(
    dataset_rows: Sequence[dict[str, Any]],
    chunks: Sequence[dict[str, Any]],
    *,
    model_name: str,
    candidate_k: int,
    filters_by_id: dict[int, dict[str, Any]] | None = None,
    token_counts: Callable[[list[str]], list[int]],
) -> dict[str, Any]:
    """Voyage 호출 없이 후보별 토큰 상한과 비용을 보수적으로 계산한다.

    검색을 실행하면 API 임베딩 질의가 발생할 수 있으므로, 각 필터 안에서 가장 긴
    candidate_k개를 사용한다. 실제 검색 후보의 비용은 이 값 이하이다.
    """

    api_model = model_name.removeprefix("voyage/")
    if api_model not in VOYAGE_PRICE_PER_MILLION_TOKENS:
        raise OfflineEvalError(f"dry-run을 지원하지 않는 Voyage 모델입니다: {model_name}")
    if candidate_k < 1:
        raise OfflineEvalError("candidate_k는 1 이상이어야 합니다")
    filters_by_id = filters_by_id or {}
    searchable = _searchable_chunks(chunks)
    total_tokens = 0
    requests = 0
    pairs = 0
    for row in dataset_rows:
        filters = _filters_for(row, filters_by_id)
        if filters is None:
            if row.get("kind") == "relevant":
                raise OfflineEvalError(f"관련 문항 id={row['id']}에 검색 필터가 없습니다")
            continue
        candidates = _chunks_matching(searchable, filters)
        if not candidates:
            continue
        document_token_counts = token_counts(
            [str(chunk["content"]) for chunk in candidates]
        )
        if len(document_token_counts) != len(candidates):
            raise OfflineEvalError("Voyage tokenizer 응답 개수가 청크와 다릅니다")
        kept_token_counts = sorted(document_token_counts, reverse=True)[:candidate_k]
        query_tokens = token_counts([str(row["input"])])[0]
        total_tokens += query_tokens * len(kept_token_counts) + sum(kept_token_counts)
        requests += 1
        pairs += len(kept_token_counts)
    price = VOYAGE_PRICE_PER_MILLION_TOKENS[api_model]
    estimated_cost = total_tokens * price / 1_000_000
    return {
        "dry_run": True,
        "provider": "voyage",
        "model": api_model,
        "requests": requests,
        "query_document_pairs": pairs,
        "estimated_tokens_upper_bound": total_tokens,
        "price_usd_per_million_tokens": price,
        "estimated_cost_usd_upper_bound": round(estimated_cost, 6),
        "free_credits_applied": False,
        "team_budget_usd": TEAM_API_BUDGET_USD,
        "within_team_budget": estimated_cost <= TEAM_API_BUDGET_USD,
        "estimate_method": "highest_token_count_filtered_candidates_upper_bound",
        "pricing_source": VOYAGE_PRICING_SOURCE,
        "pricing_checked_at": VOYAGE_PRICING_CHECKED_AT,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DB 없이 임베딩 모델별 Recall@k·MRR 비교")
    parser.add_argument("--dataset", required=True, help="동결한 평가셋 JSONL")
    parser.add_argument("--chunks", required=True, help="chunks JSONL (본문·메타데이터)")
    parser.add_argument("--embedding-prefix", help="embedding build의 --output-prefix (vector·rrf에 필요)")
    parser.add_argument("--filters", help="문항 id별 검색 필터 JSONL ({id, corp_code, fiscal_year, chapter?})")
    parser.add_argument("--output", help="원시 결과 JSONL 경로 (지표는 .metrics.json)")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--retriever", choices=("vector", "bm25", "rrf"), default="vector")
    parser.add_argument("--tokenizer", choices=("space", "bigram", "kiwi"), default="bigram")
    parser.add_argument("--rrf-k", type=int, default=DEFAULT_RRF_K)
    parser.add_argument(
        "--rrf-candidates", type=int, default=DEFAULT_RRF_CANDIDATES, help="RRF에 넣을 검색기별 후보 수"
    )
    parser.add_argument(
        "--query-vectors",
        help="질의 벡터 파일(.npz). 없으면 임베딩해 저장하고, 있으면 저장된 벡터만 쓴다(없는 질의는 오류)",
    )
    parser.add_argument("--reranker", help="1차 후보를 재정렬할 리랭커 모델")
    parser.add_argument("--candidate-k", type=int, default=50, help="재정렬할 1차 후보 수")
    parser.add_argument("--rerank-instruction", help="리랭커 질의 앞에만 붙일 지시문(1차 검색 질의는 그대로)")
    parser.add_argument("--second-reranker", help="첫 리랭커 상위 --second-k개의 순서만 다시 정할 2단계 리랭커(예: llm/openai/gpt-4.1-mini)")
    parser.add_argument("--second-k", type=int, default=10, help="2단계 리랭커가 다시 볼 개수(top_k ≤ second_k ≤ candidate_k)")
    parser.add_argument(
        "--rerank-header",
        action="store_true",
        help="리랭커에 넘기는 문서 앞에만 '회사명 | 절 경로' 머리말을 붙인다(임베딩·1차 검색은 그대로)",
    )
    parser.add_argument("--subqueries", help="문항별 하위 질의 JSONL({id, subqueries}). 리랭커와 함께 쓴다")
    parser.add_argument(
        "--rerank-batch-size",
        type=int,
        default=DEFAULT_RERANK_BATCH_SIZE,
        help="리랭커가 한 번에 처리할 후보 수",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Voyage 호출 없이 토큰 수와 예상 비용 상한만 출력",
    )
    parser.add_argument("--expand-before", type=int, default=0, help="seed 앞쪽 이웃 수")
    parser.add_argument("--expand-after", type=int, default=0, help="seed 뒤쪽 이웃 수")
    args = parser.parse_args(argv)
    # 문서 명령은 저장소 루트에서 실행한다. 이미 설정된 환경변수는 덮어쓰지 않는다.
    load_dotenv(find_dotenv(usecwd=True), override=False)
    filters_by_id = (
        {int(row["id"]): row for row in _read_jsonl(args.filters)} if args.filters else {}
    )
    if args.dry_run:
        if not args.reranker or not args.reranker.startswith("voyage/"):
            parser.error("--dry-run에는 voyage/ 리랭커가 필요합니다")
        from backend.services.llm import voyage_token_counts

        api_model = args.reranker.removeprefix("voyage/")
        payload = estimate_voyage_rerank_run(
            _read_jsonl(args.dataset),
            _read_jsonl(args.chunks),
            model_name=args.reranker,
            candidate_k=args.candidate_k,
            filters_by_id=filters_by_id,
            token_counts=lambda texts: voyage_token_counts(texts, model=api_model),
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if not args.output:
        parser.error("--output이 필요합니다")
    if args.retriever != "bm25" and not args.embedding_prefix:
        parser.error("--embedding-prefix는 vector·rrf 검색에 필요합니다")

    metadata: dict[str, Any] = {}
    model_name = None
    backend: RetrievalBackend | None = None
    if args.retriever != "bm25":
        from backend.rag.embedding import create_embedder

        metadata, vectors_by_sha = load_embedding_artifacts(args.embedding_prefix)
        model_name = str(metadata["model_name"])
        embedder = create_embedder(model_name, max_length=args.max_length)
        if int(embedder.dimension) != int(metadata["dimension"]):
            raise OfflineEvalError("질의 모델 차원이 임베딩 산출물과 다릅니다")
        if args.query_vectors:
            embedder = CachedQueryEmbedder(embedder, args.query_vectors, model_name=model_name)
        backend = InMemoryVectorBackend(
            chunks=_read_jsonl(args.chunks),
            vectors_by_sha=vectors_by_sha,
            query_embedder=embedder,
            retrieval_method=f"offline_cosine:{model_name}",
            input_header=metadata.get("input_header"),
        )
    if args.retriever != "vector":
        keyword_backend = InMemoryBM25Backend(
            chunks=_read_jsonl(args.chunks),
            tokenizer=create_tokenizer(args.tokenizer),
            retrieval_method=f"bm25:{args.tokenizer}",
        )
        backend = (
            keyword_backend
            if args.retriever == "bm25"
            else RRFBackend(
                vector_backend=backend,
                keyword_backend=keyword_backend,
                rrf_k=args.rrf_k,
                candidate_k=args.rrf_candidates,
                retrieval_method=f"rrf:{model_name}+bm25:{args.tokenizer}",
            )
        )
    output = Path(args.output)
    if args.subqueries and not args.reranker:
        parser.error("--subqueries는 --reranker와 함께 써야 합니다")
    if args.reranker:
        from backend.rag.rerank import create_reranker

        reranked = run_reranked_retrieval(
            _read_jsonl(args.dataset),
            backend,
            create_reranker(
                args.reranker,
                max_length=args.max_length,
                batch_size=args.rerank_batch_size,
            ),
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            filters_by_id=filters_by_id,
            rerank_instruction=args.rerank_instruction,
            subqueries_by_id=(
                {int(r["id"]): list(r["subqueries"]) for r in _read_jsonl(args.subqueries)}
                if args.subqueries
                else None
            ),
            rerank_header=args.rerank_header,
            second_reranker=create_reranker(args.second_reranker) if args.second_reranker else None,
            second_k=args.second_k,
        )
        _write_jsonl(output, reranked)
        payload = _write_rerank_metrics(
            args.dataset,
            output,
            reranked,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            model_name=args.reranker,
            rerank_instruction=args.rerank_instruction,
            chunks_path=_span_chunks_path(args.dataset, args.chunks),
            retrieval_settings={
                "retriever": args.retriever,
                "tokenizer": args.tokenizer if args.retriever != "vector" else None,
                "rrf_k": args.rrf_k if args.retriever == "rrf" else None,
                "rrf_candidates": args.rrf_candidates if args.retriever == "rrf" else None,
                "rerank_header": bool(args.rerank_header),
                "second_reranker": args.second_reranker,
                "second_k": args.second_k if args.second_reranker else None,
            },
        )
        summary = {key: value for key, value in payload.items() if key != "per_question"}
        print(json.dumps({"model_name": model_name, **summary}, ensure_ascii=False))
        return 0
    if args.expand_before or args.expand_after:
        expanded = run_expanded_retrieval(
            _read_jsonl(args.dataset),
            backend,
            seed_k=args.top_k,
            before=args.expand_before,
            after=args.expand_after,
            filters_by_id=filters_by_id,
        )
        _write_jsonl(output, expanded)
        metrics_path = output.with_suffix(".metrics.json")
        payload = _write_expansion_metrics(
            args.dataset,
            output,
            metrics_path,
            seed_k=args.top_k,
            before=args.expand_before,
            after=args.expand_after,
        )
        summary = {key: value for key, value in payload.items() if key != "per_question"}
        print(json.dumps({"model_name": model_name, **summary}, ensure_ascii=False))
        return 0
    results = run_retrieval(
        _read_jsonl(args.dataset), backend, top_k=args.top_k, filters_by_id=filters_by_id
    )
    _write_jsonl(output, results)
    metrics_path = output.with_suffix(".metrics.json")
    payload = evaluate_files(
        args.dataset, output, metrics_path, k=args.top_k,
        chunks_path=_span_chunks_path(args.dataset, args.chunks),
    )
    if args.retriever != "vector":
        settings: dict[str, Any] = {"retriever": args.retriever, "tokenizer": args.tokenizer}
        if args.retriever == "rrf":
            settings.update({"rrf_k": args.rrf_k, "rrf_candidates": args.rrf_candidates})
        payload.update(settings)
        metrics_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    summary = {
        "model_name": model_name,
        "embedding_config_sha256": metadata.get("config_sha256"),
        "k": payload["k"],
        f"hit_at_{args.top_k}": payload[f"hit_at_{args.top_k}"],
        f"recall_at_{args.top_k}": payload[f"recall_at_{args.top_k}"],
        f"mrr_at_{args.top_k}": payload[f"mrr_at_{args.top_k}"],
        f"ndcg_at_{args.top_k}": payload[f"ndcg_at_{args.top_k}"],
        "metrics_path": str(metrics_path),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
