"""scripts/map_gold_cover_v8.py: 제외 정책과 채점 가능성 회귀 테스트(작은 가짜 데이터)."""

import importlib.util
from pathlib import Path

from backend.rag.evaluation import compute_retrieval_metrics

_spec = importlib.util.spec_from_file_location(
    "map_gold_cover_v8", Path(__file__).resolve().parents[1] / "scripts" / "map_gold_cover_v8.py"
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

TEXT_A = "".join(chr(0xAC00 + i) for i in range(120))  # 1번 정답 본문: 양쪽 청킹에서 그대로 보존
TEXT_B = "".join(chr(0xB098 + i) for i in range(120))  # 2번 정답 본문: fixed 쪽에서만 사라짐


def _chunk(cid, content, rcept="R1", corpus="c-new"):
    return {"chunk_id": cid, "content": content, "table_title": None, "rcept_no": rcept, "corpus_id": corpus}


def _fixture():
    dataset = [
        {"id": 1, "kind": "relevant", "input": "q1", "gold_chunks": ["old-a"], "corpus_version": "c-old"},
        {"id": 2, "kind": "relevant", "input": "q2", "gold_chunks": ["old-b"], "corpus_version": "c-old"},
        {"id": 3, "kind": "irrelevant", "input": "q3", "gold_chunks": [], "corpus_version": "c-old"},
    ]
    plus = {1: {"gold_plus": ["old-a"]}, 2: {"gold_plus": ["old-b"]}}
    old = {"old-a": _chunk("old-a", TEXT_A, corpus="c-old"), "old-b": _chunk("old-b", TEXT_B, corpus="c-old")}
    new = {
        "group": [_chunk("g1", TEXT_A, corpus="c-g"), _chunk("g2", TEXT_B, corpus="c-g")],
        "fixed_window": [_chunk("f1", TEXT_A + " 다른 내용", corpus="c-f")],
    }
    return dataset, plus, old, new


def test_question_unmapped_in_one_chunking_is_excluded_from_both():
    outputs, record = mod.build_outputs(*_fixture())
    assert [e["id"] for e in record["excluded"]] == [2]
    for name in ("group", "fixed_window"):
        strict = outputs[f"dataset_v1.v8_{name}.jsonl"]
        plus = outputs[f"dataset_v1.v8_{name}.gold_plus.jsonl"]
        assert len(strict) == 2  # 1번(관련) + 3번(비관련), 2번 제외
        assert len(plus) == 1 and '"id": 2' not in plus[0]


def test_strict_output_is_scorable_and_keeps_new_corpus_id():
    import json

    outputs, _ = mod.build_outputs(*_fixture())
    rows = [json.loads(line) for line in outputs["dataset_v1.v8_fixed_window.jsonl"]]
    assert rows[0]["gold_chunks"] == ["f1"] and rows[0]["corpus_version"] == "c-f"
    results = [{"id": 1, "retrieved_chunks": ["x", "f1"]}, {"id": 3, "retrieved_chunks": ["f1"]}]
    metrics = compute_retrieval_metrics(rows, results, k=10)
    assert metrics["relevant_questions"] == 1
    assert metrics["mrr_at_10"] == 0.5


def test_cover_below_half_is_not_mapped():
    body = "".join(chr(0xAC00 + i) for i in range(200))  # 서로 다른 글자 200자 -> 조각 4개
    old = {"content": body, "table_title": None}
    new = [{"chunk_id": "n1", "content": body[:60] + " 다른 글자"}]  # 첫 조각 1개만 담음(25%)
    ids, ratio = mod.map_one(old, new)
    assert ids == [] and ratio < 0.5
