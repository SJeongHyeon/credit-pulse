"""사전 등록 28번: LLM 채점관을 체크리스트 지시문·더 큰 모델로 바꿔, 사람 판정과의 일치도를 먼저 잰다.

강의 2주17강 grading, 3주05강 'LLM judge 고정하기'(기준을 항목으로 나눠 묻는다), 특강10 '판정자 일치도'.
25번 채점관(gpt-4o-mini, 한 번에 relevant 판정)은 사람이 X로 판정한 조각 9개 중 4개만 무관으로 잡았다.

단계
  agree  : 사람 판정이 있는 45조각(엄격 정답 23·O 5·△ 8·X 9)만 채점해 일치도를 출력한다(검색 지표는 보지 않음).
  grade  : 일치도 기준을 넘은 조건 하나로 18번 상위 10개(150조각)를 채점한다(채점 파일은 apply 전에 커밋).
  apply  : 무관 판정을 뒤로 보낸 결과 파일과 지표를 만든다(LLM 호출 없음).

실행: python -X utf8 scripts/grade_checklist.py agree --prompt checklist --model openai/gpt-4o-mini
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import find_dotenv, load_dotenv  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

import grade_chunks as g1  # noqa: E402  25번 지시문·경로 재사용

CHECKLIST_PROMPT = """너는 한국 사업보고서(DART) 검색 결과를 채점한다. 답을 쓰지 않는다.
질문과 청크 하나를 보고 아래 세 가지를 순서대로 따로 확인한다. 청크에 없는 내용을 지어내지 않는다.
1. 주체: 청크가 다루는 주체를 청크의 낱말 그대로 적는다. 청크의 '당사'·'회사'·'연결회사'는 질문의 회사로 본다.
   종속회사·관계회사·펀드·조합 등 질문 회사가 아닌 다른 주체의 내용이면 subject_match=false.
2. 항목: 청크가 다루는 항목(예: 소송, 지급보증, 담보 제공 자산, 차입금, 주요 고객 매출)을 적고, 질문이 묻는 항목과 같으면 item_match=true.
3. 조건: 질문에 붙은 조건(예: '차입금과 관련해', '타인에게 제공한', '주요 고객', '1년 내')을 적고, 청크가 그 조건을 만족하는 정보를 담으면 condition_match=true. 질문에 조건이 없으면 true.
질문 항목의 일부만 담아도 match다. 질문 항목이 '해당 사항 없음'이라고 밝힌 문장도 item_match=true, condition_match=true다.
JSON 하나로만 답한다:
{"subject": "...", "subject_match": true/false, "item": "...", "item_match": true/false, "condition": "...", "condition_match": true/false, "reason": "한국어 한 문장"}"""

PROMPTS = {"v1": g1.SYSTEM_PROMPT, "checklist": CHECKLIST_PROMPT}
ADDITIONS = "evals/mappings/v8_gold_plus_additions.jsonl"
ROUTING = Path("evals/routing")


class Grade(BaseModel):
    relevant: bool
    reason: str = Field(min_length=1)


class Checklist(BaseModel):
    subject: str
    subject_match: bool
    item: str
    item_match: bool
    condition: str
    condition_match: bool
    reason: str = Field(min_length=1)


def slug(prompt: str, model: str) -> str:
    return f"{prompt}_{model.split('/')[-1].replace('.', '')}"


def human_labels() -> dict[tuple[int, str], str]:
    """18번 상위 10개 안의 사람 판정: S(엄격 정답)·O·△·X."""
    strict = {int(r["id"]): set(r["gold_chunks"]) for r in g1.read_jsonl(g1.DATASET)}
    labels = {}
    for r in g1.read_jsonl(g1.BASE):
        rid = int(r["id"])
        for cid in r["retrieved_chunks"]:
            if cid in strict.get(rid, set()):
                labels[(rid, cid)] = "S"
    for a in g1.read_jsonl(ADDITIONS):
        if a["chunking"] == "fixed_window":
            labels[(int(a["id"]), a["chunk_id"])] = a["verdict"]
    return labels


def run(pairs: list[tuple[int, int, str]], prompt: str, model: str, path: Path) -> list[dict]:
    load_dotenv(find_dotenv(usecwd=True))
    from backend.services.llm import ExternalApiStatusError, openrouter_structured

    questions = {int(r["id"]): r for r in g1.read_jsonl(g1.DATASET)}
    wanted = {cid for _, _, cid in pairs}
    texts = {}
    with open(g1.CHUNKS, encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row["chunk_id"] in wanted:
                texts[row["chunk_id"]] = row["content"]
    system = PROMPTS[prompt]
    schema = Checklist if prompt == "checklist" else Grade
    sha = hashlib.sha256(system.encode("utf-8")).hexdigest()
    rows = g1.read_jsonl(path) if path.exists() else []  # 이어서 하기
    done = {(r["id"], r["chunk_id"]) for r in rows}
    for rid, rank, cid in pairs:
        if (rid, cid) in done:
            continue
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": f"질문: {questions[rid]['input']}\n\n청크:\n{texts[cid]}"}]
        row = {"id": rid, "rank": rank, "chunk_id": cid, "model": model, "prompt": prompt, "prompt_sha256": sha}
        last = ""
        for attempt in range(3):
            try:
                out = openrouter_structured(messages, model=model, response_model=schema)
                data = out.model_dump()
                if prompt == "checklist":  # 세 항목이 모두 맞을 때만 relevant (판정은 코드가 한다)
                    data["relevant"] = data["subject_match"] and data["item_match"] and data["condition_match"]
                row.update(data)
                break
            except ExternalApiStatusError as exc:
                last = str(exc.status_code)
                time.sleep(2 * (attempt + 1))
        else:
            row.update({"relevant": True, "reason": f"채점 실패({last})", "error": True})
        rows.append(row)
        g1.write_jsonl(path, rows)
    return rows


def agreement(grades: dict[tuple[int, str], bool], labels: dict) -> dict:
    out = {}
    for group, members in (("정답·O", {"S", "O"}), ("△", {"△"}), ("X", {"X"})):
        keys = [k for k, v in labels.items() if v in members]
        irr = [k for k in keys if not grades.get(k, True)]
        out[group] = {"총": len(keys), "무관 판정": len(irr), "무관 판정 조각": [f"{k[0]}번 {k[1][-8:]}" for k in irr]}
    out["기준 통과"] = out["X"]["무관 판정"] >= 7 and out["정답·O"]["무관 판정"] <= 1 and out["△"]["무관 판정"] <= 2
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("step", choices=("agree", "grade", "apply"))
    ap.add_argument("--prompt", choices=tuple(PROMPTS), default="checklist")
    ap.add_argument("--model", default="openai/gpt-4o-mini")
    args = ap.parse_args()
    labels = human_labels()
    name = slug(args.prompt, args.model)
    ranks = {(int(r["id"]), c): i for r in g1.read_jsonl(g1.BASE) for i, c in enumerate(r["retrieved_chunks"], 1)}

    if args.step == "agree":
        if (args.prompt, args.model) == ("v1", "openai/gpt-4o-mini"):
            rows = g1.read_jsonl(g1.GRADES)  # 25번 채점을 그대로 쓴다(새 호출 없음)
        else:
            pairs = sorted((k[0], ranks[k], k[1]) for k in labels)
            rows = run(pairs, args.prompt, args.model, ROUTING / f"agree28_{name}.jsonl")
        grades = {(r["id"], r["chunk_id"]): r["relevant"] for r in rows}
        print(json.dumps({"조건": name, **agreement(grades, labels)}, ensure_ascii=False, indent=1))
        return

    path = ROUTING / f"grades28_{name}.jsonl"
    if args.step == "grade":
        questions = {int(r["id"]): r for r in g1.read_jsonl(g1.DATASET)}
        pairs = [(int(r["id"]), i, c) for r in g1.read_jsonl(g1.BASE) if questions[int(r["id"])]["kind"] == "relevant"
                 for i, c in enumerate(r["retrieved_chunks"], 1)]
        rows = run(pairs, args.prompt, args.model, path)
        print(json.dumps({"graded": len(rows), "irrelevant": sum(not r["relevant"] for r in rows),
                          "errors": sum(bool(r.get("error")) for r in rows), "output": str(path)}, ensure_ascii=False))
        return

    from backend.rag.evaluation import evaluate_files

    grades = {(r["id"], r["chunk_id"]): r["relevant"] for r in g1.read_jsonl(path)}
    out_path = Path(f"evals/results/v8_fixed_oai_rrf_kiwi_rr_voyage25_graded28_{name}_q15.jsonl")
    out = []
    for r in g1.read_jsonl(g1.BASE):
        rid = int(r["id"])
        kept = [c for c in r["retrieved_chunks"] if grades.get((rid, c), True)]
        dropped = [c for c in r["retrieved_chunks"] if not grades.get((rid, c), True)]
        out.append({"id": rid, "retrieved_chunks": kept + dropped, "graded": {"irrelevant": dropped} if dropped else {}})
    g1.write_jsonl(out_path, out)
    payload = evaluate_files(g1.DATASET, out_path, out_path.with_suffix(".metrics.json"), k=10)
    print(json.dumps({k: v for k, v in payload.items() if k != "per_question"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
