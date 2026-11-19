"""BM25 토큰 사전 계산표 시험(사전 등록 99번, 키 없이).

표를 쓰든 안 쓰든 BM25 점수·순위가 같아야 하고(①), 환경이 다르면 표를 버려야 하며(②),
표에 없는 청크만 그 자리에서 토큰화해야 하고(③), 표를 쓰면 청크 토큰화를 부르지 않아야 한다(④).
"""

import gzip
import json
import logging
from pathlib import Path

import pytest

from backend.rag import bm25_tokens
from backend.rag.bm25 import create_tokenizer, tokenize_bigram
from backend.rag.bm25_tokens import (
    BM25TokenTableError,
    build_token_table,
    content_key,
    load_token_table,
    write_token_table,
)
from backend.rag.offline_eval import InMemoryBM25Backend
from backend.rag.retrieve import SearchFilters

CORP_A = "00000001"
CORP_B = "00000002"
FILTER_A = SearchFilters(corp_code=CORP_A, fiscal_year=2025)
CHUNKS_SHA = "a" * 64
SNAPSHOT_CHUNKS = Path(__file__).resolve().parents[1] / "data" / "snapshot" / "chunks.jsonl.gz"


def _chunk(chunk_id, content, *, corp=CORP_A):
    return {
        "chunk_id": chunk_id,
        "corpus_id": "corpus-test",
        "rcept_no": "20260301000001",
        "corp_code": corp,
        "corp_name": "회사",
        "fiscal_year": 2025,
        "chapter": "II",
        "section_path": ["II. 사업의 내용"],
        "note_no": None,
        "table_title": None,
        "ordinal": int(chunk_id.split("-")[1]),
        "content": content,
        "content_sha256": content_key(content),
        "near_dup": False,
        "superseded": False,
    }


FIXTURE_CHUNKS = [
    _chunk("a-1", "차입금 담보 제공 자산 현황"),
    _chunk("a-2", "소송 사건 원고 피고 소송가액"),
    _chunk("a-3", "차입금 만기 구조와 이자율"),
    _chunk("a-4", "우발부채 지급보증 담보"),
    _chunk("b-1", "차입금 담보 다른 회사", corp=CORP_B),
]
FIXTURE_QUERIES = ["차입금 담보", "소송 현황", "지급보증 우발부채", "없는낱말"]


class CountingTokenizer:
    """호출 횟수를 센다. 표를 쓰면 질의 토큰화만 불려야 한다."""

    def __init__(self, tokenizer):
        self.tokenizer = tokenizer
        self.calls = 0

    def __call__(self, text):
        self.calls += 1
        return self.tokenizer(text)


def _round_trip(tmp_path, chunks, tokenizer, *, tokenizer_name):
    """표를 파일로 쓰고 서버와 같은 함수로 다시 읽는다(쓰기·읽기 경로까지 시험)."""

    path = tmp_path / "bm25_tokens.jsonl.gz"
    write_token_table(path, build_token_table(chunks, tokenizer), tokenizer_name=tokenizer_name, tokenizer=tokenizer, chunks_sha256=CHUNKS_SHA)
    return load_token_table(path, tokenizer_name=tokenizer_name, tokenizer=tokenizer, chunks_sha256=CHUNKS_SHA)


def _results(backend, queries, filters):
    return [
        [(hit.chunk_id, hit.score) for hit in backend.search(query, filters=filters, top_k=50)]
        for query in queries
    ]


def _assert_same_ranking(chunks, tokenizer, table, queries, filters_list):
    plain = InMemoryBM25Backend(chunks=chunks, tokenizer=tokenizer, retrieval_method="bm25")
    prebuilt = InMemoryBM25Backend(
        chunks=chunks, tokenizer=tokenizer, retrieval_method="bm25", prebuilt_tokens=table
    )
    # 청크마다 표의 토큰이 그 자리 토큰화와 같다(질의에 안 걸리는 토큰이 바뀌어도 잡는다).
    assert [list(prebuilt._chunk_tokens(c)) for c in plain.chunks] == [
        tokenizer(str(c["content"])) for c in plain.chunks
    ]
    for filters in filters_list:
        # 점수는 float 그대로 비교한다(근사 비교면 토큰 차이가 묻힐 수 있다).
        assert _results(prebuilt, queries, filters) == _results(plain, queries, filters)


# ① 표를 쓴 색인과 그 자리에서 토큰화한 색인의 점수·순위가 같다
def test_prebuilt_tokens_give_identical_scores_on_fixture(tmp_path):
    table = _round_trip(tmp_path, FIXTURE_CHUNKS, tokenize_bigram, tokenizer_name="bigram")

    assert table is not None
    assert len(table) == 5
    _assert_same_ranking(
        FIXTURE_CHUNKS,
        tokenize_bigram,
        table,
        FIXTURE_QUERIES,
        [FILTER_A, SearchFilters(corp_code=CORP_B, fiscal_year=2025)],
    )


def test_prebuilt_tokens_give_identical_scores_on_real_chunks(tmp_path):
    pytest.importorskip("kiwipiepy")
    with gzip.open(SNAPSHOT_CHUNKS, "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    corp = "00148610"
    chunks = [row for row in rows if row["corp_code"] == corp][:150]
    tokenizer = create_tokenizer("kiwi")
    table = _round_trip(tmp_path, chunks, tokenizer, tokenizer_name="kiwi")

    assert table is not None
    queries = ["차입금 담보 제공 자산", "소송 사건 현황", "매출채권 대손충당금", "특수관계자 거래"]
    _assert_same_ranking(
        chunks, tokenizer, table, queries, [SearchFilters(corp_code=corp, fiscal_year=int(chunks[0]["fiscal_year"]))]
    )


# ② 메타데이터가 다르면 표를 쓰지 않고 지금 경로로 동작하며 경고를 남긴다
@pytest.mark.parametrize(
    ("patch_version", "load_sha", "field"),
    [
        ("9.9.9", CHUNKS_SHA, "kiwipiepy_version"),
        (None, "b" * 64, "chunks_sha256"),
    ],
)
def test_metadata_mismatch_falls_back_with_warning(tmp_path, monkeypatch, caplog, patch_version, load_sha, field):
    monkeypatch.setattr(bm25_tokens, "_kiwipiepy_version", lambda: "0.24.1")
    path = tmp_path / "bm25_tokens.jsonl.gz"
    table = build_token_table(FIXTURE_CHUNKS, tokenize_bigram)
    write_token_table(path, table, tokenizer_name="kiwi", tokenizer=tokenize_bigram, chunks_sha256=CHUNKS_SHA)
    if patch_version is not None:
        monkeypatch.setattr(bm25_tokens, "_kiwipiepy_version", lambda: patch_version)

    with caplog.at_level(logging.WARNING, logger="backend.rag.bm25_tokens"):
        loaded = load_token_table(path, tokenizer_name="kiwi", tokenizer=tokenize_bigram, chunks_sha256=load_sha)

    assert loaded is None
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert field in warnings[0]
    # 표가 None이면 백엔드는 이전 경로: 후보 4개 + 질의 1개를 토큰화한다.
    counter = CountingTokenizer(tokenize_bigram)
    backend = InMemoryBM25Backend(
        chunks=FIXTURE_CHUNKS, tokenizer=counter, retrieval_method="bm25", prebuilt_tokens=loaded
    )
    backend.search("차입금 담보", filters=FILTER_A, top_k=50)
    assert counter.calls == 5


def test_kept_tags_mismatch_also_falls_back(tmp_path, monkeypatch, caplog):
    path = tmp_path / "bm25_tokens.jsonl.gz"
    write_token_table(path, {"k": ["a"]}, tokenizer_name="kiwi", tokenizer=tokenize_bigram, chunks_sha256=CHUNKS_SHA)
    monkeypatch.setattr(bm25_tokens, "_KIWI_KEPT_TAGS", {"NNG"})

    with caplog.at_level(logging.WARNING, logger="backend.rag.bm25_tokens"):
        assert load_token_table(path, tokenizer_name="kiwi", tokenizer=tokenize_bigram, chunks_sha256=CHUNKS_SHA) is None
    assert "kept_tags" in caplog.text


def _windows_like_tokenizer(text):
    """버전은 같은데 '데몬 헌터스'만 다르게 자르는 토크나이저(99번 4단계 Windows·Linux 차이를 흉내)."""

    if text == "데몬 헌터스":
        return ["데몬", "헌터스"]
    return tokenize_bigram(text)


def _linux_like_tokenizer(text):
    if text == "데몬 헌터스":
        return ["데몬 헌터", "스"]
    return tokenize_bigram(text)


def test_canary_mismatch_with_same_version_falls_back(tmp_path, caplog):
    path = tmp_path / "bm25_tokens.jsonl.gz"
    table = build_token_table(FIXTURE_CHUNKS, _windows_like_tokenizer)
    write_token_table(path, table, tokenizer_name="bigram", tokenizer=_windows_like_tokenizer, chunks_sha256=CHUNKS_SHA)

    with caplog.at_level(logging.WARNING, logger="backend.rag.bm25_tokens"):
        loaded = load_token_table(path, tokenizer_name="bigram", tokenizer=_linux_like_tokenizer, chunks_sha256=CHUNKS_SHA)

    assert loaded is None
    assert "canary" in caplog.text
    # 같은 토크나이저로 읽으면 쓴다.
    assert load_token_table(path, tokenizer_name="bigram", tokenizer=_windows_like_tokenizer, chunks_sha256=CHUNKS_SHA) is not None


def test_platform_mismatch_falls_back_for_kiwi(tmp_path, monkeypatch, caplog):
    path = tmp_path / "bm25_tokens.jsonl.gz"
    monkeypatch.setattr(bm25_tokens, "_platform", lambda: "win32")
    write_token_table(path, {"k": ["a"]}, tokenizer_name="kiwi", tokenizer=tokenize_bigram, chunks_sha256=CHUNKS_SHA)
    monkeypatch.setattr(bm25_tokens, "_platform", lambda: "linux")

    with caplog.at_level(logging.WARNING, logger="backend.rag.bm25_tokens"):
        assert load_token_table(path, tokenizer_name="kiwi", tokenizer=tokenize_bigram, chunks_sha256=CHUNKS_SHA) is None
    assert "platform" in caplog.text


def test_missing_or_broken_table_is_an_error(tmp_path):
    with pytest.raises(BM25TokenTableError, match="없습니다"):
        load_token_table(tmp_path / "none.jsonl.gz", tokenizer_name="bigram", tokenizer=tokenize_bigram, chunks_sha256=CHUNKS_SHA)

    not_gzip = tmp_path / "not_gzip.jsonl.gz"
    not_gzip.write_text("plain text", encoding="utf-8")
    with pytest.raises(BM25TokenTableError, match="읽을 수 없습니다"):
        load_token_table(not_gzip, tokenizer_name="bigram", tokenizer=tokenize_bigram, chunks_sha256=CHUNKS_SHA)

    # 메타데이터의 항목 수보다 적으면 잘린 파일로 본다.
    truncated = tmp_path / "truncated.jsonl.gz"
    write_token_table(truncated, {"k1": ["a"], "k2": ["b"]}, tokenizer_name="bigram", tokenizer=tokenize_bigram, chunks_sha256=CHUNKS_SHA)
    with gzip.open(truncated, "rt", encoding="utf-8") as stream:
        lines = stream.readlines()
    with gzip.open(truncated, "wt", encoding="utf-8") as stream:
        stream.writelines(lines[:-1])
    with pytest.raises(BM25TokenTableError, match="항목 수"):
        load_token_table(truncated, tokenizer_name="bigram", tokenizer=tokenize_bigram, chunks_sha256=CHUNKS_SHA)


# ③ 표에 없는 청크가 섞여도 그 청크만 그 자리에서 토큰화해 결과가 같다
def test_chunk_missing_from_table_is_tokenized_on_the_spot(tmp_path):
    table = _round_trip(tmp_path, FIXTURE_CHUNKS, tokenize_bigram, tokenizer_name="bigram")
    del table[content_key(FIXTURE_CHUNKS[1]["content"])]

    _assert_same_ranking(FIXTURE_CHUNKS, tokenize_bigram, table, FIXTURE_QUERIES, [FILTER_A])
    counter = CountingTokenizer(tokenize_bigram)
    backend = InMemoryBM25Backend(
        chunks=FIXTURE_CHUNKS, tokenizer=counter, retrieval_method="bm25", prebuilt_tokens=table
    )
    backend.search("소송 현황", filters=FILTER_A, top_k=50)
    # 빠진 청크 1개 + 질의 1개
    assert counter.calls == 2


# ④ 표를 쓰면 청크 토큰화를 부르지 않는다(질의 토큰화만)
def test_prebuilt_table_skips_chunk_tokenization(tmp_path):
    table = _round_trip(tmp_path, FIXTURE_CHUNKS, tokenize_bigram, tokenizer_name="bigram")
    with_table = CountingTokenizer(tokenize_bigram)
    without_table = CountingTokenizer(tokenize_bigram)
    InMemoryBM25Backend(
        chunks=FIXTURE_CHUNKS, tokenizer=with_table, retrieval_method="bm25", prebuilt_tokens=table
    ).search("차입금 담보", filters=FILTER_A, top_k=50)
    InMemoryBM25Backend(
        chunks=FIXTURE_CHUNKS, tokenizer=without_table, retrieval_method="bm25"
    ).search("차입금 담보", filters=FILTER_A, top_k=50)

    assert with_table.calls == 1
    assert without_table.calls == 5


def test_tokens_are_interned_and_file_is_reproducible(tmp_path):
    first = tmp_path / "first.jsonl.gz"
    second = tmp_path / "second.jsonl.gz"
    table = build_token_table(FIXTURE_CHUNKS, tokenize_bigram)
    write_token_table(first, table, tokenizer_name="bigram", tokenizer=tokenize_bigram, chunks_sha256=CHUNKS_SHA)
    write_token_table(second, dict(reversed(list(table.items()))), tokenizer_name="bigram", tokenizer=tokenize_bigram, chunks_sha256=CHUNKS_SHA)

    assert first.read_bytes() == second.read_bytes()
    loaded = load_token_table(first, tokenizer_name="bigram", tokenizer=tokenize_bigram, chunks_sha256=CHUNKS_SHA)
    # "차입"은 a-1·a-3·b-1에 나온다. intern했으면 같은 객체다.
    occurrences = [token for tokens in loaded.values() for token in tokens if token == "차입"]
    assert len(occurrences) == 3
    assert len({id(token) for token in occurrences}) == 1
