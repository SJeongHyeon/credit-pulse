"""Replay saved graph candidate IDs to estimate 49+1 versus 40+1 input size.

This reads local results and chunks only. It never loads credentials or calls APIs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.rag.rerank import rerank_document_with_header


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chunks", type=Path, default=ROOT / "data/service/chunks.jsonl")
    parser.add_argument("--results", type=Path, default=ROOT / "evals/results/graph50")
    args = parser.parse_args()
    chunks = {
        row["chunk_id"]: row
        for line in args.chunks.read_text(encoding="utf-8").splitlines()
        if (row := json.loads(line))
    }
    for name in (
        "graph150_paraphrase_enabled.jsonl",
        "graph150_v2_enabled.jsonl",
        "graph164_smoke_q60_limit1.jsonl",
        "graph164_smoke_q62_limit1.jsonl",
    ):
        calls = [
            (row["id"], call)
            for line in (args.results / name).read_text(encoding="utf-8").splitlines()
            for row in [json.loads(line)]
            for call in row.get("retrieval_calls", [])
            if call.get("graph_candidate_ids")
        ]
        old_chars = new_chars = 0
        excluded_citations = []
        for question_id, call in calls:
            base = call["first_stage_ids"]
            neighbor = call["graph_candidate_ids"][0]
            if len(base) < 49:
                continue
            def chars(ids: list[str]) -> int:
                return sum(len(rerank_document_with_header(chunks[cid])) + len(call["query"]) for cid in ids)
            old_chars += chars([*base[:49], neighbor])
            new_chars += chars([*base[:40], neighbor])
            excluded_citations.extend((question_id, cid) for cid in call["final_ids"] if cid in base[40:49])
        print(json.dumps({
            "source": name,
            "graph_calls": len(calls),
            "counterfactual_49_plus_1_chars": old_chars,
            "counterfactual_40_plus_1_chars": new_chars,
            "input_change_percent": round((new_chars / old_chars - 1) * 100, 2) if old_chars else None,
            "prior_final_top10_in_excluded_rrf_41_to_49": excluded_citations,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
