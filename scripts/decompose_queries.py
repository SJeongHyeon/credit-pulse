"""사전 등록 23번: 여러 항목을 함께 묻는 질문을 항목별 하위 질의로 나누고 파일로 남긴다.

강의 2주19강(질의를 나눈다, P12 R7)을 한 회사 안의 '여러 항목' 질문에 응용한다.
LLM에는 질문 원문만 준다(정답·검색 결과는 주지 않음). 결과는 검색 측정 전에 커밋하고 고치지 않는다.

실행: python -X utf8 scripts/decompose_queries.py --dry-run
      python -X utf8 scripts/decompose_queries.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import find_dotenv, load_dotenv  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

SYSTEM_PROMPT = """너는 한국 사업보고서(DART) 검색 질의를 나누는 도우미다. 답을 쓰지 않는다.
질문이 서로 다른 근거(다른 표나 다른 설명)가 필요한 항목을 여러 개 함께 묻는다면 항목마다 하위 질의 하나로 나눈다.
- 하위 질의는 2~4개. 각 하위 질의는 회사명을 포함한 완결된 한국어 검색 문장으로 쓴다.
- 질문에 없는 항목을 새로 만들지 않고, 질문의 낱말을 되도록 그대로 쓴다.
- 한 가지만 묻는 질문은 나누지 않고 원문 그대로 1개만 돌려준다.
JSON 하나로만 답한다: {"subqueries": ["...", "..."], "reason": "한국어 한 문장"}"""


class Decomposition(BaseModel):
    subqueries: list[str] = Field(min_length=1, max_length=4)
    reason: str = Field(min_length=1)


def read_jsonl(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default="evals/dataset_v1.jsonl")
    parser.add_argument("--filters", default="evals/filters_v1.jsonl", help="검색하는 문항(관련 15건)만 나눈다")
    parser.add_argument("--model", default="openai/gpt-4o-mini")
    parser.add_argument("--output", default="evals/routing/subqueries_v1.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    questions = {int(row["id"]): str(row["input"]) for row in read_jsonl(args.dataset)}
    ids = [int(row["id"]) for row in read_jsonl(args.filters)]
    prompt_sha = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    if args.dry_run:
        print(json.dumps({"dry_run": True, "requests": len(ids), "model": args.model, "prompt_sha256": prompt_sha}, ensure_ascii=False))
        return 0
    load_dotenv(find_dotenv(usecwd=True))
    from backend.services.llm import openrouter_structured

    rows = []
    for qid in ids:
        result = openrouter_structured(
            [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": f"질문: {questions[qid]}"}],
            model=args.model,
            response_model=Decomposition,
        )
        subs = [s.strip() for s in result.subqueries if s.strip()] or [questions[qid]]
        rows.append({"id": qid, "question": questions[qid], "subqueries": subs, "reason": result.reason,
                     "model": args.model, "prompt_sha256": prompt_sha})
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_bytes(("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n").encode("utf-8"))
    print(json.dumps({"written": len(rows), "split": sum(len(r["subqueries"]) > 1 for r in rows), "output": args.output}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
