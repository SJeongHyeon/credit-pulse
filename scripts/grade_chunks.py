"""사전 등록 25번: 리랭킹 뒤 상위 10개 청크를 LLM으로 채점(relevant/irrelevant)하고, 무관 판정을 뒤로 보낸다.

강의 2주17강 '가져온 것을 훑어본다 — grading'(P12 R2: 임계값과 LLM 채점은 다른 것을 본다)과
SDD BR-008(1단 리랭커 점수, 2단 LLM 채점)을 오프라인으로 시험한다.
LLM에는 질문과 청크 본문만 준다(정답은 주지 않음). 검색은 다시 하지 않는다.

실행: python -X utf8 scripts/grade_chunks.py grade   # LLM 채점 → evals/routing/grades_v1.jsonl (측정 전에 커밋)
      python -X utf8 scripts/grade_chunks.py apply   # 채점대로 순서를 바꾼 결과 파일과 지표 생성(LLM 호출 없음)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import find_dotenv, load_dotenv  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

SYSTEM_PROMPT = """너는 한국 사업보고서(DART) 검색 결과를 채점한다. 답을 쓰지 않는다.
질문과 청크 하나를 보고, 이 청크가 질문에 답하는 데 필요한 정보를 담고 있는지 판정한다.
- relevant: 질문이 묻는 주체(회사)·항목·조건에 대한 정보를 전부 또는 일부 담고 있다. 질문 항목이 '해당 사항 없음'이라고 밝힌 문장도 relevant다.
- irrelevant: 낱말이나 숫자 형식만 비슷하고, 질문이 묻는 주체·항목·조건과 다른 것을 다룬다.
JSON 하나로만 답한다: {"relevant": true 또는 false, "reason": "한국어 한 문장"}"""

BASE = "evals/results/v8_fixed_oai_rrf_kiwi_rr_voyage25_q15.jsonl"
GRADES = "evals/routing/grades_v1.jsonl"
OUT = "evals/results/v8_fixed_oai_rrf_kiwi_rr_voyage25_graded_q15.jsonl"
DATASET = "evals/mappings/dataset_v1.v8_fixed_window.jsonl"
CHUNKS = "data/experiments/b100-v8-fixed_window/chunks.jsonl"


class Grade(BaseModel):
    relevant: bool
    reason: str = Field(min_length=1)


def read_jsonl(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_jsonl(path: str | Path, rows: list[dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n").encode("utf-8"))


def grade(model: str) -> None:
    load_dotenv(find_dotenv(usecwd=True))
    from backend.services.llm import openrouter_structured

    questions = {int(r["id"]): r for r in read_jsonl(DATASET)}
    wanted = {cid for r in read_jsonl(BASE) for cid in r["retrieved_chunks"]}
    texts = {}
    with open(CHUNKS, encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row["chunk_id"] in wanted:
                texts[row["chunk_id"]] = row["content"]
    from backend.services.llm import ExternalApiStatusError

    prompt_sha = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    # 중간에 멈춰도 다시 실행하면 이어서 채점한다(이미 채점한 (문항, 청크)는 건너뜀).
    rows = read_jsonl(GRADES) if Path(GRADES).exists() else []
    done = {(r["id"], r["chunk_id"]) for r in rows}
    for result in read_jsonl(BASE):
        q = questions[int(result["id"])]
        if q["kind"] != "relevant":
            continue
        for rank, cid in enumerate(result["retrieved_chunks"], start=1):
            if (int(result["id"]), cid) in done:
                continue
            messages = [{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"질문: {q['input']}\n\n청크:\n{texts[cid]}"}]
            row = {"id": int(result["id"]), "rank": rank, "chunk_id": cid, "model": model, "prompt_sha256": prompt_sha}
            for attempt in range(3):  # 제공자 오류(400·5xx)는 최대 3번까지만 다시 묻는다
                try:
                    g = openrouter_structured(messages, model=model, response_model=Grade)
                    row.update({"relevant": g.relevant, "reason": g.reason})
                    break
                except ExternalApiStatusError as exc:
                    last = f"{exc.status_code}"
                    time.sleep(2 * (attempt + 1))
            else:
                # 채점 실패는 relevant로 두어 리랭커 순서를 그대로 유지한다(실패 수는 따로 보고).
                row.update({"relevant": True, "reason": f"채점 실패({last})", "error": True})
            rows.append(row)
        write_jsonl(GRADES, rows)
        print(f"id={result['id']} 채점 완료", flush=True)
    write_jsonl(GRADES, rows)
    print(json.dumps({"graded": len(rows), "irrelevant": sum(not r["relevant"] for r in rows),
                      "errors": sum(bool(r.get("error")) for r in rows), "output": GRADES}, ensure_ascii=False))


def apply() -> None:
    from backend.rag.evaluation import evaluate_files

    grades = {(r["id"], r["chunk_id"]): r["relevant"] for r in read_jsonl(GRADES)}
    out = []
    for result in read_jsonl(BASE):
        ids = result["retrieved_chunks"]
        rid = int(result["id"])
        kept = [c for c in ids if grades.get((rid, c), True)]
        dropped = [c for c in ids if not grades.get((rid, c), True)]
        out.append({"id": rid, "retrieved_chunks": kept + dropped,
                    "graded": {"irrelevant": dropped} if dropped else {}})
    write_jsonl(OUT, out)
    payload = evaluate_files(DATASET, OUT, Path(OUT).with_suffix(".metrics.json"), k=10)
    print(json.dumps({k: v for k, v in payload.items() if k != "per_question"}, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("step", choices=("grade", "apply"))
    ap.add_argument("--model", default="openai/gpt-4o-mini")
    args = ap.parse_args()
    grade(args.model) if args.step == "grade" else apply()
