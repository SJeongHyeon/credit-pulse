"""D3 리랭커 테스트. 실제 모델 없이 가짜 리랭커를 주입해 검증한다."""
import json
import math
import os
import sys
import types

import numpy as np
import pytest

from backend.rag.evaluation import compute_retrieval_metrics
from backend.rag.offline_eval import (
    InMemoryVectorBackend,
    _write_rerank_metrics,
    main as offline_main,
    run_reranked_retrieval,
)
from backend.rag.rerank import (
    CrossEncoderReranker,
    RerankError,
    VoyageReranker,
    latency_summary,
    rerank_candidates,
    reranked_chunk_ids,
)

CORP = "00000001"
FILTERS = {1: {"id": 1, "corp_code": CORP, "fiscal_year": 2025}}
DATASET = [
    {"id": 1, "input": "질문", "kind": "relevant", "gold_chunks": ["c-3"]},
    {"id": 2, "input": "무관", "kind": "irrelevant", "gold_chunks": []},
]


def chunk(index, content=None):
    return {
        "chunk_id": f"c-{index}",
        "corpus_id": "corpus-test",
        "rcept_no": "20260301000001",
        "corp_code": CORP,
        "corp_name": "회사",
        "fiscal_year": 2025,
        "chapter": "II",
        "section_path": ["절"],
        "note_no": None,
        "table_title": None,
        "ordinal": index,
        "content": content or f"본문 {index}",
        "content_sha256": f"s{index}",
        "near_dup": False,
        "superseded": False,
    }


class FakeReranker:
    """본문 뒤 숫자가 클수록 높은 점수를 준다. 1차 순서를 뒤집는다."""

    model_name = "fake/reranker"

    def __init__(self):
        self.calls = []

    def score(self, query, texts):
        self.calls.append((query, list(texts)))
        return [float(text.split()[-1]) for text in texts]


class FixedScores:
    model_name = "fake/fixed"

    def __init__(self, scores):
        self.scores = scores

    def score(self, query, texts):
        return self.scores


class FixedQuery:
    dimension = 2

    def encode_queries(self, texts):
        return np.asarray([[1.0, 0.0] for _ in texts], dtype=np.float32)


def unit(x):
    return np.asarray([x, (1 - x * x) ** 0.5], dtype=np.float32)


def make_backend(rows, scores):
    return InMemoryVectorBackend(
        chunks=rows,
        vectors_by_sha={r["content_sha256"]: unit(s) for r, s in zip(rows, scores)},
        query_embedder=FixedQuery(),
        retrieval_method="test",
    )


def backend_of(count):
    rows = [chunk(i) for i in range(1, count + 1)]
    return make_backend(rows, [1.0 - i * 0.01 for i in range(count)])


def test_scores_reorder_candidates_and_keep_set():
    rows = [chunk(1), chunk(2), chunk(3)]

    ranked = rerank_candidates("질문", rows, FakeReranker())

    assert [item["chunk_id"] for item in ranked] == ["c-3", "c-2", "c-1"]
    assert {item["chunk_id"] for item in ranked} == {"c-1", "c-2", "c-3"}


def test_result_records_first_stage_rank_and_raw_score():
    ranked = rerank_candidates("질문", [chunk(1), chunk(2)], FakeReranker())

    assert ranked[0] == {"chunk_id": "c-2", "first_stage_rank": 2, "score": 2.0}


def test_ties_use_chunk_id_and_are_stable():
    rows = [chunk(2), chunk(1)]
    reranker = FixedScores([0.5, 0.5])

    first = rerank_candidates("질문", rows, reranker)
    second = rerank_candidates("질문", list(reversed(rows)), reranker)

    assert [item["chunk_id"] for item in first] == ["c-1", "c-2"]
    assert [item["chunk_id"] for item in second] == ["c-1", "c-2"]


@pytest.mark.parametrize("bad", [[math.nan, 1.0], [math.inf, 1.0], [-math.inf, 1.0], [1.0]])
def test_invalid_scores_stop_the_run(bad):
    with pytest.raises(RerankError):
        rerank_candidates("질문", [chunk(1), chunk(2)], FixedScores(bad))


def test_empty_candidates_do_not_call_model():
    reranker = FakeReranker()

    assert rerank_candidates("질문", [], reranker) == []
    assert reranker.calls == []


def test_reranked_ids_are_cut_at_k():
    ranked = rerank_candidates("질문", [chunk(i) for i in range(1, 6)], FakeReranker())

    assert reranked_chunk_ids(ranked, 2) == ["c-5", "c-4"]


def test_top_n_view_uses_only_first_stage_candidates():
    # 후보 3개만으로 다시 정렬한 결과는 전체 재정렬 뒤 first_stage_rank로 거른 것과 같다.
    rows = [chunk(i) for i in range(1, 6)]
    ranked = rerank_candidates("질문", rows, FakeReranker())
    direct = rerank_candidates("질문", rows[:3], FakeReranker())

    assert reranked_chunk_ids(ranked, 3, candidate_n=3) == [item["chunk_id"] for item in direct]


def test_latency_excludes_first_query_and_reports_percentiles():
    summary = latency_summary([9999.0, 100.0, 200.0, 300.0, 400.0])

    assert summary["queries"] == 4
    assert summary["p50_ms"] == 250.0
    assert summary["p95_ms"] == pytest.approx(385.0)
    assert summary["max_ms"] == 400.0


def test_latency_needs_at_least_two_queries():
    assert latency_summary([100.0]) == {"queries": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}


def test_run_records_candidates_and_cuts_top_k():
    backend = backend_of(5)
    clock = iter([0.0, 1.5])

    results = run_reranked_retrieval(
        DATASET,
        backend,
        FakeReranker(),
        top_k=2,
        candidate_k=4,
        filters_by_id=FILTERS,
        clock=lambda: next(clock),
    )

    first = results[0]
    assert first["retrieved_chunks"] == ["c-4", "c-3"]
    assert len(first["rerank"]["candidates"]) == 4
    assert first["rerank"]["model"] == "fake/reranker"
    assert first["rerank"]["candidate_k"] == 4
    assert first["rerank"]["latency_ms"] == 1500.0
    assert first["rerank"]["candidates"][0]["first_stage_rank"] == 4


def test_irrelevant_without_filter_never_calls_reranker():
    reranker = FakeReranker()

    results = run_reranked_retrieval(
        DATASET, backend_of(3), reranker, top_k=2, candidate_k=3, filters_by_id=FILTERS
    )

    assert results[1] == {"id": 2, "retrieved_chunks": []}
    assert len(reranker.calls) == 1


def test_results_work_with_existing_metrics():
    results = run_reranked_retrieval(
        DATASET, backend_of(3), FakeReranker(), top_k=2, candidate_k=3, filters_by_id=FILTERS
    )

    metrics = compute_retrieval_metrics(DATASET, results, k=2)

    assert metrics["hit_at_2"] == 1.0
    assert metrics["mrr_at_2"] == 1.0


def test_gold_outside_candidates_cannot_be_recovered():
    dataset = [{"id": 1, "input": "질문", "kind": "relevant", "gold_chunks": ["c-5"]}]

    results = run_reranked_retrieval(
        dataset, backend_of(5), FakeReranker(), top_k=2, candidate_k=3, filters_by_id=FILTERS
    )

    assert "c-5" not in results[0]["retrieved_chunks"]
    assert compute_retrieval_metrics(dataset, results, k=2)["hit_at_2"] == 0.0


def test_top_k_larger_than_candidates_is_rejected():
    with pytest.raises(RerankError, match="candidate_k"):
        run_reranked_retrieval(
            DATASET, backend_of(3), FakeReranker(), top_k=5, candidate_k=3, filters_by_id=FILTERS
        )


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


class Embedder:
    dimension = 2

    def encode_passages(self, texts):
        return np.asarray([[1.0, 0.0] for _ in texts], dtype=np.float32)

    encode_queries = encode_passages


@pytest.fixture
def cli_files(tmp_path, monkeypatch):
    from backend.rag.embedding import build_embeddings

    chunks = write_jsonl(tmp_path / "chunks.jsonl", [chunk(1), chunk(2), chunk(3)])
    build_embeddings(chunks, tmp_path / "emb", embedder=Embedder())
    monkeypatch.setattr("backend.rag.embedding.create_embedder", lambda *a, **k: Embedder())
    return [
        "--dataset", str(write_jsonl(tmp_path / "dataset.jsonl", DATASET)),
        "--chunks", str(chunks),
        "--embedding-prefix", str(tmp_path / "emb"),
        "--filters", str(write_jsonl(tmp_path / "filters.jsonl", [FILTERS[1]])),
    ]


def test_cli_writes_scores_latency_and_top30_view(tmp_path, monkeypatch, cli_files):
    monkeypatch.setattr("backend.rag.rerank.create_reranker", lambda *a, **k: FakeReranker())
    output = tmp_path / "rerank.jsonl"

    code = offline_main(
        [*cli_files, "--output", str(output), "--top-k", "2", "--reranker", "fake/reranker", "--candidate-k", "3"]
    )

    assert code == 0
    metrics = json.loads((tmp_path / "rerank.metrics.json").read_text(encoding="utf-8"))
    assert metrics["reranker"] == "fake/reranker"
    assert metrics["candidate_k"] == 3
    assert metrics["latency"]["queries"] == 0
    assert metrics["hit_at_2"] == 1.0
    assert "reference_candidate_30" in metrics
    row = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert row["rerank"]["candidate_k"] == 3


def test_reference_view_excludes_gold_ranked_after_30(tmp_path):
    dataset = write_jsonl(
        tmp_path / "dataset.jsonl",
        [{"id": 1, "input": "질문", "kind": "relevant", "gold_chunks": ["gold"]}],
    )
    candidates = [
        {"chunk_id": "gold", "first_stage_rank": 31, "score": 9.0},
        {"chunk_id": "other", "first_stage_rank": 1, "score": 1.0},
    ]
    results = [
        {
            "id": 1,
            "retrieved_chunks": ["gold", "other"],
            "rerank": {"model": "m", "candidate_k": 50, "latency_ms": 1.0, "candidates": candidates},
        }
    ]
    output = write_jsonl(tmp_path / "rerank.jsonl", results)

    payload = _write_rerank_metrics(dataset, output, results, top_k=2, candidate_k=50, model_name="m")

    assert payload["hit_at_2"] == 1.0
    assert payload["reference_candidate_30"]["hit_at_2"] == 0.0


def test_cli_without_reranker_stays_plain(tmp_path, cli_files):
    output = tmp_path / "plain.jsonl"

    assert offline_main([*cli_files, "--output", str(output), "--top-k", "2"]) == 0

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert set(rows[0]) == {"id", "retrieved_chunks"}


def test_failed_rerank_writes_no_result_file(tmp_path, monkeypatch, cli_files):
    monkeypatch.setattr("backend.rag.rerank.create_reranker", lambda *a, **k: FixedScores([math.nan]))
    output = tmp_path / "rerank.jsonl"

    with pytest.raises(RerankError):
        offline_main([*cli_files, "--output", str(output), "--reranker", "x", "--candidate-k", "3", "--top-k", "2"])

    assert not output.exists()


class FakeLogits:
    def __init__(self, values):
        self.values = values

    def view(self, *_):
        return self

    def float(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return self.values


@pytest.fixture
def fake_torch(monkeypatch):
    state = types.SimpleNamespace(pairs=[], kwargs=[])

    class Model:
        def to(self, device):
            state.device = device
            return self

        def eval(self):
            return self

        def __call__(self, **batch):
            return types.SimpleNamespace(logits=FakeLogits([float(i) for i in range(batch["size"])]))

    class Tokenizer:
        def __call__(self, pairs, **kwargs):
            state.pairs.append(pairs)
            state.kwargs.append(kwargs)
            return {"size": FakeTensor(len(pairs))}

    class FakeTensor(int):
        def to(self, device):
            return int(self)

    class Context:
        def __enter__(self):
            return None

        def __exit__(self, *_):
            return False

    torch = types.ModuleType("torch")
    torch.inference_mode = Context
    transformers = types.ModuleType("transformers")
    transformers.AutoTokenizer = types.SimpleNamespace(
        from_pretrained=lambda name, **kwargs: state.__dict__.setdefault("token_load", kwargs) and Tokenizer() or Tokenizer()
    )
    transformers.AutoModelForSequenceClassification = types.SimpleNamespace(
        from_pretrained=lambda name, **kwargs: state.__dict__.setdefault("model_load", kwargs) and Model() or Model()
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    return state


def test_cross_encoder_batches_query_passage_pairs(fake_torch):
    model = CrossEncoderReranker("m", batch_size=2, max_length=512)

    scores = model.score("질문", ["가", "나", "다"])

    assert fake_torch.pairs == [[["질문", "가"], ["질문", "나"]], [["질문", "다"]]]
    assert scores == [0.0, 1.0, 0.0]
    assert fake_torch.kwargs[0]["max_length"] == 512
    assert fake_torch.kwargs[0]["truncation"] is True


def test_cross_encoder_uses_only_local_files(fake_torch):
    CrossEncoderReranker("m")

    assert fake_torch.model_load["local_files_only"] is True
    assert fake_torch.token_load["local_files_only"] is True
    assert fake_torch.device == "cpu"


def test_cross_encoder_forces_huggingface_offline(fake_torch, monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)

    CrossEncoderReranker("m")

    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_invalid_batch_size_is_rejected(fake_torch):
    with pytest.raises(RerankError, match="batch_size"):
        CrossEncoderReranker("m", batch_size=0)


@pytest.mark.parametrize("name", ["voyage/rerank-3", "voyage/rerank-2.5"])
def test_voyage_reranker_maps_model_and_uses_service_contract(name):
    calls = []

    def fake(query, documents, *, model):
        calls.append((query, documents, model))
        return [0.1, 0.9]

    reranker = VoyageReranker(name, score_fn=fake)

    assert reranker.score("질문", ["가", "나"]) == [0.1, 0.9]
    assert calls == [("질문", ["가", "나"], name.removeprefix("voyage/"))]


def test_voyage_reranker_requires_key_without_injected_fake(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    with pytest.raises(RerankError, match="VOYAGE_API_KEY"):
        VoyageReranker("voyage/rerank-3")


def test_rerank_module_does_not_import_voyage_sdk_directly():
    from pathlib import Path

    source = Path("backend/rag/rerank.py").read_text(encoding="utf-8")
    assert "import voyageai" not in source
def test_cli_passes_rerank_batch_size_to_factory(tmp_path, monkeypatch, cli_files):
    captured = {}


    def fake_create_reranker(model_name, *, max_length, batch_size):
        captured["model_name"] = model_name
        captured["max_length"] = max_length
        captured["batch_size"] = batch_size
        return FakeReranker()

    monkeypatch.setattr("backend.rag.rerank.create_reranker", fake_create_reranker)
    output = tmp_path / "rerank.jsonl"

    code = offline_main(
        [
            *cli_files,
            "--output",
            str(output),
            "--top-k",
            "2",
            "--reranker",
            "fake/reranker",
            "--candidate-k",
            "3",
            "--rerank-batch-size",
            "8",
        ]
    )

    assert code == 0
    assert captured == {
        "model_name": "fake/reranker",
        "max_length": 512,
        "batch_size": 8,
    }

def test_factory_passes_batch_size_to_cross_encoder(monkeypatch):
    from backend.rag.rerank import create_reranker

    captured = {}

    class FakeCrossEncoder:
        def __init__(self, model_name, *, max_length, batch_size):
            captured["model_name"] = model_name
            captured["max_length"] = max_length
            captured["batch_size"] = batch_size

    monkeypatch.setattr("backend.rag.rerank.CrossEncoderReranker", FakeCrossEncoder)

    create_reranker("fake/reranker", max_length=256, batch_size=8)

    assert captured == {
        "model_name": "fake/reranker",
        "max_length": 256,
        "batch_size": 8,
    }


class _RecordingReranker:
    """리랭커에 실제로 넘어간 문서를 기록하는 가짜 리랭커."""

    def __init__(self):
        self.documents = []

    def score(self, query, documents):
        self.documents = list(documents)
        return [float(len(d)) for d in documents]


def test_rerank_header_is_prepended_only_when_requested():
    from backend.rag.rerank import rerank_candidates, rerank_document_with_header

    chunk = {"chunk_id": "c1", "content": "단기차입금 | 100", "corp_name": "가나다",
             "section_path": ["3. 연결재무제표 주석", "16. 차입금"]}
    plain = _RecordingReranker()
    rerank_candidates("질문", [chunk], plain)
    assert plain.documents == ["단기차입금 | 100"]  # 기본 동작은 본문만

    headed = _RecordingReranker()
    rerank_candidates("질문", [chunk], headed, document_text=rerank_document_with_header)
    assert headed.documents == ["가나다 | 3. 연결재무제표 주석 > 16. 차입금\n단기차입금 | 100"]


def test_llm_rerank_scores_repairs_ranking_and_keeps_input_order(monkeypatch):
    from types import SimpleNamespace

    from backend.services import llm

    captured = {}

    def fake_structured(messages, *, model, response_model):
        captured["model"] = model
        captured["system"] = messages[0]["content"]
        # 범위 밖(9)·중복(2)은 버리고, 빠진 1번은 뒤에 붙어야 한다
        return SimpleNamespace(ranking=[2, 9, 0, 2])

    monkeypatch.setattr(llm, "openrouter_structured", fake_structured)
    scores = llm.llm_rerank_scores("질문", ["a", "b", "c"], model="openai/gpt-4.1")

    assert scores == [2.0, 1.0, 3.0]  # 입력 순서: a는 2위, b는 3위(빠짐→뒤), c는 1위
    assert captured["model"] == "openai/gpt-4.1"
    assert "데이터" in captured["system"]  # 코퍼스 본문은 지시가 아니라 데이터(AGENTS 5번)


def test_create_reranker_builds_llm_reranker_with_injected_scorer():
    from backend.rag.rerank import LLMReranker, RerankError, rerank_candidates

    reranker = LLMReranker("llm/openai/gpt-4.1", score_fn=lambda q, texts, model: [1.0, 3.0, 2.0])
    ranked = rerank_candidates("질문", [{"chunk_id": c, "content": c} for c in ("x", "y", "z")], reranker)
    assert [r["chunk_id"] for r in ranked] == ["y", "z", "x"]
    assert reranker.api_model == "openai/gpt-4.1"
    import pytest
    with pytest.raises(RerankError):
        LLMReranker("gpt-4.1")
