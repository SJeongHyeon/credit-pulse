"""주제 분류(`llm.classify_domain`) 호출만 떼어 질문 세트에 여러 번 보내 결과 갈래를 기록한다(163번).

그래프를 돌리지 않는다. 분류 지시문을 바꾸기 전과 뒤에 같은 세트로 실행해 문항별로 견준다. 유료 호출이다(호출당 수백 토큰).

  python -X utf8 scripts/check_domain_labels.py --output evals/results/domain_labels/before.jsonl --repeats 3 \
      evals/dataset_v2.jsonl evals/probes/disclosure_probe_v2.jsonl evals/probes/disclosure_probe_v3.jsonl

입력 JSONL의 각 줄은 `id`와 `input`이 있어야 한다. 출력: 문항마다 {"set", "id", "input", "labels": [갈래, ...]}.
모델은 서비스가 쓰는 기본 모델(`llm.DEFAULT_GENERATION_MODEL`)이다.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sets", nargs="+", help="질문 세트 JSONL(id, input)")
    parser.add_argument("--output", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)

    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True), override=False)
    from backend.services import llm

    cases = []
    for path in args.sets:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                cases.append({"set": Path(path).name, "id": str(row["id"]), "input": str(row["input"])})

    def classify(case: dict) -> dict:
        labels = [llm.classify_domain(question=case["input"]) for _ in range(args.repeats)]
        return {**case, "labels": labels}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(classify, cases))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results), encoding="utf-8", newline="\n")
    usage = llm.drain_usage()
    print(json.dumps({"questions": len(results), "calls": len(results) * args.repeats,
                      "cost_usd": round(sum(float(u.get("cost_usd") or 0) for u in usage), 4)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
