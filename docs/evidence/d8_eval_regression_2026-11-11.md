# D8 평가 회귀 시험 실행 기록 (2026-11-11)

**결론: 평가 점수가 기준값에 못 미치면 pytest가 실패합니다. 지금 채택 구성의 결과는 통과하고, 기준에 못 미친 실제 실행 두 건(113번·111번)을 넣으면 실패하는 것을 로컬에서 확인했습니다.** GitHub Actions는 조직 무료 한도가 소진돼 로컬 pytest 실행 기록으로 남깁니다(CI 설정 `.github/workflows/ci.yml`에는 같은 시험이 단계로 들어 있어, 한도가 풀리면 PR마다 돕니다).

## 무엇을 검사하나

- 코드: [`evals/regression_gate.py`](../../evals/regression_gate.py), 기준: [`evals/regression_baseline.json`](../../evals/regression_baseline.json), 시험: [`tests/test_eval_regression.py`](../../tests/test_eval_regression.py)
- 평가 실행(`scripts/run_graph_eval.py`)이 남긴 결과 파일을 읽어 기준과 비교합니다. 유료 API를 부르지 않습니다.
- 새로 잰 결과를 검사하려면 `EVAL_REGRESSION_SUMMARY`에 그 요약 파일 경로를 줍니다.

| 지표 (v2 50건) | 기준 | 채택 구성 7회 측정값 | 기준을 이렇게 잡은 이유 |
|---|---|---|---|
| 정답 근거 인용 | 20 이상 | 7회 모두 21 | 실행마다 1문항쯤 흔들려 1문항 여유 |
| 오답 근거 | 3 이하 | 7회 모두 2 | 1문항 여유 |
| 올바른 거절 | 23 이상 | 7회 모두 24 | 1문항 여유 |
| 과잉 거절 | 0 | 0 | 여유 없음 |
| 환각 위험 | 0 | 0 | 여유 없음 |
| 검색 1회차 Hit·Recall·MRR·nDCG@10 | 1.000·0.926·0.799·0.740 | 같음 | 질의 벡터를 고정해 결정적 |
| 검색 문항 p95 | 20초 이하 | 9.5~15.3초 | 회차별 변동 폭 위 |
| 50건 비용 | $0.35 이하 | $0.19~0.23 | 변동 폭 위 |
| 평가셋 | 동결 v2 해시, 50건 전체 | | 다른 파일·일부 문항 실행을 막음 |

## 실행 기록

환경: Windows 11, Python 3.11, 브랜치 `docs/instructor-comments-d8`(기준 커밋 `eedb687` 위), 2026-11-11 새벽. 키 없이 실행했습니다(`tests/conftest.py`가 API 키를 지움).

### 1. 전체 시험 (키 없음)

```
$ python -m pytest -q
......................                                                   [100%]
950 passed, 8 skipped in 184.33s (0:03:04)
```

### 2. 평가 회귀 시험: 지금 채택 구성의 결과 → 통과

```
$ python -m pytest tests/test_eval_regression.py -v
tests/test_eval_regression.py::test_current_result_meets_baseline PASSED [ 25%]
tests/test_eval_regression.py::test_gate_rejects_a_run_recorded_as_below_baseline PASSED [ 50%]
tests/test_eval_regression.py::test_gate_rejects_each_kind_of_regression PASSED [ 75%]
tests/test_eval_regression.py::test_p95_uses_only_questions_that_reached_search PASSED [100%]
============================== 4 passed in 0.42s ==============================
```

검사 대상은 기준 파일의 `current_result`(`w117_base_r3`, 채택 구성 코드 27e0020의 가장 최근 실행)입니다.

### 3. 기준에 못 미친 변경의 결과를 넣으면 → 실패

**(가) 113번 그래프의 실행.** 113번은 '검색 뒤 판단을 LLM에 돌려주는' 변경이었고, 25번 문항이 작성 형식 오류로 '거절' 대신 '확인 필요'로 나가 환각 위험 1이 됐습니다(사전 등록 기준으로 미채택, 114번에서 고침).

```
$ $env:EVAL_REGRESSION_SUMMARY = "evals/results/graph50/w113_new_r1_multi_d3-55_v9_v2.summary.json"
$ python -m pytest tests/test_eval_regression.py -q
E       AssertionError: 평가 결과가 기준에 못 미칩니다:
E          - hallucination_risk 1: 기준 0 이하
FAILED tests/test_eval_regression.py::test_current_result_meets_baseline - As...
1 failed, 3 passed in 0.59s
```

**(나) 111번 그래프의 실행.** 정답 근거 인용이 19건이었습니다(미채택).

```
$ $env:EVAL_REGRESSION_SUMMARY = "evals/results/graph50/w111_new_r1_multi_d3-55_v9_v2.summary.json"
$ python -m pytest tests/test_eval_regression.py -q
E       AssertionError: 평가 결과가 기준에 못 미칩니다:
E          - correct_citation 19: 기준 20 이상
FAILED tests/test_eval_regression.py::test_current_result_meets_baseline - As...
1 failed, 3 passed
```

두 실행 모두 일부러 꾸민 숫자가 아니라, 실제로 코드를 바꿔 v2 50건을 돌린 결과 파일입니다(`evals/PREREGISTER.md` 111·113번).

### 4. 검사가 항목마다 작동하는지

`test_gate_rejects_each_kind_of_regression`이 통과한 결과의 지표를 하나씩 기준 아래로 낮춰(정답 근거 19, 오답 근거 4, 올바른 거절 22, 과잉 거절 1, 환각 1, nDCG 0.70, 비용 $0.50, p95 30초, 다른 평가셋, 5건만 실행) 그 항목이 위반으로 나오는지 리터럴 문장으로 확인합니다. 104번 구성의 결과(`w104_new_r1`)를 넣으면 검색 1회차 지표·비용·p95 여섯 항목이 한꺼번에 걸립니다.

### 5. 구성을 일부러 낮춰 **새로 잰** 결과를 넣으면 → 실패 (2026-11-12 추가, 사전 등록 132번)

11/11 점검 의견("저장된 결과를 기준값과 비교하는 방식이라, 코드를 바꿨을 때 점수가 내려가는지는 새로 재지 않는다")를 받고, 점수가 내려가는 변경을 일부러 만들어 **그 자리에서 새로 재고** 검사에 넣었습니다. 위 (가)·(나)는 예전에 남은 결과 파일이었고, 이번 것은 검사를 보이려고 새로 돌린 실행입니다.

- 낮춘 것: 지금 구성에서 **주제 판정(domain check)만 끔**. 주제 밖 질문을 검색 전에 거절하지 못하게 됩니다. 코드와 기본값은 바꾸지 않고 평가 러너 옵션만 뺐습니다(`--domain-check` 제거, 필터 `evals/filters_v2.graph.jsonl`).
- 새로 잰 결과(v2 50건, 1회, $0.26, `evals/results/graph50/w132_degraded_no_domain_check_multi_d3-55_v9_v2.*`): 올바른 거절 **12**/25(기준 23 이상), 환각 위험 **13**(기준 0), 정답 근거 23, 오답 근거 1, 과잉 거절 0. 사전 등록한 기대(거절 11~14, 환각 11~14)와 맞았습니다.

```
# 1) 낮춘 구성으로 평가를 새로 돌린다(유료, 약 4~8분)
$ python -X utf8 scripts/run_graph_eval.py --graph multi --node-timing --dataset evals/dataset_v2.jsonl \
    --chunks data/experiments/b100-v9-fixed_window/chunks.jsonl --retrieval-subset evals/mappings/dataset_v2.retrieval23.jsonl \
    --query-vectors evals/results/v2_query_vectors_oai_small.npz --filters evals/filters_v2.graph.jsonl --chunk-version v9 \
    --output evals/results/graph50/w132_degraded_no_domain_check_multi_d3-55_v9_v2.jsonl

# 2) 방금 잰 결과를 검사에 넣는다(키 없음)
$ export EVAL_REGRESSION_SUMMARY="evals/results/graph50/w132_degraded_no_domain_check_multi_d3-55_v9_v2.summary.json"
$ python -m pytest tests/test_eval_regression.py -v
tests/test_eval_regression.py::test_current_result_meets_baseline FAILED [ 25%]
tests/test_eval_regression.py::test_gate_rejects_a_run_recorded_as_below_baseline PASSED [ 50%]
tests/test_eval_regression.py::test_gate_rejects_each_kind_of_regression PASSED [ 75%]
tests/test_eval_regression.py::test_p95_uses_only_questions_that_reached_search PASSED [100%]

================================== FAILURES ===================================
_____________________ test_current_result_meets_baseline ______________________

    def test_current_result_meets_baseline():
        target = os.environ.get("EVAL_REGRESSION_SUMMARY") or BASELINE["current_result"]
        path = Path(target) if Path(target).is_absolute() else ROOT / target
        found = violations(path, THRESHOLDS)
>       assert found == [], "평가 결과가 기준에 못 미칩니다:\n" + "\n".join(f" - {line}" for line in found)
E       AssertionError: 평가 결과가 기준에 못 미칩니다:
E          - correct_refusal 12: 기준 23 이상
E          - hallucination_risk 13: 기준 0 이하
E       assert ['correct_ref... 13: 기준 0 이하'] == []
E         
E         Left contains 2 more items, first extra item: 'correct_refusal 12: 기준 23 이상'
E         
E         Full diff: (-: missing in left side, +: extra in left side)
E         - []
E         + [
E         +     'correct_refusal 12: 기준 23 이상',
E         +     'hallucination_risk 13: 기준 0 이하',
E         + ]

tests\test_eval_regression.py:21: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_eval_regression.py::test_current_result_meets_baseline - As...
========================= 1 failed, 3 passed in 2.22s =========================
```

같은 시각에 환경변수 없이 돌리면(지금 채택 구성의 결과 `w129_new_r1`) 4개 모두 통과합니다.

```
$ python -m pytest tests/test_eval_regression.py -v
tests/test_eval_regression.py::test_current_result_meets_baseline PASSED [ 25%]
tests/test_eval_regression.py::test_gate_rejects_a_run_recorded_as_below_baseline PASSED [ 50%]
tests/test_eval_regression.py::test_gate_rejects_each_kind_of_regression PASSED [ 75%]
tests/test_eval_regression.py::test_p95_uses_only_questions_that_reached_search PASSED [100%]
============================== 4 passed in 1.50s ==============================
```

즉 "코드를 바꾼다 → 평가를 새로 돌린다 → 그 결과를 검사에 넣는다 → 기준에 못 미치면 pytest가 실패한다"가 한 흐름으로 동작합니다. 자동이 아닌 부분은 1)번(유료 평가를 사람이 돌려야 함)이고, 아래 한계에 적은 그대로입니다.

## 한계

- 이 시험은 **저장된 결과 파일**을 검사합니다. 코드를 바꾼 사람이 평가를 다시 돌려 결과 파일과 기준 파일의 `current_result`를 갱신해야 새 코드의 점수가 검사됩니다. PR마다 유료 평가(회당 약 $0.2, 4분)를 자동으로 돌리지는 않습니다(조직 Actions 한도, 키를 CI에 두지 않는 방침).
- 기준에 1문항 여유를 뒀기 때문에 1문항 나빠진 변경은 통과합니다. 그 차이는 같은 시각 기준과 짝지어 재는 사전 등록 절차(`evals/PREREGISTER.md`)로 봅니다.
