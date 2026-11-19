import json

import numpy as np
import pytest

from backend.rag.embedding import build_embeddings
from backend.rag.offline_eval import (
    InMemoryVectorBackend,
    OfflineEvalError,
    estimate_voyage_rerank_run,
    load_embedding_artifacts,
    run_retrieval,
)
from backend.rag.evaluation import compute_retrieval_metrics


CORP_A = "00000001"
CORP_B = "00000002"


def chunk(chunk_id, sha, corp, *, chapter="II", content=None, near_dup=False):
    return {
        "chunk_id": chunk_id,
        "corpus_id": "corpus-test",
        "rcept_no": "20260301000001" if corp == CORP_A else "20260301000002",
        "corp_code": corp,
        "corp_name": "회사A" if corp == CORP_A else "회사B",
        "fiscal_year": 2025,
        "chapter": chapter,
        "section_path": ["II. 사업의 내용"],
        "note_no": None,
        "table_title": None,
        "ordinal": int(chunk_id[-1]),
        "content": content or f"본문 {chunk_id}",
        "content_sha256": sha,
        "near_dup": near_dup,
        "superseded": False,
    }


def unit(*values):
    vector = np.asarray(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


class FixedQueryEmbedder:
    dimension = 2

    def __init__(self, vector):
        self.vector = unit(*vector)

    def encode_queries(self, texts):
        return np.asarray([self.vector for _ in texts], dtype=np.float32)


def make_backend(query=(1.0, 0.0)):
    chunks = [
        chunk("a-1", "s1", CORP_A),
        chunk("a-2", "s2", CORP_A),
        chunk("a-3", "s3", CORP_A, chapter="XI"),
        chunk("b-1", "s4", CORP_B),
        chunk("a-4", "s5", CORP_A, near_dup=True),
    ]
    vectors = {
        "s1": unit(0.2, 1.0),
        "s2": unit(1.0, 0.1),
        "s3": unit(1.0, 0.0),
        "s4": unit(1.0, 0.0),
        "s5": unit(1.0, 0.0),
    }
    return InMemoryVectorBackend(
        chunks=chunks,
        vectors_by_sha=vectors,
        query_embedder=FixedQueryEmbedder(query),
        retrieval_method="offline_cosine:test",
    )


def test_filters_are_applied_before_ranking_and_near_dup_is_excluded():
    rows = [
        {"id": 1, "input": "질문", "kind": "relevant", "gold_chunks": ["a-2"],
         "filters": {"corp_code": CORP_A, "fiscal_year": 2025}},
    ]
    results = run_retrieval(rows, make_backend(), top_k=10)

    # 회사B(b-1)와 근접 중복(a-4)은 점수가 가장 높아도 나오지 않는다
    assert results == [{"id": 1, "retrieved_chunks": ["a-3", "a-2", "a-1"]}]


def test_chapter_filter_and_filters_file():
    rows = [{"id": 7, "input": "질문", "kind": "relevant", "gold_chunks": ["a-3"]}]
    results = run_retrieval(
        rows,
        make_backend(),
        top_k=10,
        filters_by_id={7: {"id": 7, "corp_code": CORP_A, "fiscal_year": 2025, "chapter": "XI"}},
    )

    assert results == [{"id": 7, "retrieved_chunks": ["a-3"]}]


def test_relevant_row_without_filters_is_rejected_and_irrelevant_is_empty():
    backend = make_backend()
    with pytest.raises(OfflineEvalError, match="필터"):
        run_retrieval([{"id": 1, "input": "q", "kind": "relevant", "gold_chunks": ["a-1"]}], backend)

    results = run_retrieval([{"id": 2, "input": "q", "kind": "irrelevant", "gold_chunks": []}], backend)
    assert results == [{"id": 2, "retrieved_chunks": []}]


def test_missing_vectors_for_filtered_company_fail_loudly():
    backend = make_backend()
    del backend.vectors_by_sha["s2"]
    rows = [{"id": 1, "input": "q", "kind": "relevant", "gold_chunks": ["a-2"],
             "filters": {"corp_code": CORP_A, "fiscal_year": 2025}}]

    with pytest.raises(OfflineEvalError, match="벡터가 없습니다"):
        run_retrieval(rows, backend)


def test_ties_are_broken_by_chunk_id_and_metrics_follow():
    backend = make_backend(query=(0.0, 1.0))
    backend.vectors_by_sha["s2"] = backend.vectors_by_sha["s1"]
    rows = [{"id": 1, "input": "q", "kind": "relevant", "gold_chunks": ["a-2"],
             "filters": {"corp_code": CORP_A, "fiscal_year": 2025}}]

    results = run_retrieval(rows, backend, top_k=2)
    assert results[0]["retrieved_chunks"] == ["a-1", "a-2"]

    metrics = compute_retrieval_metrics(rows, results, k=2)
    assert metrics["hit_at_2"] == 1.0
    assert metrics["mrr_at_2"] == 0.5


class TwoDimEmbedder:
    dimension = 2

    def encode_passages(self, texts):
        return np.asarray([unit(1.0, float(len(t) % 3)) for t in texts], dtype=np.float32)


def test_load_embedding_artifacts_round_trip_and_tamper_detection(tmp_path):
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text(
        "".join(json.dumps(chunk(f"a-{i}", f"s{i}", CORP_A), ensure_ascii=False) + "\n" for i in range(1, 4)),
        encoding="utf-8",
    )
    prefix = tmp_path / "emb"
    build_embeddings(chunks_path, prefix, embedder=TwoDimEmbedder())

    metadata, by_sha = load_embedding_artifacts(prefix)
    assert metadata["complete"] is True
    assert sorted(by_sha) == ["s1", "s2", "s3"]

    ids_path = tmp_path / "emb.ids.jsonl"
    ids_path.write_text(ids_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(OfflineEvalError, match="ids SHA-256"):
        load_embedding_artifacts(prefix)


def test_tie_break_uses_chunk_id_not_file_order():
    chunks = [chunk("a-9", "t9", CORP_A), chunk("a-1", "t1", CORP_A)]
    same = unit(1.0, 0.0)
    backend = InMemoryVectorBackend(
        chunks=chunks,
        vectors_by_sha={"t9": same, "t1": same},
        query_embedder=FixedQueryEmbedder((1.0, 0.0)),
        retrieval_method="offline_cosine:test",
    )
    rows = [{"id": 1, "input": "q", "kind": "relevant", "gold_chunks": ["a-1"],
             "filters": {"corp_code": CORP_A, "fiscal_year": 2025}}]

    assert run_retrieval(rows, backend)[0]["retrieved_chunks"] == ["a-1", "a-9"]


def test_offline_eval_cli_loads_dotenv_before_reading_key(tmp_path, monkeypatch):
    import backend.rag.offline_eval as offline_module

    (tmp_path / ".env").write_text("UPSTAGE_API_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("UPSTAGE_API_KEY", raising=False)
    seen = {}

    class Stop(Exception):
        pass

    def fake_load(prefix):
        import os

        seen["key"] = os.getenv("UPSTAGE_API_KEY")
        raise Stop

    monkeypatch.setattr(offline_module, "load_embedding_artifacts", fake_load)
    with pytest.raises(Stop):
        offline_module.main([
            "--dataset", "d.jsonl", "--chunks", "c.jsonl",
            "--embedding-prefix", "p", "--output", "o.jsonl",
        ])
    assert seen["key"] == "from-dotenv"


def test_voyage_dry_run_uses_longest_candidates_and_cost_upper_bound():
    chunks = [
        chunk("a-1", "s1", CORP_A, content="a"),
        chunk("a-2", "s2", CORP_A, content="bbb"),
        chunk("a-3", "s3", CORP_A, content="ccccc"),
    ]
    dataset = [
        {
            "id": 1,
            "input": "qq",
            "kind": "relevant",
            "gold_chunks": ["a-1"],
            "filters": {"corp_code": CORP_A, "fiscal_year": 2025},
        }
    ]

    payload = estimate_voyage_rerank_run(
        dataset,
        chunks,
        model_name="voyage/rerank-3",
        candidate_k=2,
        token_counts=lambda texts: [len(text) for text in texts],
    )

    assert payload["requests"] == 1
    assert payload["query_document_pairs"] == 2
    assert payload["estimated_tokens_upper_bound"] == 12  # 2*2 + 5 + 3
    assert payload["estimated_cost_usd_upper_bound"] == pytest.approx(0.000001, abs=1e-6)
    assert payload["free_credits_applied"] is False


def test_voyage_cli_dry_run_needs_no_embedding_or_output(tmp_path, monkeypatch, capsys):
    import backend.rag.offline_eval as offline_module
    from backend.services import llm

    chunks_path = tmp_path / "chunks.jsonl"
    dataset_path = tmp_path / "dataset.jsonl"
    chunks_path.write_text(
        json.dumps(chunk("a-1", "s1", CORP_A), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    dataset_path.write_text(
        json.dumps({
            "id": 1,
            "input": "질문",
            "kind": "relevant",
            "gold_chunks": ["a-1"],
            "filters": {"corp_code": CORP_A, "fiscal_year": 2025},
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        llm,
        "voyage_token_counts",
        lambda texts, *, model: [len(text) for text in texts],
    )

    assert offline_module.main([
        "--dataset", str(dataset_path),
        "--chunks", str(chunks_path),
        "--reranker", "voyage/rerank-3",
        "--candidate-k", "1",
        "--dry-run",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["requests"] == 1
    assert not list(tmp_path.glob("*.metrics.json"))


class _CountingEmbedder:
    """호출 횟수를 세는 가짜 질의 임베더(API 대신)."""

    dimension = 2

    def __init__(self):
        self.calls = 0

    def encode_queries(self, texts):
        self.calls += 1
        return np.array([[1.0, 0.0] if "차입" in t else [0.0, 1.0] for t in texts], dtype=np.float32)


def test_query_vector_cache_builds_then_reuses_without_calling_api(tmp_path):
    from backend.rag.offline_eval import CachedQueryEmbedder

    path = tmp_path / "query_vectors.npz"
    first = _CountingEmbedder()
    built = CachedQueryEmbedder(first, path, model_name="m").encode_queries(["차입금은?", "소송은?"])
    assert first.calls == 1 and path.exists()

    second = _CountingEmbedder()
    reused = CachedQueryEmbedder(second, path, model_name="m").encode_queries(["소송은?", "차입금은?"])

    assert second.calls == 0  # 재사용 모드: API를 부르지 않는다
    assert np.array_equal(reused, built[::-1])


def test_query_vector_cache_rejects_unknown_query_in_reuse_mode(tmp_path):
    from backend.rag.offline_eval import CachedQueryEmbedder, OfflineEvalError

    path = tmp_path / "query_vectors.npz"
    CachedQueryEmbedder(_CountingEmbedder(), path, model_name="m").encode_queries(["차입금은?"])

    embedder = _CountingEmbedder()
    with pytest.raises(OfflineEvalError, match="질의 벡터 파일에 없는 질의"):
        CachedQueryEmbedder(embedder, path, model_name="m").encode_queries(["새 질문"])
    assert embedder.calls == 0
    # 모델 이름이 다르면 같은 문장이라도 다른 키다(다른 모델 벡터를 섞어 쓰지 않음)
    with pytest.raises(OfflineEvalError):
        CachedQueryEmbedder(embedder, path, model_name="other").encode_queries(["차입금은?"])


def test_second_stage_reorders_only_first_stage_head():
    """2단계 리랭커는 1단계 상위 second_k개의 순서만 바꾸고 집합은 그대로 둔다."""

    from backend.rag.offline_eval import _scored_chunks, run_reranked_retrieval

    chunks = [
        {"chunk_id": f"c{i}", "content": f"본문{i}", "corp_code": "00000001", "corp_name": "가나다",
         "fiscal_year": 2025, "corpus_id": "corpus-" + "0" * 24, "rcept_no": "20260101000001",
         "chapter": "NOTES", "section_path": ["3. 연결재무제표 주석"], "ordinal": i + 1, "content_sha256": f"{i:064d}"}
        for i in range(5)
    ]

    class Backend:
        def __init__(self):
            self.chunks = chunks

        def search(self, question, *, filters, top_k):
            return _scored_chunks([(c, 1.0 - i / 10) for i, c in enumerate(self.chunks)][:top_k], "fake")

    class First:
        model_name = "first"
        def score(self, query, texts):  # 뒤 문서일수록 점수가 커짐 → 1단계 순서 c4, c3, c2, c1, c0
            return [float(i) for i in range(len(texts))]

    class Second:
        model_name = "second"
        def score(self, query, texts):  # 뒤쪽 입력일수록 점수가 커서 순서가 뒤집힘 → (c4, c3, c2)가 c2, c3, c4로
            return [float(i) for i in range(len(texts))]

    rows = [{"id": 1, "kind": "relevant", "input": "질문"}]
    filters = {1: {"corp_code": "00000001", "fiscal_year": 2025}}
    out = run_reranked_retrieval(rows, Backend(), First(), top_k=3, candidate_k=5,
                                 filters_by_id=filters, second_reranker=Second(), second_k=3)
    assert out[0]["retrieved_chunks"] == ["c2", "c3", "c4"]
    assert out[0]["rerank"]["second_stage"]["second_k"] == 3
    # 2단계가 없으면 1단계 순서 그대로
    plain = run_reranked_retrieval(rows, Backend(), First(), top_k=3, candidate_k=5, filters_by_id=filters)
    assert plain[0]["retrieved_chunks"] == ["c4", "c3", "c2"]
