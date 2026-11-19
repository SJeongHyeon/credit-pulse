# Spec - Store 기반 시멘틱 메모리

## Why

- **페르소나**: 반복 심사를 수행하며 이전 심사에서 직접 고친 판정 기준을 재사용하려는 기업여신 심사역
- **상황**: 승인 뒤 `save_memo`가 심사 메모를 저장하고, #98은 사용자별 규칙 CRUD와 그래프의 규칙 적용 기반을 제공한다.
- **문제**: #98의 규칙 저장은 자체 SQL 테이블이며, G4가 요구하는 LangGraph Store namespace 및 질문 기반 의미 검색을 아직 사용하지 않는다. 승인 때 실제로 고친 항목도 자동 규칙으로 저장되지 않는다.
- **측정 지표**: 같은 사용자의 유사 질문에서 관련 규칙만 Writer 입력에 포함, 서로 다른 사용자의 규칙 노출 0건, 규칙 0건일 때 기존 가짜 에이전트 입력 변화 0건.

## Goal

- **해결 목표**: LangGraph Store를 규칙의 운영 저장소로 사용하고, 질문과 비슷한 규칙만 Writer에 적용한다.
- **성공 기준**:
  - 모든 Store 읽기·쓰기는 정확히 `("memories", user_id)` namespace를 사용한다.
  - 빈 user_id로 Store 읽기·쓰기를 시도하지 않는다.
  - 사용자별 저장 규칙은 최대 200건을 유지한다.
  - `save_memo`의 `items[].edited is True` 항목만 규칙으로 저장한다.
  - 규칙 0건이면 Writer 입력이 기존 입력과 바이트 단위로 같다.
  - 단일·멀티 가짜 검색기·작성기 승인 시험에서 다른 사용자의 규칙이 노출되지 않는다.
- **Out of Scope**: 규칙 HTTP CRUD/API, `reviews`·`review_memos` 스키마, `backend/db.py`·`backend/errors.py`·`db/02_schema.sql` 변경, LLM 호출·임베딩 모델·평가셋 변경.

## What

### Happy Path

1. 서버가 인증된 `user_id`와 질문을 그래프 state에 넣는다.
2. 그래프는 `("memories", user_id)` 안에서 질문으로 의미 검색해 상위 `SEMANTIC_RULE_TOP_K = 5`개(SPEC §4)만 읽는다. 저장 상한(200)과 달리 한 질문에 적용하는 수다.
3. 검색 결과가 있으면 Writer 입력의 `applied_rules`로 전달한다. 없으면 기존 Writer 입력을 그대로 사용한다.
4. 사람이 승인에서 초안을 수정하면 `save_memo`가 메모를 저장한 뒤 수정된 항목만 같은 사용자 namespace에 규칙으로 저장한다.
5. 다른 사용자의 namespace는 검색·저장·조회 대상이 아니다.

### Edge Cases

| # | 상황 | 처리 방식 |
|---|---|---|
| EC-01 | `user_id`가 비어 있거나 공백 | Store `search`·`put`을 호출하지 않고 입력 오류로 종료 |
| EC-02 | 해당 사용자 규칙 0건 | `applied_rules=[]`; Writer에 추가 필드·문자열을 만들지 않음 |
| EC-03 | 승인했지만 항목 수정 없음 | 메모만 저장하고 Store 규칙은 쓰지 않음 |
| EC-04 | 승인에서 여러 항목 수정 | 수정 항목마다 사용자 namespace에 규칙 저장 |
| EC-05 | 사용자 A와 B가 같은 질문을 함 | 각자의 namespace만 검색하며 상대 규칙은 0건 |
| EC-06 | 이미 200개 규칙이 있음 | 새 고유 규칙 저장을 거절(`RuleLimitError`, 코드 `CONFLICT_RULE_LIMIT`)하고 기존 규칙을 덮어쓰지 않음 |
| EC-07 | Store가 시멘틱 인덱스를 지원하지 않음 | `SemanticRuleStore`를 만들 때 `SemanticIndexNotConfigured`로 멈춘다(`index_config`가 없거나 비어 있으면). 전체 namespace 스캔으로 대체하지 않음 |
| EC-08 | 승인했는데 규칙 저장이 200건 상한(EC-06)에 걸림 | 메모는 이미 저장됐으므로 `save_memo`는 경고 로그만 남기고 끝낸다(승인 완료, 재시도해도 같은 결과라 막히지 않게). 그 밖의 규칙 저장 오류(Store 장애 등)는 삼키지 않고 올린다: 메모 저장이 멱등이라 재시도가 안전하다 |

## How

- 운영 Store는 LangGraph `PostgresStore`이며, 테스트는 기존 `InMemoryStore` 또는 `BaseStore` 주입을 사용한다. Store 초기화와 인덱스 설정은 앱 조립 지점에서 한 번만 수행한다.
- namespace 생성은 기존 `backend.memory.namespace(user_id)`만 사용한다. 직접 tuple을 만들지 않는다.
- Store search는 정확한 사용자 namespace를 prefix로 하고 질문을 `query`로 넘긴다. 검색 개수는 `SEMANTIC_RULE_TOP_K = 5`(SPEC §4)이고, 저장 상한 `MAX_RULES_PER_USER = 200`과 별개다.
- 규칙 값은 #98의 `item`, `instruction`, `source_memo_id` 계약을 유지한다. 의미 검색 결과는 Writer가 소비하는 동일한 규칙 사전 형태로 변환한다.
- `build_graph`와 `build_supervisor_graph`는 선택적 Store 의존성을 주입받는다. Store가 없거나 규칙 검색 결과가 비어 있으면 기존 가짜 작성기 호출 인자와 LLM Writer 프롬프트를 바꾸지 않는다.
- `save_memo`는 메모의 idempotency를 유지한 뒤 `items` 중 `edited is True`인 항목만 Store에 쓴다. 그대로 승인된 항목·모델 초안·회사별 수치는 저장하지 않는다.
- 새 API 라우터, SQL DDL, DB helper, 오류 코드 파일은 수정하지 않는다.

## AC (Given-When-Then)

### AC-01 사용자 namespace 고정

- GIVEN: `user_id="user-a"`와 Store가 있다.
- WHEN: 규칙을 검색하거나 저장한다.
- THEN: 모든 호출 namespace는 `("memories", "user-a")`이며 다른 namespace 호출은 없다.

### AC-02 빈 사용자 차단

- GIVEN: `user_id`가 빈 문자열 또는 공백이다.
- WHEN: 그래프가 규칙을 읽거나 승인 수정 규칙을 저장하려 한다.
- THEN: Store `search`·`put`은 0회이고 입력 오류가 발생한다.

### AC-03 의미 검색과 사용자 격리

- GIVEN: 사용자 A와 B가 각각 서로 다른 규칙을 저장했고 같은 질문을 한다.
- WHEN: 각 그래프가 규칙을 불러온다.
- THEN: 각 Writer는 자신의 namespace에서 질문과 유사한 규칙만 받고 상대 규칙은 받지 않는다.

### AC-04 0개 규칙 기준선 보존

- GIVEN: 해당 사용자 namespace에 규칙이 없다.
- WHEN: 가짜 검색기·작성기로 그래프를 실행한다.
- THEN: 작성기 호출의 입력은 Store 연결 전과 글자 하나 다르지 않고 `applied_rules`는 빈 목록이다.

### AC-05 수정 항목만 자동 저장

- GIVEN: 승인 payload에 `edited=False` 항목과 `edited=True` 항목이 함께 있다.
- WHEN: `save_memo`가 멱등키로 메모를 저장한다.
- THEN: `edited=True` 항목만 동일 사용자 namespace에 규칙으로 저장되고 나머지는 저장되지 않는다.

### AC-06 재전송 멱등성

- GIVEN: 같은 `(review_id, approval_seq)` 승인 재개가 다시 들어온다.
- WHEN: `resume_approval()`를 호출한다.
- THEN: 메모는 1건이고 Store 규칙도 중복 생성되지 않으며 409 상당의 대기 없음 오류를 반환한다.

### AC-07 200건 상한

- GIVEN: 사용자 namespace에 200개 고유 규칙이 있다.
- WHEN: 새로운 수정 항목 규칙을 저장한다.
- THEN: 저장을 거절하고 기존 200개 규칙은 보존한다.

### AC-08 가짜 경로 유지

- GIVEN: API 키 없이 기존 가짜 검색기·작성기와 `MemorySaver`를 사용한다.
- WHEN: 단일·멀티 승인 흐름을 실행한다.
- THEN: 외부 LLM·검색 호출 없이 Store 격리·자동 저장·0건 기준선 시험이 재현 가능하다.

### AC-09 질문당 적용 규칙 5건

- GIVEN: 사용자 namespace에 규칙이 30개 있다.
- WHEN: 질문으로 규칙을 검색한다.
- THEN: Store `search`는 `limit=5`로 불리고 Writer에 가는 규칙은 5개다(리터럴 5와 비교).

### AC-10 시멘틱 인덱스 없는 Store 거절

- GIVEN: 시멘틱 인덱스가 없는 `InMemoryStore()`, 또는 `index_config`를 알리지 않는 Store
- WHEN: `SemanticRuleStore`를 만든다.
- THEN: `SemanticIndexNotConfigured`가 발생하고 Store의 `search`·`put`은 0회 불린다.

### AC-11 규칙 상한이어도 승인은 끝남

- GIVEN: 사용자에게 규칙 200개가 있고, 승인에서 새 규칙이 될 항목을 고쳤다. 단일·멀티 그래프 모두.
- WHEN: 승인을 재개한다.
- THEN: 메모 1건이 저장되고 `status=open`·`outcome=answered`로 끝나며 기존 200개 규칙은 그대로다. 경고 로그가 남고, 같은 승인을 다시 보내면 대기 없음 오류이며 메모는 늘지 않는다. 규칙 저장이 상한 외의 이유로 실패하면 그 오류는 그대로 올라온다.
