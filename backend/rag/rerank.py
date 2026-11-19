"""1차 검색 후보를 교차 인코더로 재정렬한다. Spec: docs/specs/d3-reranker.md

재정렬 로직은 ``Reranker`` 인터페이스에만 의존하므로 테스트는 가짜 리랭커를 주입한다.
점수는 모델의 원시 logit을 그대로 저장한다. 후보 목록 안에서 정규화한 값은 질문끼리
비교할 수 없어 그래프의 "근거 충분" 임계값(W2.10)에 쓸 수 없다.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

DEFAULT_RERANK_BATCH_SIZE = 16


class RerankError(ValueError):
    """재정렬 입력이나 모델 출력이 계약과 다르다. 1차 순서로 대체하지 않는다."""


class Reranker(Protocol):
    model_name: str

    def score(self, query: str, texts: Sequence[str]) -> Sequence[float]: ...


def rerank_document_with_header(chunk: Mapping[str, Any]) -> str:
    """리랭커 입력용 문서: '회사명 | 절 경로' 머리말 한 줄 + 본문.

    청크 본문에는 '당사'처럼 주체가 없고 절 이름(연결·별도 주석, II장 등)도 없어, 질문의
    '연결 기준'·회사명과 맞춰 보기 어렵다. 절 경로 맨 앞이 '3. 연결재무제표 주석' 같은 구분이다.
    """

    from backend.graph.trust_boundary import sanitized_for_index  # 141번: 공격 문장이 리랭커 점수를 바꾸지 못하게

    path = " > ".join(str(part) for part in chunk.get("section_path") or [])
    return f"{chunk.get('corp_name', '')} | {path}\n{sanitized_for_index(str(chunk['content']))}"


def rerank_candidates(
    query: str,
    candidates: Sequence[Mapping[str, Any]],
    reranker: Reranker,
    *,
    document_text: Callable[[Mapping[str, Any]], str] | None = None,
) -> list[dict[str, Any]]:
    """후보를 점수 내림차순으로 정렬한다. 동률은 chunk_id 오름차순으로 고정한다.

    document_text가 없으면 본문(content)만 리랭커에 넘긴다(기존 동작).
    """

    if not candidates:
        return []
    from backend.graph.trust_boundary import sanitized_for_index

    to_text = document_text or (lambda chunk: sanitized_for_index(str(chunk["content"])))  # 141번
    scores = list(reranker.score(query, [to_text(chunk) for chunk in candidates]))
    if len(scores) != len(candidates):
        raise RerankError(f"점수 개수가 후보와 다릅니다: {len(scores)} != {len(candidates)}")
    if not all(math.isfinite(value) for value in scores):
        raise RerankError("리랭커 점수에 NaN 또는 Inf가 있습니다")
    ranked = [
        {
            "chunk_id": str(chunk["chunk_id"]),
            "first_stage_rank": rank,
            "score": float(score),
        }
        for rank, (chunk, score) in enumerate(zip(candidates, scores), start=1)
    ]
    return sorted(ranked, key=lambda item: (-item["score"], item["chunk_id"]))


def reranked_chunk_ids(
    ranked: Sequence[Mapping[str, Any]],
    top_k: int,
    *,
    candidate_n: int | None = None,
) -> list[str]:
    """상위 k개. ``candidate_n``을 주면 1차 상위 n개 안의 후보만 남긴다.

    점수는 쌍마다 독립이므로 후보를 줄인 결과를 다시 계산하지 않고 얻을 수 있다.
    """

    kept = [
        item
        for item in ranked
        if candidate_n is None or int(item["first_stage_rank"]) <= candidate_n
    ]
    return [str(item["chunk_id"]) for item in kept[:top_k]]


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def latency_summary(latencies_ms: Sequence[float]) -> dict[str, Any]:
    """첫 질의는 캐시가 차는 구간이라 뺀다."""

    measured = list(latencies_ms[1:])
    if not measured:
        return {"queries": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    return {
        "queries": len(measured),
        "p50_ms": _percentile(measured, 0.5),
        "p95_ms": _percentile(measured, 0.95),
        "max_ms": max(measured),
    }


class CrossEncoderReranker:
    """transformers로 돌리는 로컬 교차 인코더. 모델은 미리 받아 둬야 한다."""

    def __init__(
        self,
        model_name: str,
        *,
        batch_size: int = DEFAULT_RERANK_BATCH_SIZE,
        max_length: int = 512,
        device: str = "cpu",
    ) -> None:
        if batch_size < 1:
            raise RerankError("batch_size는 1 이상이어야 합니다")
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:
            raise RerankError("backend/requirements-ml.txt 의존성이 필요합니다") from exc
        self._torch = torch
        self.model_name = model_name
        self.batch_size = batch_size
        self.max_length = max_length
        self.device = device
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=True)
            self.model = AutoModelForSequenceClassification.from_pretrained(
                model_name, local_files_only=True
            ).to(device)
        except OSError as exc:
            raise RerankError(f"로컬에 모델이 없습니다. 먼저 내려받으세요: {model_name}") from exc
        self.model.eval()

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        scores: list[float] = []
        for start in range(0, len(texts), self.batch_size):
            pairs = [[query, text] for text in texts[start : start + self.batch_size]]
            batch = self.tokenizer(
                pairs,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )
            batch = {key: value.to(self.device) for key, value in batch.items()}
            with self._torch.inference_mode():
                logits = self.model(**batch).logits
            scores.extend(logits.view(-1).float().cpu().tolist())
        return scores


class VoyageReranker:
    """Voyage API를 기존 Reranker 점수 계약에 맞춘 어댑터."""

    SUPPORTED = {"voyage/rerank-3", "voyage/rerank-2.5"}

    def __init__(self, model_name: str, *, score_fn=None) -> None:
        if model_name not in self.SUPPORTED:
            raise RerankError(f"지원하지 않는 Voyage 모델입니다: {model_name}")
        self.model_name = model_name
        self.api_model = model_name.removeprefix("voyage/")
        if score_fn is None:
            if not os.getenv("VOYAGE_API_KEY"):
                raise RerankError(
                    "VOYAGE_API_KEY가 없습니다. 저장소 루트의 .env에 키를 넣고 다시 실행하세요"
                )
            from backend.services.llm import voyage_rerank

            score_fn = voyage_rerank
        self.score_fn = score_fn

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        return list(self.score_fn(query, list(texts), model=self.api_model))


class OpenRouterReranker:
    """OpenRouter /rerank를 기존 Reranker 점수 계약에 맞춘 어댑터(예: openrouter/voyageai/rerank-3)."""

    def __init__(self, model_name: str, *, score_fn=None) -> None:
        if not model_name.startswith("openrouter/") or model_name.count("/") < 2:
            raise RerankError(f"OpenRouter 모델 이름 형식이 아닙니다: {model_name}")
        self.model_name = model_name
        self.api_model = model_name.removeprefix("openrouter/")
        if score_fn is None:
            from backend.services.llm import openrouter_rerank

            score_fn = openrouter_rerank
        self.score_fn = score_fn

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        return list(self.score_fn(query, list(texts), model=self.api_model))


class LLMReranker:
    """LLM listwise 순위를 Reranker 점수 계약에 맞춘 어댑터(예: llm/openai/gpt-4.1).

    호출은 llm.py(llm_rerank_scores)에서만 한다(AGENTS.md 2번). 후보 전체를 한 번에 보낸다.
    """

    def __init__(self, model_name: str, *, score_fn=None) -> None:
        if not model_name.startswith("llm/") or model_name.count("/") < 2:
            raise RerankError(f"LLM 리랭커 이름 형식이 아닙니다(llm/<공급자>/<모델>): {model_name}")
        self.model_name = model_name
        self.api_model = model_name.removeprefix("llm/")
        if score_fn is None:
            from backend.services.llm import llm_rerank_scores

            score_fn = llm_rerank_scores
        self.score_fn = score_fn

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        return list(self.score_fn(query, list(texts), model=self.api_model))


def create_reranker(
    model_name: str,
    *,
    max_length: int = 512,
    batch_size: int = DEFAULT_RERANK_BATCH_SIZE,
) -> Reranker:
    if model_name.startswith("llm/"):
        return LLMReranker(model_name)
    if model_name.startswith("openrouter/"):
        return OpenRouterReranker(model_name)
    if model_name.startswith("voyage/"):
        return VoyageReranker(model_name)
    return CrossEncoderReranker(
        model_name,
        max_length=max_length,
        batch_size=batch_size,
    )
