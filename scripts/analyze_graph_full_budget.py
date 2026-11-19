"""Summarize saved graph167 results without credentials or API calls."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from statistics import median
from collections import Counter


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals/results/graph50"
DATASETS = {
    "paraphrase": ROOT / "evals/paraphrase_v2_plain.jsonl",
    "v2": ROOT / "evals/dataset_v2.jsonl",
}
MODES = ("budget_default", "budget_40")
RESPONSE_METRICS = (
    "correct_refusal", "over_refusal", "correct_citation", "wrong_citation", "hallucination_risk",
)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[math.ceil(percentile * len(ordered)) - 1], 1)


def _load(dataset: str, mode: str) -> tuple[dict, dict[int, dict]]:
    files = sorted(RESULTS.glob(f"graph167_full_{dataset}_*_{mode}.summary.json"))
    expected_ids = {
        int(json.loads(line)["id"])
        for line in DATASETS[dataset].read_text(encoding="utf-8").splitlines()
    }
    rows: dict[int, dict] = {}
    nodes: dict[int, dict] = {}
    summaries = []
    for summary_path in files:
        stem = summary_path.name.removesuffix(".summary.json")
        summaries.append(json.loads(summary_path.read_text(encoding="utf-8")))
        for line in (RESULTS / f"{stem}.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            question_id = int(row["id"])
            if question_id in rows:
                raise ValueError(f"duplicate {dataset}/{mode} question ID: {question_id}")
            rows[question_id] = row
        for line in (RESULTS / f"{stem}.node_ms.jsonl").read_text(encoding="utf-8").splitlines():
            node = json.loads(line)
            nodes[int(node["id"])] = node
    if set(rows) != expected_ids or set(nodes) != expected_ids:
        raise ValueError(f"incomplete {dataset}/{mode}: rows={len(rows)}, nodes={len(nodes)}, expected={len(expected_ids)}")
    response = {
        name: {
            "numerator": sum(s["metrics"]["response"][name]["numerator"] for s in summaries),
            "denominator": sum(s["metrics"]["response"][name]["denominator"] for s in summaries),
        }
        for name in RESPONSE_METRICS
    }
    retrieval_questions = [
        item
        for summary in summaries
        for item in summary["metrics"]["retrieval_first_round"]["per_question"]
        if item["kind"] == "relevant"
    ]
    search_ms = [
        node["node_ms"]["supervisor/retriever"]["ms"]
        for node in nodes.values()
        if "supervisor/retriever" in node["node_ms"]
    ]
    rerank_ms = [
        sum(call["rerank_latency_ms"] for call in row["retrieval_calls"])
        for row in rows.values()
        if row["retrieval_calls"]
    ]
    elapsed_ms = [row["elapsed_ms"] for row in rows.values()]
    all_calls = [call for row in rows.values() for call in row["retrieval_calls"]]
    graph_calls = [call for call in all_calls if call["graph_candidate_ids"]]
    paired_timings = {
        question_id: {
            "kind": row["kind"],
            "query_vector_sources": row.get("query_vector_sources"),
            "search_ms": nodes[question_id]["node_ms"]["supervisor/retriever"]["ms"],
            "rerank_ms": round(sum(call["rerank_latency_ms"] for call in row["retrieval_calls"]), 1),
        }
        for question_id, row in rows.items()
        if row["retrieval_calls"] and "supervisor/retriever" in nodes[question_id]["node_ms"]
    }
    for timing in paired_timings.values():
        timing["search_other_ms"] = round(timing["search_ms"] - timing["rerank_ms"], 1)
    data = {
        "questions": len(rows),
        "source_summaries": [file.name for file in files],
        "response": response,
        "retrieval_relevant": len(retrieval_questions),
        "retrieval_hit_at_10": sum(bool(item["hit"]) for item in retrieval_questions),
        "graph_calls": len(graph_calls),
        "rerank_input_chars": sum(call["rerank_input_chars"] for call in all_calls),
        "graph_call_input_chars": sum(call["rerank_input_chars"] for call in graph_calls),
        "query_vector_sources": dict(Counter(
            source for row in rows.values() for source in (row.get("query_vector_sources") or [])
        )),
        "search_ms": {"n": len(search_ms), "p50": round(median(search_ms), 1), "p95": _percentile(search_ms, .95)},
        "search_other_ms": {
            "n": len(paired_timings),
            "p50": round(median(timing["search_other_ms"] for timing in paired_timings.values()), 1),
            "p95": _percentile([timing["search_other_ms"] for timing in paired_timings.values()], .95),
        },
        "rerank_ms": {"n": len(rerank_ms), "p50": round(median(rerank_ms), 1), "p95": _percentile(rerank_ms, .95)},
        "elapsed_ms": {"n": len(elapsed_ms), "p50": round(median(elapsed_ms), 1), "p95": _percentile(elapsed_ms, .95)},
        "known_cost_usd": round(sum(s["llm_usage"]["cost_usd_known_total"] for s in summaries), 6),
        "cost_unknown_calls": sum(s["llm_usage"]["cost_unknown_calls"] for s in summaries),
        "per_question_timing": paired_timings,
    }
    return data, rows


def main() -> None:
    report = {"method": "nearest-rank p95; search timing only for rows with retriever node", "datasets": {}}
    for dataset, dataset_path in DATASETS.items():
        modes = {}
        rows_by_mode = {}
        for mode in MODES:
            modes[mode], rows_by_mode[mode] = _load(dataset, mode)
        old = rows_by_mode["budget_default"]
        new = rows_by_mode["budget_40"]
        report["datasets"][dataset] = {
            "input_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
            "modes": modes,
            "changed_outcome_ids": sorted(qid for qid in old if old[qid]["outcome"] != new[qid]["outcome"]),
            "changed_citation_ids": sorted(
                qid for qid in old
                if {c["chunk_id"] for c in old[qid]["citations"]}
                != {c["chunk_id"] for c in new[qid]["citations"]}
            ),
            "changed_retrieval_ids": sorted(
                qid for qid in old
                if [call["final_ids"] for call in old[qid]["retrieval_calls"]]
                != [call["final_ids"] for call in new[qid]["retrieval_calls"]]
            ),
        }
    output = RESULTS / "graph167_full_comparison.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    for dataset, data in report["datasets"].items():
        print(dataset, {mode: {
            "response": value["response"], "hit": value["retrieval_hit_at_10"],
            "chars": value["rerank_input_chars"], "search_ms": value["search_ms"],
            "elapsed_ms": value["elapsed_ms"], "cost": value["known_cost_usd"],
        } for mode, value in data["modes"].items()})


if __name__ == "__main__":
    main()
