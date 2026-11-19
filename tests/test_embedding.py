import json
import sys
import types

import numpy as np
import pytest

from backend.rag.embedding import (
    EmbeddingBuildError,
    build_embeddings,
    load_unique_embedding_rows,
)


def write_chunks(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def row(chunk_id, sha, content, *, near_dup=False, superseded=False):
    return {
        "chunk_id": chunk_id,
        "content_sha256": sha,
        "content": content,
        "near_dup": near_dup,
        "superseded": superseded,
    }


class FakeEmbedder:
    dimension = 3

    def __init__(self):
        self.calls = []

    def encode_passages(self, texts):
        self.calls.append(list(texts))
        output = []
        for index, _ in enumerate(texts, start=1):
            vector = np.array([index, 1.0, 0.5], dtype=np.float32)
            output.append(vector / np.linalg.norm(vector))
        return np.asarray(output, dtype=np.float32)


def test_unique_rows_exclude_near_dup_and_superseded(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    write_chunks(
        chunks,
        [
            row("c1", "a" * 64, "긴 정상 본문"),
            row("c2", "a" * 64, "긴 정상 본문"),
            row("c3", "b" * 64, "근접 중복", near_dup=True),
            row("c4", "c" * 64, "정정 전", superseded=True),
            row("c5", "d" * 64, "짧음"),
        ],
    )

    rows = load_unique_embedding_rows(chunks)

    assert [item["content_sha256"] for item in rows] == ["d" * 64, "a" * 64]


def test_unique_rows_reject_hash_collision(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    write_chunks(
        chunks,
        [row("c1", "a" * 64, "본문 A"), row("c2", "a" * 64, "본문 B")],
    )

    with pytest.raises(EmbeddingBuildError, match="다른 본문"):
        load_unique_embedding_rows(chunks)


def test_build_embeddings_writes_normalized_artifacts_and_resumes(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    prefix = tmp_path / "e5"
    write_chunks(
        chunks,
        [
            row("c1", "a" * 64, "첫 번째 본문"),
            row("c2", "b" * 64, "두 번째로 더 긴 본문"),
            row("c3", "c" * 64, "세 번째 본문입니다"),
        ],
    )
    first = FakeEmbedder()

    metadata = build_embeddings(
        chunks,
        prefix,
        batch_size=2,
        max_length=32,
        model_name="fake",
        embedder=first,
    )

    vectors = np.load(f"{prefix}.vectors.npy")
    assert metadata["complete"] is True
    assert metadata["completed"] == 3
    assert vectors.shape == (3, 3)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)
    assert len(first.calls) == 2

    resumed = FakeEmbedder()
    second = build_embeddings(
        chunks,
        prefix,
        batch_size=2,
        max_length=32,
        model_name="fake",
        embedder=resumed,
    )
    assert second["processed_this_run"] == 0
    assert resumed.calls == []


def test_build_embeddings_rejects_mismatched_progress(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    prefix = tmp_path / "e5"
    write_chunks(chunks, [row("c1", "a" * 64, "본문")])
    build_embeddings(
        chunks,
        prefix,
        batch_size=1,
        max_length=32,
        model_name="fake-a",
        embedder=FakeEmbedder(),
    )

    with pytest.raises(EmbeddingBuildError, match="설정이 다릅니다"):
        build_embeddings(
            chunks,
            prefix,
            batch_size=1,
            max_length=32,
            model_name="fake-b",
            embedder=FakeEmbedder(),
        )


def test_model_profiles_cover_comparison_models_and_reject_unknown():
    from backend.rag.embedding import model_profile

    assert model_profile("intfloat/multilingual-e5-small") == {
        "pooling": "mean",
        "query_prefix": "query: ",
        "passage_prefix": "passage: ",
    }
    for name in ("nlpai-lab/KURE-v1", "BAAI/bge-m3"):
        assert model_profile(name) == {
            "pooling": "cls",
            "query_prefix": "",
            "passage_prefix": "",
        }
    with pytest.raises(EmbeddingBuildError, match="등록되지 않은"):
        model_profile("unknown/model")


def test_e5_config_hash_is_unchanged_by_profile_refactor(tmp_path):
    """기존 E5 산출물을 그대로 재사용할 수 있어야 한다(설정 키·값이 그대로)."""

    chunks = tmp_path / "chunks.jsonl"
    write_chunks(chunks, [row("c1", "a" * 64, "본문")])
    metadata = build_embeddings(chunks, tmp_path / "e5", embedder=FakeEmbedder())

    assert metadata["passage_prefix"] == "passage: "
    assert "pooling" not in metadata
    assert "corp_codes" not in metadata


def test_corp_codes_filter_limits_rows_and_is_recorded(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    rows_in = [row("c1", "a" * 64, "회사A 본문"), row("c2", "b" * 64, "회사B 본문")]
    rows_in[0]["corp_code"] = "00000001"
    rows_in[1]["corp_code"] = "00000002"
    write_chunks(chunks, rows_in)

    kept = load_unique_embedding_rows(chunks, corp_codes={"00000001"})
    assert [item["content_sha256"] for item in kept] == ["a" * 64]

    metadata = build_embeddings(
        chunks,
        tmp_path / "kure",
        model_name="nlpai-lab/KURE-v1",
        embedder=FakeEmbedder(),
        corp_codes={"00000001"},
    )
    assert metadata["rows"] == 1
    assert metadata["pooling"] == "cls"
    assert metadata["passage_prefix"] == ""
    assert metadata["corp_codes"] == ["00000001"]

    with pytest.raises(EmbeddingBuildError, match="비어 있습니다"):
        load_unique_embedding_rows(chunks, corp_codes=set())



class FakeEmbedFn:
    """services.llm.upstage_embeddings 자리에 주입하는 가짜. 실제 API를 부르지 않는다."""

    def __init__(self, dimension=1024):
        self.dimension = dimension
        self.calls = []

    def __call__(self, texts, *, model):
        self.calls.append((model, list(texts)))
        output = []
        for text in texts:
            vector = [0.0] * self.dimension
            vector[0] = float(len(text) + 1)
            vector[1] = 3.0
            output.append(vector)
        return output


def test_upstage_profile_and_embedder_use_separate_models_and_normalize():
    from backend.rag.embedding import UpstageEmbedder, model_profile

    profile = model_profile("upstage/solar-embedding-2")
    assert profile["query_model"] == "solar-embedding-2-query"
    assert profile["passage_model"] == "solar-embedding-2-passage"

    fake = FakeEmbedFn()
    embedder = UpstageEmbedder(embed_fn=fake)
    passages = embedder.encode_passages(["가", "나다"])
    queries = embedder.encode_queries(["질문"])

    assert [model for model, _ in fake.calls] == [
        "solar-embedding-2-passage",
        "solar-embedding-2-query",
    ]
    assert passages.shape == (2, 1024) and queries.shape == (1, 1024)
    assert np.allclose(np.linalg.norm(passages, axis=1), 1.0, atol=1e-6)
    assert passages[0][0] < passages[1][0]


def test_upstage_batches_at_most_100_and_requires_key(monkeypatch):
    from backend.rag.embedding import UpstageEmbedder

    fake = FakeEmbedFn(dimension=4)
    embedder = UpstageEmbedder(embed_fn=fake)
    embedder.dimension = 4
    embedder.encode_passages([f"t{i}" for i in range(250)])
    assert [len(batch) for _, batch in fake.calls] == [100, 100, 50]

    monkeypatch.delenv("UPSTAGE_API_KEY", raising=False)
    with pytest.raises(EmbeddingBuildError, match="UPSTAGE_API_KEY"):
        UpstageEmbedder()


def test_upstage_wrong_dimension_or_count_is_rejected():
    from backend.rag.embedding import UpstageEmbedder

    with pytest.raises(EmbeddingBuildError, match="shape"):
        UpstageEmbedder(embed_fn=FakeEmbedFn(dimension=512)).encode_queries(["질문"])

    def short(texts, *, model):
        return [[1.0] * 1024]

    with pytest.raises(EmbeddingBuildError, match="개수"):
        UpstageEmbedder(embed_fn=short).encode_passages(["a", "b"])


def test_build_embeddings_with_upstage_records_api_config(tmp_path):
    from backend.rag.embedding import UpstageEmbedder

    chunks = tmp_path / "chunks.jsonl"
    write_chunks(chunks, [row("c1", "a" * 64, "본문 하나"), row("c2", "b" * 64, "본문 둘")])
    metadata = build_embeddings(
        chunks,
        tmp_path / "upstage",
        model_name="upstage/solar-embedding-2",
        embedder=UpstageEmbedder(embed_fn=FakeEmbedFn()),
    )

    assert metadata["dimension"] == 1024
    assert metadata["pooling"] == "api"
    assert metadata["passage_prefix"] == ""
    assert metadata["complete"] is True


def test_embedding_module_does_not_import_openai_directly():
    """AGENTS.md 2번: 외부 API 클라이언트는 backend/services/llm.py에서만 만든다."""

    from pathlib import Path

    source = Path("backend/rag/embedding.py").read_text(encoding="utf-8")
    assert "from openai" not in source and "import openai" not in source


def test_embedding_cli_loads_dotenv_without_overriding(tmp_path, monkeypatch):
    import backend.rag.embedding as embedding_module

    (tmp_path / ".env").write_text("UPSTAGE_API_KEY=from-dotenv\nEMBED_TEST_KEEP=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("UPSTAGE_API_KEY", raising=False)
    monkeypatch.setenv("EMBED_TEST_KEEP", "from-shell")
    seen = {}

    def fake_build(*args, **kwargs):
        import os

        seen["key"] = os.getenv("UPSTAGE_API_KEY")
        seen["keep"] = os.getenv("EMBED_TEST_KEEP")
        return {"ok": True}

    monkeypatch.setattr(embedding_module, "build_embeddings", fake_build)
    embedding_module.main(["build", "--chunks", "c.jsonl", "--output-prefix", "o"])

    assert seen == {"key": "from-dotenv", "keep": "from-shell"}


class FakeOpenAIEmbedFn:
    def __init__(self, dimension=1536):
        self.dimension = dimension
        self.calls = []

    def __call__(self, texts, *, model, dimensions):
        self.calls.append((model, dimensions, list(texts)))
        vectors = []
        for index, _ in enumerate(texts):
            vector = [0.0] * self.dimension
            vector[index % self.dimension] = 1.0
            vectors.append(vector)
        return vectors


@pytest.mark.parametrize(
    "profile_name,api_model,dimension",
    [
        ("openai/text-embedding-3-small", "text-embedding-3-small", 1536),
        ("openai/text-embedding-3-large", "text-embedding-3-large", 1536),
        ("openai/text-embedding-3-large-3072", "text-embedding-3-large", 3072),
    ],
)
def test_openai_embedder_uses_profile_dimension_and_normalizes(
    profile_name, api_model, dimension
):
    from backend.rag.embedding import OpenAIEmbedder, model_profile

    assert model_profile(profile_name)["dimension"] == str(dimension)
    fake = FakeOpenAIEmbedFn(dimension)
    embedder = OpenAIEmbedder(profile_name, embed_fn=fake)

    vectors = embedder.encode_passages(["가", "나"])

    assert fake.calls == [(api_model, dimension, ["가", "나"])]
    assert vectors.shape == (2, dimension)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)


def test_openai_embedder_batches_at_100_and_rejects_wrong_dimension():
    from backend.rag.embedding import OpenAIEmbedder

    fake = FakeOpenAIEmbedFn()
    embedder = OpenAIEmbedder(embed_fn=fake)
    embedder.encode_queries([str(i) for i in range(201)])
    assert [len(call[2]) for call in fake.calls] == [100, 100, 1]

    with pytest.raises(EmbeddingBuildError, match="shape"):
        OpenAIEmbedder(embed_fn=FakeOpenAIEmbedFn(3)).encode_queries(["질문"])


def test_openai_embedder_accepts_openrouter_key_without_openai_key(monkeypatch):
    from backend.rag.embedding import OpenAIEmbedder
    from backend.services import llm

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fake = FakeOpenAIEmbedFn(1536)
    monkeypatch.setattr(llm, "openai_embeddings", fake)

    embedder = OpenAIEmbedder("openai/text-embedding-3-small")

    assert embedder.embed_fn is fake


def test_openai_dry_run_estimates_cost_without_writing(tmp_path, monkeypatch):
    from backend.rag.embedding import estimate_openai_embedding_run

    chunks = tmp_path / "chunks.jsonl"
    write_chunks(
        chunks,
        [row("c1", "a" * 64, "abc"), row("c2", "b" * 64, "12345")],
    )

    class Encoding:
        def encode(self, text):
            return list(text)

    monkeypatch.setitem(
        sys.modules,
        "tiktoken",
        types.SimpleNamespace(get_encoding=lambda name: Encoding()),
    )
    prefix = tmp_path / "openai"

    payload = estimate_openai_embedding_run(
        chunks, prefix, model_name="openai/text-embedding-3-large"
    )

    assert payload["input_tokens"] == 8
    assert payload["estimated_cost_usd_if_run"] == pytest.approx(0.000001, abs=1e-6)
    assert payload["api_call_required"] is True
    assert not list(tmp_path.glob("openai.*"))


def test_openai_large_3072_dry_run_has_same_cost_and_different_config(
    tmp_path, monkeypatch
):
    from backend.rag.embedding import estimate_openai_embedding_run

    chunks = tmp_path / "chunks.jsonl"
    write_chunks(chunks, [row("c1", "a" * 64, "abc")])

    class Encoding:
        def encode(self, text):
            return list(text)

    monkeypatch.setitem(
        sys.modules,
        "tiktoken",
        types.SimpleNamespace(get_encoding=lambda name: Encoding()),
    )

    small_dimension = estimate_openai_embedding_run(
        chunks,
        tmp_path / "large-1536",
        model_name="openai/text-embedding-3-large",
    )
    full_dimension = estimate_openai_embedding_run(
        chunks,
        tmp_path / "large-3072",
        model_name="openai/text-embedding-3-large-3072",
    )

    assert small_dimension["dimension"] == 1536
    assert full_dimension["dimension"] == 3072
    assert small_dimension["input_tokens"] == full_dimension["input_tokens"]
    assert (
        small_dimension["price_usd_per_million_tokens"]
        == full_dimension["price_usd_per_million_tokens"]
    )
    assert (
        small_dimension["estimated_cost_usd_if_run"]
        == full_dimension["estimated_cost_usd_if_run"]
    )
    assert small_dimension["config_sha256"] != full_dimension["config_sha256"]


def test_openai_large_dimensions_cannot_reuse_same_output(tmp_path, monkeypatch):
    from backend.rag.embedding import OpenAIEmbedder

    chunks = tmp_path / "chunks.jsonl"
    prefix = tmp_path / "openai-large"
    write_chunks(chunks, [row("c1", "a" * 64, "본문")])
    build_embeddings(
        chunks,
        prefix,
        model_name="openai/text-embedding-3-large",
        embedder=OpenAIEmbedder(embed_fn=FakeOpenAIEmbedFn(1536)),
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(EmbeddingBuildError, match="설정이 다릅니다"):
        build_embeddings(
            chunks,
            prefix,
            model_name="openai/text-embedding-3-large-3072",
        )


def test_completed_openai_vectors_skip_without_key_or_api_call(tmp_path, monkeypatch):
    from backend.rag.embedding import OpenAIEmbedder

    chunks = tmp_path / "chunks.jsonl"
    prefix = tmp_path / "openai"
    write_chunks(chunks, [row("c1", "a" * 64, "본문")])
    fake = FakeOpenAIEmbedFn()
    build_embeddings(
        chunks,
        prefix,
        model_name="openai/text-embedding-3-large",
        embedder=OpenAIEmbedder(embed_fn=fake),
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    resumed = build_embeddings(
        chunks, prefix, model_name="openai/text-embedding-3-large"
    )

    assert resumed["skipped_existing"] is True
    assert resumed["processed_this_run"] == 0
    assert len(fake.calls) == 1


def test_incomplete_existing_vector_stops_before_creating_api_client(tmp_path, monkeypatch):
    import backend.rag.embedding as embedding_module

    chunks = tmp_path / "chunks.jsonl"
    prefix = tmp_path / "openai"
    write_chunks(chunks, [row("c1", "a" * 64, "본문")])
    (tmp_path / "openai.vectors.npy").write_bytes(b"incomplete")

    def should_not_create(*args, **kwargs):
        raise AssertionError("API client must not be created")

    monkeypatch.setattr(embedding_module, "create_embedder", should_not_create)
    with pytest.raises(EmbeddingBuildError, match="기존 산출물"):
        build_embeddings(
            chunks, prefix, model_name="openai/text-embedding-3-large"
        )


def test_embedding_cli_dry_run_never_calls_builder(monkeypatch, capsys):
    import backend.rag.embedding as embedding_module

    seen = {}

    def fake_estimate(*args, **kwargs):
        seen["estimate"] = (args, kwargs)
        return {"dry_run": True, "input_tokens": 123, "estimated_cost_usd_this_run": 0.01}

    def fail_build(*args, **kwargs):
        raise AssertionError("dry-run에서 build_embeddings를 호출하면 안 됩니다")

    monkeypatch.setattr(embedding_module, "estimate_openai_embedding_run", fake_estimate)
    monkeypatch.setattr(embedding_module, "build_embeddings", fail_build)

    assert embedding_module.main([
        "build", "--chunks", "c.jsonl", "--output-prefix", "out",
        "--model", "openai/text-embedding-3-large", "--dry-run",
    ]) == 0

    assert seen["estimate"][1]["model_name"] == "openai/text-embedding-3-large"
    assert json.loads(capsys.readouterr().out)["input_tokens"] == 123
