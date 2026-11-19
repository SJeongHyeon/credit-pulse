# Spec - Answer Judge (평가용 답 판정자)

평가 하네스 보조 지표. 근거: SPEC §7(보고 지표), §8(생성 모델과 judge 모델은 다름), 강의 P17 R7(judge 고정·기록)·R9(사람 판정과 kappa 대조, 통과선 사전 등록), 기존 선례 28번(`scripts/grade_checklist.py agree`: 사람 판정 대조 뒤에만 채점 사용).

## Why

- **페르소나**: 작성 모델·지시문을 바꾸는 실험을 판정하는 평가 담당(측정 결과 검토, SPEC §7).
- **상황**: SPEC §7 보고 지표 5개는 모두 결정적 판정이다. 정답 근거는 "인용한 청크가 정답 청크와 겹치는가"만 보고 **reason 문장의 내용은 보지 않는다.** verifier는 "reason에 쓴 숫자가 청크에 있는가"만 본다.
- **문제**:
  1. 92번 dry-run에서 `gpt-4.1-mini`는 reason에서 금액을 빼고 "내역이 명시되어 있음"처럼 썼다(reason 숫자 비율 91번 0.875~0.917 → 0.250). 숫자가 없으니 verifier 하향이 사라져 1번이 −→O가 됐다. 지금 지표로는 **답의 정보량이 줄어든 모델이 더 좋아 보인다.**
  2. 다음 모델 비교(solar-mini4, gpt-6-luna)를 이 상태로 하면 같은 착시로 잘못 채택할 수 있다.
- **측정 지표**: 판정자가 사람 판정과 kappa 통과선을 넘는가(사전 등록), 넘으면 91번 기준선과 다음 실험에 보조 지표를 붙인다.

## Goal

- **해결 목표**: 그래프 실행 결과 파일을 사후에 읽어, 관련 문항의 판정 항목마다 "reason이 인용 청크로 뒷받침되는가(grounded)"와 "질문이 물은 사실을 구체적으로 담았는가(specific)"를 LLM 판정자가 O/X로 매긴다. 사람 판정과 대조해 통과한 뒤에만 지표로 쓴다.
- **성공 기준**:
  1. 사람 판정 40항목과의 Cohen's kappa가 두 라벨 모두 0.6 이상(사전 등록 93번에서 확정). 사람 판정에서 각 라벨의 O·X가 각각 5개 이상(한쪽만 있으면 kappa가 무의미).
  2. 판정자 모델·지시문 버전·temperature를 결과 파일에 기록한다(P17 R7).
  3. 그래프·작성기·verifier·지표 5개 정의는 바뀌지 않는다. 판정은 결과 파일을 읽기만 한다.
  4. 같은 결과 파일을 두 번 판정해 항목별 라벨 일치율을 기록한다(temperature 0의 흔들림 확인).
- **Out of Scope**: 서비스 그래프 안에서 판정(검색 채점 `grade_chunks`와 다름), 판정 결과로 답을 고치거나 거절하기, 정답 span과 대조한 사실 정확도 채점(정답 근거 지표가 따로 있음), 비관련 문항 판정, 러너(`run_graph_eval.py`) 실행 중 판정, 판정 지표를 채택 기준으로 쓰는 일(각 실험의 사전 등록에서 정한다).

## What

### Happy Path

1. 평가 러너가 만든 결과 파일(`*.jsonl`)과 같은 청크 파일을 준다.
2. 스크립트가 관련 문항(`kind == relevant`)의 최종 `draft_items` 중 **인용이 1개 이상인 항목**만 고른다.
3. 문항 하나에 LLM 호출 1번: 질문, 판정 항목들(item·verdict·reason), 그 항목들이 인용한 청크 본문을 보낸다.
4. 판정자가 항목마다 `grounded`(O/X), `specific`(O/X), 짧은 근거(100자 이내)를 JSON으로 돌려준다.
5. 결과를 `<결과>.judge.jsonl`(항목별)과 `<결과>.judge.summary.json`(비율·비용·모델·지시문 버전)으로 쓴다.

### 라벨 정의 (판정자 지시문과 사람 판정 기준이 같다)

| 라벨 | O | X |
|---|---|---|
| `grounded` | reason의 사실·숫자·당사자가 모두 인용 청크에 있다(표현이 달라도 같은 뜻이면 O) | 인용 청크에 없는 사실·숫자, 청크와 다른 숫자, 청크로 계산해야만 나오는 합계·비율이 있다 |
| `specific` | 질문이 물은 것에 대한 구체 사실(금액·건수·상대방·기간·조건 중 질문에 맞는 것)을 하나 이상 적었다. `not_applicable`이면 "없다"고 적힌 근거를 구체적으로 말했다 | "내역이 명시되어 있음", "관련 공시가 있음"처럼 무엇이 적혀 있는지 말하지 않는다 |

### Edge Cases

| # | 상황 | 처리 방식 |
|---|---|---|
| EC-01 | 인용 없는 항목(needs_check 등) | 판정하지 않고 `skipped_no_citation`으로 센다 |
| EC-02 | 인용 청크 id가 청크 파일에 없음 | 조용히 건너뛰지 않고 오류로 멈춘다(청크 파일이 다른 것) |
| EC-03 | 판정자 응답에 항목이 빠지거나 남는 항목이 있음 | 형식 오류로 1회 재요청, 그래도 다르면 그 문항을 `judge_failed`로 적고 계속한다(실행 전체를 멈추지 않음) |
| EC-04 | 판정자 모델이 결과 파일의 작성 모델과 같음 | 실행 전에 거절한다(SPEC §8) |
| EC-05 | 청크 본문에 지시문처럼 보이는 문장 | 데이터로만 다룬다는 문장을 지시문에 넣는다(`grade_chunks`와 같은 문구) |
| EC-06 | 사람 판정 파일에 판정자가 볼 수 없는 항목 | `agree`에서 오류로 멈춘다 |

## How

- **LLM 호출**: `backend/services/llm.py`에 `judge_answers(question, items, chunks, model)` 하나를 더한다(AGENTS.md: LLM 호출은 llm.py에서만). `openrouter_structured`, temperature 0, Pydantic 응답 모델로 검증. 지시문 버전 상수 `ANSWER_JUDGE_PROMPT_VERSION = "answer-judge-v1"`.
- **모델**: `openai/gpt-4.1`(`DEFAULT_JUDGE_MODEL`). 생성 후보(gpt-4o-mini·solar-mini4·gpt-6-luna)와 모두 다르다.
- **스크립트** `scripts/judge_answers.py`
  - `run --results <jsonl> --chunks <jsonl> [--model]`: 판정해 `.judge.jsonl`·`.judge.summary.json`을 쓴다.
  - `sheet --results <jsonl>... --n 40 --contrast-per-kind 5 --seed 20261108 --out evals/judge_labels_v1`: 사람 판정용 시트를 만든다. 읽기용 `.md`(질문·항목·reason·인용 청크 본문, 출처 없이 번호만)와 라벨 칸이 빈 `.csv`, 대조 항목이 있으면 `.contrast.jsonl`(결과 파일과 같은 형식, 바꾼 내용과 원래 reason 기록)을 쓴다. 판정자 출력은 넣지 않는다.
  - `agree --labels evals/judge_labels_v1.csv --chunks <jsonl> [--model]`: 사람 라벨이 있는 항목만 판정해 라벨별 kappa·일치율·혼동표를 출력한다.
- **사람 판정 표본(40항목 = 실제 30 + 대조 10)**: 실제 항목은 91번 r2, 86번 haiku, 62번 haiku, 92번 dry-run 결과의 판정 대상 57항목에서 무작위로 뽑는다. 실제 결과만으로는 X가 거의 없다(reason에 숫자 없는 항목 4개, verifier가 숫자 불일치 항목의 인용을 떼어 grounded X 후보 거의 0). 그래서 라벨마다 O·X가 5개 이상이라는 조건을 채우려고 **대조 항목**을 섞는다. 대조 항목은 실제 항목의 reason을 기계적으로 바꾼 것이다. `number_swap` 5개는 연도와 한 자리 수를 뺀 숫자 하나의 첫 자리를 바꾸고(grounded X 후보), `vague` 5개는 숫자가 있던 reason을 "해당 항목 관련 내역이 공시에 명시되어 있음"으로 바꾼다(specific X 후보, 92번 dry-run에서 본 실패). 대조 항목은 문항이 겹치지 않게 고르고, 원본 항목은 실제 표본에서 뺀다. seed로 다시 만들 수 있다. 대조 항목의 정답도 사람이 매긴다("X 후보"일 뿐 X로 가정하지 않음). 결과는 실제·대조를 합친 kappa와 실제 항목만의 일치율을 함께 보고한다. 사람 판정 파일은 **판정자를 돌리기 전에 커밋**하고 이후 고치지 않는다(동결 규칙과 같음).
- **보조 지표**(판정자 통과 뒤에만 보고, SPEC §7에 추가)
  - 근거 충실률 = `grounded` O 항목 / 판정한 항목
  - 구체성률 = `specific` O 항목 / 판정한 항목
  - 쓸만한 정답 = 정답 근거 O 문항 중, 그 문항의 판정한 항목이 모두 `grounded`·`specific` O인 문항 수
- **비용**: 91번 한 회차 기준 판정 24항목, 인용 청크 약 8.3만~9.1만 자 → gpt-4.1로 회차당 약 $0.15~0.20(추정, dry-run으로 확인). `agree` 40항목 약 $0.3.
- **변경 파일 범위**: `backend/services/llm.py`(함수 1개·상수), `scripts/judge_answers.py`(새), `tests/test_answer_judge.py`(새), `evals/judge_labels_v1.{md,csv,contrast.jsonl}`(사람 판정 시트, 새), `SPEC.md` §7(보조 지표 한 줄), 이 Spec.

## AC (Given-When-Then)

**AC-01 · 판정 대상 고르기**
- GIVEN: 관련 문항 하나에 인용 있는 항목 2개·인용 없는 needs_check 1개, 비관련 문항 1개
- WHEN: `run`
- THEN: 판정자에 2항목만 보내고, needs_check는 `skipped_no_citation`, 비관련 문항은 대상이 아니다.

**AC-02 · 인용 청크만 보냄**
- GIVEN: 항목이 청크 A·B를 인용, 같은 문항의 검색 결과에 C도 있음
- WHEN: 판정자 입력을 만듦
- THEN: A·B 본문만 들어가고 C는 들어가지 않는다.

**AC-03 · 없는 청크 id**
- GIVEN: 인용 id가 청크 파일에 없음
- WHEN: `run`
- THEN: 그 id를 알리는 오류로 멈추고 출력 파일을 쓰지 않는다.

**AC-04 · 응답 항목 불일치**
- GIVEN: 판정자가 두 번 모두 항목 수가 다른 응답
- WHEN: `run`
- THEN: 그 문항은 `judge_failed`, 나머지 문항은 계속 판정하고 요약에 실패 수를 적는다.

**AC-05 · 같은 모델 거절**
- GIVEN: 결과 요약의 작성 모델이 `openai/gpt-4.1`이고 판정자도 `openai/gpt-4.1`
- WHEN: `run`
- THEN: LLM을 부르기 전에 거절한다.

**AC-06 · 기록**
- GIVEN: 판정 완료
- WHEN: 요약 파일을 읽음
- THEN: 판정자 모델, 지시문 버전, temperature 0, 판정 항목 수, 건너뛴 수, 실패 수, 두 비율, 쓸만한 정답 수, 기록 비용이 있다.

**AC-07 · kappa**
- GIVEN: 사람·판정자 라벨 쌍(알려진 예: 일치율 0.8이지만 판정자가 모두 O → kappa 0)
- WHEN: `agree`
- THEN: 라벨별 kappa·일치율·혼동표를 출력하고, 위 예에서 kappa는 0이다.

**AC-08 · 시트에 판정자 출력 없음**
- GIVEN: `sheet`
- WHEN: 시트를 만듦
- THEN: 라벨 칸은 비어 있고 판정자 호출이 없다. 같은 seed면 같은 40항목이다.

**AC-09 · 키 없이 시험**
- GIVEN: 가짜 판정 함수
- WHEN: `pytest`
- THEN: 위 AC가 API 키 없이 통과한다.
