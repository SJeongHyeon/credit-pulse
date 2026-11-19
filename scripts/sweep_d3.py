"""사전 등록 22번: 이미 만든 벡터로 돌릴 수 있는 조합을 한 번에 재고 표 하나로 모은다(탐색 실험).

조합 = 청킹(표 묶음·고정 크기) x 임베딩(e5-small·OpenAI 1,536·OpenAI 3,072)
       x 검색기(벡터 단독·BM25 Kiwi RRF) x 리랭커(없음·Voyage rerank-2.5·rerank-3, 후보 20).
문서 벡터는 다시 만들지 않는다. 질문 임베딩과 Voyage 호출만 생긴다.
결과 파일이 이미 있으면 건너뛰므로 중간에 멈춰도 다시 실행하면 이어서 돈다.

실행: python -X utf8 scripts/sweep_d3.py            # 측정 + 요약
      python -X utf8 scripts/sweep_d3.py --summary  # 요약만(측정 안 함)
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.rag.evaluation import compute_retrieval_metrics  # noqa: E402

CHUNKINGS = {
    "group": ("evals/mappings/dataset_v1.v8_group.jsonl", "data/experiments/b100-v8-group/chunks.jsonl"),
    "fixed": ("evals/mappings/dataset_v1.v8_fixed_window.jsonl", "data/experiments/b100-v8-fixed_window/chunks.jsonl"),
}
EMBEDDINGS = {"e5": "e5-{c}-v1corps16", "oai1536": "oai-large-{c}", "oai3072": "oai-large3072-{c}"}
RETRIEVERS = {"vec": [], "rrf": ["--retriever", "rrf", "--tokenizer", "kiwi", "--rrf-k", "60", "--rrf-candidates", "50"]}
RERANKERS = {"none": [], "v25": ["--reranker", "voyage/rerank-2.5", "--candidate-k", "20"],
             "v3": ["--reranker", "voyage/rerank-3", "--candidate-k", "20"]}
OUT_DIR = Path("evals/results/sweep22")


def read_jsonl(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def combos():
    for c, e, r, k in itertools.product(CHUNKINGS, EMBEDDINGS, RETRIEVERS, RERANKERS):
        yield c, e, r, k, OUT_DIR / f"{c}_{e}_{r}_{k}.jsonl"


def run_all() -> None:
    from backend.rag.offline_eval import main as offline_main

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    todo = [x for x in combos() if not x[4].exists()]
    for n, (c, e, r, k, out) in enumerate(todo, 1):
        chunk_key = "fixed" if c == "fixed" else "group"
        prefix = "data/embeddings/v8/" + EMBEDDINGS[e].format(c=chunk_key)
        dataset, chunks = CHUNKINGS[c]
        argv = ["--dataset", dataset, "--chunks", chunks, "--embedding-prefix", prefix,
                "--filters", "evals/filters_v1.jsonl", *RETRIEVERS[r], *RERANKERS[k], "--output", str(out)]
        print(f"[{n}/{len(todo)}] {out.name}", flush=True)
        offline_main(argv)


def summarize() -> list[dict]:
    rows = []
    for c, e, r, k, out in combos():
        if not out.exists():
            continue
        dataset_path = CHUNKINGS[c][0]
        strict = read_jsonl(dataset_path)
        plus_by_id = {row["id"]: row["gold_plus"] for row in read_jsonl(dataset_path.replace(".jsonl", ".gold_plus.jsonl"))}
        plus = [{**row, "gold_chunks": plus_by_id[row["id"]]} if row["kind"] == "relevant" else row for row in strict]
        results = [{"id": x["id"], "retrieved_chunks": x["retrieved_chunks"]} for x in read_jsonl(out)]
        s = compute_retrieval_metrics(strict, results)
        p = compute_retrieval_metrics(plus, results)
        metrics_path = out.with_suffix(".metrics.json")
        latency = json.loads(metrics_path.read_text(encoding="utf-8")).get("latency", {}) if metrics_path.exists() else {}
        rows.append({"chunking": c, "embedding": e, "retriever": r, "reranker": k,
                     "hit": round(s["hit_at_10"], 3), "recall": round(s["recall_at_10"], 3),
                     "mrr": round(s["mrr_at_10"], 3), "ndcg": round(s["ndcg_at_10"], 3),
                     "plus_mrr": round(p["mrr_at_10"], 3), "p95_ms": round(latency.get("p95_ms", 0) or 0)})
    rows.sort(key=lambda x: (-x["hit"], -x["mrr"], -x["recall"]))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{'청킹':<6}{'임베딩':<9}{'검색':<5}{'리랭커':<6}{'Hit':>6}{'Recall':>8}{'MRR':>7}{'nDCG':>7}{'확장MRR':>8}{'p95ms':>7}")
    for x in rows:
        print(f"{x['chunking']:<6}{x['embedding']:<9}{x['retriever']:<5}{x['reranker']:<6}{x['hit']:>6}{x['recall']:>8}{x['mrr']:>7}{x['ndcg']:>7}{x['plus_mrr']:>8}{x['p95_ms']:>7}")
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()
    if not args.summary:
        run_all()
    summarize()
