# Spec - Evaluation Set Governance

## Why

- **Persona**: 서정현(평가셋 질문 작성·하네스 담당), 류승민(정답 판정 담당), 박민영(결과 재검토 담당).
- **Situation**: G5는 관련·비관련이 균형인 동결 평가셋으로 단일과 멀티 에이전트를 같은 조건에서 두 번 이상 측정해야 한다.
- **Problem**: 현재 공통 형식은 있으나, 질문 작성자와 정답 판정자의 분리, 문항 검수 순서, 동결 전 검증, 결과 보관 책임이 팀 운영 규칙으로 한곳에 명시되지 않았다.
- **Metric**: v1은 관련 15·비관련 15의 30건, v2는 관련 25·비관련 25 이상의 50건을 만족하고, 동결 뒤 해시 검증이 통과한다.

## Goal

- `evals/README.md`에 팀의 평가셋 작성·검수·동결·재측정 규칙을 추가할 기준을 확정한다.
- 평가셋 작성과 gold 판정을 분리해 정답 문구를 베낀 쉬운 질문을 막는다.
- **Success criteria**: 각 문항의 필수 필드, 비관련 4종 분포, 동결 명령, 사전 등록·결과 보관 위치를 검수표 하나로 확인할 수 있다.
- **Out of Scope**: 30개 실제 질문 작성, gold chunk 탐색, 자동 채점기 구현, 동결된 정답 수정.

## What

### Happy Path

1. 서정현이 gold 문서를 보지 않고 질문 초안을 작성한다.
2. 류승민이 코퍼스를 대조해 `expected`, `gold_chunks`, `why`를 판정한다.
3. 류승민이 관련/비관련 수·비관련 4종·필수 필드를 동결 전에 검수하고 최종 확인한다.
4. v1 30건 또는 v2 50건 기준을 만족하면 `freeze.py`로 해시를 기록한다.
5. 측정 전 변경 하나와 기대 범위를 `PREREGISTER.md`에 적고, 결과 원시 JSON을 `evals/results/`에 보관한 뒤 박민영이 측정 결과를 재검토한다.

### Edge Cases

| 상황 | 처리 |
|---|---|
| 코퍼스가 아직 없어 gold chunk를 판정할 수 없음 | 질문 초안만 임시 작업물로 보관하고 동결하지 않는다. |
| 비관련 문항이 한 종류에 치우침 | 4종이 모두 포함될 때까지 추가·교체하되, 동결 전 파일에서만 수정한다. |
| 동결 후 코퍼스 변경으로 정답 근거가 달라짐 | 평가셋을 수정하지 않고 `evals/exclusions.json`에 id와 사유를 기록한다. |
| 개선 실험 | 사전 등록 없이 측정 결과를 비교하지 않으며, 한 번에 한 변경만 적용한다. |
| 질문 작성자와 정답 판정자가 같음 | 해당 문항은 독립 판정자가 다시 검수하기 전 동결 후보에 넣지 않는다. |

## How

- 파일 형식은 기존 `evals/README.md`의 JSONL 계약을 따른다: `id`, `input`, `kind`, `expected`, `gold_chunks`, `why`, `as_of`, `corpus_version`; 비관련에는 `irrelevant_type`도 필수다.
- 역할 분리는 WBS에 따라 다음으로 고정한다.
  - 서정현: 질문 초안, 하네스, 결과·실패 문항 메모
  - 류승민: gold chunks와 정답 판정, 동결 전 검수·최종 확인
  - 박민영: 측정 결과 재검토
- 비관련 문항은 `out_of_domain`, `unknown_entity`, `missing_attribute`, `wrong_period`가 각각 최소 1건 이상이어야 한다.
- 동결 전 검수표는 다음을 확인한다: JSON 파싱 가능, 고유 id, 관련/비관련 수, 비관련 4종, 관련 문항의 gold 1개 이상, 비관련의 빈 gold 목록, 기준일·코퍼스 버전·판정 사유.
- 동결은 `python evals/freeze.py dataset_v1.jsonl` 또는 `dataset_v2.jsonl`을 한 번만 실행한다. 이후에는 `python evals/freeze.py --check`로만 무결성을 확인한다.
- 원시 측정 결과는 날짜·그래프 버전·코퍼스 버전을 식별할 수 있는 파일명으로 `evals/results/`에 저장한다. 개선 실험은 측정 전에 `evals/PREREGISTER.md`에 한 변경과 수치 기대 범위를 기록한다.

## AC (Given-When-Then)

**AC-01 · 작성과 판정 분리**

- GIVEN: v1 질문 초안
- WHEN: gold 판정을 추가
- THEN: 판정자는 질문 작성자(서정현)가 아니고, 판정자와 검수자를 기록할 수 있다.

**AC-02 · v1 구성 검수**

- GIVEN: 동결 후보 `dataset_v1.jsonl`
- WHEN: 동결 전 검수표를 실행
- THEN: 총 30건, 관련 15건, 비관련 15건이며 비관련 4종이 모두 1건 이상이다.

**AC-03 · 필수 필드 검수**

- GIVEN: 평가셋의 각 문항
- WHEN: 형식 검수를 실행
- THEN: 공통 필수 필드가 모두 있고, 관련 문항은 `gold_chunks`가 1개 이상, 비관련 문항은 빈 배열과 유효한 `irrelevant_type`을 가진다.

**AC-04 · 동결 무결성**

- GIVEN: 동결된 v1 또는 v2 파일
- WHEN: `python evals/freeze.py --check`를 실행
- THEN: 파일이 바뀌지 않았으면 종료 코드 0이고, 바뀌었으면 종료 코드 1이다.

**AC-05 · 사전 등록과 결과 보관**

- GIVEN: 검색 또는 그래프 변경을 평가하려는 실험
- WHEN: 측정을 시작
- THEN: `PREREGISTER.md`에 한 변경과 수치 기대 범위가 먼저 있고, 측정 뒤 원시 결과가 `evals/results/`에 저장된다.
