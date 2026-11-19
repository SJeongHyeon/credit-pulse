"""검색 조합 설정과 그래프용 retriever 팩토리(통합 계층).

검색 조합(청크·임베딩·RRF·리랭커·top-k)을 코드 곳곳에 박지 않고 조합 id 하나로 고른다.
값은 사전 등록 18번·22번과 ``scripts/sweep_d3.py``가 실제로 조립한 설정, 55번은
``backend/rag/offline_eval.py`` 측정 설정(결과 ``evals/results/v2_c55_*``)에서 옮겼다.
조립 방식도 sweep과 같다: 벡터(InMemoryVectorBackend) + BM25(InMemoryBM25Backend)
→ RRFBackend → 융합 상위 N → 리랭커 → top-k.

``backend/rag/``와 ``backend/graph/``는 import만 하고 고치지 않는다(경로 소유권, docs/CONTRACTS.md).

환경변수(.env):
  RETRIEVAL_COMBO            조합 id (예: d3-55, d3-18, d3-22-rerank3)
  RETRIEVAL_CHUNKS_PATH      chunks JSONL 경로(BM25는 어느 모드든 이 파일을 메모리에 올린다)
  VECTOR_BACKEND             벡터 쪽 백엔드: memory(기본) | pgvector (사전 등록 90번)
  RETRIEVAL_EMBEDDING_PREFIX memory일 때 문서 벡터 산출물 prefix(.ids.jsonl·.vectors.npy·.metadata.json)
  RETRIEVAL_CORPUS_ID        pgvector일 때 DB에서 검색할 활성 corpus_id(chunks 파일의 corpus_id와 같아야 함)
  DATABASE_URL               pgvector일 때 필요(서비스는 런타임 연결 풀을 넘겨받는다)
  RETRIEVAL_BM25_TOKENS_PATH 선택. BM25 토큰 사전 계산표(scripts/build_bm25_tokens.py, 사전 등록 99번).
                             없으면 이전처럼 회사별 첫 검색 때 토큰화한다. 파일이 없거나 깨지면 기동 실패
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from backend.rag.bm25 import create_tokenizer
from backend.rag.bm25_tokens import BM25TokenTableError, load_token_table
from backend.rag.document_graph import DocumentGraph
from backend.rag.offline_eval import (
    InMemoryBM25Backend,
    InMemoryVectorBackend,
    RRFBackend,
    load_embedding_artifacts,
)
from backend.rag.term_bridge import TermBridge
from backend.rag.rerank import (
    Reranker,
    create_reranker,
    rerank_candidates,
    rerank_document_with_header,
)
from backend.rag.retrieve import (
    RetrievalContractError,
    RetrieverNotConfigured,
    ScoredChunk,
    SearchFilters,
    retrieve,
)


class RetrievalComboError(ValueError):
    """조합 설정이나 데이터 산출물이 서로 맞지 않을 때 발생한다."""


class CorpNotIndexed(RetrieverNotConfigured):
    """문서 벡터가 없는 회사로 검색하려 할 때 발생한다.

    빈 결과로 돌려주면 그래프가 ITEM_NOT_DISCLOSED로 거절해 '공시 없음'과
    '백엔드 미연결'이 구별되지 않는다(CONTRACTS: 미연결을 빈 결과로 숨기지 않음).
    """


@dataclass(frozen=True)
class RetrievalCombo:
    combo_id: str
    preregister_no: str
    chunking: str
    embedding_model: str
    embedding_dim: int
    tokenizer: str
    rrf_k: int
    rrf_candidates: int
    reranker: str
    rerank_candidates: int
    top_k: int
    # 리랭커에 넘기는 문서 앞에만 '회사명 | 절 경로' 머리말을 붙인다(사전 등록 49번). 1차 검색·임베딩은 그대로.
    rerank_header: bool = False
    # 리랭크 1위 점수가 이 값 미만이면 검색 에이전트가 LLM 채점 없이 '근거 부족'으로 보고한다(사전 등록 103번).
    # 관련·없는 항목 질문의 분포로 조합마다 따로 잰다(P10 R2). 재지 않은 조합은 None(게이트 없음).
    rerank_top1_min_score: float | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.top_k <= self.rerank_candidates <= self.rrf_candidates:
            raise RetrievalComboError(
                "1 ≤ top_k ≤ rerank_candidates ≤ rrf_candidates 이어야 합니다"
            )


# 사전 등록 55번(D3 최종 후보): v9 고정 1,000/100 + 3-small + Kiwi RRF k=10·검색기별 후보 50
# → voyage/rerank-2.5 후보 50(머리말 켬) → top-10. 3-small은 39번 운영 방침(API만, 문서 임베딩 비용 약 0.15배).
# 18번(이전 채택)·22번 후보(rerank-3)는 32번 재현용으로 남긴다. 바꿀 때는 RETRIEVAL_COMBO만 바꾼다.
COMBOS: dict[str, RetrievalCombo] = {
    "d3-55": RetrievalCombo(
        combo_id="d3-55",
        preregister_no="55",
        chunking="fixed_window_1000_overlap100",
        embedding_model="openai/text-embedding-3-small",
        embedding_dim=1536,
        tokenizer="kiwi",
        rrf_k=10,
        rrf_candidates=50,
        reranker="voyage/rerank-2.5",
        rerank_candidates=50,
        top_k=10,
        rerank_header=True,
        rerank_top1_min_score=0.6767,  # 103번: v2 관련 23건 1위 점수 최솟값(없는 항목 6건 최대 0.637)
    ),
    "d3-18": RetrievalCombo(
        combo_id="d3-18",
        preregister_no="18",
        chunking="fixed_window_1000_overlap100",
        embedding_model="openai/text-embedding-3-large",
        embedding_dim=1536,
        tokenizer="kiwi",
        rrf_k=60,
        rrf_candidates=50,
        reranker="voyage/rerank-2.5",
        rerank_candidates=20,
        top_k=10,
    ),
    "d3-22-rerank3": RetrievalCombo(
        combo_id="d3-22-rerank3",
        preregister_no="22",
        chunking="fixed_window_1000_overlap100",
        embedding_model="openai/text-embedding-3-large",
        embedding_dim=1536,
        tokenizer="kiwi",
        rrf_k=60,
        rrf_candidates=50,
        reranker="voyage/rerank-3",
        rerank_candidates=20,
        top_k=10,
    ),
}


def get_combo(combo_id: str) -> RetrievalCombo:
    try:
        return COMBOS[combo_id]
    except KeyError as exc:
        raise RetrievalComboError(
            f"알 수 없는 RETRIEVAL_COMBO입니다: {combo_id!r} (가능: {sorted(COMBOS)})"
        ) from exc


VECTOR_BACKENDS = ("memory", "pgvector")


@dataclass(frozen=True)
class RetrievalSettings:
    combo: RetrievalCombo
    chunks_path: Path
    embedding_prefix: Path | None = None  # memory일 때만 쓴다
    vector_backend: str = "memory"
    corpus_id: str | None = None  # pgvector일 때만 쓴다
    database_url: str | None = None  # pgvector이고 연결 풀을 넘기지 않을 때(스크립트)
    bm25_tokens_path: Path | None = None  # 99번 BM25 토큰 사전 계산표(없으면 그 자리에서 토큰화)
    graph_retrieval_enabled: bool = False
    graph_expansion_limit: int = 10
    graph_rerank_baseline_limit: int | None = None
    graph_low_score_review: bool = False
    term_bridge_enabled: bool = False  # 사전 등록 152번: 첫 검색 질의에 보고서 항목 이름을 덧붙임
    term_bridge_model: str | None = None  # None이면 backend.services.llm.TERM_BRIDGE_MODEL


def _required_env(vector_backend: str) -> tuple[str, ...]:
    if vector_backend == "pgvector":
        return ("RETRIEVAL_COMBO", "RETRIEVAL_CHUNKS_PATH", "DATABASE_URL", "RETRIEVAL_CORPUS_ID")
    return ("RETRIEVAL_COMBO", "RETRIEVAL_CHUNKS_PATH", "RETRIEVAL_EMBEDDING_PREFIX")


def vector_backend_from_env(env: Mapping[str, str]) -> str:
    """VECTOR_BACKEND 값(비어 있으면 memory). 모르는 값은 조용히 memory로 넘기지 않고 거부한다."""

    value = str(env.get("VECTOR_BACKEND", "")).strip() or "memory"
    if value not in VECTOR_BACKENDS:
        raise RetrievalComboError(
            f"VECTOR_BACKEND는 {' 또는 '.join(VECTOR_BACKENDS)}여야 합니다: {value!r}"
        )
    return value


def missing_retrieval_env(env: Mapping[str, str]) -> list[str]:
    """검색 설정 중 비어 있는 환경변수 이름(VECTOR_BACKEND에 따라 필요한 것이 다르다)."""

    return [name for name in _required_env(vector_backend_from_env(env)) if not str(env.get(name, "")).strip()]


def settings_from_env(environ: Mapping[str, str] | None = None) -> RetrievalSettings:
    env = os.environ if environ is None else environ
    vector_backend = vector_backend_from_env(env)
    missing = missing_retrieval_env(env)
    if missing:
        # pgvector인데 설정이 없을 때 메모리로 넘어가지 않는다(CONTRACTS: 미연결을 숨기지 않음).
        raise RetrieverNotConfigured(f".env에 검색 설정이 없습니다: {', '.join(missing)}")
    pgvector = vector_backend == "pgvector"
    bm25_tokens = str(env.get("RETRIEVAL_BM25_TOKENS_PATH", "")).strip()
    graph_mode = str(env.get("GRAPH_RETRIEVAL_ENABLED", "false")).strip().lower()
    if graph_mode not in {"true", "false"}:
        raise RetrievalComboError("GRAPH_RETRIEVAL_ENABLED는 true 또는 false여야 합니다")
    raw_graph_limit = str(env.get("GRAPH_EXPANSION_LIMIT", "10")).strip()
    if not raw_graph_limit.isdecimal() or not 1 <= int(raw_graph_limit) <= 10:
        raise RetrievalComboError("GRAPH_EXPANSION_LIMIT는 1~10의 정수여야 합니다")
    raw_baseline_limit = str(env.get("GRAPH_RERANK_BASELINE_LIMIT", "")).strip()
    if raw_baseline_limit and (not raw_baseline_limit.isdecimal() or not 40 <= int(raw_baseline_limit) <= 50):
        raise RetrievalComboError("GRAPH_RERANK_BASELINE_LIMIT는 40~50의 정수여야 합니다")
    graph_review = str(env.get("GRAPH_LOW_SCORE_REVIEW", "false")).strip().lower()
    if graph_review not in {"true", "false"}:
        raise RetrievalComboError("GRAPH_LOW_SCORE_REVIEW는 true 또는 false여야 합니다")
    bridge_mode = str(env.get("TERM_BRIDGE_ENABLED", "false")).strip().lower()
    if bridge_mode not in {"true", "false"}:
        raise RetrievalComboError("TERM_BRIDGE_ENABLED는 true 또는 false여야 합니다")
    return RetrievalSettings(
        combo=get_combo(env["RETRIEVAL_COMBO"].strip()),
        chunks_path=Path(env["RETRIEVAL_CHUNKS_PATH"].strip()),
        embedding_prefix=None if pgvector else Path(env["RETRIEVAL_EMBEDDING_PREFIX"].strip()),
        vector_backend=vector_backend,
        corpus_id=env["RETRIEVAL_CORPUS_ID"].strip() if pgvector else None,
        database_url=env["DATABASE_URL"].strip() if pgvector else None,
        bm25_tokens_path=Path(bm25_tokens) if bm25_tokens else None,
        graph_retrieval_enabled=graph_mode == "true",
        graph_expansion_limit=int(raw_graph_limit),
        graph_rerank_baseline_limit=int(raw_baseline_limit) if raw_baseline_limit else None,
        graph_low_score_review=graph_review == "true",
        term_bridge_enabled=bridge_mode == "true",
        term_bridge_model=str(env.get("TERM_BRIDGE_MODEL", "")).strip() or None,
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class RetrievalCall:
    """retriever 한 번 호출의 기록(러너가 결과 파일·비용 추정에 쓴다)."""

    query: str
    filters: dict[str, Any]
    first_stage_ids: list[str]
    final_ids: list[str]
    rerank_input_chars: int
    rerank_latency_ms: float
    graph_candidate_ids: list[str] = field(default_factory=list)
    first_stage_query: str | None = None  # 항목 이름을 덧붙였을 때만(query는 원래 질의 그대로)
    bridge_terms: list[str] = field(default_factory=list)
    bridge_error: str | None = None


@dataclass
class ComboRetriever:
    """그래프 ``retriever(state) -> list[dict]``. build_graph(retriever=...)에 그대로 넘긴다."""

    combo: RetrievalCombo
    backend: RRFBackend
    reranker: Reranker
    indexed_corp_codes: frozenset[str]
    info: dict[str, Any]
    document_graph: DocumentGraph | None = None
    graph_expansion_limit: int = 10
    graph_rerank_baseline_limit: int | None = None
    graph_low_score_review: bool = False
    term_bridge: TermBridge | None = None
    clock: Callable[[], float] = time.perf_counter
    calls: list[RetrievalCall] = field(default_factory=list)

    def _filters(self, state: Mapping[str, Any]) -> SearchFilters:
        raw = state.get("filters")
        if not raw or "corp_code" not in raw or "fiscal_year" not in raw:
            # 필터 없이 검색하면 다른 회사 조각이 섞인다(사전 등록 27번). 조용히 넘기지 않는다.
            raise RetrievalContractError("state.filters에 corp_code·fiscal_year가 필요합니다")
        filters = SearchFilters(
            corp_code=str(raw["corp_code"]),
            fiscal_year=int(raw["fiscal_year"]),
            chapter=raw.get("chapter"),
            note_no=raw.get("note_no"),
        )
        if filters.corp_code not in self.indexed_corp_codes:
            where = (
                "pgvector DB(RETRIEVAL_CORPUS_ID)에 이 회사를 적재하세요"
                if self.info.get("vector_backend") == "pgvector"
                else "RETRIEVAL_EMBEDDING_PREFIX 산출물에 이 회사를 넣어 다시 만드세요"
            )
            raise CorpNotIndexed(f"corp_code={filters.corp_code}의 문서 벡터가 없습니다. {where}")
        return filters

    def __call__(self, state: Mapping[str, Any]) -> list[dict[str, Any]]:
        query = " ".join(str(state.get("retrieval_query") or state.get("question", "")).split())
        filters = self._filters(state)
        top_k = int(state.get("top_k") or self.combo.top_k)
        if not 1 <= top_k <= self.combo.rerank_candidates:
            raise RetrievalContractError(
                f"top_k는 1~{self.combo.rerank_candidates}(리랭크 후보 수)여야 합니다"
            )

        # 사전 등록 152번: 첫 검색(BM25·벡터)에만 항목 이름을 덧붙인다. 리랭커·기록의 query는 원래 질의다.
        first_stage_query = query
        bridge_terms: list[str] = []
        bridge_error: str | None = None
        if self.term_bridge is not None:
            bridged = self.term_bridge.bridge(
                str(state.get("question") or query), query,
                corp_code=filters.corp_code, fiscal_year=filters.fiscal_year,
            )
            first_stage_query, bridge_terms, bridge_error = bridged.query, bridged.terms, bridged.error

        # 1차: retrieve()가 필터 일치·rank·중복을 검증한다.
        first_stage = retrieve(
            first_stage_query, filters=filters, top_k=self.combo.rerank_candidates, backend=self.backend
        )
        candidates = first_stage
        graph_candidates: list[dict[str, Any]] = []
        if self.document_graph is not None and first_stage:
            neighbors = self.document_graph.expand(
                [item["chunk_id"] for item in first_stage[:5]],
                filters,
                max_hops=2,
                limit=min(self.graph_expansion_limit, self.combo.rerank_candidates),
                exclude_ids={item["chunk_id"] for item in first_stage},
            )
            for index, row in enumerate(neighbors, start=1):
                candidate = {**row, "score": 0.0, "rank": index, "retrieval_method": "graph:adjacent"}
                validated = ScoredChunk(**{
                    name: tuple(candidate[name]) if name == "section_path" else candidate[name]
                    for name in ScoredChunk.__dataclass_fields__
                })
                graph_candidates.append(validated.to_dict())
            if graph_candidates:
                baseline_slots = self.combo.rerank_candidates - len(graph_candidates)
                if self.graph_rerank_baseline_limit is not None:
                    baseline_slots = min(baseline_slots, self.graph_rerank_baseline_limit)
                candidates = [
                    *first_stage[:baseline_slots],
                    *graph_candidates,
                ]
        by_id = {item["chunk_id"]: item for item in candidates}
        # 머리말이 꺼져 있으면 None → rerank_candidates가 본문만 넘긴다(18·22번 동작 그대로).
        document_text = rerank_document_with_header if self.combo.rerank_header else None
        started = self.clock()
        ranked = rerank_candidates(query, candidates, self.reranker, document_text=document_text)
        latency_ms = (self.clock() - started) * 1000

        method = f"{first_stage[0]['retrieval_method']}+rerank:{self.reranker.model_name}" if first_stage else ""
        if self.document_graph is not None and graph_candidates:
            method += "+graph:adjacent"
        graph_ids = {candidate["chunk_id"] for candidate in graph_candidates} if self.graph_low_score_review else set()
        results: list[dict[str, Any]] = []
        for rank, item in enumerate(ranked[:top_k], start=1):
            source_tag = "+graph:source" if item["chunk_id"] in graph_ids else ""
            row = {**by_id[item["chunk_id"]], "score": item["score"], "rank": rank,
                   "retrieval_method": method + source_tag}
            # 재정렬 뒤 결과도 같은 계약(ScoredChunk)으로 다시 검증한다.
            ScoredChunk(**{**row, "section_path": tuple(row["section_path"])})
            results.append(row)

        self.calls.append(
            RetrievalCall(
                query=query,
                filters=asdict(filters),
                first_stage_ids=[item["chunk_id"] for item in first_stage],
                final_ids=[row["chunk_id"] for row in results],
                # 리랭커에 실제로 넘긴 문자열 길이(머리말 포함)로 센다. 비용 추정에 쓴다.
                rerank_input_chars=sum(
                    len(document_text(item) if document_text else item["content"]) for item in candidates
                )
                + len(query) * len(candidates),
                rerank_latency_ms=latency_ms,
                graph_candidate_ids=[item["chunk_id"] for item in graph_candidates],
                first_stage_query=first_stage_query if bridge_terms else None,
                bridge_terms=bridge_terms,
                bridge_error=bridge_error,
            )
        )
        return results


def _pgvector_backend(
    settings: RetrievalSettings,
    *,
    embedder: Any,
    pool: Any,
    chunks: list[dict[str, Any]],
    chunks_sha256: str,
) -> tuple[Any, frozenset[str]]:
    """pgvector 벡터 백엔드를 만들고, DB 적재본이 BM25용 chunks 파일과 같은 코퍼스인지 기동 때 확인한다."""

    from backend.rag.vector_store import PgVectorBackend

    combo = settings.combo
    missing = [
        name
        for name, value in (
            ("RETRIEVAL_CORPUS_ID", settings.corpus_id),
            ("DATABASE_URL", pool is not None or settings.database_url),
        )
        if not value
    ]
    if missing:
        raise RetrieverNotConfigured(f"VECTOR_BACKEND=pgvector에 필요한 설정이 없습니다: {', '.join(missing)}")
    file_corpus_ids = sorted({str(chunk.get("corpus_id")) for chunk in chunks})
    if file_corpus_ids != [settings.corpus_id]:
        raise RetrievalComboError(
            f"RETRIEVAL_CHUNKS_PATH의 corpus_id {file_corpus_ids}가 RETRIEVAL_CORPUS_ID {settings.corpus_id}와 다릅니다"
        )
    backend = PgVectorBackend(
        corpus_id=str(settings.corpus_id),
        query_embedder=embedder,
        retrieval_method=f"pgvector_cosine:{combo.embedding_model}",
        pool=pool,
        database_url=None if pool is not None else settings.database_url,
    )
    version = backend.corpus_version()
    if version is None:
        raise RetrievalComboError(
            f"DB에 corpus_id={settings.corpus_id} 적재 기록이 없습니다. "
            "scripts/prepare_supabase.py --service-corpus로 적재하세요"
        )
    if version["embedding_model"] != combo.embedding_model:
        raise RetrievalComboError(
            f"DB 벡터 모델 {version['embedding_model']}이 조합 {combo.embedding_model}과 다릅니다"
        )
    if version["vector_dimension"] != combo.embedding_dim:
        raise RetrievalComboError("DB 벡터 차원이 조합 설정과 다릅니다")
    if version["chunks_sha256"] != chunks_sha256:
        # BM25(파일)와 벡터(DB)가 다른 청크를 보면 RRF가 서로 다른 코퍼스를 섞는다.
        raise RetrievalComboError("RETRIEVAL_CHUNKS_PATH가 DB에 적재한 청크 파일과 다릅니다")
    db_corp_codes = backend.corp_codes()
    file_corp_codes = frozenset(str(chunk["corp_code"]) for chunk in chunks)
    if db_corp_codes != file_corp_codes:
        raise RetrievalComboError(
            f"DB 적재 회사 {len(db_corp_codes)}곳과 chunks 파일 회사 {len(file_corp_codes)}곳이 다릅니다"
        )
    return backend, db_corp_codes


def _term_bridge(chunks: list[dict[str, Any]], settings: RetrievalSettings) -> TermBridge:
    from backend.services import llm

    model = settings.term_bridge_model or llm.TERM_BRIDGE_MODEL

    def selector(question: str, headings: Any) -> Any:
        return llm.select_headings(question, list(headings), model=model)

    return TermBridge(chunks, selector)


def build_retriever(
    settings: RetrievalSettings,
    *,
    embedder: Any | None = None,
    reranker: Reranker | None = None,
    pool: Any = None,
) -> ComboRetriever:
    """설정으로 sweep_d3.py와 같은 RRF + 리랭커를 조립한다. embedder·reranker는 시험용 주입.

    pool: VECTOR_BACKEND=pgvector일 때 런타임 연결 풀(체크포인터·Store와 같은 것). 없으면 settings.database_url로 연다.
    """

    combo = settings.combo
    if settings.vector_backend not in VECTOR_BACKENDS:
        raise RetrievalComboError(f"알 수 없는 vector_backend입니다: {settings.vector_backend!r}")
    chunks_sha256 = _sha256(settings.chunks_path)
    metadata: dict[str, Any] = {}
    vectors_by_sha: dict[str, Any] = {}
    if settings.vector_backend == "memory":
        if settings.embedding_prefix is None:
            raise RetrieverNotConfigured("VECTOR_BACKEND=memory에는 RETRIEVAL_EMBEDDING_PREFIX가 필요합니다")
        metadata, vectors_by_sha = load_embedding_artifacts(settings.embedding_prefix)
        if metadata.get("model_name") != combo.embedding_model:
            raise RetrievalComboError(
                f"벡터 모델 {metadata.get('model_name')}이 조합 {combo.embedding_model}과 다릅니다"
            )
        if int(metadata["dimension"]) != combo.embedding_dim:
            raise RetrievalComboError("벡터 차원이 조합 설정과 다릅니다")
        if metadata.get("chunks_sha256") != chunks_sha256:
            # 다른 청크 파일의 벡터를 쓰면 일부 청크가 '벡터 없음'이 되거나 버전이 섞인다.
            raise RetrievalComboError("RETRIEVAL_CHUNKS_PATH가 벡터를 만든 청크 파일과 다릅니다")

    if embedder is None:
        from backend.rag.embedding import create_embedder

        embedder = create_embedder(combo.embedding_model)
    if int(embedder.dimension) != combo.embedding_dim:
        raise RetrievalComboError("질의 임베딩 차원이 조합 설정과 다릅니다")
    if reranker is None:
        reranker = create_reranker(combo.reranker)

    chunks = _read_jsonl(settings.chunks_path)
    vector_backend: Any
    if settings.vector_backend == "pgvector":
        vector_backend, indexed = _pgvector_backend(
            settings, embedder=embedder, pool=pool, chunks=chunks, chunks_sha256=chunks_sha256
        )
    else:
        vector_backend = InMemoryVectorBackend(
            chunks=chunks,
            vectors_by_sha=vectors_by_sha,
            query_embedder=embedder,
            retrieval_method=f"offline_cosine:{combo.embedding_model}",
            input_header=metadata.get("input_header"),
        )
        corp_codes = metadata.get("corp_codes")
        indexed = frozenset(corp_codes) if corp_codes else frozenset(str(c["corp_code"]) for c in chunks)
    tokenizer = create_tokenizer(combo.tokenizer)
    prebuilt_tokens = None
    if settings.bm25_tokens_path is not None:
        try:
            # 환경과 다르면 경고 후 None(이전 경로), 파일이 없거나 깨지면 설정 오류로 멈춘다.
            # 검색에 쓸 토크나이저로 기준 구절을 잘라 보므로 같은 객체를 넘긴다.
            prebuilt_tokens = load_token_table(
                settings.bm25_tokens_path,
                tokenizer_name=combo.tokenizer,
                tokenizer=tokenizer,
                chunks_sha256=chunks_sha256,
            )
        except BM25TokenTableError as exc:
            raise RetrievalComboError(str(exc)) from exc
    keyword_backend = InMemoryBM25Backend(
        chunks=chunks,
        tokenizer=tokenizer,
        retrieval_method=f"bm25:{combo.tokenizer}",
        prebuilt_tokens=prebuilt_tokens,
    )
    backend = RRFBackend(
        vector_backend=vector_backend,
        keyword_backend=keyword_backend,
        rrf_k=combo.rrf_k,
        candidate_k=combo.rrf_candidates,
        retrieval_method=f"rrf:{combo.embedding_model}+bm25:{combo.tokenizer}",
    )
    corpus_ids = sorted({str(chunk.get("corpus_id")) for chunk in chunks})
    return ComboRetriever(
        combo=combo,
        backend=backend,
        reranker=reranker,
        indexed_corp_codes=indexed,
        document_graph=DocumentGraph(chunks) if settings.graph_retrieval_enabled else None,
        graph_expansion_limit=settings.graph_expansion_limit,
        graph_rerank_baseline_limit=settings.graph_rerank_baseline_limit,
        graph_low_score_review=settings.graph_low_score_review,
        term_bridge=_term_bridge(chunks, settings) if settings.term_bridge_enabled else None,
        info={
            "combo": asdict(combo),
            "chunks_path": str(settings.chunks_path),
            "chunks_sha256": chunks_sha256,
            "corpus_ids": corpus_ids,
            "vector_backend": settings.vector_backend,
            "embedding_prefix": None if settings.embedding_prefix is None else str(settings.embedding_prefix),
            "embedding_config_sha256": metadata.get("config_sha256"),
            "indexed_corp_codes": sorted(indexed),
            "bm25_tokens_path": None if settings.bm25_tokens_path is None else str(settings.bm25_tokens_path),
            "bm25_tokens_used": prebuilt_tokens is not None,
            "graph_retrieval_enabled": settings.graph_retrieval_enabled,
            "graph_expansion_limit": settings.graph_expansion_limit,
            "graph_rerank_baseline_limit": settings.graph_rerank_baseline_limit,
            "graph_low_score_review": settings.graph_low_score_review,
            "term_bridge_enabled": settings.term_bridge_enabled,
        },
    )
