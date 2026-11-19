"""보조 지표(127번): 평가셋 기대 답에 적힌 숫자가 답(reason)에 얼마나 들어 있는지 센다. 결과 파일만 읽는다(호출 없음).

  python -X utf8 scripts/answer_number_coverage.py evals/results/graph50/w126_new_r1_multi_d3-55_v9_v2.jsonl [다른 결과 ...]

정답 근거 지표는 '맞는 청크를 하나라도 인용했는가'만 봐서, 재검색으로 틀린 숫자가 바로잡혀도 점수가 그대로다(124번 62번).
이 지표는 관련 문항마다 기대 답(expected)의 숫자 가운데 답의 reason에 그대로 나온 비율을 낸다.
한계: 숫자 표기가 다르면(천원 대 억원, 반올림) 못 잡는다. 기대 답에 숫자가 없는 문항은 뺀다. 숫자가 있다고 문장이 맞는 것은 아니다.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers(text: str) -> set[str]:
    """쉼표를 뺀 숫자 문자열. 네 자리 미만(연도·순번·퍼센트)은 금액이 아닐 때가 많아 뺀다."""

    found = {match.replace(",", "").rstrip(".") for match in NUMBER.findall(text or "")}
    return {value for value in found if len(value.replace(".", "")) >= 4}


def coverage(dataset: dict[int, dict], rows: list[dict]) -> dict[int, tuple[int, int]]:
    result = {}
    for row in rows:
        expected = numbers(str(dataset[int(row["id"])].get("expected") or "")) if dataset[int(row["id"])]["kind"] == "relevant" else set()
        if not expected:
            continue
        answer = numbers(" ".join(str(item.get("reason") or "") for item in row.get("draft_items") or []))
        result[int(row["id"])] = (len(expected & answer), len(expected))
    return result


def main() -> int:
    dataset = {int(row["id"]): row for row in map(json.loads, (ROOT / "evals/dataset_v2.jsonl").read_text(encoding="utf-8").splitlines())}
    for path in sys.argv[1:]:
        rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
        per = coverage(dataset, rows)
        ratios = [hit / total for hit, total in per.values()]
        print(f"{Path(path).name}: 문항 {len(per)}건, 기대 숫자 포함 비율 평균 {statistics.mean(ratios):.3f}, "
              f"하나라도 포함 {sum(1 for hit, _ in per.values() if hit)}건, 포함 숫자 합 {sum(hit for hit, _ in per.values())}/{sum(total for _, total in per.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
