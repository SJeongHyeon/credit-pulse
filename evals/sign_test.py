"""두 그래프 실행 결과를 문항별로 짝지어 부호 검정한다.

전체 점수(예: 20 → 22)만 보면 우연한 흔들림과 효과를 가를 수 없다.
같은 평가셋의 같은 문항끼리 비교해 '좋아짐 b건 대 나빠짐 c건'을 세고,
효과가 없다면 b와 c가 반반이라는 가정 아래 양측 정확 이항 p값을 낸다.
바뀐 문항이 5건 이하면 모두 한쪽이어도 p >= 0.0625라 개선이라고 쓸 수 없다.

사용:
    python evals/sign_test.py BEFORE.jsonl AFTER.jsonl [--metric correct_citation] [--dataset evals/dataset_v2.jsonl]
"""
from __future__ import annotations

import argparse
import json
from math import comb
from pathlib import Path

from evals.response_metrics import per_question_flags


def _read_jsonl(path: str | Path) -> dict[int, dict]:
    with open(path, encoding="utf-8") as f:
        return {row["id"]: row for row in map(json.loads, f) if row}


def sign_test_p(improved: int, worsened: int) -> float:
    """양측 정확 부호 검정 p값. 바뀐 문항이 없으면 1.0."""
    n = improved + worsened
    if n == 0:
        return 1.0
    k = min(improved, worsened)
    return min(1.0, 2 * sum(comb(n, j) for j in range(k + 1)) / 2**n)


def compare(dataset: dict[int, dict], before: dict[int, dict], after: dict[int, dict], metric: str) -> dict:
    ids = sorted(set(before) & set(after))
    improved, worsened = [], []
    total_before = total_after = 0
    for qid in ids:
        a = per_question_flags(dataset[qid], before[qid]).get(metric)
        b = per_question_flags(dataset[qid], after[qid]).get(metric)
        if a is None or b is None:  # 이 지표가 정의되지 않는 문항(예: 비관련 문항의 인용 지표)
            continue
        total_before += a
        total_after += b
        if a < b:
            improved.append(qid)
        elif a > b:
            worsened.append(qid)
    return {
        "metric": metric,
        "before": total_before,
        "after": total_after,
        "improved": improved,
        "worsened": worsened,
        "p_two_sided": sign_test_p(len(improved), len(worsened)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--metric", default="correct_citation")
    parser.add_argument("--dataset", default="evals/dataset_v2.jsonl")
    args = parser.parse_args(argv)

    result = compare(
        _read_jsonl(args.dataset), _read_jsonl(args.before), _read_jsonl(args.after), args.metric
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
