# Spec - 사용자별 심사 규칙 저장 기반 (A5 후속 구현 필요)

## Why

- **페르소나**: 같은 기업여신 심사 건을 반복 검토하는 심사역.
- **상황**: 심사역이 초안의 판정 문구 또는 판단 기준을 고쳤고, 다음 질의에서도 그 교정을 재사용하고 싶다.
- **문제**: 현재 `backend/memory.py`는 사용자 namespace만 제공한다. 교정 규칙을 저장·조회할 수 없고, 다른 사용자의 규칙과 섞이지 않음을 증명할 수 없다.
- **측정 지표**: 규칙을 저장한 사용자의 다음 질의에는 `applied_rules`가 기록되고, 다른 사용자는 0건을 받는다. 사용자당 200건 상한과 수정·삭제가 단위 테스트로 검증된다.

## Goal

- 테스트용 `InMemoryStore`와 SQL 기반 검증용 `PostgresRuleStore`를 분리해 사용자별 교정 규칙을 영속화한다.
- 그래프가 질의 시작 시 해당 사용자의 규칙만 읽고 `applied_rules`에 적용 사실을 남긴다.
- **성공 기준**: 사용자 격리, 생성·수정·삭제, 200건 상한, 그래프 적용을 AC 테스트로 확인한다.
- **Out of Scope**: 규칙 CRUD HTTP API(V1), 승인 interrupt 재개 뒤 수정 항목을 자동 저장하는 연결(A4 병합 뒤 별도 작업), 규칙의 의미 검색·임베딩, 모델이 스스로 추론한 사실·회사별 수치 저장.

## What

### Happy Path

1. 승인된 심사 메모에서 심사역이 실제로 고친 항목만 규칙 후보가 된다.
2. 규칙은 `("memories", user_id)` namespace와 같은 사용자 경계로, `user_memory_rules` 테이블의 `(user_id, rule_id)`에 `item`, `instruction`, `source_memo_id`, `created_at`, `updated_at`를 저장한다.
3. 다음 질의 시작 시 그래프는 같은 namespace의 규칙만 읽는다.
4. 그래프는 최대 200건의 활성 규칙을 `applied_rules`에 기록하고, writer가 사용할 수 있는 명시적 입력으로 전달한다.
5. 규칙 수정은 같은 key 값을 교체하고, 삭제는 해당 key만 제거한다.

### Edge Cases

| # | 상황 | 처리 방식 |
|---|---|---|
| EC-01 | user_id가 비어 있음 | Store 읽기·쓰기를 하지 않고 `ValueError`로 중단한다. |
| EC-02 | 다른 사용자의 rule_id 요청 | namespace가 달라 조회 결과가 없으며, 수정·삭제하지 않는다. |
| EC-03 | 사용자 규칙이 200건 | 새 규칙 생성은 `CONFLICT_RULE_LIMIT`으로 거절한다. 기존 규칙 수정은 허용한다. |
| EC-04 | 모델이 만든 초안 또는 그대로 승인한 항목 | 저장 후보가 아니며 Store에 쓰지 않는다. |
| EC-05 | 규칙이 없는 사용자 | `applied_rules=[]`로 계속 진행하며 검색·작성 경로는 바뀌지 않는다. |
| EC-06 | 같은 rule_id 재시도 | `put`이 같은 key를 교체하므로 중복 행을 만들지 않는다. |
| EC-07 | 존재하지 않는 rule_id 수정 | 새 규칙을 만들지 않고 `RULE_NOT_FOUND` 오류로 거절한다. |
| EC-08 | 규칙 payload에 허용하지 않은 필드 또는 비문자열·빈 필드가 있음 | 저장하지 않고 `INVALID_RULE_PAYLOAD` 오류로 거절한다. `rule_id`, `created_at`, `updated_at`는 클라이언트가 넣을 수 없다. |
| EC-09 | 서로 다른 요청이 동시에 200번째 규칙을 생성 | 사용자별 DB advisory transaction lock으로 한 요청만 저장하고, 다른 요청은 `CONFLICT_RULE_LIMIT`으로 거절한다. |

## How

- `backend/memory.py`에 저장소 경계를 둔다. `RuleStore`는 `InMemoryStore`를 쓰는 단위 테스트용이고, 운영은 `PostgresRuleStore`를 사용한다. `PostgresRuleStore`는 `user_memory_rules` 테이블에 직접 저장해 한 트랜잭션 안에서 사용자별 advisory lock, 상한 검사, 저장을 수행한다.
- namespace는 정본대로 `("memories", user_id)` 사용자 경계만 사용한다. `PostgresRuleStore`도 같은 `user_id`를 DB 기본키의 첫 열로 사용한다. user_id 없는 namespace 또는 전역 namespace는 금지한다.
- `AgentState`에 `applied_rules: list[dict]`를 추가한다. 새 질의 시작 시 빈 목록으로 시작하며 누적 reducer를 쓰지 않는다.
- 그래프 조립부는 선택적 `rule_store`를 주입받아 supervisor 이전의 규칙 조회 노드에서 사용자 규칙을 읽는다. 저장된 규칙이 없으면 이전 그래프 결과와 같아야 한다.
- 이 PR은 규칙 API와 승인 완료 시점의 자동 저장을 만들지 않는다. 두 연결은 V1·A4 병합 뒤 별도 Spec과 PR에서 수행한다.
- 생성·수정 입력은 `item`, `instruction`, `source_memo_id` 세 문자열만 허용하며 모두 공백이 아니어야 한다. `rule_id`, `created_at`, `updated_at`는 서버가 만든다. 반환 시에는 DB key를 `rule_id`로 고정해 payload가 식별자를 덮을 수 없게 한다.
- 규칙 본문에는 사용자 입력으로 확정한 일반 심사 지침만 둔다. 보고서 원문·비밀값·회사별 금액·모델이 생성한 규칙은 저장하지 않는다.

## AC (Given-When-Then)

**AC-01 · 사용자 namespace 격리**

- GIVEN: 사용자 A의 namespace에 규칙 1개, 사용자 B의 namespace에 규칙 1개가 있다.
- WHEN: A와 B가 각각 규칙을 조회한다.
- THEN: 각 결과는 자기 규칙 1개만 포함하며 상대의 rule_id·본문은 없다.

**AC-02 · 생성·수정·삭제**

- GIVEN: 사용자 A의 새 규칙 payload와 rule_id가 있다.
- WHEN: 생성 후 같은 rule_id를 수정하고 삭제한다.
- THEN: 수정 뒤에는 최신 값 1개만 조회되고, 삭제 뒤에는 조회되지 않는다.

**AC-03 · 규칙 수 상한**

- GIVEN: 사용자 A가 이미 서로 다른 규칙 200개를 보유한다.
- WHEN: 201번째 새 규칙 생성을 요청한다.
- THEN: `CONFLICT_RULE_LIMIT` 오류가 나고 기존 200개는 변하지 않는다.

**AC-04 · 빈 규칙의 그래프 호환성**

- GIVEN: 규칙이 없는 사용자와 기존 가짜 검색기·가짜 작성기다.
- WHEN: rule_store를 주입한 그래프를 실행한다.
- THEN: `applied_rules=[]`이고 기존 outcome·검색 호출 수·인용 결과가 바뀌지 않는다.

**AC-05 · 그래프 적용과 타 사용자 차단**

- GIVEN: 사용자 A만 규칙을 저장했고 B에게는 규칙이 없다.
- WHEN: 같은 질문을 A와 B의 그래프에 각각 넣는다.
- THEN: A의 `applied_rules`에는 A 규칙만 있고 B의 목록은 비어 있으며, Store 접근 namespace는 각각 `("memories", A)`와 `("memories", B)`다.

**AC-06 · 규칙 payload와 시간 필드 고정**

- GIVEN: 유효한 세 필드의 규칙 입력 또는 `rule_id`·임의 필드가 포함된 규칙 입력이 있다.
- WHEN: 규칙을 생성하거나 수정한다.
- THEN: 유효한 규칙에는 서버가 만든 `created_at`·`updated_at`이 기록되고, 허용하지 않은 필드가 있는 입력은 `INVALID_RULE_PAYLOAD`로 저장되지 않는다. 반환 `rule_id`는 저장 key와 항상 같다.

**AC-07 · 동시 생성 상한**

- GIVEN: 사용자 A에게 199개 규칙이 있고 두 독립 Postgres 저장소 인스턴스가 있다.
- WHEN: 두 인스턴스가 서로 다른 200번째 rule_id를 동시에 생성한다.
- THEN: 정확히 한 요청만 성공하고 다른 요청은 `CONFLICT_RULE_LIMIT`이며, 최종 규칙 수는 200개다.


### PR #98 기반 작업 범위

현재 규칙 CRUD와 멀티 그래프의 `load_rules`는 기반 작업이다. 자체 SQL
`user_memory_rules`의 사용자 키 격리는 LangGraph PostgresStore의 namespace·시멘틱 검색
구현을 대신하지 않는다. SPEC §3.5의 운영 Store 계약과 A5/G4 완료는 후속 구현·검증 대상이다.
단일 그래프 적용, 승인 수정 항목 자동 저장, 규칙 HTTP API도 후속 작업으로 남긴다.
테스트 DB는 로컬의 `_test` 접미사 데이터베이스를 `TEST_MEMORY_DATABASE_URL`로 명시한다.
