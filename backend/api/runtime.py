"""서비스용 심사 그래프 조립(G1): 검색기 + Postgres 체크포인터 + Store 규칙 메모리 + 메모 저장.

키·DB·검색 산출물이 모두 있을 때만 만든다. 하나라도 없으면 `missing_runtime_env()`가 이름을 돌려주고,
앱은 지금처럼 질의·승인에 503 `SERVICE_UNAVAILABLE_GRAPH`를 준다.

데모 모드(키 없음, `DEMO_MODE=1`, SPEC §5.6): 검색기는 커밋된 데모 스냅샷(`data/snapshot/`, 16사)으로 만들고,
임베딩·리랭크·채팅은 `data/demo/recordings.jsonl.gz`의 기록을 재생한다(`backend/services/demo_replay.py`).
기록은 키가 있는 환경에서 `scripts/record_demo.py`가 같은 조립으로 만든다. 데모 모드에서는 규칙 메모리를 끈다
(규칙 검색도 임베딩을 불러 기록과 입력이 달라지므로).

그래프 설정은 평가에서 채택한 구성과 같다: 멀티 그래프, 주제 판정 켬(59번), 작성만 다시 켬(82번),
재검색 꺼짐(75·80번 보류), 검색 조합은 `RETRIEVAL_COMBO`. 여기에 승인(interrupt)을 켠다.
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.api.limits import PerUserRateLimiter, PostgresDailyUsage, QueryGuard

EMBEDDING_DIMS = 1536  # 채택 조합 d3-55의 openai/text-embedding-3-small
RULE_INDEX_FIELDS = ["instruction"]
CORPUS_MANIFEST = Path("data/manifest.jsonl")
SNAPSHOT_DIR = Path("data/snapshot")
DEFAULT_COMBO = "d3-55"  # D3 채택 조합(SPEC §4 검색 결과 행)


def demo_mode(env: Mapping[str, str] | None = None) -> bool:
    """키 없이 기록을 재생하는 데모 모드인가: DEMO_MODE가 0이 아니고 임베딩·채팅 키가 없다."""

    env = os.environ if env is None else env
    has_key = bool(str(env.get("OPENAI_API_KEY", "")).strip() or str(env.get("OPENROUTER_API_KEY", "")).strip())
    return str(env.get("DEMO_MODE", "1")).strip() != "0" and not has_key


def news_search_enabled(env: Mapping[str, str] | None = None) -> bool:
    """거절 질문 뉴스 참고(docs/specs/refusal-news-search.md)를 켤 것인가: NEWS_SEARCH_ENABLED=1일 때만. 기본 꺼짐.

    데모 모드에서는 켜지 않는다(거절 종류 분류 LLM 호출이 데모 기록에 없다).
    """

    env = os.environ if env is None else env
    return str(env.get("NEWS_SEARCH_ENABLED", "")).strip() == "1" and not demo_mode(env)


def missing_runtime_env(env: Mapping[str, str] | None = None) -> list[str]:
    """실제 그래프에 필요한 환경변수 중 비어 있는 것. 값은 돌려주지 않는다(로그에 키가 남지 않게)."""

    env = os.environ if env is None else env
    if demo_mode(env):
        missing = [] if str(env.get("DATABASE_URL", "")).strip() else ["DATABASE_URL"]
        if not (SNAPSHOT_DIR / "snapshot.json").exists():
            missing.append(str(SNAPSHOT_DIR))
        return missing
    from backend.retrieval_combo import RetrievalComboError, missing_retrieval_env

    missing = [
        name
        for name in ("DATABASE_URL", "OPENAI_API_KEY", "VOYAGE_API_KEY")
        if not str(env.get(name, "")).strip()
    ]
    # 검색 설정은 VECTOR_BACKEND에 따라 다르다: memory는 RETRIEVAL_EMBEDDING_PREFIX, pgvector는 RETRIEVAL_CORPUS_ID.
    try:
        missing += [name for name in missing_retrieval_env(env) if name not in missing]
    except RetrievalComboError:
        missing.append("VECTOR_BACKEND")  # memory·pgvector가 아닌 값

    if not (env.get("OPENROUTER_API_KEY") or env.get("OPENAI_API_KEY")):
        missing.append("OPENROUTER_API_KEY")
    return missing


class PostgresMemoSaver:
    """승인된 결정을 review_memos에 저장한다. 같은 (심사 건, 승인 번호)면 기존 id를 돌려준다(멱등, 2주11).

    그래프의 save_memo 노드는 재시도·재전송 때 같은 키로 다시 부를 수 있어, 표의 UNIQUE(review_id, approval_seq)에 기댄다.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def __call__(self, record: dict[str, Any], idempotency_key: tuple[str, str, int]) -> str:
        from psycopg.types.json import Jsonb

        _, review_id, approval_seq = idempotency_key
        with self._pool.connection() as connection:
            row = connection.execute(
                "INSERT INTO review_memos (review_id, approval_seq, items, approved_by) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (review_id, approval_seq) DO NOTHING RETURNING id",
                (review_id, approval_seq, Jsonb(list(record["items"])), record["approved_by"]),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT id FROM review_memos WHERE review_id = %s AND approval_seq = %s",
                    (review_id, approval_seq),
                ).fetchone()
        return str(row["id"] if isinstance(row, dict) else row[0])


def open_pool(database_url: str):
    """체크포인터·Store·메모·비용이 함께 쓰는 연결 풀. autocommit.

    Supabase 트랜잭션 풀러(6543)는 여러 요청이 DB 연결 하나를 나눠 쓰므로 준비된 문장(prepare)을 유지하지 못한다.
    psycopg는 `prepare_threshold=None`이어야 prepare를 끈다(`0`은 '항상 prepare'라 운영에서
    DuplicatePreparedStatement로 질의마다 실패했다, 11/6 배포. PostgresSaver.from_conn_string의 기본값 0을 따라 하면 안 됨).
    """

    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    return ConnectionPool(
        database_url,
        min_size=1,
        max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": POOL_PREPARE_THRESHOLD, "row_factory": dict_row},
        open=True,
    )


POOL_PREPARE_THRESHOLD = None  # 트랜잭션 풀러에서 prepare를 끈다(위 설명)


def rule_index_config(embedder: Any) -> dict[str, Any]:
    """#114 규칙 메모리의 의미 검색 색인. 검색 조합과 같은 임베딩 모델을 쓴다."""

    def embed(texts: list[str]) -> list[list[float]]:
        return embedder.encode_queries(list(texts)).tolist()

    return {"dims": EMBEDDING_DIMS, "embed": embed, "fields": RULE_INDEX_FIELDS}


def snapshot_retrieval(env: Mapping[str, str], recorder: Any = None) -> tuple[Any, Any, Path]:
    """데모 스냅샷을 검증해 풀고, 같은 검색 조합으로 검색기를 만든다. 반환: (검색기, 임베더, manifest 경로).

    recorder가 있으면 실제 API를 부르며 기록하고(scripts/record_demo.py), 없으면 기록을 재생한다.
    """

    from backend.rag.embedding import OpenAIEmbedder
    from backend.rag.rerank import VoyageReranker
    from backend.rag.snapshot import extract_verified_files, read_snapshot_info
    from backend.retrieval_combo import RetrievalSettings, build_retriever, get_combo
    from backend.services import demo_replay

    work_dir = Path(tempfile.mkdtemp(prefix="creditpulse-snapshot-"))
    extract_verified_files(SNAPSHOT_DIR, read_snapshot_info(SNAPSHOT_DIR), work_dir)
    combo = get_combo(str(env.get("RETRIEVAL_COMBO") or DEFAULT_COMBO))
    if recorder is not None:
        from backend.services import llm

        embed_fn = demo_replay.recording_embeddings(llm.openai_embeddings, recorder)
        score_fn = demo_replay.recording_rerank(llm.voyage_rerank, recorder)
    else:
        demo_replay.enable()  # 키 없는 채팅도 기록 재생으로(llm.openrouter_structured)
        embed_fn, score_fn = demo_replay.replay_embeddings, demo_replay.replay_rerank
    embedder = OpenAIEmbedder(combo.embedding_model, embed_fn=embed_fn)
    reranker = VoyageReranker(combo.reranker, score_fn=score_fn)
    settings = RetrievalSettings(combo=combo, chunks_path=work_dir / "chunks.jsonl", embedding_prefix=work_dir / "embeddings")
    return build_retriever(settings, embedder=embedder, reranker=reranker), embedder, work_dir / "manifest.jsonl"


def question_target_resolver(corpus_catalog: Any):
    """질문 하나에서 심사 대상을 정한다. 그래프 입구(build._supervisor_node)와 같은 함수를 같은 순서로 쓴다.

    돌려주는 값: 회사·사업연도가 하나로 정해지면 dict, 아니면 None. None이면 그래프가 거절하거나 되묻는 질문이다
    (코퍼스 밖 회사·기간, 접수연도만 있는 질문, 사업연도가 둘 이상, 회사 이름 없음).
    """

    from backend.graph.refusal import classify_question, requested_fiscal_years
    from backend.graph.build import kst_today

    def resolve(question: str) -> dict[str, Any] | None:
        today = kst_today()
        if classify_question(question, corpus_catalog, reference_date=today):
            return None
        if len(requested_fiscal_years(question, reference_date=today)) > 1:
            return None
        target = corpus_catalog.resolve_target(question, reference_date=today)
        if target is None:
            return None
        return {"corp_code": target.corp_code, "corp_name": target.corp_name, "fiscal_year": target.fiscal_year}

    return resolve


@dataclass
class ReviewRuntime:
    runner: Any
    guard: QueryGuard
    pool: Any
    rules: Any = None  # SemanticRuleStore. 데모 모드는 규칙 메모리를 꺼서 None(/api/v1/rules가 503)
    resolve_target: Any = None  # 질문 → {"corp_code", "corp_name", "fiscal_year"} 또는 None(바로 질문, SPEC §3.3)

    def close(self) -> None:
        self.pool.close()


def build_review_runtime(
    env: Mapping[str, str] | None = None,
    *,
    pool: Any = None,
    retriever: Any = None,
    embedder: Any = None,
    store: Any = None,
    checkpointer: Any = None,
    rate_limiter: PerUserRateLimiter | None = None,
    recorder: Any = None,
) -> ReviewRuntime:
    """실제 실행기를 조립한다. 키워드 인자는 시험에서 가짜를 넣는 자리이고, 운영은 env만 넘긴다.

    recorder: scripts/record_demo.py가 데모 기록을 만들 때만 준다(데모 모드와 같은 스냅샷 조립을 쓴다).
    """

    from backend.api.reviews import LangGraphReviewRunner
    from backend.graph.build import build_supervisor_graph
    from backend.graph.refusal import load_corpus_catalog
    from backend.memory import SemanticRuleStore

    env = os.environ if env is None else env
    use_snapshot = recorder is not None or demo_mode(env)
    if pool is None:
        pool = open_pool(str(env["DATABASE_URL"]))
    manifest = CORPUS_MANIFEST
    if use_snapshot and retriever is None:
        retriever, embedder, manifest = snapshot_retrieval(env, recorder)
    if retriever is None or embedder is None:
        from backend.rag.embedding import create_embedder
        from backend.retrieval_combo import build_retriever, settings_from_env

        settings = settings_from_env(env)
        embedder = embedder or create_embedder(settings.combo.embedding_model)
        # VECTOR_BACKEND=pgvector면 체크포인터·Store와 같은 연결 풀(prepare_threshold=None)로 검색한다.
        retriever = retriever or build_retriever(settings, embedder=embedder, pool=pool)
    if checkpointer is None:
        from langgraph.checkpoint.postgres import PostgresSaver

        checkpointer = PostgresSaver(pool)
    memory_store = None
    if not use_snapshot:
        if store is None:
            from langgraph.store.postgres import PostgresStore

            store = PostgresStore(pool, index=rule_index_config(embedder))
        memory_store = SemanticRuleStore(store)

    corpus_catalog = load_corpus_catalog(manifest)
    graph = build_supervisor_graph(
        retriever=retriever,
        checkpointer=checkpointer,
        corpus_catalog=corpus_catalog,
        memo_saver=PostgresMemoSaver(pool),
        memory_store=memory_store,
        approval_enabled=True,
        domain_check_enabled=True,
        news_search_enabled=news_search_enabled(env),
    )
    guard = QueryGuard(rate_limiter=rate_limiter, usage=PostgresDailyUsage(pool))
    return ReviewRuntime(
        runner=LangGraphReviewRunner(graph), guard=guard, pool=pool, rules=memory_store,
        resolve_target=question_target_resolver(corpus_catalog),
    )
