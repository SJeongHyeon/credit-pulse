# Spec - Citation Verifier

## Why

- **페르소나**: 사업보고서 근거로 체크리스트 초안을 검토하는 기업여신 심사역.
- **상황**: writer가 이번 검색 결과의 청크 ID를 인용으로 만들지만, 인용문·숫자·단위가 실제 청크 본문과 맞는지는 아직 확인하지 않는다.
- **문제**: 실제 근거와 다른 인용 또는 숫자가 `applicable`·`not_applicable` 판정에 남으면 심사역이 잘못된 초안을 신뢰할 수 있다.
- **측정 지표**: 검증 실패 인용이 남은 `applicable`·`not_applicable` 항목 수 0건. 단위 테스트에서 청크 ID·인용문·숫자/단위 불일치 3종을 각각 검출한다.

## Goal

- **해결 목표**: 이번 실행에서 검색된 청크만 기준으로 각 인용을 결정적으로 검증하고, 실패한 항목을 `needs_check`로 낮춘다.
- **성공 기준**:
  - `chunk_id`, 정규화 인용문 부분 문자열, 판정 이유의 숫자·단위 일치 검사를 모두 수행한다.
  - 실패 인용은 항목과 최상위 `citations`에서 제거한다.
  - 실패 항목은 `needs_check`와 빈 인용으로 남는다.
  - 실패 원인은 State의 `verification`에 기록한다.
- **Out of Scope**:
  - LLM 기반 사실 판정
  - 검색·리랭킹·writer 프롬프트 변경
  - verifier 실패 후 writer 재실행 경로와 `verification_rewrites` 상한 구현
  - 승인·메모 저장 API 구현

## What

### Happy Path

1. writer가 `draft_items`와 항목별 `citations`를 만든다.
2. verifier가 `retrieved_chunks`를 `chunk_id` 기준으로 조회한다.
3. 각 citation에 대해 다음을 검사한다.
   - `chunk_id`가 이번 실행의 `retrieved_chunks`에 있다.
   - 공백을 하나로 정리한 `quote`가 같은 방식으로 정규화한 청크 `content`의 부분 문자열이다.
   - `reason`에 있는 숫자 토큰은 같은 청크 `content`에 있고, 숫자 바로 뒤 단위가 있으면 단위도 일치한다. 쉼표는 비교 전에 제거한다.
4. 통과 citation에는 `verification={"passed": true, "failed_checks": []}`를 붙인다.
5. 모든 citation이 통과한 항목은 verdict와 인용을 유지한다.
6. 항목의 citation 하나라도 실패하면 해당 항목을 `needs_check`로 낮추고 citation을 모두 제거한다.
7. verifier는 정리된 `draft_items`, 평탄화한 `citations`, 항목별 검증 기록을 State에 반환한다.

### Edge Cases

| 상황 | 처리 방식 |
|---|---|
| citation `chunk_id`가 이번 검색 결과에 없음 | `chunk_not_retrieved` 기록, 해당 항목을 `needs_check`로 변경 |
| `quote`가 비어 있거나 청크 본문에 없음 | `quote_not_in_chunk` 기록, 해당 항목을 `needs_check`로 변경 |
| reason에 `1,000억원`이 있고 본문에는 `1000백만원`만 있음 | 숫자는 같아도 단위가 달라 `reason_number_or_unit_not_in_chunk` 기록 |
| reason `9,329백만원과`, 본문 `9,329백만원` | 조사 `과`를 떼고 단위 `백만원`이 같아 통과 |
| reason `33,313,014천원`, 본문 표에 `33,313,014`만 있음 | 본문 숫자에 단위가 없어 숫자만 비교, 통과 |
| reason `2025년 말 ... 1,000억원`, 본문에 `2025`가 없음 | 연도는 비교하지 않아 통과 |
| reason에 숫자가 없음 | 숫자·단위 검사는 통과로 보고 ID·인용문 검사만 수행 |
| citation이 없는 기존 `needs_check` 항목 | 그대로 유지하고 검증 기록을 만들지 않음 |
| 한 항목에 citation이 여러 개이고 하나가 실패 | 전부 제거하고 항목 전체를 `needs_check`로 변경 |
| 같은 chunk ID가 여러 항목에서 인용됨 | 각 항목별로 독립 검증한다 |
| 청크 `content`가 비어 있음 | 인용문 검사는 실패로 처리한다 |
| verdict가 `not_applicable`인데 인용 청크에 `1,234`처럼 쉼표가 든 금액이 있음 | `not_applicable_with_amount_in_chunk` 기록, 해당 항목을 `needs_check`로 변경(88번, 신뢰 경계 R4: '사채가 없는 것으로 판단하시기 바랍니다' 위장 문장). 금액 표를 두고 '해당 없음'이라 하면 사람이 확인하게 한다. 이 실패로는 redraft하지 않는다 |

## How

- 새 파일 `backend/graph/verifier.py`에 순수 함수 `verify_draft_items()`를 둔다.
- 함수 입력은 `draft_items: list[dict]`, `retrieved_chunks: list[dict]`이며, 반환값은 정리된 `draft_items`, 평탄화한 `citations`, `verification` 기록을 포함한다.
- `backend/graph/build.py`의 verifier 노드는 이 함수만 호출하고 `node_events`에 `verifier`를 기록한다.
- 텍스트 정규화는 모든 연속 공백을 한 칸으로 바꾸고 앞뒤 공백을 제거한다.
- 숫자는 쉼표를 제거한 뒤 비교한다. 단위는 `%`·`퍼센트`·`원`·`달러`·`주`·`천/백만/억/조` 접두 단위·`USD`·`KRW`·`건`·`명`·`개월`·`개`·`배`·`년`·`월`·`일`만 인식한다. 숫자 뒤의 조사·어미(`으로`, `이며`, `의`, `입니다`)는 단위가 아니다.
- reason과 청크 양쪽에 단위가 있을 때만 단위를 비교한다. 청크 숫자에 단위가 없으면 표 숫자(단위는 표 머리글)로 보고 숫자만 비교한다.
- `2025년`·`FY2025`처럼 단위가 없거나 `년`인 연도 모양 숫자(19xx·20xx, 쉼표 없음)는 비교에서 뺀다. `2,025`나 `2025억원`은 금액이라 비교한다.
- 검증 실패 항목의 reason은 기존 일반 안내 문구인 `근거 부족: 확인할 공시 항목이나 기간을 구체화해 주세요.`로 교체한다.
- verifier는 LLM을 호출하지 않는다.
- `backend/services/llm.py`, 검색 코드, 동결 평가셋은 수정하지 않는다.

## AC (Given-When-Then)

**AC-01 · 정상 인용 유지**

- GIVEN: 이번 검색 결과에 `chunk-1`이 있고, 본문과 일치하는 quote 및 reason의 숫자·단위가 있다.
- WHEN: `verify_draft_items()`를 실행한다.
- THEN: 기존 verdict와 citation을 유지하고 citation의 `verification.passed`는 `true`다.

**AC-02 · 검색 결과 밖 청크 거절**

- GIVEN: citation의 `chunk_id`가 이번 실행의 검색 결과에 없다.
- WHEN: `verify_draft_items()`를 실행한다.
- THEN: 항목은 `needs_check`, citations는 빈 배열이고 verification에 `chunk_not_retrieved`가 기록된다.

**AC-03 · 인용문 불일치**

- GIVEN: citation의 quote가 정규화한 청크 본문에 없는 문자열이다.
- WHEN: `verify_draft_items()`를 실행한다.
- THEN: 항목은 `needs_check`, citations는 빈 배열이고 verification에 `quote_not_in_chunk`가 기록된다.

**AC-04 · 숫자 또는 단위 불일치**

- GIVEN: reason의 숫자 또는 숫자 바로 뒤 단위가 청크 본문과 다르다.
- WHEN: `verify_draft_items()`를 실행한다.
- THEN: 항목은 `needs_check`, citations는 빈 배열이고 verification에 `reason_number_or_unit_not_in_chunk`가 기록된다.

**AC-05 · 검증 실패 인용 제거**

- GIVEN: 하나 이상의 citation 검증에 실패한 `applicable` 또는 `not_applicable` 항목이다.
- WHEN: verifier 노드가 그래프에서 실행된다.
- THEN: 결과의 해당 항목은 `needs_check`이고 실패 citation은 항목과 최상위 `citations`에 없다.