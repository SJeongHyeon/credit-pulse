"""Run bounded graph retrieval smoke comparisons.

This script uses real OpenRouter and Voyage credentials when run without
--dry-run. It stops on the first failed evaluation and never overwrites a
previous result. Run it only after the key owner has approved the API use.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_V9_DIR = (
    Path.home() / ".codex" / "worktrees" / "graphrag" / "proj3-a"
    / "data" / "experiments" / "b100-v9-fixed_window"
)
CASES = (
    ("60", "off", "evals/paraphrase_v2_plain.jsonl", True),
    ("60", "limit1", "evals/paraphrase_v2_plain.jsonl", True),
    ("62", "off", "evals/dataset_v2.jsonl", False),
    ("62", "limit1", "evals/dataset_v2.jsonl", False),
)
REVIEW_CASES = (
    ("60", "review_off", "evals/paraphrase_v2_plain.jsonl", True),
    ("60", "review_on", "evals/paraphrase_v2_plain.jsonl", True),
    ("26,62", "review_off", "evals/dataset_v2.jsonl", False),
    ("26,62", "review_on", "evals/dataset_v2.jsonl", False),
)
BUDGET_CASES = tuple(
    (question_id, mode, dataset, supplementary)
    for question_id, dataset, supplementary in (
        ("10,36,60", "evals/paraphrase_v2_plain.jsonl", True),
        ("58,62", "evals/dataset_v2.jsonl", False),
    )
    for mode in ("budget_default", "budget_40")
)

def budget_full_cases() -> list[tuple[str, str, str, bool]]:
    """Reuse the completed 23-question baseline; checkpoint remaining runs in fives."""
    cases = [("all", "budget_default", "evals/paraphrase_v2_plain.jsonl", True)]
    for dataset, supplementary, modes in (
        ("evals/paraphrase_v2_plain.jsonl", True, ("budget_40",)),
        ("evals/dataset_v2.jsonl", False, ("budget_default", "budget_40")),
    ):
        rows = [json.loads(line) for line in (ROOT / dataset).read_text(encoding="utf-8").splitlines()]
        if supplementary:
            groups = [rows[start:start + 5] for start in range(0, len(rows), 5)]
        else:
            # The first two v2 groups already have complete results. Keep their IDs.
            # Every remaining group needs a relevant row for retrieval metrics.
            groups = [rows[:5], rows[5:10]]
            remaining = rows[10:]
            relevant = [row for row in remaining if row["kind"] == "relevant"]
            irrelevant = [row for row in remaining if row["kind"] != "relevant"]
            group_count = (len(remaining) + 4) // 5
            if len(relevant) < group_count:
                raise ValueError("not enough relevant v2 questions to form measurable batches")
            balanced = [[relevant.pop(0)] for _ in range(group_count)]
            leftovers = [*relevant, *irrelevant]
            for group in balanced:
                take = 5 - len(group)
                group.extend(leftovers[:take])
                del leftovers[:take]
            groups.extend(balanced)
        for group in groups:
            question_ids = ",".join(str(row["id"]) for row in group)
            cases.extend((question_ids, mode, dataset, supplementary) for mode in modes)
    return cases


def adoption_full_cases() -> list[tuple[str, str, str, bool]]:
    """Compare graph off/on on identical five-question groups."""
    cases: list[tuple[str, str, str, bool]] = []
    seen: set[tuple[str, str]] = set()
    for question_ids, _, dataset, supplementary in budget_full_cases():
        if question_ids == "all" or (question_ids, dataset) in seen:
            continue
        seen.add((question_ids, dataset))
        cases.extend((question_ids, mode, dataset, supplementary) for mode in ("graph_off", "graph_on"))
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chunks", type=Path, default=DEFAULT_V9_DIR / "chunks.jsonl")
    parser.add_argument("--tokens", type=Path, default=DEFAULT_V9_DIR / "bm25_tokens.win.jsonl.gz")
    parser.add_argument("--embeddings", type=Path, default=ROOT / "data/service/embeddings")
    parser.add_argument("--env-file", type=Path, help="Credential .env file (required for budget smoke; never prints keys)")
    parser.add_argument("--dry-run", action="store_true", help="Show planned runs; do not load keys or call APIs")
    parser.add_argument("--review-smoke", action="store_true", help="Compare low-score review off/on for 60, 26, 62")
    parser.add_argument("--budget-smoke", action="store_true", help="Compare graph rerank baseline limit unset/40 on five questions")
    parser.add_argument("--budget-full", action="store_true", help="Compare unset/40 on the full 23+50 question datasets")
    parser.add_argument("--adoption-full", action="store_true", help="Compare graph off/on with the 40+1 budget on full datasets")
    parser.add_argument("--resume", action="store_true", help="Reuse complete results and run only remaining cases")
    parser.add_argument("--max-known-cost-usd", type=float)
    args = parser.parse_args(argv)
    if sum((args.review_smoke, args.budget_smoke, args.budget_full, args.adoption_full)) > 1:
        parser.error("choose only one smoke mode")

    if args.max_known_cost_usd is None:
        args.max_known_cost_usd = 2.00 if args.adoption_full else (2.50 if args.budget_full else (0.15 if args.budget_smoke else (0.12 if args.review_smoke else 0.10)))
    if args.max_known_cost_usd <= 0:
        parser.error("--max-known-cost-usd must be positive")
    for path in (args.chunks, args.tokens, args.embeddings.with_suffix(".metadata.json")):
        if not path.is_file():
            parser.error(f"required local artifact is missing: {path}")

    output_dir = ROOT / "evals/results/graph50"
    planned: list[tuple[list[str], dict[str, str], Path]] = []
    cases = adoption_full_cases() if args.adoption_full else (budget_full_cases() if args.budget_full else (BUDGET_CASES if args.budget_smoke else (REVIEW_CASES if args.review_smoke else CASES)))
    for question_id, mode, dataset, supplementary in cases:
        prefix = "graph168_full" if args.adoption_full else ("graph167_full" if args.budget_full else ("graph166_smoke" if args.budget_smoke else ("graph165_smoke" if args.review_smoke else "graph164_smoke")))
        dataset_label = "paraphrase" if supplementary else "v2"
        output = output_dir / f"{prefix}_{dataset_label}_q{question_id.replace(',', '_')}_{mode}.jsonl" if args.budget_full or args.adoption_full else output_dir / f"{prefix}_q{question_id.replace(',', '_')}_{mode}.jsonl"
        command = [
            sys.executable, "-X", "utf8", str(ROOT / "scripts/run_graph_eval.py"),
            "--graph", "multi", "--domain-check", "--node-timing",
            "--dataset", str(ROOT / dataset), "--chunks", str(args.chunks),
            "--filters", str(ROOT / "evals/filters_v2.graph.domain.jsonl"),
            "--chunk-version", "v9",
            "--output", str(output),
        ]
        if question_id != "all":
            command.extend(["--ids", question_id])
        if supplementary:
            command.append("--supplementary")
        else:
            command.extend([
                "--retrieval-subset", str(ROOT / "evals/mappings/dataset_v2.retrieval23.jsonl"),
                "--query-vectors", str(ROOT / "evals/results/v2_query_vectors_oai_small.npz"),
            ])
        environment = {
            "RETRIEVAL_CHUNKS_PATH": str(args.chunks),
            "RETRIEVAL_EMBEDDING_PREFIX": str(args.embeddings),
            "RETRIEVAL_BM25_TOKENS_PATH": str(args.tokens),
            "GRAPH_RETRIEVAL_ENABLED": "false" if mode in {"off", "graph_off"} else "true",
            "GRAPH_EXPANSION_LIMIT": "1",
            "GRAPH_LOW_SCORE_REVIEW": "true" if mode == "review_on" else "false",
            "GRAPH_RERANK_BASELINE_LIMIT": "40" if mode in {"budget_40", "graph_off", "graph_on"} else "",
        }
        planned.append((command, environment, output))

    if args.dry_run:
        for command, environment, output in planned:
            print(output.name, "graph=" + environment["GRAPH_RETRIEVAL_ENABLED"],
                  "review=" + environment["GRAPH_LOW_SCORE_REVIEW"],
                  "baseline_limit=" + (environment["GRAPH_RERANK_BASELINE_LIMIT"] or "unset"),
                  command[-4:])
        print("Dry run only: no .env loaded, no APIs called.")
        return 0

    from dotenv import load_dotenv

    if (args.budget_smoke or args.budget_full or args.adoption_full) and args.env_file is None:
        parser.error("budget comparison needs an explicit --env-file; choose the .env containing the current keys")
    args.env_file = args.env_file or ROOT / ".env"
    if not args.env_file.is_file():
        parser.error(f"credential file is missing: {args.env_file}")
    print(f"Loading credentials from: {args.env_file.resolve()} (values hidden)", flush=True)
    # Use the selected file even if this shell has an older key.
    load_dotenv(args.env_file, override=True)
    if not os.environ.get("OPENROUTER_API_KEY") or not os.environ.get("VOYAGE_API_KEY"):
        parser.error("OPENROUTER_API_KEY and VOYAGE_API_KEY must be present; keys are never printed")
    for _, _, output in planned:
        if output.exists() and not args.resume:
            parser.error(f"result already exists; refusing to overwrite: {output}")

    known_total = 0.0
    for command, overrides, output in planned:
        if output.exists() and args.resume:
            summary_path = output.with_suffix(".summary.json")
            if not summary_path.is_file():
                print(f"Stopped: existing result has no usage summary: {output}")
                return 2
            previous = json.loads(summary_path.read_text(encoding="utf8"))
            previous_cost = previous.get("llm_usage", {}).get("cost_usd_known_total")
            if previous_cost is None:
                print(f"Stopped: existing result has no known cost: {output}")
                return 2
            known_total += float(previous_cost)
            print(f"Reusing {output.name}; cumulative known cost=${known_total:.4f}")
            continue
        if known_total >= args.max_known_cost_usd:
            print(f"Stopped: recorded cost reached ${known_total:.4f}.")
            return 2
        print(f"Running {output.name} ...", flush=True)
        completed = subprocess.run(command, cwd=ROOT, env={**os.environ, **overrides}, check=False)
        if completed.returncode:
            print(f"Stopped: {output.name} failed (exit {completed.returncode}). No remaining cases were run.")
            return completed.returncode
        summary_path = output.with_suffix(".summary.json")
        if not summary_path.is_file():
            print(f"Stopped: missing usage summary {summary_path}.")
            return 2
        summary = json.loads(summary_path.read_text(encoding="utf8"))
        cost = summary.get("llm_usage", {}).get("cost_usd_known_total")
        if cost is None:
            print("Stopped: recorded cost is unavailable.")
            return 2
        known_total += float(cost)
        for line in output.read_text(encoding="utf8").splitlines():
            row = json.loads(line)
            citation_ids = [citation["chunk_id"] for citation in row.get("citations", [])]
            graph_ids = [cid for call in row.get("retrieval_calls", []) for cid in call.get("graph_candidate_ids", [])]
            print(
                f"  id={row.get('id')} outcome={row.get('outcome')} citations={citation_ids} "
                f"graph_candidates={graph_ids} elapsed_ms={row.get('elapsed_ms')} "
                f"run_known_cost=${cost:.4f} cumulative=${known_total:.4f}"
            )
        if summary.get("llm_usage", {}).get("cost_unknown_calls", 0):
            print("  Note: some API calls have unknown cost; the recorded total is a lower bound.")
    print(f"{len(planned)} runs finished. Compare each pair's citations and retrieval timing before drawing conclusions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
