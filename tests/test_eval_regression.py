"""평가 회귀 시험(D8): 평가 점수가 기준값에 못 미치면 pytest가 실패한다. 유료 API를 부르지 않는다.

- 기본: 기준 파일(evals/regression_baseline.json)이 가리키는 '지금 채택 구성의 결과'를 검사한다.
- 새로 잰 결과를 검사하려면 EVAL_REGRESSION_SUMMARY에 그 .summary.json 경로를 준다.
- 검사가 실제로 실패하는지도 본다: 기준 미달로 기록된 실행(113번, 환각 위험 1)과 점수를 일부러 낮춘 요약.
"""
import json
import os
from pathlib import Path

from evals.regression_gate import ROOT, load_baseline, searched_p95_seconds, violations

BASELINE = load_baseline()
THRESHOLDS = BASELINE["thresholds"]


def test_current_result_meets_baseline():
    target = os.environ.get("EVAL_REGRESSION_SUMMARY") or BASELINE["current_result"]
    path = Path(target) if Path(target).is_absolute() else ROOT / target
    found = violations(path, THRESHOLDS)
    assert found == [], "평가 결과가 기준에 못 미칩니다:\n" + "\n".join(f" - {line}" for line in found)


def test_gate_rejects_a_run_recorded_as_below_baseline():
    """113번 새 그래프 실행은 25번이 거절 대신 확인 필요로 나가 환각 위험 1이었다(미채택). 검사가 이것을 잡아야 한다."""

    found = violations(ROOT / BASELINE["known_bad_result"], THRESHOLDS)
    assert any(line.startswith("hallucination_risk 1") for line in found), found


def test_gate_rejects_each_kind_of_regression(tmp_path):
    """지표를 하나씩 일부러 기준 아래로 낮추면 그 항목이 위반으로 나온다(리터럴 기준과 비교)."""

    source = ROOT / BASELINE["current_result"]
    summary = json.loads(source.read_text(encoding="utf-8"))
    rows = source.with_name(source.name.replace(".summary.json", ".jsonl")).read_text(encoding="utf-8")

    def check(mutate, rows_text=rows) -> list[str]:
        changed = json.loads(json.dumps(summary))
        mutate(changed)
        path = tmp_path / "case.summary.json"
        path.write_text(json.dumps(changed), encoding="utf-8")
        (tmp_path / "case.jsonl").write_text(rows_text, encoding="utf-8")
        return violations(path, THRESHOLDS)

    response = lambda s: s["metrics"]["response"]
    assert check(lambda s: None) == []  # 고치지 않으면 통과
    assert check(lambda s: response(s)["correct_citation"].update(numerator=19)) == ["correct_citation 19: 기준 20 이상"]
    assert check(lambda s: response(s)["wrong_citation"].update(numerator=4)) == ["wrong_citation 4: 기준 3 이하"]
    assert check(lambda s: response(s)["correct_refusal"].update(numerator=22)) == ["correct_refusal 22: 기준 23 이상"]
    assert check(lambda s: response(s)["over_refusal"].update(numerator=1)) == ["over_refusal 1: 기준 0 이하"]
    assert check(lambda s: response(s)["hallucination_risk"].update(numerator=1)) == ["hallucination_risk 1: 기준 0 이하"]
    assert check(lambda s: s["metrics"]["retrieval_first_round"].update(ndcg_at_10=0.70)) == [
        "검색 1회차 ndcg_at_10 0.700: 기준 0.740 이상"]
    assert check(lambda s: s["llm_usage"].update(cost_usd_known_total=0.5)) == ["비용 $0.500: 기준 $0.35 이하"]
    assert check(lambda s: s["dataset"].update(sha256="다른 파일")) == ["평가셋 해시가 기준과 다릅니다(동결 v2 50건으로 잰 결과가 아님)"]
    assert check(lambda s: s["metrics"].update(n=5)) == ["문항 수 5건: 기준은 50건 전체 실행"]

    slow = "\n".join(
        json.dumps({**json.loads(line), "elapsed_ms": 30000.0}) for line in rows.splitlines() if line.strip())
    assert check(lambda s: None, slow) == ["검색 문항 p95 30.0초: 기준 20.0초 이하"]


def test_p95_uses_only_questions_that_reached_search():
    rows = [{"elapsed_ms": 1000.0, "search_calls": 0}, {"elapsed_ms": 5000.0, "search_calls": 1},
            {"elapsed_ms": 9000.0, "search_calls": 2}]
    assert searched_p95_seconds(rows) == 9.0
    assert searched_p95_seconds([{"elapsed_ms": 1000.0, "search_calls": 0}]) is None
