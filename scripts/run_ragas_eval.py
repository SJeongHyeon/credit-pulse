"""RAGAS 참고 지표(사전 등록 151번): 이미 있는 평가 결과의 답을 다시 만들지 않고 채점만 한다.

무엇을 재나
- 답 관련성(ResponseRelevancy): 답에서 질문을 거꾸로 만들어 원래 질문과 임베딩이 얼마나 비슷한지.
- 충실도(Faithfulness): 답의 문장이 문맥(인용한 청크 본문)으로 뒷받침되는 비율.

왜 응답 지표 5개와 따로 두나
- 5개는 인용 청크를 정답 청크와 대조하는 규칙 기반이고, 이것은 LLM 판정이다. 이 판정자는 사람 판정과 대조하지 않았다
  (93번의 답 판정자는 kappa 미달). 그래서 채택·회귀 기준으로 쓰지 않는다.
- 거절·되묻기·인용 없는 '확인 필요'는 넣지 않고 건수만 센다(Faithfulness는 정직한 거절을 0점으로 매긴다).

LLM·임베딩 호출은 모두 backend/services/llm.py를 거친다(AGENTS.md 2번). ragas는 서비스와 다른 가상환경에 둔다
(requirements-ragas.txt).

실행:
  .venv-ragas/Scripts/python.exe -X utf8 scripts/run_ragas_eval.py \
    --results evals/results/graph50/w146_now_r1_multi_d3-55_v9_v2.jsonl \
    --chunks data/experiments/b100-v9-fixed_window/chunks.jsonl \
    --output evals/results/ragas/ragas_w146_now_r1.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

JUDGE_MODEL = "openai/gpt-4.1"
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536
STRICTNESS = 3  # 답 하나에서 거꾸로 만드는 질문 수(RAGAS 기본)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_samples(dataset: list[dict], results: list[dict], chunk_text: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """채점할 문항과 뺀 문항(사유 포함)을 나눈다. 답 = 인용이 있는 초안 항목의 reason, 문맥 = 인용 청크 본문."""

    questions = {int(row["id"]): row for row in dataset}
    samples: list[dict] = []
    skipped: list[dict] = []
    for row in results:
        row_id = int(row["id"])
        gold = questions[row_id]
        if gold["kind"] != "relevant":
            skipped.append({"id": row_id, "reason": "irrelevant_question"})
            continue
        if row.get("outcome") != "answered":
            skipped.append({"id": row_id, "reason": f"outcome_{row.get('outcome')}"})
            continue
        answers: list[str] = []
        chunk_ids: list[str] = []
        for item in row.get("draft_items") or []:
            cited = [c["chunk_id"] for c in item.get("citations") or [] if c.get("chunk_id")]
            if not cited or not str(item.get("reason") or "").strip():
                continue
            answers.append(str(item["reason"]).strip())
            chunk_ids.extend(chunk_id for chunk_id in cited if chunk_id not in chunk_ids)
        if not answers:
            skipped.append({"id": row_id, "reason": "no_cited_answer"})  # 되묻기, 인용 없는 '확인 필요'
            continue
        missing = [chunk_id for chunk_id in chunk_ids if chunk_id not in chunk_text]
        if missing:
            raise KeyError(f"{row_id}번: 청크 파일에 없는 인용 청크 {missing[:3]}")
        samples.append({
            "id": row_id,
            "user_input": gold["input"],
            "response": "\n".join(answers),
            "retrieved_contexts": [chunk_text[chunk_id] for chunk_id in chunk_ids],
            "chunk_ids": chunk_ids,
        })
    return samples, skipped


def summarize(records: list[dict], skipped: list[dict]) -> dict:
    """문항별 점수의 평균·최소와 점수를 못 매긴 문항(NaN). 뺀 문항은 사유별 건수."""

    summary: dict = {"scored_questions": len(records), "skipped": {}}
    for item in skipped:
        summary["skipped"][item["reason"]] = summary["skipped"].get(item["reason"], 0) + 1
    for name in ("answer_relevancy", "faithfulness"):
        values = [r[name] for r in records if r.get(name) is not None]
        summary[name] = {
            "n": len(values),
            "mean": round(sum(values) / len(values), 4) if values else None,
            "min": round(min(values), 4) if values else None,
            "below_0_5": sorted(r["id"] for r in records if r.get(name) is not None and r[name] < 0.5),
            "not_scored": sorted(r["id"] for r in records if r.get(name) is None),
        }
    return summary


def ragas_adapters(judge_model: str):
    """ragas가 부르는 LLM·임베딩을 llm.py 함수로 잇는다. ragas는 여기서만 import한다(서비스 환경에는 없다)."""

    from langchain_core.outputs import Generation, LLMResult
    from ragas.embeddings.base import BaseRagasEmbeddings
    from ragas.llms.base import BaseRagasLLM

    from backend.services import llm

    @dataclass
    class JudgeLLM(BaseRagasLLM):
        model: str = judge_model

        def generate_text(self, prompt, n=1, temperature=0.01, stop=None, callbacks=None):
            temperature = self.get_temperature(n) if temperature is None else temperature
            texts = [llm.eval_judge_text(prompt.to_string(), model=self.model, temperature=temperature) for _ in range(n)]
            return LLMResult(generations=[[Generation(text=text) for text in texts]])

        async def agenerate_text(self, prompt, n=1, temperature=0.01, stop=None, callbacks=None):
            return await asyncio.to_thread(self.generate_text, prompt, n, temperature, stop, callbacks)

        def is_finished(self, response):
            return True

    class Embeddings(BaseRagasEmbeddings):
        def embed_documents(self, texts):
            return llm.openai_embeddings(list(texts), model=EMBEDDING_MODEL, dimensions=EMBEDDING_DIMENSIONS)

        def embed_query(self, text):
            return self.embed_documents([text])[0]

        async def aembed_documents(self, texts):
            return await asyncio.to_thread(self.embed_documents, texts)

        async def aembed_query(self, text):
            return await asyncio.to_thread(self.embed_query, text)

    return JudgeLLM(), Embeddings()


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else round(number, 4)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--results", type=Path, required=True, help="run_graph_eval.py가 만든 문항별 결과 JSONL")
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals/dataset_v2.jsonl")
    parser.add_argument("--chunks", type=Path, required=True, help="인용 청크 본문을 읽을 청크 파일")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--judge-model", default=JUDGE_MODEL)
    parser.add_argument("--ids", help="이 문항 id만(쉼표 구분)")
    args = parser.parse_args()

    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True), override=False)
    # 이 도구의 가상환경에는 langfuse가 없다. 판정 호출은 서비스 트레이스가 아니므로 관측을 끈다(비용은 요약에 남는다).
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        os.environ.pop(name, None)

    results = _read_jsonl(args.results)
    if args.ids:
        wanted = {int(part) for part in args.ids.split(",")}
        results = [row for row in results if int(row["id"]) in wanted]
    cited = {c["chunk_id"] for row in results for item in row.get("draft_items") or [] for c in item.get("citations") or []}
    chunk_text: dict[str, str] = {}
    with args.chunks.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                chunk = json.loads(line)
                if chunk["chunk_id"] in cited:
                    chunk_text[chunk["chunk_id"]] = chunk["content"]
    samples, skipped = build_samples(_read_jsonl(args.dataset), results, chunk_text)
    print(f"채점 {len(samples)}건, 제외 {len(skipped)}건", flush=True)

    import ragas
    from ragas import EvaluationDataset, evaluate
    from ragas.metrics import Faithfulness, ResponseRelevancy
    from ragas.run_config import RunConfig

    from backend.services import llm

    judge, embeddings = ragas_adapters(args.judge_model)
    dataset = EvaluationDataset.from_list(
        [{key: sample[key] for key in ("user_input", "response", "retrieved_contexts")} for sample in samples]
    )
    llm.drain_usage()
    result = evaluate(
        dataset=dataset,
        metrics=[ResponseRelevancy(strictness=STRICTNESS), Faithfulness()],
        llm=judge,
        embeddings=embeddings,
        run_config=RunConfig(max_workers=4, timeout=180),
        raise_exceptions=False,
    )
    usage = llm.drain_usage()
    frame = result.to_pandas()
    records = [
        {"id": sample["id"], "answer_relevancy": _number(frame.iloc[index]["answer_relevancy"]),
         "faithfulness": _number(frame.iloc[index]["faithfulness"]), "chunk_ids": sample["chunk_ids"],
         "response_chars": len(sample["response"])}
        for index, sample in enumerate(samples)
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8", newline="\n")
    costs = [entry["cost_usd"] for entry in usage if entry.get("cost_usd") is not None]
    summary = {
        "what": "RAGAS 참고 지표(사전 등록 151번). 채택·회귀 기준 아님. 사람 판정과 대조하지 않은 LLM 판정",
        "results": args.results.as_posix() if not args.results.is_absolute() else args.results.name,
        "ragas_version": ragas.__version__,
        "judge_model": args.judge_model,
        "judge_temperature": "RAGAS 기본(1회 0.01, 여러 개 생성 0.3)",
        "embedding_model": f"{EMBEDDING_MODEL} ({EMBEDDING_DIMENSIONS}차원)",
        "prompts": "RAGAS 기본(영어)",
        "strictness": STRICTNESS,
        **summarize(records, skipped),
        "skipped_ids": skipped,
        "calls": len(usage),
        "cost_usd": round(sum(costs), 4),
        "cost_unknown_calls": len(usage) - len(costs),
    }
    args.output.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps({k: summary[k] for k in ("scored_questions", "skipped", "answer_relevancy", "faithfulness", "cost_usd")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
