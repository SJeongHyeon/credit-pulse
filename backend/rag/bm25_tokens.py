"""BM25 토큰 사전 계산표(사전 등록 99번). 검색 순위·점수는 바꾸지 않고 회사별 첫 검색의 토큰화만 없앤다.

회사·연도 필터마다 그 범위 청크를 Kiwi로 토큰화하면 큰 회사는 10초 넘게 걸린다(99번 0단계).
토큰은 청크 본문에만 달려 있으므로, 오프라인에서 한 번 만들어 두고 서버는 표에서 꺼낸다.

파일 형식(gzip JSONL 한 파일):
  첫 줄  메타데이터 {"format", "tokenizer", "kiwipiepy_version", "kept_tags", "platform", "canary",
                    "chunks_sha256", "entries"}
  다음 줄 {"content_sha256": 본문 sha256, "tokens": [...]}
메타데이터와 토큰을 한 파일에 두어 둘이 따로 놀 수 없게 한다.

메타데이터가 지금 환경과 다르면(토크나이저·Kiwi 버전·남기는 품사·플랫폼·기준 구절 토큰·청크 파일)
같은 본문이라도 토큰이 달라질 수 있어 표를 쓰지 않는다(경고 후 None). 파일이 없거나 깨졌으면 설정 오류라 예외를 낸다.

플랫폼과 기준 구절(canary)을 보는 이유: 같은 kiwipiepy 0.24.1·모델 0.24.0이라도 Windows 빌드는 여러 낱말
사전(multi.dict)을 적용하지 않아 "데몬 헌터스"를 Linux와 다르게 자른다(99번 4단계, 3.2만 청크 중 2개).
버전만 보면 Windows에서 만든 표가 Linux 운영에서 조용히 순위를 바꾼다. 표는 운영과 같은 이미지에서 만든다.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import sys
from collections.abc import Iterable, Mapping
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from backend.rag.bm25 import _KIWI_KEPT_TAGS, Tokenizer

logger = logging.getLogger(__name__)

TOKEN_TABLE_FORMAT = "bm25-tokens/v2"  # v2: platform·canary 추가
# 표를 만든 환경과 지금 환경이 같은 토큰을 내는지 실제로 잘라 보는 구절.
# 첫 줄은 Windows·Linux가 다르게 자르는 것으로 확인한 구절(multi.dict 적용 여부).
CANARY_TEXTS = (
    "데몬 헌터스",
    "차입금 담보 제공 자산과 우발부채",
    "특수관계자와의 거래 내역 및 지급보증",
)


class BM25TokenTableError(ValueError):
    """토큰표 파일이 없거나 형식이 깨졌다(설정 오류라 조용히 넘기지 않는다)."""


def content_key(text: str) -> str:
    """표의 키. 청크의 content_sha256 필드를 믿지 않고, 토크나이저에 넘길 문자열 그대로 해시한다."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _kiwipiepy_version() -> str | None:
    try:
        return importlib_metadata.version("kiwipiepy")
    except importlib_metadata.PackageNotFoundError:
        return None


def _platform() -> str:
    return sys.platform


def tokenizer_fingerprint(tokenizer_name: str, tokenizer: Tokenizer) -> dict[str, Any]:
    """같은 본문에서 같은 토큰이 나오는지를 가르는 값들. kiwi가 아니면 버전·품사·플랫폼은 상관없어 None.

    canary는 토크나이저를 실제로 불러 얻는다. kiwi면 Kiwi의 첫 tokenize 준비 비용이 여기서 든다.
    """

    is_kiwi = tokenizer_name == "kiwi"
    return {
        "tokenizer": tokenizer_name,
        "kiwipiepy_version": _kiwipiepy_version() if is_kiwi else None,
        "kept_tags": sorted(_KIWI_KEPT_TAGS) if is_kiwi else None,
        "platform": _platform() if is_kiwi else None,
        "canary": [tokenizer(text) for text in CANARY_TEXTS],
    }


def build_token_table(
    chunks: Iterable[Mapping[str, Any]], tokenizer: Tokenizer
) -> dict[str, list[str]]:
    """본문 sha256 → 토큰. 같은 본문은 한 번만 토큰화한다."""

    table: dict[str, list[str]] = {}
    for chunk in chunks:
        text = str(chunk["content"])
        key = content_key(text)
        if key not in table:
            table[key] = tokenizer(text)
    return table


def write_token_table(
    path: Path,
    table: Mapping[str, list[str]],
    *,
    tokenizer_name: str,
    tokenizer: Tokenizer,
    chunks_sha256: str,
) -> None:
    """키 순서로 쓰고 gzip 시각을 0으로 고정해 같은 입력이면 같은 바이트가 나오게 한다.

    tokenizer는 표를 만든 토크나이저와 같은 것을 넘긴다(기준 구절 토큰을 메타데이터에 남긴다).
    """

    header = {
        "format": TOKEN_TABLE_FORMAT,
        **tokenizer_fingerprint(tokenizer_name, tokenizer),
        "chunks_sha256": chunks_sha256,
        "entries": len(table),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # filename=""을 주지 않으면 gzip 헤더에 출력 파일 이름이 들어가 같은 내용도 바이트가 달라진다.
    with path.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as packed:
        with io.TextIOWrapper(packed, encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(header, ensure_ascii=False) + "\n")
            for key in sorted(table):
                row = {"content_sha256": key, "tokens": list(table[key])}
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def _mismatches(header: Mapping[str, Any], expected: Mapping[str, Any]) -> list[str]:
    return [
        f"{name}: 표={header.get(name)!r} 지금={value!r}"
        for name, value in expected.items()
        if header.get(name) != value
    ]


def load_token_table(
    path: Path, *, tokenizer_name: str, tokenizer: Tokenizer, chunks_sha256: str
) -> dict[str, list[str]] | None:
    """표를 읽는다. 환경과 맞지 않으면 경고하고 None(호출자는 지금처럼 그 자리에서 토큰화).

    tokenizer는 검색에 쓸 토크나이저 그 자체를 넘긴다. 기준 구절을 실제로 잘라 표와 비교한다.
    토큰 문자열은 ``sys.intern``으로 공유한다. 그대로 읽으면 같은 낱말이 등장마다 따로 생겨
    서비스 청크 기준 파이썬 힙이 452MB, intern하면 78MB다(99번 0단계).
    """

    if not path.is_file():
        raise BM25TokenTableError(f"BM25 토큰표 파일이 없습니다: {path}")
    expected = {
        "format": TOKEN_TABLE_FORMAT,
        **tokenizer_fingerprint(tokenizer_name, tokenizer),
        "chunks_sha256": chunks_sha256,
    }
    table: dict[str, list[str]] = {}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            header = json.loads(stream.readline())
            if not isinstance(header, dict):
                raise BM25TokenTableError(f"BM25 토큰표 첫 줄이 메타데이터가 아닙니다: {path}")
            mismatched = _mismatches(header, expected)
            if mismatched:
                logger.warning(
                    "BM25 토큰표를 쓰지 않고 그 자리에서 토큰화합니다(환경과 다름, %s): %s",
                    path,
                    "; ".join(mismatched),
                )
                return None
            for line in stream:
                row = json.loads(line)
                tokens = row["tokens"]
                if not isinstance(tokens, list) or not all(isinstance(token, str) for token in tokens):
                    raise BM25TokenTableError(f"BM25 토큰표의 tokens가 문자열 목록이 아닙니다: {path}")
                table[str(row["content_sha256"])] = [sys.intern(token) for token in tokens]
    except BM25TokenTableError:
        raise
    except (OSError, EOFError, ValueError, KeyError, TypeError) as exc:
        # gzip.BadGzipFile은 OSError, json 오류는 ValueError다.
        raise BM25TokenTableError(f"BM25 토큰표를 읽을 수 없습니다({type(exc).__name__}): {path}") from exc
    if len(table) != header.get("entries"):
        # 중간에 잘린 파일이면 빠진 청크가 조용히 느린 경로로 간다. 깨진 파일로 본다.
        raise BM25TokenTableError(
            f"BM25 토큰표 항목 수 {len(table)}가 메타데이터 {header.get('entries')}와 다릅니다: {path}"
        )
    return table
