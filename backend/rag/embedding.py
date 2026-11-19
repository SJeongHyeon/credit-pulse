"""로컬 임베딩 배치: 고유 본문만 처리하고 중단 후 재개한다.

모델마다 풀링 방식과 질의·문서 접두사가 다르므로 ``MODEL_PROFILES``에 등록한
모델만 받는다. 등록되지 않은 모델의 규칙을 추측하지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from dotenv import find_dotenv, load_dotenv

import numpy as np


INPUT_HEADER_VERSIONS = ("v1",)
MAX_HEADER_FIELD_CHARS = 40
MAX_HEADER_CHARS = 120


class EmbeddingBuildError(ValueError):
    """임베딩 입력·진행상태·모델 출력이 계약과 다를 때 발생한다."""


MODEL_PROFILES: dict[str, dict[str, str]] = {
    # E5: mean pooling, "query: " / "passage: " 접두사 (D2 기준선)
    "intfloat/multilingual-e5-small": {
        "pooling": "mean",
        "query_prefix": "query: ",
        "passage_prefix": "passage: ",
    },
    # KURE-v1: sentence-transformers 1_Pooling/config.json의 pooling_mode_cls_token=true
    "nlpai-lab/KURE-v1": {
        "pooling": "cls",
        "query_prefix": "",
        "passage_prefix": "",
    },
    # bge-m3 dense: M3Embedder 기본값 pooling_method="cls",
    # query_instruction_for_retrieval=None, normalize_embeddings=True
    "BAAI/bge-m3": {
        "pooling": "cls",
        "query_prefix": "",
        "passage_prefix": "",
    },
    # Upstage Solar Embedding 2 (API, 1024차원). 질의·문서에 서로 다른 모델을 쓴다.
    # OpenAI 호환 API(base_url https://api.upstage.ai/v1). 옛 별칭 embedding-query/passage는
    # 2026-12-31 지원 종료라 쓰지 않는다. 키는 UPSTAGE_API_KEY 환경변수에서만 읽는다.
    "upstage/solar-embedding-2": {
        "pooling": "api",
        "query_prefix": "",
        "passage_prefix": "",
        "provider": "upstage",
        "query_model": "solar-embedding-2-query",
        "passage_model": "solar-embedding-2-passage",
        "dimension": "1024",
    },
    "openai/text-embedding-3-small": {
        "pooling": "api",
        "query_prefix": "",
        "passage_prefix": "",
        "provider": "openai",
        "api_model": "text-embedding-3-small",
        "dimension": "1536",
    },
    "openai/text-embedding-3-large": {
        "pooling": "api",
        "query_prefix": "",
        "passage_prefix": "",
        "provider": "openai",
        "api_model": "text-embedding-3-large",
        "dimension": "1536",
    },
    "openai/text-embedding-3-large-3072": {
        "pooling": "api",
        "query_prefix": "",
        "passage_prefix": "",
        "provider": "openai",
        "api_model": "text-embedding-3-large",
        "dimension": "3072",
    },
}

UPSTAGE_MAX_BATCH = 100
OPENAI_MAX_BATCH = 100
TEAM_API_BUDGET_USD = 30.0
OPENAI_PRICE_PER_MILLION_TOKENS = {
    "text-embedding-3-small": 0.02,
    "text-embedding-3-large": 0.13,
}
OPENAI_PRICING_SOURCE = "https://developers.openai.com/api/docs/models/text-embedding-3-large"
OPENAI_PRICING_CHECKED_AT = "2026-11-03"


def model_profile(model_name: str) -> dict[str, str]:
    """등록된 모델의 풀링·접두사 규칙을 돌려준다. 미등록 모델은 오류."""

    try:
        return dict(MODEL_PROFILES[model_name])
    except KeyError as exc:
        known = ", ".join(sorted(MODEL_PROFILES))
        raise EmbeddingBuildError(
            f"등록되지 않은 임베딩 모델입니다: {model_name}. 등록 모델: {known}"
        ) from exc


class PassageEmbedder(Protocol):
    dimension: int

    def encode_passages(self, texts: list[str]) -> np.ndarray: ...


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def _header_field(value: Any) -> str:
    text = " ".join(str(value).split())
    if len(text) <= MAX_HEADER_FIELD_CHARS:
        return text
    return f"{text[:MAX_HEADER_FIELD_CHARS - 1]}…"


def _metadata_header(row: dict[str, Any]) -> str:
    """청크 메타데이터로 머리말을 만든다. 없는 칸은 구분자까지 뺀다."""

    sections = [_header_field(value) for value in (row.get("section_path") or [])[-2:]]
    parts = [_header_field(row["corp_name"]), f"FY{row['fiscal_year']} 사업보고서"]
    if sections:
        parts.append(" > ".join(sections))
    table_title = row.get("table_title")
    if table_title and _header_field(table_title) not in sections:
        parts.append(f"표: {_header_field(table_title)}")
    header = f"[{' | '.join(parts)}]"
    if len(header) > MAX_HEADER_CHARS:
        header = f"{header[:MAX_HEADER_CHARS - 2]}…]"
    return header


def embedding_input(row: dict[str, Any], input_header: str | None) -> str:
    """모델에 넣을 텍스트(passage 접두사 제외). 옵션이 꺼져 있으면 본문 그대로다."""

    if input_header is None:
        return str(row["content"])
    if input_header not in INPUT_HEADER_VERSIONS:
        raise EmbeddingBuildError(f"지원하지 않는 input_header입니다: {input_header}")
    return f"{_metadata_header(row)}\n{row['content']}"


def embedding_key(row: dict[str, Any], input_header: str | None) -> str:
    """청크가 쓸 벡터의 키. 머리말이 다르면 본문이 같아도 다른 벡터를 쓴다."""

    if input_header is None:
        return str(row["content_sha256"])
    return hashlib.sha256(embedding_input(row, input_header).encode("utf-8")).hexdigest()


def load_unique_embedding_rows(
    chunks_path: str | Path,
    *,
    limit: int | None = None,
    corp_codes: set[str] | None = None,
    input_header: str | None = None,
) -> list[dict[str, Any]]:
    """eligible 청크를 content hash로 중복 제거해 길이·해시순으로 반환한다.

    ``corp_codes``가 주어지면 그 회사의 청크만 남긴다(평가 질문에 나온 회사만
    임베딩해 모델 비교 시간을 줄이는 용도).

    ``input_header``가 주어지면 머리말을 붙인 입력 기준으로 중복을 제거한다.
    이때 행의 ``content``는 머리말을 포함하고 ``embedding_key``를 갖는다.
    """

    if corp_codes is not None and not corp_codes:
        raise EmbeddingBuildError("corp_codes가 비어 있습니다")
    unique: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(
        Path(chunks_path).open(encoding="utf-8"), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            sha256 = embedding_key(row, input_header)
            content = embedding_input(row, input_header)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise EmbeddingBuildError(f"chunks JSONL {line_number}행 형식 오류") from exc
        if row.get("near_dup") or row.get("superseded"):
            continue
        if corp_codes is not None and str(row.get("corp_code")) not in corp_codes:
            continue
        previous = unique.get(sha256)
        if previous is not None and previous["content"] != content:
            raise EmbeddingBuildError(f"같은 content_sha256에 다른 본문: {sha256}")
        unique.setdefault(
            sha256,
            {
                "content_sha256": sha256,
                "content": content,
                "representative_chunk_id": str(row["chunk_id"]),
            },
        )
    rows = sorted(unique.values(), key=lambda row: (len(row["content"]), row["content_sha256"]))
    if input_header is not None:
        for row in rows:
            row["embedding_key"] = row.pop("content_sha256")
    if limit is not None:
        if limit < 1:
            raise EmbeddingBuildError("limit은 1 이상이어야 합니다")
        rows = rows[:limit]
    return rows


class LocalEmbedder:
    """transformers AutoModel 기반 로컬 embedder. 풀링·접두사는 MODEL_PROFILES를 따른다."""

    def __init__(
        self,
        model_name: str,
        *,
        max_length: int = 512,
        device: str = "cpu",
    ) -> None:
        profile = model_profile(model_name)
        self.pooling = profile["pooling"]
        self.query_prefix = profile["query_prefix"]
        self.passage_prefix = profile["passage_prefix"]
        # transformers 5.x는 local_files_only=True여도 PEFT adapter 존재 여부를
        # 확인하려고 Hub에 HEAD 요청을 보낼 수 있다. 실험 모델은 미리 받은
        # 파일만 써야 하므로 Hub와 transformers를 명시적으로 오프라인으로 둔다.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise EmbeddingBuildError(
                "backend/requirements-ml.txt 의존성이 필요합니다"
            ) from exc
        if device == "cuda" and not torch.cuda.is_available():
            raise EmbeddingBuildError(
                "GPU를 쓸 수 없습니다. CUDA용 torch와 드라이버를 확인하거나 --device cpu로 실행하세요"
            )
        self._torch = torch
        self.max_length = max_length
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            local_files_only=True,
        )
        self.model = AutoModel.from_pretrained(
            model_name,
            local_files_only=True,
        ).to(device)
        self.model.eval()
        self.device = device
        dimension = getattr(self.model.config, "hidden_size", None)
        if not isinstance(dimension, int):
            raise EmbeddingBuildError("모델 hidden_size를 확인할 수 없습니다")
        self.dimension = dimension

    def _encode(self, texts: list[str], *, prefix: str) -> np.ndarray:
        torch = self._torch
        prefixed = [f"{prefix}{text}" for text in texts]
        batch = self.tokenizer(
            prefixed,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        batch = {key: value.to(self.device) for key, value in batch.items()}
        with torch.inference_mode():
            hidden = self.model(**batch).last_hidden_state
            if self.pooling == "cls":
                vectors = hidden[:, 0]
            else:
                mask = batch["attention_mask"].unsqueeze(-1)
                vectors = (hidden * mask).sum(dim=1) / mask.sum(dim=1)
            vectors = torch.nn.functional.normalize(vectors, p=2, dim=1)
        return vectors.cpu().numpy().astype(np.float32, copy=False)

    def encode_passages(self, texts: list[str]) -> np.ndarray:
        return self._encode(texts, prefix=self.passage_prefix)

    def encode_queries(self, texts: list[str]) -> np.ndarray:
        return self._encode(texts, prefix=self.query_prefix)


# D2 코드와 문서가 쓰던 이름. E5 동작은 그대로다.
LocalE5Embedder = LocalEmbedder


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if not np.isfinite(vectors).all() or (norms == 0).any():
        raise EmbeddingBuildError("API 임베딩에 NaN·Inf 또는 0 벡터가 있습니다")
    return (vectors / norms).astype(np.float32, copy=False)


class UpstageEmbedder:
    """Upstage Solar Embedding 2 API embedder.

    외부 API 호출은 ``backend.services.llm.upstage_embeddings``가 맡고(AGENTS.md 2번),
    이 클래스는 배치 분할·차원 검사·L2 정규화만 한다. 테스트는 ``embed_fn``을 주입한다.
    요청에는 청크 본문(공개 공시)과 질의만 실린다.
    """

    def __init__(
        self,
        model_name: str = "upstage/solar-embedding-2",
        *,
        embed_fn: Callable[..., list[list[float]]] | None = None,
    ) -> None:
        profile = model_profile(model_name)
        if profile.get("provider") != "upstage":
            raise EmbeddingBuildError(f"Upstage 모델이 아닙니다: {model_name}")
        self.query_model = profile["query_model"]
        self.passage_model = profile["passage_model"]
        self.dimension = int(profile["dimension"])
        self.pooling = profile["pooling"]
        self.query_prefix = profile["query_prefix"]
        self.passage_prefix = profile["passage_prefix"]
        if embed_fn is None:
            if not os.getenv("UPSTAGE_API_KEY"):
                raise EmbeddingBuildError(
                    "UPSTAGE_API_KEY가 없습니다. 저장소 루트의 .env에 키를 넣고 다시 실행하세요"
                )
            from backend.services.llm import upstage_embeddings

            embed_fn = upstage_embeddings
        self.embed_fn = embed_fn

    def _encode(self, texts: list[str], *, model: str) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        output: list[list[float]] = []
        for start in range(0, len(texts), UPSTAGE_MAX_BATCH):
            batch = texts[start : start + UPSTAGE_MAX_BATCH]
            vectors_batch = self.embed_fn(batch, model=model)
            if len(vectors_batch) != len(batch):
                raise EmbeddingBuildError("API 응답 개수가 요청과 다릅니다")
            output.extend(vectors_batch)
        vectors = np.asarray(output, dtype=np.float32)
        if vectors.shape != (len(texts), self.dimension):
            raise EmbeddingBuildError(
                f"API 임베딩 shape 오류: {vectors.shape}, 기대 {(len(texts), self.dimension)}"
            )
        return _l2_normalize(vectors)

    def encode_passages(self, texts: list[str]) -> np.ndarray:
        return self._encode(texts, model=self.passage_model)

    def encode_queries(self, texts: list[str]) -> np.ndarray:
        return self._encode(texts, model=self.query_model)


class OpenAIEmbedder:
    """OpenAI v3 API embedder. 호출 자체는 services.llm에만 둔다."""

    def __init__(
        self,
        model_name: str = "openai/text-embedding-3-large",
        *,
        embed_fn: Callable[..., list[list[float]]] | None = None,
    ) -> None:
        profile = model_profile(model_name)
        if profile.get("provider") != "openai":
            raise EmbeddingBuildError(f"OpenAI 모델이 아닙니다: {model_name}")
        self.api_model = profile["api_model"]
        self.dimension = int(profile["dimension"])
        self.pooling = profile["pooling"]
        self.query_prefix = profile["query_prefix"]
        self.passage_prefix = profile["passage_prefix"]
        if embed_fn is None:
            if not (os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")):
                raise EmbeddingBuildError(
                    "OPENROUTER_API_KEY 또는 OPENAI_API_KEY가 없습니다. 저장소 루트의 .env에 키를 넣고 다시 실행하세요"
                )
            from backend.services import llm

            embed_fn = llm.openai_embeddings
        self.embed_fn = embed_fn

    def _encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        output: list[list[float]] = []
        for start in range(0, len(texts), OPENAI_MAX_BATCH):
            batch = texts[start : start + OPENAI_MAX_BATCH]
            vectors_batch = self.embed_fn(
                batch, model=self.api_model, dimensions=self.dimension
            )
            if len(vectors_batch) != len(batch):
                raise EmbeddingBuildError("API 응답 개수가 요청과 다릅니다")
            output.extend(vectors_batch)
        vectors = np.asarray(output, dtype=np.float32)
        if vectors.shape != (len(texts), self.dimension):
            raise EmbeddingBuildError(
                f"API 임베딩 shape 오류: {vectors.shape}, 기대 {(len(texts), self.dimension)}"
            )
        return _l2_normalize(vectors)

    def encode_passages(self, texts: list[str]) -> np.ndarray:
        return self._encode(texts)

    def encode_queries(self, texts: list[str]) -> np.ndarray:
        return self._encode(texts)


def create_embedder(model_name: str, *, max_length: int = 512, device: str = "cpu") -> Any:
    """등록된 모델 규칙에 맞는 embedder를 만든다(로컬 또는 API)."""

    profile = model_profile(model_name)
    if profile.get("provider") == "upstage":
        if device != "cpu":
            raise EmbeddingBuildError("API 모델에는 --device를 지정할 수 없습니다")
        return UpstageEmbedder(model_name)
    if profile.get("provider") == "openai":
        if device != "cpu":
            raise EmbeddingBuildError("API 모델에는 --device를 지정할 수 없습니다")
        return OpenAIEmbedder(model_name)
    return LocalEmbedder(model_name, max_length=max_length, device=device)


def _artifact_paths(output_prefix: str | Path) -> dict[str, Path]:
    prefix = Path(output_prefix)
    return {
        "ids": Path(f"{prefix}.ids.jsonl"),
        "vectors": Path(f"{prefix}.vectors.npy"),
        "progress": Path(f"{prefix}.progress.json"),
        "metadata": Path(f"{prefix}.metadata.json"),
    }


def _config_hash(config: dict[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _embedding_config(
    source: Path,
    *,
    model_name: str,
    batch_size: int,
    max_length: int,
    limit: int | None,
    rows: list[dict[str, Any]],
    profile: dict[str, str],
    corp_codes: set[str] | None,
    input_header: str | None,
    device: str,
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "chunks_path": str(source),
        "chunks_sha256": _sha256(source),
        "model_name": model_name,
        "batch_size": batch_size,
        "max_length": max_length,
        "limit": limit,
        "rows": len(rows),
        "ordering": "content_length_then_sha256",
        "passage_prefix": profile["passage_prefix"],
    }
    if profile["pooling"] != "mean":
        config["pooling"] = profile["pooling"]
    if "api_model" in profile:
        config["api_model"] = profile["api_model"]
    if "dimension" in profile:
        config["dimension"] = int(profile["dimension"])
    if corp_codes is not None:
        config["corp_codes"] = sorted(corp_codes)
    if input_header is not None:
        config["input_header"] = input_header
    if device != "cpu":
        config["device"] = device
    return config


def estimate_openai_embedding_run(
    chunks_path: str | Path,
    output_prefix: str | Path,
    *,
    model_name: str,
    batch_size: int = 64,
    max_length: int = 512,
    limit: int | None = None,
    overwrite: bool = False,
    corp_codes: set[str] | None = None,
    input_header: str | None = None,
    device: str = "cpu",
) -> dict[str, Any]:
    """외부 호출·파일 쓰기 없이 OpenAI 임베딩 토큰과 최대 비용을 계산한다."""

    profile = model_profile(model_name)
    if profile.get("provider") != "openai":
        raise EmbeddingBuildError("--dry-run은 OpenAI 임베딩 모델에만 지원됩니다")
    source = Path(chunks_path)
    rows = load_unique_embedding_rows(
        source, limit=limit, corp_codes=corp_codes, input_header=input_header
    )
    if not rows:
        raise EmbeddingBuildError("임베딩할 eligible 본문이 없습니다")
    config = _embedding_config(
        source,
        model_name=model_name,
        batch_size=batch_size,
        max_length=max_length,
        limit=limit,
        rows=rows,
        profile=profile,
        corp_codes=corp_codes,
        input_header=input_header,
        device=device,
    )
    config_sha256 = _config_hash(config)
    paths = _artifact_paths(output_prefix)
    api_call_required = True
    if not overwrite and all(paths[name].exists() for name in ("ids", "vectors", "progress", "metadata")):
        progress = json.loads(paths["progress"].read_text(encoding="utf-8"))
        metadata = json.loads(paths["metadata"].read_text(encoding="utf-8"))
        api_call_required = not (
            progress.get("config_sha256") == config_sha256
            and metadata.get("config_sha256") == config_sha256
            and int(progress.get("completed", -1)) == len(rows)
            and metadata.get("complete") is True
        )

    try:
        import tiktoken
    except ImportError as exc:
        raise EmbeddingBuildError("backend/requirements-ml.txt의 tiktoken이 필요합니다") from exc
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
    except Exception as exc:
        raise EmbeddingBuildError(
            "cl100k_base 토크나이저를 준비할 수 없습니다. 첫 실행은 네트워크가 필요합니다"
        ) from exc
    input_tokens = sum(len(encoding.encode(str(row["content"]))) for row in rows)
    api_model = profile["api_model"]
    price = OPENAI_PRICE_PER_MILLION_TOKENS[api_model]
    cost_if_run = input_tokens * price / 1_000_000
    return {
        "dry_run": True,
        "provider": "openai",
        "model": api_model,
        "dimension": int(profile["dimension"]),
        "rows": len(rows),
        "input_tokens": input_tokens,
        "price_usd_per_million_tokens": price,
        "estimated_cost_usd_if_run": round(cost_if_run, 6),
        "estimated_cost_usd_this_run": round(cost_if_run if api_call_required else 0.0, 6),
        "api_call_required": api_call_required,
        "team_budget_usd": TEAM_API_BUDGET_USD,
        "within_team_budget": cost_if_run <= TEAM_API_BUDGET_USD,
        "pricing_source": OPENAI_PRICING_SOURCE,
        "pricing_checked_at": OPENAI_PRICING_CHECKED_AT,
        "config_sha256": config_sha256,
    }


def build_embeddings(
    chunks_path: str | Path,
    output_prefix: str | Path,
    *,
    model_name: str = "intfloat/multilingual-e5-small",
    batch_size: int = 64,
    max_length: int = 512,
    limit: int | None = None,
    overwrite: bool = False,
    embedder: PassageEmbedder | None = None,
    corp_codes: set[str] | None = None,
    input_header: str | None = None,
    device: str = "cpu",
) -> dict[str, Any]:
    if batch_size < 1:
        raise EmbeddingBuildError("batch_size는 1 이상이어야 합니다")
    if max_length < 8:
        raise EmbeddingBuildError("max_length는 8 이상이어야 합니다")

    source = Path(chunks_path)
    if embedder is None or model_name in MODEL_PROFILES:
        profile = model_profile(model_name)
    else:
        # 테스트처럼 embedder를 직접 주입한 경우: 그 embedder가 밝힌 규칙을 기록한다.
        profile = {
            "pooling": getattr(embedder, "pooling", "mean"),
            "query_prefix": getattr(embedder, "query_prefix", "query: "),
            "passage_prefix": getattr(embedder, "passage_prefix", "passage: "),
        }
    rows = load_unique_embedding_rows(
        source, limit=limit, corp_codes=corp_codes, input_header=input_header
    )
    if not rows:
        raise EmbeddingBuildError("임베딩할 eligible 본문이 없습니다")
    paths = _artifact_paths(output_prefix)
    config = _embedding_config(
        source,
        model_name=model_name,
        batch_size=batch_size,
        max_length=max_length,
        limit=limit,
        rows=rows,
        profile=profile,
        corp_codes=corp_codes,
        input_header=input_header,
        device=device,
    )
    config_sha256 = _config_hash(config)

    if overwrite:
        completed = 0
    elif paths["progress"].exists():
        progress = json.loads(paths["progress"].read_text(encoding="utf-8"))
        if progress.get("config_sha256") != config_sha256:
            raise EmbeddingBuildError(
                "기존 progress의 입력·모델·설정이 다릅니다. --overwrite가 필요합니다"
            )
        completed = int(progress.get("completed", 0))
    elif any(path.exists() for path in paths.values()):
        raise EmbeddingBuildError("기존 산출물이 있습니다. --overwrite가 필요합니다")
    else:
        completed = 0

    if (
        not overwrite
        and completed == len(rows)
        and paths["metadata"].exists()
        and paths["ids"].exists()
        and paths["vectors"].exists()
    ):
        metadata = json.loads(paths["metadata"].read_text(encoding="utf-8"))
        if metadata.get("config_sha256") != config_sha256:
            raise EmbeddingBuildError("완료 metadata의 설정 해시가 다릅니다")
        return {
            **metadata,
            "processed_this_run": 0,
            "elapsed_seconds_this_run": 0.0,
            "rows_per_second_this_run": None,
            "skipped_existing": True,
        }

    if embedder is None:
        if profile.get("provider") == "upstage" and batch_size > UPSTAGE_MAX_BATCH:
            raise EmbeddingBuildError("Upstage API는 요청당 최대 100개입니다. --batch-size를 줄이세요")
        if profile.get("provider") == "openai" and batch_size > OPENAI_MAX_BATCH:
            raise EmbeddingBuildError("OpenAI API는 이 프로젝트에서 요청당 최대 100개입니다. --batch-size를 줄이세요")
        embedder = create_embedder(model_name, max_length=max_length, device=device)
    dimension = int(embedder.dimension)
    if dimension < 1:
        raise EmbeddingBuildError("임베딩 차원은 1 이상이어야 합니다")

    # 머리말 산출물에는 content_sha256을 넣지 않는다. 본문 해시로 연결하는
    # load_pgvector()가 이 파일을 형식 오류로 거부해 DB에 잘못 들어가지 않는다.
    key_field = "content_sha256" if input_header is None else "embedding_key"
    id_rows = [
        {
            "index": index,
            key_field: row[key_field],
            "representative_chunk_id": row["representative_chunk_id"],
        }
        for index, row in enumerate(rows)
    ]
    if completed == 0:
        _write_jsonl_atomic(paths["ids"], id_rows)
        paths["vectors"].parent.mkdir(parents=True, exist_ok=True)
        vectors = np.lib.format.open_memmap(
            paths["vectors"],
            mode="w+",
            dtype=np.float32,
            shape=(len(rows), dimension),
        )
        _write_json_atomic(
            paths["progress"],
            {
                "config_sha256": config_sha256,
                "completed": 0,
                "rows": len(rows),
                "dimension": dimension,
            },
        )
    else:
        vectors = np.lib.format.open_memmap(paths["vectors"], mode="r+")
        if vectors.shape != (len(rows), dimension):
            raise EmbeddingBuildError("기존 벡터 shape가 현재 설정과 다릅니다")
        if completed < 0 or completed > len(rows):
            raise EmbeddingBuildError("progress completed 범위가 올바르지 않습니다")

    started = time.perf_counter()
    start_completed = completed
    for start in range(completed, len(rows), batch_size):
        end = min(start + batch_size, len(rows))
        batch_vectors = np.asarray(
            embedder.encode_passages([row["content"] for row in rows[start:end]]),
            dtype=np.float32,
        )
        if batch_vectors.shape != (end - start, dimension):
            raise EmbeddingBuildError("모델 출력 shape가 올바르지 않습니다")
        if not np.isfinite(batch_vectors).all():
            raise EmbeddingBuildError("모델 출력에 NaN 또는 Inf가 있습니다")
        norms = np.linalg.norm(batch_vectors, axis=1)
        if not np.allclose(norms, 1.0, atol=1e-5):
            raise EmbeddingBuildError("모델 출력이 L2 정규화되지 않았습니다")
        vectors[start:end] = batch_vectors
        vectors.flush()
        completed = end
        _write_json_atomic(
            paths["progress"],
            {
                "config_sha256": config_sha256,
                "completed": completed,
                "rows": len(rows),
                "dimension": dimension,
            },
        )

    elapsed = time.perf_counter() - started
    processed_now = completed - start_completed
    metadata = {
        **config,
        "config_sha256": config_sha256,
        "dimension": dimension,
        "dtype": "float32",
        "completed": completed,
        "complete": completed == len(rows),
        "processed_this_run": processed_now,
        "elapsed_seconds_this_run": round(elapsed, 3),
        "rows_per_second_this_run": (
            round(processed_now / elapsed, 3) if elapsed > 0 and processed_now else None
        ),
        "ids_sha256": _sha256(paths["ids"]),
        "vectors_sha256": _sha256(paths["vectors"]),
    }
    _write_json_atomic(paths["metadata"], metadata)
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="로컬 임베딩 배치")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--chunks", required=True)
    build.add_argument("--output-prefix", required=True)
    build.add_argument(
        "--model",
        default="intfloat/multilingual-e5-small",
        choices=sorted(MODEL_PROFILES),
    )
    build.add_argument("--batch-size", type=int, default=64)
    build.add_argument("--max-length", type=int, default=512)
    build.add_argument("--limit", type=int)
    build.add_argument("--overwrite", action="store_true")
    build.add_argument(
        "--dry-run",
        action="store_true",
        help="OpenAI API 호출·파일 쓰기 전에 토큰 수와 예상 비용만 출력",
    )
    build.add_argument(
        "--corp-codes",
        help="쉼표로 구분한 corp_code. 주면 그 회사 청크만 임베딩한다",
    )
    build.add_argument(
        "--input-header",
        choices=INPUT_HEADER_VERSIONS,
        help="임베딩 입력에 붙일 메타데이터 머리말 버전. 없으면 본문만 쓴다",
    )
    build.add_argument(
        "--device",
        choices=("cpu", "cuda"),
        default="cpu",
        help="로컬 모델을 돌릴 장치. API 모델에는 지정할 수 없다",
    )
    args = parser.parse_args(argv)
    # 문서 명령은 저장소 루트에서 실행한다. 이미 설정된 환경변수는 덮어쓰지 않는다.
    load_dotenv(find_dotenv(usecwd=True), override=False)
    corp_codes = (
        {code.strip() for code in args.corp_codes.split(",") if code.strip()}
        if args.corp_codes
        else None
    )
    action = estimate_openai_embedding_run if args.dry_run else build_embeddings
    metadata = action(
        args.chunks,
        args.output_prefix,
        model_name=args.model,
        batch_size=args.batch_size,
        max_length=args.max_length,
        limit=args.limit,
        overwrite=args.overwrite,
        corp_codes=corp_codes,
        input_header=args.input_header,
        device=args.device,
    )
    print(json.dumps(metadata, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
