"""임베딩 장치 옵션 테스트. 실제 GPU와 모델 없이 검증한다."""
import json
import os
import sys
import types

import numpy as np
import pytest

from backend.rag import embedding
from backend.rag.embedding import (
    EmbeddingBuildError,
    LocalEmbedder,
    build_embeddings,
    create_embedder,
    main as embedding_main,
)

E5 = "intfloat/multilingual-e5-small"
UPSTAGE = "upstage/solar-embedding-2"


class FakeEmbedder:
    dimension = 2

    def encode_passages(self, texts):
        return np.asarray([[1.0, 0.0] for _ in texts], dtype=np.float32)


def write_chunks(path):
    row = {"chunk_id": "c1", "content_sha256": "a" * 64, "content": "본문"}
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


class FakeModel:
    def __init__(self):
        self.device = None
        self.config = types.SimpleNamespace(hidden_size=4)

    def to(self, device):
        self.device = device
        return self

    def eval(self):
        return self


@pytest.fixture
def fake_libraries(monkeypatch):
    """torch·transformers를 가짜로 바꾼다. loaded에 모델을 읽은 기록이 남는다."""

    state = types.SimpleNamespace(cuda_available=True, loaded=[])

    def load_model(name, **_):
        state.loaded.append(name)
        return FakeModel()

    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: state.cuda_available)
    transformers = types.ModuleType("transformers")
    transformers.AutoModel = types.SimpleNamespace(from_pretrained=load_model)
    transformers.AutoTokenizer = types.SimpleNamespace(from_pretrained=lambda *a, **k: object())
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    return state


def test_default_device_keeps_config_hash(tmp_path):
    chunks = write_chunks(tmp_path / "chunks.jsonl")

    default = build_embeddings(chunks, tmp_path / "default", embedder=FakeEmbedder())
    explicit = build_embeddings(chunks, tmp_path / "cpu", embedder=FakeEmbedder(), device="cpu")

    assert "device" not in default
    assert default["config_sha256"] == explicit["config_sha256"]


def test_gpu_device_is_recorded_and_changes_hash(tmp_path):
    chunks = write_chunks(tmp_path / "chunks.jsonl")

    cpu = build_embeddings(chunks, tmp_path / "cpu", embedder=FakeEmbedder())
    gpu = build_embeddings(chunks, tmp_path / "gpu", embedder=FakeEmbedder(), device="cuda")

    assert gpu["device"] == "cuda"
    assert gpu["config_sha256"] != cpu["config_sha256"]


def test_device_and_input_header_are_recorded_together(tmp_path, monkeypatch):
    row = {
        "chunk_id": "c1",
        "content_sha256": "a" * 64,
        "content": "본문",
        "corp_name": "회사A",
        "fiscal_year": 2025,
        "section_path": ["가"],
    }
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    metadata = build_embeddings(
        chunks, tmp_path / "out", embedder=FakeEmbedder(), device="cuda", input_header="v1"
    )

    assert metadata["device"] == "cuda"
    assert metadata["input_header"] == "v1"


def test_resume_on_different_device_is_rejected(tmp_path):
    chunks = write_chunks(tmp_path / "chunks.jsonl")
    build_embeddings(chunks, tmp_path / "out", embedder=FakeEmbedder())
    before = (tmp_path / "out.vectors.npy").read_bytes()

    with pytest.raises(EmbeddingBuildError, match="progress"):
        build_embeddings(chunks, tmp_path / "out", embedder=FakeEmbedder(), device="cuda")

    assert (tmp_path / "out.vectors.npy").read_bytes() == before


def test_build_passes_device_to_create_embedder(tmp_path, monkeypatch):
    chunks = write_chunks(tmp_path / "chunks.jsonl")
    calls = []

    def fake_create(model_name, *, max_length, device):
        calls.append(device)
        return FakeEmbedder()

    monkeypatch.setattr(embedding, "create_embedder", fake_create)

    build_embeddings(chunks, tmp_path / "out", device="cuda")

    assert calls == ["cuda"]


def test_create_embedder_passes_device_to_local_model(fake_libraries):
    model = create_embedder(E5, device="cuda")

    assert model.device == "cuda"
    assert model.model.device == "cuda"


def test_create_embedder_defaults_to_cpu(fake_libraries):
    assert create_embedder(E5).device == "cpu"


def test_local_embedder_forces_huggingface_offline(fake_libraries, monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)

    create_embedder(E5)

    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_unavailable_gpu_fails_before_loading_model(fake_libraries):
    fake_libraries.cuda_available = False

    with pytest.raises(EmbeddingBuildError, match="GPU"):
        LocalEmbedder(E5, device="cuda")

    assert fake_libraries.loaded == []


def test_cpu_does_not_require_gpu(fake_libraries):
    fake_libraries.cuda_available = False

    assert LocalEmbedder(E5).device == "cpu"


def test_api_model_rejects_gpu_device(monkeypatch):
    # 키를 넣어 둔다. 키가 없으면 키 오류가 먼저 나서 거부 코드를 확인할 수 없다.
    monkeypatch.setenv("UPSTAGE_API_KEY", "test-placeholder")

    with pytest.raises(EmbeddingBuildError, match="--device"):
        create_embedder(UPSTAGE, device="cuda")

    assert create_embedder(UPSTAGE).dimension == 1024


def test_cli_passes_device(monkeypatch):
    calls = []

    def fake_build(*args, **kwargs):
        calls.append(kwargs)
        return {}

    monkeypatch.setattr(embedding, "build_embeddings", fake_build)
    base = ["build", "--chunks", "c.jsonl", "--output-prefix", "out"]

    embedding_main([*base, "--device", "cuda"])
    embedding_main(base)

    assert [call["device"] for call in calls] == ["cuda", "cpu"]


def test_cli_rejects_unknown_device():
    with pytest.raises(SystemExit):
        embedding_main(["build", "--chunks", "c", "--output-prefix", "o", "--device", "tpu"])
