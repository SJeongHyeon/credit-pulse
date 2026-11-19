"""D3 이웃 조각 확장 테스트. 임베딩 없이 작은 fixture로 검증한다."""
import json

import numpy as np
import pytest

from backend.rag.neighbors import (
    InMemoryNeighborLookup,
    NeighborExpansionError,
    budget_n_by_chars,
    expand_neighbors,
    expansion_metrics,
)
from backend.rag.offline_eval import (
    InMemoryVectorBackend,
    OfflineEvalError,
    main as offline_main,
    run_expanded_retrieval,
)
from backend.rag.retrieve import SearchFilters

CORP = "00000001"
DOC = "20260301000001"


def chunk(ordinal, *, rcept_no=DOC, section=("절",), chapter="II", content=None, note_no=None, near_dup=False):
    return {
        "chunk_id": f"{rcept_no[-1]}-{ordinal}",
        "corpus_id": "corpus-test",
        "rcept_no": rcept_no,
        "corp_code": CORP,
        "corp_name": "회사",
        "fiscal_year": 2025,
        "chapter": chapter,
        "section_path": list(section),
        "note_no": note_no,
        "table_title": None,
        "ordinal": ordinal,
        "content": content or "가" * 10,
        "content_sha256": f"sha-{rcept_no[-1]}-{ordinal}",
        "near_dup": near_dup,
        "superseded": False,
    }


def ids(items):
    return [item.chunk["chunk_id"] for item in items]


def expand(chunks, seeds, **kwargs):
    return expand_neighbors(seeds, InMemoryNeighborLookup(chunks), **kwargs)


def test_neighbors_stop_at_section_boundary():
    rows = [chunk(1), chunk(2), chunk(3, section=("다른 절",))]

    assert ids(expand(rows, [rows[1]], before=1, after=1)) == ["1-1", "1-2"]


def test_bundle_is_in_ordinal_order_around_seed():
    rows = [chunk(i) for i in range(1, 6)]

    assert ids(expand(rows, [rows[2]], before=1, after=1)) == ["1-2", "1-3", "1-4"]


def test_direction_controls_which_side_is_reached():
    rows = [chunk(784), chunk(785)]

    assert "1-785" in ids(expand(rows, [rows[0]], before=0, after=1))
    assert "1-785" not in ids(expand(rows, [rows[0]], before=1, after=0))


def test_window_larger_than_one_reaches_further():
    rows = [chunk(i) for i in range(1, 5)]

    assert ids(expand(rows, [rows[0]], before=0, after=2)) == ["1-1", "1-2", "1-3"]


def test_section_gap_stops_only_that_chunk_not_counted_as_neighbor():
    rows = [chunk(1), chunk(2, section=("다른 절",)), chunk(3)]

    assert ids(expand(rows, [rows[0]], before=0, after=2)) == ["1-1", "1-3"]


def test_duplicate_keeps_first_position_and_seed_rank():
    rows = [chunk(10), chunk(11), chunk(12)]

    items = expand(rows, [rows[0], rows[1]], before=1, after=1)

    assert ids(items) == ["1-10", "1-11", "1-12"]
    assert [item.seed_rank for item in items] == [1, 1, 2]
    assert [item.is_seed for item in items] == [True, False, False]


def test_lower_seed_keeps_seed_flag_when_not_pulled_earlier():
    rows = [chunk(1), chunk(9)]

    items = expand(rows, rows, before=0, after=0)

    assert [(item.seed_rank, item.is_seed) for item in items] == [(1, True), (2, True)]


def test_no_expansion_returns_seeds_in_given_order():
    rows = [chunk(2), chunk(1)]

    assert ids(expand(rows, rows, before=0, after=0)) == ["1-2", "1-1"]


def test_other_document_is_never_attached():
    rows = [chunk(1), chunk(2, rcept_no="20260301000002")]

    assert ids(expand(rows, [rows[0]], before=1, after=1)) == ["1-1"]


def test_filters_apply_to_neighbors():
    rows = [chunk(1), chunk(2, chapter="XI"), chunk(3, note_no="9")]
    filters = SearchFilters(corp_code=CORP, fiscal_year=2025, chapter="II")

    assert ids(expand(rows, [rows[0]], before=0, after=1, filters=filters)) == ["1-1"]
    note_filter = SearchFilters(corp_code=CORP, fiscal_year=2025, note_no="9")
    assert ids(expand(rows, [rows[2]], before=1, after=0, filters=note_filter)) == ["1-3"]


def test_near_dup_is_not_attached():
    rows = [chunk(1), chunk(2, near_dup=True)]

    assert ids(expand(rows, [rows[0]], before=0, after=1)) == ["1-1"]


def test_document_edges_are_safe():
    rows = [chunk(1)]

    assert ids(expand(rows, [rows[0]], before=3, after=3)) == ["1-1"]


def test_negative_window_is_rejected():
    with pytest.raises(NeighborExpansionError, match="0 이상"):
        expand([chunk(1)], [chunk(1)], before=-1, after=0)


@pytest.mark.parametrize(
    ("lengths", "budget", "expected"),
    [([100, 150, 100], 300, 2), ([100, 150, 100], 350, 3), ([100], 99, 0), ([100, 100], 100, 1), ([], 10, 0)],
)
def test_budget_is_largest_prefix_within_chars(lengths, budget, expected):
    assert budget_n_by_chars(lengths, budget) == expected


def result_row(row_id, items, baseline, *, chars_n, chunks_n):
    return {
        "id": row_id,
        "retrieved_chunks": [item["chunk_id"] for item in items],
        "expansion": {
            "seed_k": 2,
            "before": 1,
            "after": 1,
            "items": items,
            "total_chunks": len(items),
            "total_chars": 10 * len(items),
        },
        "baseline_budget": {"by_chars_n": chars_n, "by_chunks_n": chunks_n},
        "baseline_chunks": baseline,
    }


def item(chunk_id, seed_rank, is_seed=False):
    return {"chunk_id": chunk_id, "seed_rank": seed_rank, "is_seed": is_seed}


def test_seed_rank_and_flat_rank_differ():
    dataset = [{"id": 1, "kind": "relevant", "gold_chunks": ["gold"]}]
    rows = [result_row(1, [item("seed", 1, True), item("gold", 1)], ["other", "x", "gold"], chars_n=2, chunks_n=3)]

    metrics = expansion_metrics(dataset, rows)

    assert metrics["hit"] == 1.0
    assert metrics["mrr_seed_rank"] == 1.0
    assert metrics["mrr_flat_rank"] == 0.5
    assert metrics["baseline_by_chars"] == {"hit": 0.0, "mrr": 0.0}
    assert metrics["baseline_by_chunks"]["mrr"] == pytest.approx(1 / 3)


def test_first_gold_uses_earliest_flat_position():
    dataset = [{"id": 1, "kind": "relevant", "gold_chunks": ["g2", "g1"]}]
    rows = [result_row(1, [item("g1", 2), item("g2", 1)], ["g1"], chars_n=1, chunks_n=1)]

    assert expansion_metrics(dataset, rows)["per_question"][0]["flat_rank"] == 1


def test_improved_and_worsened_use_flat_rank_against_char_budget():
    dataset = [
        {"id": 1, "kind": "relevant", "gold_chunks": ["a"]},
        {"id": 2, "kind": "relevant", "gold_chunks": ["b"]},
        {"id": 3, "kind": "relevant", "gold_chunks": ["c"]},
        {"id": 4, "kind": "relevant", "gold_chunks": ["d"]},
    ]
    rows = [
        result_row(1, [item("a", 1)], ["x", "a"], chars_n=2, chunks_n=2),
        result_row(2, [item("x", 1), item("b", 1)], ["b"], chars_n=1, chunks_n=1),
        result_row(3, [item("c", 1)], ["c"], chars_n=1, chunks_n=1),
        # 글자 예산으로는 정답을 못 찾고 조각 예산으로는 같은 순위라, 어느 예산인지 구별된다.
        result_row(4, [item("y", 1), item("d", 1)], ["x", "d"], chars_n=1, chunks_n=2),
    ]

    metrics = expansion_metrics(dataset, rows)

    assert [q["change"] for q in metrics["per_question"]] == ["improved", "worsened", "same", "improved"]
    assert (metrics["improved"], metrics["worsened"], metrics["same"]) == (2, 1, 1)


def test_metrics_skip_irrelevant_and_report_cost():
    dataset = [
        {"id": 1, "kind": "relevant", "gold_chunks": ["a"]},
        {"id": 2, "kind": "irrelevant", "gold_chunks": []},
    ]
    rows = [
        result_row(1, [item("a", 1), item("b", 1)], ["a"], chars_n=1, chunks_n=1),
        {"id": 2, "retrieved_chunks": []},
    ]

    metrics = expansion_metrics(dataset, rows)

    assert metrics["relevant_questions"] == 1
    assert metrics["mean_total_chunks"] == 2
    assert metrics["mean_total_chars"] == 20
    assert metrics["max_total_chars"] == 20


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


FILTERS = {1: {"id": 1, "corp_code": CORP, "fiscal_year": 2025}}
DATASET = [
    {"id": 1, "input": "질문", "kind": "relevant", "gold_chunks": ["1-2"]},
    {"id": 2, "input": "무관", "kind": "irrelevant", "gold_chunks": []},
]


def test_expanded_run_records_flat_list_and_budget():
    rows = [chunk(1, content="가" * 30), chunk(2, content="나" * 30), chunk(9, content="다" * 10)]
    backend = make_backend(rows, [1.0, 0.1, 0.5])

    results = run_expanded_retrieval(
        DATASET, backend, seed_k=1, before=0, after=1, filters_by_id=FILTERS
    )

    first = results[0]
    assert first["retrieved_chunks"] == ["1-1", "1-2"]
    assert first["expansion"]["total_chunks"] == 2
    assert first["expansion"]["total_chars"] == 60
    assert first["expansion"]["items"][1] == {"chunk_id": "1-2", "seed_rank": 1, "is_seed": False}
    assert first["baseline_budget"] == {"by_chars_n": 2, "by_chunks_n": 2}
    assert first["baseline_chunks"] == ["1-1", "1-9"]
    assert results[1] == {"id": 2, "retrieved_chunks": []}


def test_budget_beyond_retrieve_limit_fails_loudly(monkeypatch):
    rows = [chunk(i, content="가") for i in range(1, 121)]
    rows[0]["content"] = "가" * 500
    rows[1]["content"] = "나" * 700  # 이웃이 길어 예산이 상위 100개의 글자 수를 넘는다
    backend = make_backend(rows, [1.0, 0.1] + [0.5] * 118)

    with pytest.raises(OfflineEvalError, match="100"):
        run_expanded_retrieval(DATASET, backend, seed_k=1, before=0, after=1, filters_by_id=FILTERS)


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def test_cli_writes_expansion_metrics_and_default_stays_plain(tmp_path, monkeypatch):
    from backend.rag.embedding import build_embeddings

    rows = [chunk(1), chunk(2)]
    chunks = write_jsonl(tmp_path / "chunks.jsonl", rows)
    dataset = write_jsonl(tmp_path / "dataset.jsonl", DATASET)
    filters = write_jsonl(tmp_path / "filters.jsonl", [FILTERS[1]])

    class Embedder:
        dimension = 2

        def encode_passages(self, texts):
            return np.asarray([[1.0, 0.0] for _ in texts], dtype=np.float32)

        encode_queries = encode_passages

    build_embeddings(chunks, tmp_path / "emb", embedder=Embedder())
    monkeypatch.setattr("backend.rag.embedding.create_embedder", lambda *a, **k: Embedder())
    base = [
        "--dataset", str(dataset), "--chunks", str(chunks),
        "--embedding-prefix", str(tmp_path / "emb"), "--filters", str(filters),
    ]

    assert offline_main([*base, "--output", str(tmp_path / "plain.jsonl"), "--top-k", "1"]) == 0
    assert offline_main(
        [*base, "--output", str(tmp_path / "wide.jsonl"), "--top-k", "1", "--expand-after", "1"]
    ) == 0

    plain = [json.loads(l) for l in (tmp_path / "plain.jsonl").read_text(encoding="utf-8").splitlines()]
    assert set(plain[0]) == {"id", "retrieved_chunks"}
    metrics = json.loads((tmp_path / "wide.metrics.json").read_text(encoding="utf-8"))
    assert metrics["seed_k"] == 1
    assert metrics["after"] == 1
    assert metrics["hit"] == 1.0
    assert len(metrics["dataset_sha256"]) == 64
    assert len(metrics["results_sha256"]) == 64
