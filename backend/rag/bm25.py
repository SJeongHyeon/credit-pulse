"""키워드 검색(BM25)과 순위 결합(RRF)의 순수 계산. Spec: docs/specs/d3-bm25-hybrid.md

토크나이저와 색인은 저장소나 모델에 의존하지 않는다. 필터와 결과 변환은
``offline_eval``의 백엔드가 맡는다. BM25 점수와 코사인 유사도는 척도가 달라
더하지 않고, RRF로 순위만 합친다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

DEFAULT_RRF_K = 60

# 밑줄은 \w에 들어가므로 낱말 경계로 다루려고 뺀다.
_WORD_RE = re.compile(r"[^\W_]+")
# 내용어만 남긴다: 명사, 어근, 외국어, 한자, 숫자, 동사·형용사 어간
_KIWI_KEPT_TAGS = {"NNG", "NNP", "XR", "SL", "SH", "SN", "VV", "VA"}

Tokenizer = Callable[[str], list[str]]


class BM25Error(ValueError):
    """BM25·RRF 설정이나 입력이 계약과 다르다."""


def tokenize_space(text: str) -> list[str]:
    return text.lower().split()


def tokenize_bigram(text: str) -> list[str]:
    """낱말 안을 2글자씩 겹쳐 자른다. "담보를"이 질의 "담보"와 맞는다."""

    tokens: list[str] = []
    for word in _WORD_RE.findall(text.lower()):
        if len(word) == 1:
            tokens.append(word)
        else:
            tokens.extend(word[start : start + 2] for start in range(len(word) - 1))
    return tokens


def _create_kiwi_tokenizer() -> Tokenizer:
    try:
        from kiwipiepy import Kiwi
    except ImportError as exc:
        raise BM25Error("kiwipiepy는 저장소 의존성이 아닙니다. 참고 측정 환경에 설치하세요") from exc
    kiwi = Kiwi()

    def tokenize(text: str) -> list[str]:
        return [
            token.form.lower()
            for token in kiwi.tokenize(text)
            if token.tag in _KIWI_KEPT_TAGS
        ]

    return tokenize


def create_tokenizer(name: str) -> Tokenizer:
    if name == "space":
        return tokenize_space
    if name == "bigram":
        return tokenize_bigram
    if name == "kiwi":
        return _create_kiwi_tokenizer()
    raise BM25Error(f"알 수 없는 토크나이저입니다: {name}")


class BM25Index:
    """토큰화된 문서 목록의 BM25 색인. ``rank-bm25``의 기본값 k1=1.5, b=0.75를 쓴다."""

    def __init__(self, documents: Sequence[Sequence[str]]) -> None:
        self._size = len(documents)
        # 라이브러리는 문서가 없거나 전부 비면 평균 길이 계산에서 실패한다.
        if any(documents):
            from rank_bm25 import BM25Okapi

            self._model = BM25Okapi([list(tokens) for tokens in documents])
        else:
            self._model = None

    def score(self, query_tokens: Sequence[str]) -> list[float]:
        if self._model is None:
            return [0.0] * self._size
        return [float(value) for value in self._model.get_scores(list(query_tokens))]


def rrf_fuse(ranked_lists: Sequence[Sequence[str]], *, k: int = DEFAULT_RRF_K) -> list[tuple[str, float]]:
    """순위 목록들을 ``Σ 1/(k+rank)``로 합친다. 동률은 id 오름차순이다."""

    if k < 1:
        raise BM25Error("RRF k는 1 이상이어야 합니다")
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
