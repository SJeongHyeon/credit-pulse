"""검색 조합 팩토리와 그래프 평가 러너 시험(키 없이: 가짜 임베더·리랭커·scorer·rewriter)."""

import dataclasses
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from backend.graph.refusal import CorpusCatalog, CorpusTarget
from backend.rag.retrieve import RetrievalContractError, RetrieverNotConfigured
from backend.rag.document_graph import DocumentGraph
from backend.rag.retrieve import ScoredChunk
from backend.retrieval_combo import (
    COMBOS,
    ComboRetriever,
    CorpNotIndexed,
    RetrievalCombo,
    RetrievalComboError,
    RetrievalSettings,
    build_retriever,
    get_combo,
    settings_from_env,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_graph_eval  # noqa: E402

CORP_A = "00000001"  # 벡터 있음
CORP_B = "00000002"  # 청크는 있지만 벡터 없음
TEST_COMBO = RetrievalCombo(
    combo_id="test",
    preregister_no="-",
    chunking="fixed_window_1000_overlap100",
    embedding_model="fake/emb",
    embedding_dim=2,
    tokenizer="bigram",  # kiwipiepy는 저장소 의존성이 아니라 시험에서는 2-gram
    rrf_k=60,
    rrf_candidates=5,
    reranker="fake/rerank",
    rerank_candidates=3,
    top_k=2,
)


def _chunk(chunk_id, corp, content, ordinal):
    return {
        "chunk_id": chunk_id,
        "corpus_id": "corpus-test",
        "rcept_no": "20260301000001",
        "corp_code": corp,
        "corp_name": "테스트",
        "fiscal_year": 2025,
        "chapter": "XI",
        "section_path": ["XI. 그 밖에 투자자 보호를 위하여 필요한 사항"],
        "note_no": None,
        "table_title": None,
        "ordinal": ordinal,
        "content": content,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }


CHUNKS = [
    _chunk("a-1", CORP_A, "소송 사건 현황 원고 피고", 1),
    _chunk("a-2", CORP_A, "차입금 만기 구조", 2),
    _chunk("a-3", CORP_A, "소송 가액 합계", 3),
    _chunk("a-4", CORP_A, "환율 변동 위험", 4),
    _chunk("b-1", CORP_B, "소송 사건 현황", 1),
]


class FakeEmbedder:
    dimension = 2

    def encode_queries(self, texts):
        return np.asarray([[1.0, 0.0] for _ in texts], dtype=np.float32)


class LengthReranker:
    """본문이 짧을수록 높은 점수(1차 순서와 달라지도록)."""

    model_name = "fake/rerank"

    def score(self, query, texts):
        return [-float(len(text)) for text in texts]


def _write_artifacts(tmp_path: Path, chunks=CHUNKS) -> RetrievalSettings:
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks), encoding="utf-8")
    embedded = [c for c in chunks if c["corp_code"] == CORP_A]
    vectors = np.asarray(
        [[np.cos(i / 3), np.sin(i / 3)] for i in range(len(embedded))], dtype=np.float32
    )
    prefix = tmp_path / "emb"
    np.save(f"{prefix}.vectors.npy", vectors)
    Path(f"{prefix}.ids.jsonl").write_text(
        "".join(
            json.dumps({"index": i, "content_sha256": c["content_sha256"], "representative_chunk_id": c["chunk_id"]}) + "\n"
            for i, c in enumerate(embedded)
        ),
        encoding="utf-8",
    )

    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    Path(f"{prefix}.metadata.json").write_text(
        json.dumps(
            {
                "complete": True,
                "model_name": "fake/emb",
                "dimension": 2,
                "corp_codes": [CORP_A],
                "chunks_sha256": sha(chunks_path),
                "ids_sha256": sha(f"{prefix}.ids.jsonl"),
                "vectors_sha256": sha(f"{prefix}.vectors.npy"),
                "config_sha256": "test",
            }
        ),
        encoding="utf-8",
    )
    return RetrievalSettings(combo=TEST_COMBO, chunks_path=chunks_path, embedding_prefix=prefix)


@pytest.fixture
def retriever(tmp_path):
    return build_retriever(_write_artifacts(tmp_path), embedder=FakeEmbedder(), reranker=LengthReranker())


def _state(corp=CORP_A, **extra):
    return {"question": "소송 현황", "filters": {"corp_code": corp, "fiscal_year": 2025}, **extra}


def test_d3_18_combo_matches_preregister_18_and_22_differs_only_in_reranker():
    combo = get_combo("d3-18")
    assert (combo.embedding_model, combo.embedding_dim) == ("openai/text-embedding-3-large", 1536)
    assert (combo.tokenizer, combo.rrf_k, combo.rrf_candidates) == ("kiwi", 60, 50)
    assert (combo.reranker, combo.rerank_candidates, combo.top_k) == ("voyage/rerank-2.5", 20, 10)
    other = COMBOS["d3-22-rerank3"]
    assert other.reranker == "voyage/rerank-3"
    differing = {k for k, v in vars(combo).items() if vars(other)[k] != v}
    assert differing == {"combo_id", "preregister_no", "reranker"}


def test_settings_from_env_requires_all_keys_and_known_combo(tmp_path):
    with pytest.raises(RetrieverNotConfigured):
        settings_from_env({"RETRIEVAL_COMBO": "d3-18"})
    with pytest.raises(RetrievalComboError):
        settings_from_env(
            {"RETRIEVAL_COMBO": "없음", "RETRIEVAL_CHUNKS_PATH": "x", "RETRIEVAL_EMBEDDING_PREFIX": "y"}
        )
    settings = settings_from_env(
        {"RETRIEVAL_COMBO": "d3-18", "RETRIEVAL_CHUNKS_PATH": "c.jsonl", "RETRIEVAL_EMBEDDING_PREFIX": "e"}
    )
    assert settings.combo.combo_id == "d3-18"


def test_retriever_returns_reranked_contract_dicts_within_filters(retriever):
    results = retriever(_state())

    assert len(results) == 2  # top_k
    assert [r["rank"] for r in results] == [1, 2]
    assert {r["corp_code"] for r in results} == {CORP_A}
    assert all(r["retrieval_method"].endswith("+rerank:fake/rerank") for r in results)
    # LengthReranker: 1차 후보 3개 중 본문이 가장 짧은 것이 1위
    first_stage = retriever.calls[-1].first_stage_ids
    assert len(first_stage) == 3
    shortest = min(first_stage, key=lambda cid: len(next(c["content"] for c in CHUNKS if c["chunk_id"] == cid)))
    assert results[0]["chunk_id"] == shortest
    assert retriever.calls[-1].final_ids == [r["chunk_id"] for r in results]


def test_graph_mode_recovers_adjacent_source_chunk_missing_from_rrf(tmp_path):
    settings = dataclasses.replace(_write_artifacts(tmp_path), graph_retrieval_enabled=True,
                                   graph_low_score_review=True)
    graph_retriever = build_retriever(settings, embedder=FakeEmbedder(), reranker=LengthReranker())

    results = graph_retriever(_state())

    assert graph_retriever.calls[-1].first_stage_ids == ["a-1", "a-2", "a-3"]
    assert "a-4" in [row["chunk_id"] for row in results]
    assert next(row for row in results if row["chunk_id"] == "a-4")["content"] == "환율 변동 위험"
    assert next(row for row in results if row["chunk_id"] == "a-4")["retrieval_method"].endswith("+graph:source")
    assert all(not row["retrieval_method"].endswith("+graph:source") for row in results if row["chunk_id"] != "a-4")


def test_graph_neighbor_with_source_only_metadata_uses_search_result_contract(tmp_path):
    chunks = [{**row, "is_table": row["chunk_id"] == "a-4"} for row in CHUNKS]
    settings = dataclasses.replace(_write_artifacts(tmp_path, chunks=chunks), graph_retrieval_enabled=True)
    retriever = build_retriever(settings, embedder=FakeEmbedder(), reranker=LengthReranker())

    results = retriever(_state())

    assert "a-4" in [row["chunk_id"] for row in results]
    assert all(set(row) == set(ScoredChunk.__dataclass_fields__) for row in results)


def test_graph_setting_rejects_unknown_value(tmp_path):
    values = {
        "RETRIEVAL_COMBO": "d3-55",
        "RETRIEVAL_CHUNKS_PATH": str(tmp_path / "chunks.jsonl"),
        "RETRIEVAL_EMBEDDING_PREFIX": str(tmp_path / "emb"),
    }
    assert settings_from_env(values).graph_retrieval_enabled is False
    assert settings_from_env({**values, "GRAPH_RETRIEVAL_ENABLED": "true"}).graph_retrieval_enabled is True
    with pytest.raises(RetrievalComboError, match="GRAPH_RETRIEVAL_ENABLED"):
        settings_from_env({**values, "GRAPH_RETRIEVAL_ENABLED": "sometimes"})


def test_graph_expansion_limit_defaults_to_ten_and_rejects_invalid_values(tmp_path):
    values = {
        "RETRIEVAL_COMBO": "d3-55",
        "RETRIEVAL_CHUNKS_PATH": str(tmp_path / "chunks.jsonl"),
        "RETRIEVAL_EMBEDDING_PREFIX": str(tmp_path / "emb"),
    }
    assert settings_from_env(values).graph_expansion_limit == 10
    assert settings_from_env({**values, "GRAPH_EXPANSION_LIMIT": "1"}).graph_expansion_limit == 1
    for invalid in ("0", "11", "many", "1.5"):
        with pytest.raises(RetrievalComboError, match="GRAPH_EXPANSION_LIMIT"):
            settings_from_env({**values, "GRAPH_EXPANSION_LIMIT": invalid})


def test_graph_low_score_review_is_off_by_default_and_rejects_invalid_value(tmp_path):
    values = {
        "RETRIEVAL_COMBO": "d3-55",
        "RETRIEVAL_CHUNKS_PATH": str(tmp_path / "chunks.jsonl"),
        "RETRIEVAL_EMBEDDING_PREFIX": str(tmp_path / "emb"),
    }
    assert settings_from_env(values).graph_low_score_review is False
    assert settings_from_env({**values, "GRAPH_LOW_SCORE_REVIEW": "true"}).graph_low_score_review is True
    with pytest.raises(RetrievalComboError, match="GRAPH_LOW_SCORE_REVIEW"):
        settings_from_env({**values, "GRAPH_LOW_SCORE_REVIEW": "maybe"})


def test_graph_rerank_baseline_limit_is_optional_and_rejects_out_of_range(tmp_path):
    values = {
        "RETRIEVAL_COMBO": "d3-55",
        "RETRIEVAL_CHUNKS_PATH": str(tmp_path / "chunks.jsonl"),
        "RETRIEVAL_EMBEDDING_PREFIX": str(tmp_path / "emb"),
    }
    assert settings_from_env(values).graph_rerank_baseline_limit is None
    assert settings_from_env({**values, "GRAPH_RERANK_BASELINE_LIMIT": "40"}).graph_rerank_baseline_limit == 40
    for invalid in ("39", "51", "many", "40.5"):
        with pytest.raises(RetrievalComboError, match="GRAPH_RERANK_BASELINE_LIMIT"):
            settings_from_env({**values, "GRAPH_RERANK_BASELINE_LIMIT": invalid})


def test_graph_expansion_limit_one_passes_only_first_neighbor_to_reranker():
    class FixedBackend:
        def search(self, question, *, filters, top_k):
            return [ScoredChunk(**{
                **{key: value for key, value in seed.items() if key in ScoredChunk.__dataclass_fields__},
                "section_path": tuple(seed["section_path"]),
                "rank": 1, "score": 1.0, "retrieval_method": "fixed",
            })]

    class CaptureReranker:
        model_name = "fixed"

        def score(self, query, texts):
            self.seen = list(texts)
            return [float(len(texts) - index) for index in range(len(texts))]

    rows = [{**_chunk(f"n-{index}", CORP_A, f"neighbor {index}", index + 1), "section_path": ["target"]}
            for index in range(4)]
    seed = rows[1]
    reranker = CaptureReranker()
    retriever = ComboRetriever(
        combo=dataclasses.replace(TEST_COMBO, rrf_candidates=4, rerank_candidates=4, top_k=4),
        backend=FixedBackend(), reranker=reranker,
        indexed_corp_codes=frozenset({CORP_A}), info={},
        document_graph=DocumentGraph(rows), graph_expansion_limit=1,
    )

    retriever(_state())

    assert retriever.calls[-1].graph_candidate_ids == ["n-0"]
    assert len(reranker.seen) == 2


def test_graph_candidates_never_exceed_small_rerank_budget(tmp_path):
    class CountingReranker(LengthReranker):
        def score(self, query, texts):
            self.input_count = len(texts)
            return super().score(query, texts)

    settings = _write_artifacts(tmp_path)
    settings = dataclasses.replace(
        settings,
        combo=dataclasses.replace(TEST_COMBO, rerank_candidates=1, top_k=1),
        graph_retrieval_enabled=True,
    )
    reranker = CountingReranker()
    graph_retriever = build_retriever(settings, embedder=FakeEmbedder(), reranker=reranker)

    graph_retriever(_state())

    assert reranker.input_count == 1


def test_graph_mode_without_eligible_neighbors_keeps_baseline_candidates(tmp_path):
    rows = [{**row, "section_path": [row["chunk_id"]]} for row in CHUNKS]
    settings = _write_artifacts(tmp_path, chunks=rows)
    baseline = build_retriever(settings, embedder=FakeEmbedder(), reranker=LengthReranker())
    graph = build_retriever(
        dataclasses.replace(settings, graph_retrieval_enabled=True, graph_rerank_baseline_limit=40),
        embedder=FakeEmbedder(), reranker=LengthReranker(),
    )
    disabled_with_budget = build_retriever(
        dataclasses.replace(settings, graph_rerank_baseline_limit=40),
        embedder=FakeEmbedder(), reranker=LengthReranker(),
    )

    base_results, graph_results, disabled_results = baseline(_state()), graph(_state()), disabled_with_budget(_state())

    assert [row["chunk_id"] for row in graph_results] == [row["chunk_id"] for row in base_results]
    assert [row["chunk_id"] for row in disabled_results] == [row["chunk_id"] for row in base_results]
    assert graph.calls[-1].graph_candidate_ids == []
    assert graph.calls[-1].rerank_input_chars == baseline.calls[-1].rerank_input_chars


@pytest.mark.parametrize("limit, budget, retained, expected_neighbors", [
    (10, None, 48, {"g-0", "g-1"}),
    (1, None, 49, {"g-0"}),
    (1, 40, 40, {"g-0"}),
])
def test_graph_mode_preserves_baseline_candidates_with_fifty_cap(limit, budget, retained, expected_neighbors):
    class FixedBackend:
        def search(self, question, *, filters, top_k):
            assert top_k == 50
            return [
                ScoredChunk(**{
                    **{key: value for key, value in row.items() if key in ScoredChunk.__dataclass_fields__},
                    "section_path": tuple(row["section_path"]),
                    "rank": rank,
                    "score": float(51 - rank),
                    "retrieval_method": "fixed",
                })
                for rank, row in enumerate(baseline_rows, start=1)
            ]

    class CaptureReranker:
        model_name = "fixed"

        def score(self, query, texts):
            self.seen = list(texts)
            return [float(len(texts) - index) for index in range(len(texts))]

    baseline_rows = [
        {**_chunk(f"b-{index}", CORP_A, f"b-{index}", index + 1),
         "section_path": ["target"] if index == 0 else [f"other-{index}"]}
        for index in range(50)
    ]
    neighbors = [
        {**_chunk(f"g-{index}", CORP_A, f"g-{index}", index + 2), "section_path": ["target"]}
        for index in range(2)
    ]
    reranker = CaptureReranker()
    retriever = ComboRetriever(
        combo=dataclasses.replace(TEST_COMBO, rrf_candidates=50, rerank_candidates=50, top_k=50),
        backend=FixedBackend(), reranker=reranker,
        indexed_corp_codes=frozenset({CORP_A}), info={},
        document_graph=DocumentGraph([*baseline_rows, *neighbors]),
        graph_expansion_limit=limit, graph_rerank_baseline_limit=budget,
    )

    result = retriever(_state())

    assert len(reranker.seen) == retained + len(expected_neighbors)
    assert [row["chunk_id"] for row in result[:retained]] == [f"b-{index}" for index in range(retained)]
    assert set(retriever.calls[-1].graph_candidate_ids) == expected_neighbors


def test_company_without_vectors_fails_loudly_instead_of_empty(retriever):
    with pytest.raises(CorpNotIndexed):
        retriever(_state(corp=CORP_B))


def test_missing_filters_are_rejected(retriever):
    with pytest.raises(RetrievalContractError):
        retriever({"question": "소송 현황"})


def test_chunks_file_must_match_vectors(tmp_path):
    settings = _write_artifacts(tmp_path)
    settings.chunks_path.write_text(settings.chunks_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(RetrievalComboError):
        build_retriever(settings, embedder=FakeEmbedder(), reranker=LengthReranker())


# ---- VECTOR_BACKEND(사전 등록 90번) ----

PG_ENV = {
    "RETRIEVAL_COMBO": "d3-55", "RETRIEVAL_CHUNKS_PATH": "c.jsonl", "VECTOR_BACKEND": "pgvector",
    "DATABASE_URL": "postgresql://x", "RETRIEVAL_CORPUS_ID": "corpus-test",
}


def test_vector_backend_defaults_to_memory():
    """AC-01: VECTOR_BACKEND가 없으면 memory. 로컬 데모·평가 경로는 그대로 벡터 파일 prefix가 필요하다."""

    settings = settings_from_env(
        {"RETRIEVAL_COMBO": "d3-55", "RETRIEVAL_CHUNKS_PATH": "c.jsonl", "RETRIEVAL_EMBEDDING_PREFIX": "e"}
    )
    assert settings.vector_backend == "memory"
    assert settings.embedding_prefix == Path("e")
    with pytest.raises(RetrieverNotConfigured, match="RETRIEVAL_EMBEDDING_PREFIX"):
        settings_from_env({"RETRIEVAL_COMBO": "d3-55", "RETRIEVAL_CHUNKS_PATH": "c.jsonl"})


def test_unknown_vector_backend_is_rejected():
    """AC-02: 모르는 값은 memory로 넘기지 않는다."""

    with pytest.raises(RetrievalComboError, match="memory 또는 pgvector"):
        settings_from_env({**PG_ENV, "VECTOR_BACKEND": "faiss"})


@pytest.mark.parametrize("name", ["RETRIEVAL_CORPUS_ID", "DATABASE_URL"])
def test_pgvector_without_required_setting_fails_with_its_name(name):
    """AC-03: pgvector인데 설정이 비면 그 이름으로 실패하고 memory로 만들어지지 않는다."""

    with pytest.raises(RetrieverNotConfigured, match=name):
        settings_from_env({**PG_ENV, name: " "})
    settings = settings_from_env(PG_ENV)  # 벡터 파일 prefix는 필요 없다
    assert (settings.vector_backend, settings.corpus_id, settings.embedding_prefix) == ("pgvector", "corpus-test", None)


class FakeSqlPool:
    """PgVectorBackend가 보내는 세 SQL(corpus_versions·회사 목록·검색)에 고정 답을 준다."""

    def __init__(self, *, version, corp_codes, chunks):
        self.version, self.corp_codes, self.chunks = version, corp_codes, chunks
        self.sql = []

    def connection(self):
        pool = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params):
                pool.sql.append(sql)
                self.sql, self.params = sql, params

            def fetchall(self):
                if "FROM corpus_versions" in self.sql:
                    return [pool.version] if pool.version else []
                if "DISTINCT corp_code" in self.sql:
                    return [(code,) for code in sorted(pool.corp_codes)]
                rows = [c for c in pool.chunks if c["corp_code"] == self.params[2]]
                return [(c["chunk_id"], c["corpus_id"], c["rcept_no"], c["corp_code"], c["corp_name"], c["fiscal_year"],
                         c["chapter"], c["section_path"], c["note_no"], c["table_title"], c["ordinal"], c["content"],
                         c["content_sha256"], 1.0 - 0.1 * i) for i, c in enumerate(rows)][: self.params[-1]]

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self, row_factory=None):
                return Cursor()

        return Connection()


def _pg_settings(tmp_path, corpus_id="corpus-test"):
    chunks_path = _write_artifacts(tmp_path).chunks_path
    return RetrievalSettings(combo=TEST_COMBO, chunks_path=chunks_path, vector_backend="pgvector", corpus_id=corpus_id)


def _pg_pool(settings, **override):
    sha = hashlib.sha256(settings.chunks_path.read_bytes()).hexdigest()
    values = {"version": ("fake/emb", 2, sha, len(CHUNKS)), "corp_codes": {CORP_A, CORP_B}, "chunks": CHUNKS}
    return FakeSqlPool(**{**values, **override})


def test_pgvector_retriever_uses_pool_and_db_corp_codes(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBED_DIM", "2")
    settings = _pg_settings(tmp_path)
    pool = _pg_pool(settings)

    retriever = build_retriever(settings, embedder=FakeEmbedder(), reranker=LengthReranker(), pool=pool)
    results = retriever(_state(corp=CORP_B))  # 메모리 모드에서는 벡터가 없던 회사도 DB에 있으면 검색된다

    assert retriever.info["vector_backend"] == "pgvector"
    assert retriever.indexed_corp_codes == frozenset({CORP_A, CORP_B})
    assert [r["chunk_id"] for r in results] == ["b-1"]
    assert retriever.backend.vector_backend.retrieval_method == "pgvector_cosine:fake/emb"
    with pytest.raises(CorpNotIndexed, match="pgvector"):
        retriever(_state(corp="00000099"))


@pytest.mark.parametrize(
    "override, message",
    [
        ({"version": None}, "적재 기록이 없습니다"),
        ({"version": ("other/emb", 2, "x", 5)}, "벡터 모델"),
        ({"corp_codes": {CORP_A}}, "회사"),
    ],
)
def test_pgvector_startup_rejects_db_that_differs_from_chunks_file(tmp_path, monkeypatch, override, message):
    """AC-04: DB 적재본이 BM25용 chunks 파일과 다른 코퍼스면 기동을 멈춘다."""

    monkeypatch.setenv("EMBED_DIM", "2")
    settings = _pg_settings(tmp_path)
    with pytest.raises(RetrievalComboError, match=message):
        build_retriever(settings, embedder=FakeEmbedder(), reranker=LengthReranker(), pool=_pg_pool(settings, **override))


def test_pgvector_startup_rejects_other_chunks_sha_and_corpus_id(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBED_DIM", "2")
    settings = _pg_settings(tmp_path)
    with pytest.raises(RetrievalComboError, match="적재한 청크 파일과 다릅니다"):
        build_retriever(settings, embedder=FakeEmbedder(), reranker=LengthReranker(),
                        pool=_pg_pool(settings, version=("fake/emb", 2, "0" * 64, 5)))
    other = dataclasses.replace(settings, corpus_id="corpus-other")
    with pytest.raises(RetrievalComboError, match="RETRIEVAL_CORPUS_ID"):
        build_retriever(other, embedder=FakeEmbedder(), reranker=LengthReranker(), pool=_pg_pool(settings))


def test_d3_55_combo_matches_preregister_55():
    combo = get_combo("d3-55")
    assert combo.preregister_no == "55"
    assert combo.chunking == "fixed_window_1000_overlap100"
    assert (combo.embedding_model, combo.embedding_dim) == ("openai/text-embedding-3-small", 1536)
    assert (combo.tokenizer, combo.rrf_k, combo.rrf_candidates) == ("kiwi", 10, 50)
    assert (combo.reranker, combo.rerank_candidates, combo.top_k) == ("voyage/rerank-2.5", 50, 10)
    assert combo.rerank_header is True
    # 기존 조합은 머리말 없이(본문만) 그대로
    assert COMBOS["d3-18"].rerank_header is False
    assert COMBOS["d3-22-rerank3"].rerank_header is False


class RecordingReranker:
    """리랭커가 받은 문서 문자열을 기록한다."""

    model_name = "fake/rerank"

    def __init__(self):
        self.received: list[str] = []

    def score(self, query, texts):
        self.received.extend(texts)
        return [-float(i) for i in range(len(texts))]


def _recording_retriever(tmp_path, *, rerank_header):
    settings = _write_artifacts(tmp_path)
    combo = dataclasses.replace(TEST_COMBO, rerank_header=rerank_header)
    reranker = RecordingReranker()
    retriever = build_retriever(
        dataclasses.replace(settings, combo=combo), embedder=FakeEmbedder(), reranker=reranker
    )
    return retriever, reranker


def test_rerank_header_on_sends_corp_and_section_header_before_body(tmp_path):
    retriever, reranker = _recording_retriever(tmp_path, rerank_header=True)
    retriever(_state())

    assert len(reranker.received) == 3  # rerank_candidates
    by_id = {c["chunk_id"]: c for c in CHUNKS}
    for chunk_id, text in zip(retriever.calls[-1].first_stage_ids, reranker.received):
        header = "테스트 | XI. 그 밖에 투자자 보호를 위하여 필요한 사항"
        assert text == f"{header}\n{by_id[chunk_id]['content']}"
    # 비용 추정용 글자 수도 머리말을 포함해 센다
    assert retriever.calls[-1].rerank_input_chars == sum(map(len, reranker.received)) + len("소송 현황") * 3
    assert retriever.info["combo"]["rerank_header"] is True


def test_rerank_header_off_sends_body_only(tmp_path):
    retriever, reranker = _recording_retriever(tmp_path, rerank_header=False)
    retriever(_state())

    by_id = {c["chunk_id"]: c for c in CHUNKS}
    assert reranker.received == [by_id[cid]["content"] for cid in retriever.calls[-1].first_stage_ids]
    assert retriever.info["combo"]["rerank_header"] is False


CATALOG = CorpusCatalog(
    corp_names=frozenset({"테스트"}),
    fiscal_years=frozenset({2025}),
    report_types=frozenset({"annual"}),
    targets=frozenset({CorpusTarget(corp_name="테스트", corp_code=CORP_A, fiscal_year=2025)}),
)
ROWS = [
    {"id": 1, "input": "테스트의 사업보고서에서 소송 현황을 알려주세요.", "kind": "relevant"},
    {"id": 2, "input": "없는회사의 사업보고서에서 소송 현황을 알려주세요.", "kind": "irrelevant",
     "irrelevant_type": "unknown_entity"},
]
FILTERS = {1: {"id": 1, "corp_code": CORP_A, "fiscal_year": 2025}}


def test_runner_caps_retrieval_at_three_rounds_and_skips_retrieval_for_refusals(retriever):
    rewrite_count = iter(range(100))

    responses = run_graph_eval.run_eval(
        ROWS,
        FILTERS,
        retriever=retriever,
        graph_kwargs={
            **run_graph_eval.FAKE_AGENTS,
            "scorer": lambda state: False,  # 매번 근거 부족
            # 매번 다른 질의(회사·연도는 보존해야 그래프가 재검색한다)
            "rewriter": lambda state: f"테스트 2025 소송 현황 {next(rewrite_count)}",
        },
        corpus_catalog=CATALOG,
    )

    relevant, refused = responses
    assert relevant["retrieval_rounds"] == 3
    assert len(relevant["retrieval_calls"]) == 3
    assert relevant["rewrites"] == 2
    assert refused["outcome"] == "refused"
    assert refused["refusal_reason"] == "CORP_NOT_IN_CORPUS"
    assert refused["retrieval_calls"] == []


def test_runner_stops_when_supervisor_target_differs_from_filter_file(retriever):
    wrong = {1: {"id": 1, "corp_code": "00000009", "fiscal_year": 2025}}
    with pytest.raises(run_graph_eval.GraphEvalError, match="supervisor"):
        run_graph_eval.run_eval(
            ROWS[:1], wrong, retriever=retriever, graph_kwargs=run_graph_eval.FAKE_AGENTS, corpus_catalog=CATALOG
        )


def test_runner_summary_uses_response_and_retrieval_metrics(retriever):
    responses = run_graph_eval.run_eval(
        ROWS, FILTERS, retriever=retriever, graph_kwargs=run_graph_eval.FAKE_AGENTS, corpus_catalog=CATALOG,
    )
    top1 = responses[0]["retrieval_calls"][0]["final_ids"][0]
    gold = [{**ROWS[0], "gold_chunks": [top1]}, {**ROWS[1], "gold_chunks": []}]

    metrics = run_graph_eval.summarize(gold, responses)

    assert responses[0]["retrieval_rounds"] == 1
    assert metrics["retrieval_first_round"]["mrr_at_10"] == 1.0
    assert metrics["response"]["correct_refusal"]["numerator"] == 1
    assert metrics["response"]["hallucination_risk"]["numerator"] == 0
    # 가짜 작성기는 1위 청크를 인용한다 → 정답 근거 인용 1건
    assert metrics["response"]["correct_citation"]["numerator"] == 1
    assert metrics["relevant_answered_without_citations"] == 0


def test_usage_totals_prices_voyage_and_counts_unknown_cost():
    responses = [
        {"llm_usage": [
            {"kind": "rerank", "model": "rerank-2.5", "input_tokens": 1_000_000, "cost_usd": None},
            {"kind": "chat", "model": "m", "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.25},
            {"kind": "chat", "model": "m", "input_tokens": 10, "output_tokens": 5, "cost_usd": None},
        ]}
    ]

    totals = run_graph_eval.usage_totals(responses)

    assert totals["by_model"]["rerank:rerank-2.5"]["cost_usd"] == pytest.approx(0.05)
    assert totals["cost_usd_known_total"] == pytest.approx(0.30)
    assert totals["cost_unknown_calls"] == 1  # 비용 없는 호출을 0원으로 세지 않는다


# ── v2(동결 50건, gold_spans) 러너 시험: 가짜 검색기·가짜 LLM, 키·청크 산출물 없이 ──
REPO = Path(__file__).resolve().parents[1]
V2_ROWS = run_graph_eval._read_jsonl(REPO / "evals/dataset_v2.jsonl")
V2_RETRIEVAL23 = run_graph_eval._read_jsonl(REPO / "evals/mappings/dataset_v2.retrieval23.jsonl")


def _synthetic_span_chunks(rows):
    """문항마다 그 문항 span 본문을 모두 담은 청크 1개를 span이 나온 문서별로 만든다(실제 v9 청크 대신)."""

    from backend.rag.dart_parser import normalize_text

    by_rcept: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        texts: dict[str, list[str]] = {}
        for span in row.get("gold_spans") or []:
            for location in span["locations"]:
                texts.setdefault(location["rcept_no"], []).append(normalize_text(span["text"]))
        for rcept, parts in texts.items():
            by_rcept.setdefault(rcept, []).append((f"gold-{row['id']}-{rcept}", "\n".join(parts)))
    return by_rcept


class GoldFirstRetriever:
    """질문에 해당하는 문항의 합성 정답 청크를 1위에, 오답 청크를 2위에 돌려준다."""

    def __init__(self, rows, chunks_by_rcept):
        self.calls = []
        owned = {cid: content for pairs in chunks_by_rcept.values() for cid, content in pairs}
        self.gold_by_question = {
            row["input"]: [cid for cid in owned if cid.startswith(f"gold-{row['id']}-")] for row in rows
        }

    def __call__(self, state):
        from backend.retrieval_combo import RetrievalCall

        filters = state["filters"]
        ids = [*self.gold_by_question.get(state["question"], []), "distractor"]
        results = [
            {"chunk_id": cid, "corp_code": filters["corp_code"], "fiscal_year": filters["fiscal_year"],
             "content": cid, "rank": rank}
            for rank, cid in enumerate(ids, start=1)
        ]
        self.calls.append(RetrievalCall(
            query=state.get("retrieval_query", ""), filters=dict(filters), first_stage_ids=ids,
            final_ids=ids, rerank_input_chars=0, rerank_latency_ms=0.0,
        ))
        return results


def test_runner_v2_50_items_without_keys_with_string_ids():
    assert len(V2_ROWS) == 50
    chunks_by_rcept = _synthetic_span_chunks(V2_ROWS)
    rows_str_ids = [{**row, "id": str(row["id"])} for row in V2_ROWS]  # '1'처럼 문자열 id여도 동작
    extracted = run_graph_eval.extract_supervisor_targets(rows_str_ids)  # manifest 기반, 키 없음
    filters_by_id = {record["id"]: record for record in extracted}

    retriever = GoldFirstRetriever(V2_ROWS, chunks_by_rcept)
    responses = run_graph_eval.run_eval(
        rows_str_ids, filters_by_id, retriever=retriever, graph_kwargs=run_graph_eval.FAKE_AGENTS
    )
    gold_rows = run_graph_eval.span_gold_rows(rows_str_ids, chunks_by_rcept)
    metrics = run_graph_eval.summarize(
        gold_rows, responses, retrieval_rows=V2_RETRIEVAL23, chunks_by_rcept=chunks_by_rcept
    )

    response = metrics["response"]
    assert metrics["n"] == 50
    assert response["correct_refusal"]["denominator"] == 25  # 비관련 25
    assert response["correct_citation"]["denominator"] == 25  # 관련 25(31·32번 포함)
    # 검색 지표는 31·32번을 뺀 관련 23건
    first = metrics["retrieval_first_round"]
    assert metrics["retrieval_n"] == 48 and first["relevant_questions"] == 23
    assert {row["id"] for row in first["per_question"] if row["kind"] == "relevant"} == {
        int(row["id"]) for row in V2_RETRIEVAL23 if row["kind"] == "relevant"
    }
    # 검색까지 간 관련 문항은 가짜 작성기가 1위(정답) 청크를 인용 → 정답 근거 인용
    searched_relevant = [r for r in responses if r["kind"] == "relevant" and r["retrieval_calls"]]
    assert response["correct_citation"]["numerator"] == len(searched_relevant)
    searched_23 = {r["id"] for r in searched_relevant} & {int(row["id"]) for row in V2_RETRIEVAL23}
    assert first["hit_at_10"] == pytest.approx(len(searched_23) / 23)
    assert all(row["first_gold_rank"] == 1 for row in first["per_question"] if row["id"] in searched_23)
    # supervisor가 거절한 문항은 검색하지 않는다
    for record, result in zip(extracted, responses):
        if record["refusal_reason"] or record["target_required"]:
            assert result["retrieval_calls"] == []


def test_span_gold_rows_use_union_of_item_chunks():
    row = {
        "id": "7", "kind": "relevant",
        "gold_spans": [
            {"item": "가", "text": "알파", "locations": [{"rcept_no": "R1"}]},
            {"item": "나", "text": "베타", "locations": [{"rcept_no": "R1"}]},
        ],
        "gold_chunks": ["옛-id"],
    }
    chunks = {"R1": [("c1", "알파 문단"), ("c2", "베타 문단"), ("c3", "감마")]}

    [gold] = run_graph_eval.span_gold_rows([row], chunks)

    assert gold["gold_chunks"] == ["c1", "c2"]
    assert row["gold_chunks"] == ["옛-id"]  # 원본(동결 행)은 바꾸지 않는다


def test_runner_stops_when_supervisor_refusal_differs_from_v2_filter_file(retriever):
    # v2 추출 파일처럼 refusal_reason 칸이 있으면 그것도 대조한다
    expected = {2: {"id": 2, "corp_code": None, "fiscal_year": None,
                    "refusal_reason": "OUT_OF_DOMAIN", "target_required": False}}
    with pytest.raises(run_graph_eval.GraphEvalError, match="refusal_reason"):
        run_graph_eval.run_eval(
            ROWS[1:], expected, retriever=retriever, graph_kwargs=run_graph_eval.FAKE_AGENTS, corpus_catalog=CATALOG
        )


def test_pinned_query_embedder_uses_stored_vectors_and_embeds_only_missing(tmp_path):
    from backend.rag.offline_eval import CachedQueryEmbedder

    class CountingEmbedder:
        dimension = 2

        def __init__(self, value):
            self.value, self.calls = value, 0

        def encode_queries(self, texts):
            self.calls += len(texts)
            return np.asarray([[self.value, 0.0] for _ in texts], dtype=np.float32)

    path = tmp_path / "q.npz"
    CachedQueryEmbedder(CountingEmbedder(1.0), path, model_name="m").encode_queries(["원래 질문"])
    fresh = CountingEmbedder(9.0)
    pinned = run_graph_eval.PinnedQueryEmbedder(fresh, path, model_name="m")

    vectors = pinned.encode_queries(["원래 질문", "재작성 질의"])

    assert vectors[0].tolist() == [1.0, 0.0]  # 저장 벡터
    assert vectors[1].tolist() == [9.0, 0.0]  # 새로 임베딩
    assert fresh.calls == 1
    assert pinned.sources == ["pinned", "fresh"]
    with pytest.raises(run_graph_eval.GraphEvalError):
        run_graph_eval.PinnedQueryEmbedder(fresh, tmp_path / "없음.npz", model_name="m")


# 사전 등록 99번: BM25 토큰 사전 계산표 연결(환경변수 → build_retriever)
def test_bm25_tokens_path_is_optional_env(tmp_path):
    base = {"RETRIEVAL_COMBO": "d3-55", "RETRIEVAL_CHUNKS_PATH": "c.jsonl", "RETRIEVAL_EMBEDDING_PREFIX": "e"}

    assert settings_from_env(base).bm25_tokens_path is None
    assert settings_from_env({**base, "RETRIEVAL_BM25_TOKENS_PATH": "  "}).bm25_tokens_path is None
    assert settings_from_env({**base, "RETRIEVAL_BM25_TOKENS_PATH": "t.jsonl.gz"}).bm25_tokens_path == Path("t.jsonl.gz")


def test_build_retriever_uses_matching_table_and_rejects_missing_file(tmp_path, caplog):
    from backend.rag.bm25 import tokenize_bigram
    from backend.rag.bm25_tokens import build_token_table, write_token_table

    settings = _write_artifacts(tmp_path)
    chunks_sha = hashlib.sha256(settings.chunks_path.read_bytes()).hexdigest()
    plain = build_retriever(settings, embedder=FakeEmbedder(), reranker=LengthReranker())

    matching = tmp_path / "tokens.jsonl.gz"
    write_token_table(matching, build_token_table(CHUNKS, tokenize_bigram), tokenizer_name="bigram", tokenizer=tokenize_bigram, chunks_sha256=chunks_sha)
    used = build_retriever(
        dataclasses.replace(settings, bm25_tokens_path=matching), embedder=FakeEmbedder(), reranker=LengthReranker()
    )
    assert plain.info["bm25_tokens_used"] is False
    assert used.info["bm25_tokens_used"] is True
    assert used(_state()) == plain(_state())

    # 다른 청크 파일로 만든 표: 경고하고 이전 경로로 기동한다.
    stale = tmp_path / "stale.jsonl.gz"
    write_token_table(stale, build_token_table(CHUNKS, tokenize_bigram), tokenizer_name="bigram", tokenizer=tokenize_bigram, chunks_sha256="0" * 64)
    with caplog.at_level("WARNING", logger="backend.rag.bm25_tokens"):
        fallback = build_retriever(
            dataclasses.replace(settings, bm25_tokens_path=stale), embedder=FakeEmbedder(), reranker=LengthReranker()
        )
    assert fallback.info["bm25_tokens_used"] is False
    assert "chunks_sha256" in caplog.text

    # 지정한 파일이 없으면 설정 오류로 기동하지 않는다.
    with pytest.raises(RetrievalComboError, match="토큰표 파일이 없습니다"):
        build_retriever(
            dataclasses.replace(settings, bm25_tokens_path=tmp_path / "none.jsonl.gz"),
            embedder=FakeEmbedder(),
            reranker=LengthReranker(),
        )


def test_term_bridge_widens_only_the_first_stage_query(tmp_path):
    """사전 등록 152번: 항목 이름은 첫 검색에만 붙고 리랭커·기록의 질의는 원래 그대로다."""

    from backend.rag.term_bridge import TermBridge

    seen = {"first": [], "rerank": []}

    class SpyBackend:
        def __init__(self, inner):
            self.inner = inner

        def search(self, question, *, filters, top_k):
            seen["first"].append(question)
            return self.inner.search(question, filters=filters, top_k=top_k)

    class SpyReranker(LengthReranker):
        def score(self, query, texts):
            seen["rerank"].append(query)
            return super().score(query, texts)

    settings = _write_artifacts(tmp_path)
    retriever = build_retriever(settings, embedder=FakeEmbedder(), reranker=SpyReranker())
    retriever.backend = SpyBackend(retriever.backend)
    retriever.term_bridge = TermBridge(CHUNKS, lambda q, v: ["그 밖에 투자자 보호를 위하여 필요한 사항", "없는 이름"])

    retriever(_state())

    assert seen["first"] == ["소송 현황 그 밖에 투자자 보호를 위하여 필요한 사항"]
    assert seen["rerank"] == ["소송 현황"]
    call = retriever.calls[-1]
    assert call.query == "소송 현황"
    assert call.first_stage_query == "소송 현황 그 밖에 투자자 보호를 위하여 필요한 사항"
    assert call.bridge_terms == ["그 밖에 투자자 보호를 위하여 필요한 사항"]


def test_term_bridge_selector_error_does_not_break_search(tmp_path):
    from backend.rag.term_bridge import TermBridge

    def failing(question, vocabulary):
        raise RuntimeError("선택기 실패")

    settings = _write_artifacts(tmp_path)
    baseline = build_retriever(settings, embedder=FakeEmbedder(), reranker=LengthReranker())
    bridged = build_retriever(settings, embedder=FakeEmbedder(), reranker=LengthReranker())
    bridged.term_bridge = TermBridge(CHUNKS, failing)

    assert [r["chunk_id"] for r in bridged(_state())] == [r["chunk_id"] for r in baseline(_state())]
    assert bridged.calls[-1].first_stage_query is None
    assert "RuntimeError" in bridged.calls[-1].bridge_error


def test_term_bridge_is_off_by_default_and_setting_rejects_unknown_value(tmp_path):
    values = {
        "RETRIEVAL_COMBO": "d3-55",
        "RETRIEVAL_CHUNKS_PATH": str(tmp_path / "chunks.jsonl"),
        "RETRIEVAL_EMBEDDING_PREFIX": str(tmp_path / "emb"),
    }
    assert settings_from_env(values).term_bridge_enabled is False
    assert settings_from_env({**values, "TERM_BRIDGE_ENABLED": "true"}).term_bridge_enabled is True
    with pytest.raises(RetrievalComboError, match="TERM_BRIDGE_ENABLED"):
        settings_from_env({**values, "TERM_BRIDGE_ENABLED": "maybe"})
    off = build_retriever(_write_artifacts(tmp_path), embedder=FakeEmbedder(), reranker=LengthReranker())
    assert off.term_bridge is None and off.info["term_bridge_enabled"] is False
