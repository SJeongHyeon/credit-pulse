"""BM25·RRF 검색 테스트. Spec: docs/specs/d3-bm25-hybrid.md"""

import json
import math

import numpy as np
import pytest

from backend.rag.bm25 import (
    BM25Error,
    BM25Index,
    create_tokenizer,
    rrf_fuse,
    tokenize_bigram,
    tokenize_space,
)
from backend.rag.offline_eval import (
    InMemoryBM25Backend,
    InMemoryVectorBackend,
    RRFBackend,
    main,
)
from backend.rag.retrieve import SearchFilters, retrieve

CORP_A = "00000001"
CORP_B = "00000002"
FILTER_A = SearchFilters(corp_code=CORP_A, fiscal_year=2025)


def chunk(chunk_id, content, *, corp=CORP_A, year=2025, chapter="II", near_dup=False, superseded=False):
    return {
        "chunk_id": chunk_id,
        "corpus_id": "corpus-test",
        "rcept_no": "20260301000001",
        "corp_code": corp,
        "corp_name": "회사",
        "fiscal_year": year,
        "chapter": chapter,
        "section_path": ["II. 사업의 내용"],
        "note_no": None,
        "table_title": None,
        "ordinal": 1,
        "content": content,
        "content_sha256": f"sha-{chunk_id}",
        "near_dup": near_dup,
        "superseded": superseded,
    }


def bm25_backend(chunks, tokenizer=tokenize_bigram):
    return InMemoryBM25Backend(chunks=chunks, tokenizer=tokenizer, retrieval_method="bm25:test")


FILLER = [chunk(f"z-{number}", f"관계없는 문장 {number}번 내용") for number in range(6)]


def ids(found):
    return [item.chunk_id for item in found]


# 토크나이저
def test_bigram_overlaps_within_a_word_and_keeps_single_letters():
    assert tokenize_bigram("담보를 제공") == ["담보", "보를", "제공"]
    assert tokenize_bigram("A 은행") == ["a", "은행"]


def test_bigram_splits_on_symbols_and_underscore():
    assert tokenize_bigram("차입금(단기),USD_환율") == ["차입", "입금", "단기", "us", "sd", "환율"]


def test_bigram_of_symbols_only_is_empty():
    assert tokenize_bigram(" -- ,, ") == []


def test_space_tokenizer_lowercases_and_splits():
    assert tokenize_space("USD  담보를") == ["usd", "담보를"]


def test_create_tokenizer_returns_each_and_rejects_unknown():
    assert create_tokenizer("space") is tokenize_space
    assert create_tokenizer("bigram") is tokenize_bigram
    with pytest.raises(BM25Error):
        create_tokenizer("없는것")


def test_kiwi_keeps_content_morphemes_only():
    pytest.importorskip("kiwipiepy")
    tokens = create_tokenizer("kiwi")("담보를 제공했다")
    assert "담보" in tokens
    assert "를" not in tokens


# BM25Index
def test_index_rewards_rare_term_over_common_term():
    index = BM25Index([["담보"], ["흔함"], ["흔함"], ["흔함"], ["없음"]])
    scores = index.score(["담보", "흔함"])
    assert scores[0] > scores[1] > 0
    assert scores[4] == 0


def test_index_of_empty_documents_scores_zero():
    assert BM25Index([[], []]).score(["담보"]) == [0.0, 0.0]


def test_index_of_no_documents_is_empty():
    assert BM25Index([]).score(["담보"]) == []


def test_index_uses_okapi_defaults():
    # 문서 길이가 평균과 같으면 길이 보정이 사라져 tf 1의 점수는 IDF와 같다.
    docs = [["담보", "x"], ["y", "z"], ["w", "v"], ["u", "t"]]
    expected_idf = math.log(4 - 1 + 0.5) - math.log(1 + 0.5)
    assert BM25Index(docs).score(["담보"])[0] == pytest.approx(expected_idf)
    # tf 2이면 포화 항 2 * (k1 + 1) / (2 + k1)이 곱해진다. k1=1.5를 고정한다.
    repeated = [["담보", "담보"], ["y", "z"], ["w", "v"], ["u", "t"]]
    assert BM25Index(repeated).score(["담보"])[0] == pytest.approx(expected_idf * 2 * 2.5 / 3.5)


def test_index_length_normalization_prefers_short_document():
    docs = [["담보"], ["담보", "a", "b", "c", "d", "e"], ["x"], ["y"], ["z"]]
    scores = BM25Index(docs).score(["담보"])
    assert scores[0] > scores[1] > 0


# RRF
def test_rrf_scores_match_formula():
    fused = dict(rrf_fuse([["a", "b"], ["b", "c"]], k=60))
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["c"] == pytest.approx(1 / 62)


def test_rrf_orders_by_score_then_id():
    assert [item for item, _ in rrf_fuse([["a", "b"], ["b", "c"]], k=60)] == ["b", "a", "c"]
    assert [item for item, _ in rrf_fuse([["b"], ["a"]], k=60)] == ["a", "b"]


def test_rrf_k_changes_who_wins():
    # k가 크면 공통 2위가 단독 1위를 이기고, k가 작으면 단독 1위가 이긴다.
    lists = [["solo", "both"], ["other", "both"]]
    assert rrf_fuse(lists, k=60)[0][0] == "both"
    assert dict(rrf_fuse(lists, k=1))["solo"] == pytest.approx(1 / 2)


def test_rrf_rejects_nonpositive_k():
    with pytest.raises(BM25Error):
        rrf_fuse([["a"]], k=0)


def test_rrf_with_empty_list_keeps_other_order():
    assert [item for item, _ in rrf_fuse([["b", "a"], []], k=60)] == ["b", "a"]


# AC-BM-01~03 BM25 백엔드
def test_ac01_filter_applies_and_contract_passes():
    chunks = [chunk("a-1", "담보 제공 내역"), chunk("b-1", "담보 제공 내역", corp=CORP_B), *FILLER]
    found = retrieve("담보", filters=FILTER_A, top_k=10, backend=bm25_backend(chunks))
    assert [item["chunk_id"] for item in found] == ["a-1"]
    assert found[0]["retrieval_method"] == "bm25:test"
    assert found[0]["rank"] == 1


def test_ac01_year_and_chapter_filters_apply():
    chunks = [
        chunk("a-1", "담보"),
        chunk("a-2", "담보", year=2024),
        chunk("a-3", "담보", chapter="XI"),
        *FILLER,
    ]
    backend = bm25_backend(chunks)
    assert ids(backend.search("담보", filters=FILTER_A, top_k=10)) == ["a-1", "a-3"]
    narrowed = SearchFilters(corp_code=CORP_A, fiscal_year=2025, chapter="XI")
    other_chapter = [chunk(f"x-{number}", f"다른 내용 {number}", chapter="XI") for number in range(4)]
    backend = bm25_backend([*chunks, *other_chapter])
    assert ids(backend.search("담보", filters=narrowed, top_k=10)) == ["a-3"]


def test_known_limit_single_chunk_filter_returns_nothing():
    # 문서가 1개면 라이브러리 IDF가 음수라 EC-02로 빠진다. Spec의 한계 항목이다.
    backend = bm25_backend([chunk("a-1", "담보")])
    assert backend.search("담보", filters=FILTER_A, top_k=10) == []


def test_idf_is_computed_inside_the_filter():
    # B사 청크가 많아도 A사 안에서 드문 낱말이면 A사 점수는 같아야 한다.
    own = [chunk("a-1", "담보 내용"), *FILLER]
    other = [chunk(f"b-{n}", "담보 내용", corp=CORP_B) for n in range(50)]
    alone = bm25_backend(own).search("담보", filters=FILTER_A, top_k=1)[0].score
    mixed = bm25_backend(own + other).search("담보", filters=FILTER_A, top_k=1)[0].score
    assert mixed == pytest.approx(alone)


def test_ac02_ties_use_chunk_id_and_unmatched_are_dropped():
    chunks = [
        chunk("a-2", "담보 내용"),
        chunk("a-1", "담보 내용"),
        chunk("a-3", "전혀 다른 말"),
        *FILLER,
    ]
    assert ids(bm25_backend(chunks).search("담보", filters=FILTER_A, top_k=10)) == ["a-1", "a-2"]


def test_ac02_near_dup_and_superseded_are_excluded():
    chunks = [
        chunk("a-1", "담보"),
        chunk("a-2", "담보", near_dup=True),
        chunk("a-3", "담보", superseded=True),
        *FILLER,
    ]
    assert ids(bm25_backend(chunks).search("담보", filters=FILTER_A, top_k=10)) == ["a-1"]


def test_ac02_score_order_and_top_k():
    chunks = [
        chunk("a-1", "담보 다른 말 잔뜩 더 있는 긴 문장입니다"),
        chunk("a-2", "담보"),
        chunk("a-3", "담보 약간"),
        *FILLER,
    ]
    found = bm25_backend(chunks).search("담보", filters=FILTER_A, top_k=2)
    assert ids(found) == ["a-2", "a-3"]
    assert found[0].score > found[1].score
    assert [item.rank for item in found] == [1, 2]


def test_ac03_bigram_finds_inflected_word_space_does_not():
    chunks = [chunk("a-1", "담보를 제공"), *FILLER]
    assert ids(bm25_backend(chunks).search("담보", filters=FILTER_A, top_k=10)) == ["a-1"]
    assert bm25_backend(chunks, tokenize_space).search("담보", filters=FILTER_A, top_k=10) == []


def test_query_without_tokens_and_empty_filter_return_nothing():
    backend = bm25_backend([chunk("a-1", "담보"), *FILLER])
    assert backend.search("--", filters=FILTER_A, top_k=10) == []
    missing = SearchFilters(corp_code=CORP_B, fiscal_year=2025)
    assert backend.search("담보", filters=missing, top_k=10) == []


def test_index_is_built_once_per_filter():
    calls = []

    def counting(text):
        calls.append(text)
        return tokenize_bigram(text)

    backend = bm25_backend([chunk("a-1", "담보"), *FILLER], counting)
    backend.search("담보", filters=FILTER_A, top_k=1)
    built = len(calls)
    backend.search("담보", filters=FILTER_A, top_k=1)
    assert len(calls) == built + 1  # 두 번째는 질의 토큰화만 한다


# AC-BM-04·05 RRF 백엔드
class FixedQueryEmbedder:
    dimension = 2

    def encode_queries(self, texts):
        return np.asarray([[1.0, 0.0] for _ in texts], dtype=np.float32)


def unit_vector(first, second):
    vector = np.asarray([first, second], dtype=np.float32)
    return vector / np.linalg.norm(vector)


def hybrid_chunks():
    return [
        chunk("a-1", "관계없는 첫째"),
        chunk("a-2", "담보 둘째"),
        chunk("a-3", "담보 담보 셋째"),
        *FILLER,
    ]


def hybrid_vectors():
    vectors = {"sha-a-1": unit_vector(1.0, 0.0), "sha-a-2": unit_vector(0.9, 0.1), "sha-a-3": unit_vector(0.1, 0.9)}
    vectors.update({item["content_sha256"]: unit_vector(0.0, 1.0) for item in FILLER})
    return vectors


def vector_backend(chunks):
    return InMemoryVectorBackend(
        chunks=chunks,
        vectors_by_sha=hybrid_vectors(),
        query_embedder=FixedQueryEmbedder(),
        retrieval_method="offline_cosine:test",
    )


class RecordingBackend:
    def __init__(self, inner):
        self.inner = inner
        self.top_k = None

    def search(self, question, *, filters, top_k):
        self.top_k = top_k
        return self.inner.search(question, filters=filters, top_k=top_k)


def rrf_backend(*, rrf_k=60, candidate_k=50):
    chunks = hybrid_chunks()
    return RRFBackend(
        vector_backend=vector_backend(chunks),
        keyword_backend=bm25_backend(chunks),
        rrf_k=rrf_k,
        candidate_k=candidate_k,
        retrieval_method="rrf:test",
    )


def test_ac04_rrf_backend_fuses_ranks_and_passes_contract():
    found = retrieve("담보", filters=FILTER_A, top_k=3, backend=rrf_backend())
    # 벡터 a-1, a-2, a-3 / BM25 a-3, a-2 → a-2는 양쪽 2위, a-3은 3위와 1위
    assert [item["chunk_id"] for item in found] == ["a-3", "a-2", "a-1"]
    assert found[0]["score"] == pytest.approx(1 / 63 + 1 / 61)
    assert found[1]["score"] == pytest.approx(2 / 62)
    assert found[0]["retrieval_method"] == "rrf:test"
    assert [item["rank"] for item in found] == [1, 2, 3]


def test_ac05_each_side_gets_candidate_k_and_empty_keyword_keeps_vector_order():
    chunks = hybrid_chunks()
    vector = RecordingBackend(vector_backend(chunks))
    keyword = RecordingBackend(bm25_backend(chunks))
    backend = RRFBackend(
        vector_backend=vector,
        keyword_backend=keyword,
        rrf_k=60,
        candidate_k=7,
        retrieval_method="rrf:test",
    )
    found = backend.search("말뭉치", filters=FILTER_A, top_k=2)
    assert (vector.top_k, keyword.top_k) == (7, 7)
    assert ids(found) == ["a-1", "a-2"]


def test_rrf_uses_configured_k():
    assert rrf_backend(rrf_k=10).search("담보", filters=FILTER_A, top_k=1)[0].score == pytest.approx(
        1 / 13 + 1 / 11
    )


@pytest.mark.parametrize("rrf_k,candidate_k", [(0, 50), (60, 0), (60, 101)])
def test_rrf_rejects_bad_settings(rrf_k, candidate_k):
    with pytest.raises(BM25Error):
        rrf_backend(rrf_k=rrf_k, candidate_k=candidate_k)


def test_rrf_accepts_contract_limit_of_candidates():
    assert rrf_backend(candidate_k=100).candidate_k == 100


def test_rrf_rejects_top_k_above_candidates():
    with pytest.raises(BM25Error):
        rrf_backend(candidate_k=2).search("담보", filters=FILTER_A, top_k=3)


# CLI
def write_inputs(tmp_path):
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in hybrid_chunks()) + "\n",
        encoding="utf-8",
    )
    dataset_path = tmp_path / "dataset.jsonl"
    dataset_path.write_text(
        json.dumps(
            {"id": 1, "input": "담보", "kind": "relevant", "gold_chunks": ["a-3"], "corpus_version": "t"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    filters_path = tmp_path / "filters.jsonl"
    filters_path.write_text(
        json.dumps({"id": 1, "corp_code": CORP_A, "fiscal_year": 2025}) + "\n", encoding="utf-8"
    )
    return ["--dataset", str(dataset_path), "--chunks", str(chunks_path), "--filters", str(filters_path)]


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def read_metrics(output):
    return json.loads(output.with_suffix(".metrics.json").read_text(encoding="utf-8"))


def test_cli_bm25_needs_no_embeddings_and_records_settings(tmp_path):
    output = tmp_path / "out.jsonl"
    assert main([*write_inputs(tmp_path), "--retriever", "bm25", "--output", str(output)]) == 0
    assert read_rows(output)[0]["retrieved_chunks"][0] == "a-3"
    metrics = read_metrics(output)
    assert metrics["retriever"] == "bm25"
    assert metrics["tokenizer"] == "bigram"
    assert metrics["mrr_at_10"] == 1.0


def test_cli_tokenizer_option_reaches_backend(tmp_path, monkeypatch):
    import backend.rag.offline_eval as offline_module

    requested = []
    real = offline_module.create_tokenizer
    monkeypatch.setattr(
        offline_module, "create_tokenizer", lambda name: requested.append(name) or real(name)
    )
    output = tmp_path / "out.jsonl"
    argv = [*write_inputs(tmp_path), "--retriever", "bm25", "--tokenizer", "space", "--output", str(output)]
    assert main(argv) == 0
    assert read_metrics(output)["tokenizer"] == "space"
    assert requested == ["space"]


def test_cli_top_k_applies_to_bm25(tmp_path):
    output = tmp_path / "out.jsonl"
    argv = [*write_inputs(tmp_path), "--retriever", "bm25", "--top-k", "1", "--output", str(output)]
    assert main(argv) == 0
    assert len(read_rows(output)[0]["retrieved_chunks"]) == 1


def test_cli_rrf_passes_settings(tmp_path, monkeypatch):
    import backend.rag.embedding as embedding_module
    import backend.rag.offline_eval as offline_module

    monkeypatch.setattr(
        offline_module,
        "load_embedding_artifacts",
        lambda prefix: ({"model_name": "fake", "dimension": 2}, hybrid_vectors()),
    )
    monkeypatch.setattr(embedding_module, "create_embedder", lambda *args, **kwargs: FixedQueryEmbedder())
    seen = {}
    real = offline_module.RRFBackend

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(offline_module, "RRFBackend", spy)
    output = tmp_path / "out.jsonl"
    argv = [
        *write_inputs(tmp_path),
        "--embedding-prefix", "p",
        "--retriever", "rrf",
        "--rrf-k", "20",
        "--rrf-candidates", "30",
        "--output", str(output),
    ]
    assert main(argv) == 0
    assert (seen["rrf_k"], seen["candidate_k"]) == (20, 30)
    metrics = read_metrics(output)
    assert (metrics["retriever"], metrics["rrf_k"], metrics["rrf_candidates"]) == ("rrf", 20, 30)


def test_cli_vector_default_stays_vector(tmp_path, monkeypatch):
    import backend.rag.embedding as embedding_module
    import backend.rag.offline_eval as offline_module

    monkeypatch.setattr(
        offline_module,
        "load_embedding_artifacts",
        lambda prefix: ({"model_name": "fake", "dimension": 2}, hybrid_vectors()),
    )
    monkeypatch.setattr(embedding_module, "create_embedder", lambda *args, **kwargs: FixedQueryEmbedder())
    output = tmp_path / "out.jsonl"
    assert main([*write_inputs(tmp_path), "--embedding-prefix", "p", "--output", str(output)]) == 0
    # 벡터 1위는 질의 낱말이 없는 a-1이다. BM25가 끼어들면 결과가 달라진다.
    assert read_rows(output)[0]["retrieved_chunks"][0] == "a-1"
    assert "retriever" not in read_metrics(output)


def test_ac07_rrf_without_embeddings_fails_without_output(tmp_path):
    output = tmp_path / "out.jsonl"
    with pytest.raises(SystemExit):
        main([*write_inputs(tmp_path), "--retriever", "rrf", "--output", str(output)])
    assert not output.exists()


def test_ac07_vector_without_embeddings_fails(tmp_path):
    output = tmp_path / "out.jsonl"
    with pytest.raises(SystemExit):
        main([*write_inputs(tmp_path), "--output", str(output)])
    assert not output.exists()


def test_ac07_unknown_tokenizer_fails_without_output(tmp_path):
    output = tmp_path / "out.jsonl"
    with pytest.raises(SystemExit):
        main([*write_inputs(tmp_path), "--retriever", "bm25", "--tokenizer", "없음", "--output", str(output)])
    assert not output.exists()
