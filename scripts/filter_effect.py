"""사전 등록 27번: 회사 필터를 끄면 검색이 얼마나 나빠지는지 잰다(1주10강 '필터가 진짜 이유다').

18번 조합(고정 크기 + OpenAI 1,536 + BM25 Kiwi RRF + Voyage rerank-2.5, 후보 20)에서
회사 필터만 끄고 사업연도 필터는 유지한다. 검색 범위는 문서 벡터가 있는 평가 16개사다.
서비스 검색 계약은 회사·연도 필터를 필수로 요구하므로, 이 진단 스크립트 안에서만
필터 일치 검사를 건너뛴다(저장소 코드는 바꾸지 않음).

실행: python -X utf8 scripts/filter_effect.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv(find_dotenv(usecwd=True), override=False)

import backend.rag.offline_eval as oe  # noqa: E402
from backend.rag.bm25 import create_tokenizer  # noqa: E402
from backend.rag.embedding import create_embedder  # noqa: E402
from backend.rag.evaluation import compute_retrieval_metrics  # noqa: E402
from backend.rag.rerank import create_reranker  # noqa: E402
from backend.rag.retrieve import SearchFilters  # noqa: E402

DATASET = "evals/mappings/dataset_v1.v8_fixed_window.jsonl"
CHUNKS = "data/experiments/b100-v8-fixed_window/chunks.jsonl"
PREFIX = "data/embeddings/v8/oai-large-fixed"
OUT = Path("evals/results/v8_fixed_oai_rrf_kiwi_rr_voyage25_nocorp_q15.jsonl")


def main() -> None:
    meta, vectors = oe.load_embedding_artifacts(PREFIX)
    allowed = set(meta["corp_codes"])

    def match_year_only(chunks, filters):  # 회사 필터를 끄고 연도만 본다(벡터가 있는 16개사 안에서)
        return [c for c in chunks if str(c.get("corp_code")) in allowed and int(c.get("fiscal_year", 0)) == filters.fiscal_year]

    def retrieve_unchecked(question, *, filters, top_k, backend):  # 계약의 필터 일치 검사만 건너뜀
        return [c.to_dict() for c in backend.search(" ".join(question.split()), filters=filters, top_k=top_k)]

    def filters_year_only(row, filters_by_id):  # 회사 자리를 고정값으로 두어 BM25 색인을 한 번만 만든다(매칭은 연도만 봄)
        raw = row.get("filters") or filters_by_id.get(int(row["id"]))
        return SearchFilters(corp_code="00000000", fiscal_year=int(raw["fiscal_year"])) if raw else None

    oe._chunks_matching = match_year_only
    oe.retrieve = retrieve_unchecked
    oe._filters_for = filters_year_only

    chunks = oe._read_jsonl(CHUNKS)
    vec = oe.InMemoryVectorBackend(chunks=chunks, vectors_by_sha=vectors,
                                   query_embedder=create_embedder(str(meta["model_name"]), max_length=512),
                                   retrieval_method=f"offline_cosine:{meta['model_name']}")
    bm25 = oe.InMemoryBM25Backend(chunks=chunks, tokenizer=create_tokenizer("kiwi"), retrieval_method="bm25:kiwi")
    backend = oe.RRFBackend(vector_backend=vec, keyword_backend=bm25, rrf_k=60, candidate_k=50,
                            retrieval_method="rrf:oai1536+bm25:kiwi(no corp filter)")
    filters = {int(r["id"]): r for r in oe._read_jsonl("evals/filters_v1.jsonl")}
    strict = oe._read_jsonl(DATASET)
    if not OUT.exists():
        rows = oe.run_reranked_retrieval(strict, backend, create_reranker("voyage/rerank-2.5", max_length=512),
                                         top_k=10, candidate_k=20, filters_by_id=filters)
        oe._write_jsonl(OUT, rows)
    payload = oe._write_rerank_metrics(DATASET, OUT, oe._read_jsonl(OUT), top_k=10, candidate_k=20, model_name="voyage/rerank-2.5")

    corp_of = {c["chunk_id"]: str(c["corp_code"]) for c in chunks if str(c["corp_code"]) in allowed}
    other = top1_other = total = 0
    for r in oe._read_jsonl(OUT):
        f = filters.get(int(r["id"]))
        if not f:
            continue
        ids = r["retrieved_chunks"]
        wrong = [c for c in ids if corp_of.get(c) != str(f["corp_code"])]
        other += len(wrong); total += len(ids); top1_other += int(bool(ids) and corp_of.get(ids[0]) != str(f["corp_code"]))
    summary = {k: v for k, v in payload.items() if k not in ("per_question",)}
    summary.update({"다른회사_조각_비율_top10": round(other / total, 3), "1위가_다른회사인_문항": top1_other})
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
