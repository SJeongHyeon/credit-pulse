"""평가 회귀 검사(D8): 평가 실행 결과가 기준값에 못 미치면 실패하게 한다.

평가 실행(scripts/run_graph_eval.py)이 남긴 요약(.summary.json)과 문항별 결과(.jsonl)를 읽어
evals/regression_baseline.json의 기준과 비교한다. 유료 API를 부르지 않는다(이미 저장된 결과만 읽는다).

  python -X utf8 -m evals.regression_gate                         # 기준 파일이 가리키는 '지금 결과' 검사
  python -X utf8 -m evals.regression_gate <결과>.summary.json     # 새로 잰 결과 검사
  pytest tests/test_eval_regression.py                            # 같은 검사를 pytest로(CI·로컬)
  $env:EVAL_REGRESSION_SUMMARY = "<결과>.summary.json"; pytest tests/test_eval_regression.py

기준을 넘지 못한 항목을 한 줄씩 돌려준다(빈 목록이면 통과). 기준값을 왜 그렇게 잡았는지는 기준 파일의 note에 있다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "evals" / "regression_baseline.json"

# 요약의 응답 지표 이름 → (기준 키, 방향). min이면 그 값 이상, max면 그 값 이하여야 한다.
RESPONSE_RULES = {
    "correct_citation": ("correct_citation_min", "min"),
    "wrong_citation": ("wrong_citation_max", "max"),
    "correct_refusal": ("correct_refusal_min", "min"),
    "over_refusal": ("over_refusal_max", "max"),
    "hallucination_risk": ("hallucination_risk_max", "max"),
}
RETRIEVAL_KEYS = ("hit_at_10", "recall_at_10", "mrr_at_10", "ndcg_at_10")


def load_baseline(path: Path = BASELINE_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def searched_p95_seconds(rows: list[dict]) -> float | None:
    """검색까지 간 문항의 응답 시간 p95(초). EVAL_REPORT 8장과 같은 정의."""

    # 검색 에이전트가 실제로 돈 문항만 센다(입구에서 거절된 질문의 미리 시작한 검색 기록은 세지 않는다).
    seconds = sorted(
        row["elapsed_ms"] / 1000
        for row in rows
        if (int(row["search_calls"]) > 0 if "search_calls" in row else len(row.get("retrieval_calls") or []) > 0)
    )
    if not seconds:
        return None
    return seconds[min(len(seconds) - 1, round(0.95 * (len(seconds) - 1)))]


def violations(summary_path: Path, thresholds: dict) -> list[str]:
    """기준에 못 미친 항목 목록. 결과 파일이 없거나 문항 수가 다르면 그것도 위반이다."""

    summary_path = Path(summary_path)
    if not summary_path.exists():
        return [f"결과 요약 파일이 없습니다: {summary_path}"]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    found: list[str] = []

    expected_sha = thresholds.get("dataset_sha256")
    if expected_sha and summary.get("dataset", {}).get("sha256") != expected_sha:
        found.append("평가셋 해시가 기준과 다릅니다(동결 v2 50건으로 잰 결과가 아님)")
    if summary.get("metrics", {}).get("n") != thresholds.get("n", 50):
        found.append(f"문항 수 {summary.get('metrics', {}).get('n')}건: 기준은 {thresholds.get('n', 50)}건 전체 실행")

    response = summary["metrics"]["response"]
    for metric, (key, direction) in RESPONSE_RULES.items():
        if key not in thresholds:
            continue
        value, limit = response[metric]["numerator"], thresholds[key]
        if direction == "min" and value < limit:
            found.append(f"{metric} {value}: 기준 {limit} 이상")
        if direction == "max" and value > limit:
            found.append(f"{metric} {value}: 기준 {limit} 이하")

    first_round = summary["metrics"].get("retrieval_first_round") or {}
    for key in RETRIEVAL_KEYS:
        limit = (thresholds.get("first_round_min") or {}).get(key)
        # 기준 파일의 값은 소수 셋째 자리로 적은 것이라 0.001의 여유를 둔다
        if limit is not None and float(first_round.get(key, 0)) < limit - 1e-3:
            found.append(f"검색 1회차 {key} {first_round.get(key):.3f}: 기준 {limit:.3f} 이상")

    cost_limit = thresholds.get("cost_usd_max")
    cost = summary.get("llm_usage", {}).get("cost_usd_known_total")
    if cost_limit is not None and cost is not None and cost > cost_limit:
        found.append(f"비용 ${cost:.3f}: 기준 ${cost_limit:.2f} 이하")

    p95_limit = thresholds.get("searched_p95_seconds_max")
    rows_path = summary_path.with_name(summary_path.name.replace(".summary.json", ".jsonl"))
    if p95_limit is not None:
        if not rows_path.exists():
            found.append(f"문항별 결과 파일이 없습니다: {rows_path}")
        else:
            rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            p95 = searched_p95_seconds(rows)
            if p95 is None:
                found.append("검색까지 간 문항이 없어 p95를 잴 수 없습니다")
            elif p95 > p95_limit:
                found.append(f"검색 문항 p95 {p95:.1f}초: 기준 {p95_limit:.1f}초 이하")
    return found


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    baseline = load_baseline()
    summary_path = Path(args[0]) if args else ROOT / baseline["current_result"]
    found = violations(summary_path, baseline["thresholds"])
    print(f"검사 대상: {summary_path}")
    if found:
        print("기준 미달:")
        for line in found:
            print(" -", line)
        return 1
    print("기준 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
