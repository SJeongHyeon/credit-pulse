# Spec - Review API

## Why

- **페르소나**: 로그인한 기업여신 심사역과 심사 상세 화면.
- **상황**: 심사역은 회사를 선택해 심사 건을 만들고, 질문으로 판정 초안을 받은 뒤, 본인만 그 초안을 승인 또는 취소해야 한다.
- **문제**: `reviews`, `review_memos`, 승인 그래프는 각각 존재하지만 이를 인증·소유권·HTTP 계약으로 연결하는 API가 없다. 클라이언트가 `thread_id`나 승인자를 정하면 다른 사용자의 심사 상태에 접근할 위험이 있다.
- **측정 지표**: 가짜 DB·그래프로 생성/목록/상세/질의/승인/취소 자동화 시험 통과, 다른 사용자의 심사 건 404, 같은 승인 재전송 시 메모 1건·409, 취소 시 메모 0건.

## Goal

- `POST·GET /api/v1/reviews`, `GET /api/v1/reviews/{id}`, `POST /api/v1/reviews/{id}/queries`, `POST /api/v1/reviews/{id}/approvals`, `POST /api/v1/reviews/{id}/approvals:cancel`을 제공한다.
- 서버가 인증된 `user_id`와 `review_id`로만 `thread_id = "{user_id}:{review_id}"`를 만든다. 요청 본문의 `thread_id`, `user_id`, `approved_by`는 받지 않는다.
- 모든 심사 건 조회·변경은 로그인 사용자 소유일 때만 허용하며, 아니면 존재 여부를 숨기는 404를 반환한다.
- 승인 재개는 `backend.graph.approval.resume_approval()`만 사용하며, 승인자는 세션 `user_id`다.
- **Out of Scope**: `db/02_schema.sql`, `backend/errors.py`, `backend/db.py` 변경; 심사 건 종료·메모 되돌리기·규칙 CRUD·화면 JavaScript 변경; LLM/실제 검색 품질 변경 및 평가셋 재측정.

## What

### Happy Path

1. 로그인한 사용자가 `corp_query`, `fiscal_year`로 심사 건을 생성한다. 서버는 회사를 확인하고 `reviews` 행과 서버 생성 `thread_id`를 기록한다.
2. 사용자는 본인 심사 건만 페이지 목록 또는 상세로 읽는다. 상세가 승인 대기면 같은 체크포인트에서 `pending_draft`, `pending_question`, `pending_applied_rules`를 읽는다. 대기 중인 질문이 없으면 `pending_question`은 null이고 규칙이 없으면 `pending_applied_rules`는 빈 배열이다.

승인 대기 중 `POST /api/v1/reviews/{id}/queries:follow-up`에 `{"question": string}`을 보내면 해당 심사 건의 회사·사업연도로 분석한다. 기존 질의와 같은 응답·한도를 사용하며, 별도 실행 스레드에서 승인·메모 저장 없이 답변이나 거절을 반환한다. `awaiting_approval → running` 전이로 승인·취소와 직렬화하고 실행이 끝나면 승인 대기 상태로 돌아간다. 기존 초안 체크포인트는 건드리지 않는다.
3. 사용자가 질문을 보내면 서버가 소유권·상태·질의 한도를 확인하고, 심사 건을 `open → running`으로 옮긴 뒤(이 전이가 잠금) 그래프를 실행한다. 그래프가 승인 대기로 멈추면 `running → awaiting_approval`, 아니면 `running → open`이 되고 초안을 돌려준다.
4. 사용자가 모든 항목의 결정으로 승인하면 서버가 `awaiting_approval → saving`으로 옮긴 뒤(잠금) `resume_approval()`만 호출하고, 끝나면 `saving → closed`가 된다(2026-11-11, SPEC §4: 승인이 끝난 건은 더 질의할 수 없다. 취소는 `saving → open`). 그래프의 `memo_saver`가 `(user_id, review_id, approval_seq)`로 `review_memos`를 한 번만 쓴다.
5. 사용자가 취소하면 서버가 같은 `resume_approval()`에 `action=cancel`만 전달한다. 메모는 쓰지 않는다.

### Edge Cases

| 상황 | 처리 방식 |
|---|---|
| 세션 없음·만료 | 기존 `AUTH_REQUIRED` 401 |
| UUID 형식 오류·요청 본문 오류 | 기존 오류 형식의 `VALIDATION_ERROR` 400 |
| 다른 사용자의 심사 건·없는 심사 건 | 동일한 404 `RESOURCE_NOT_FOUND`(`backend.errors.resource_not_found()`) |
| `open` 외 상태에서 질의(승인 대기 중 포함) | 409 `CONFLICT_REVIEW_NOT_OPEN`, 그래프를 실행하지 않음. 대기 중인 승인 interrupt는 그대로 남는다 |
| 동시에 두 요청이 같은 심사 건을 `open → running`으로 옮기려 함 | 먼저 전이한 요청만 실행하고 나머지는 409 `CONFLICT_REVIEW_NOT_OPEN` |
| 질의 중 그래프가 오류 | 심사 건을 `open`으로 되돌리고 `query_count`는 늘리지 않음(500, 내부 상세 비노출) |
| 질의 50건 도달 | 409 `CONFLICT_REVIEW_QUERY_LIMIT`(SPEC §4), 그래프를 실행하지 않음 |
| 승인 대기가 아닌데 승인·취소 | `CONFLICT_NOT_AWAITING_APPROVAL` 409, 메모를 쓰지 않음 |
| 같은 승인 재전송 | 첫 요청만 메모 1건, 다음 요청은 409 |
| 승인 결정이 원본 초안과 맞지 않음(없는 항목·빠진 항목 등) | 400 `VALIDATION_ERROR`, 심사 건은 `awaiting_approval`로 되돌아가 다시 보낼 수 있음 |
| 승인 처리 중 서버 오류 | 500, `saving`에서 `awaiting_approval`로 되돌림 |
| DB는 승인 대기인데 그래프에는 대기가 없음 | 409 `CONFLICT_NOT_AWAITING_APPROVAL`, 심사 건을 `open`으로 정리 |
| `DATABASE_URL` 없음 | 503 `SERVICE_UNAVAILABLE_DB`(`backend.errors.database_unavailable()`) |
| `approved_by`·`thread_id`를 요청에 넣음 | Pydantic 입력 금지로 400; 서버 값으로 대체하거나 신뢰하지 않음 |
| 그래프가 승인 대기 초안을 만들지 못함 | 500, 내부 상세·입력값은 응답에 노출하지 않음 |

## How

- 파일: `backend/api/reviews.py`가 `APIRouter(prefix="/api/v1/reviews")`와 요청/응답 모델을 둔다. `backend/main.py`는 이 라우터만 등록한다.
- 경계: 라우터는 HTTP·`Depends(current_user_id)`·Pydantic 검증·응답 변환만 맡는다. `ReviewRepository`는 `reviews`/`review_memos` SQL과 소유권 확인을, `ReviewGraphRunner`는 `invoke`, `pending_approval`, `resume_approval` 호출을 맡는다. 테스트는 두 인터페이스의 가짜 구현을 `dependency_overrides`로 주입한다.
- DB: 실제 Repository는 기존 `psycopg` 의존성을 쓰고 매 SQL에 `review_id AND user_id` 조건을 넣는다. 다른 사용자의 건은 존재 확인을 별도로 하지 않는다. 이 PR은 스키마·공통 DB 파일을 바꾸지 않는다.
- 생성: 회사 확인은 기존 코퍼스/회사 조회 계약을 재사용한다. `reviews.thread_id`에는 서버가 만든 `thread_id_for(user_id, review_id)`만 저장한다.
- 질의: 서버는 저장된 `corp_code`, `corp_name`, `fiscal_year`를 `review_target`으로, 서버 생성 thread 설정과 사용자/심사 ID를 GraphRunner에 전달한다. Supervisor는 `review_target`이 있으면 질문에서 회사를 다시 추출하지 않고 그 값만 검색 필터로 쓴다. `query_count`는 성공적으로 그래프를 시작한 경우에만 한 번 증가하며 50을 넘지 않는다.
- 승인: body는 `{"decisions": [{"item", "verdict", "reason"}]}`이고 `approval_seq`·승인자는 받지 않는다. 서버가 현재 대기 payload에서 `approval_seq`를 읽어 `{"approval_seq", "decisions"}`을 만들어 `resume_approval()`에 전달한다. 취소도 body 없이 서버가 같은 방식으로 `{"approval_seq", "action":"cancel"}`을 만든다.
- 성공 응답: 생성 201은 심사 요약, 목록 200은 `{items, next_cursor}`, 상세 200은 심사 요약과 승인 대기 때의 `pending_draft`, 질의 200은 그래프 `outcome`/`answer`/`draft`/`refusal_reason`과 `applied_rules`(항상 배열, 규칙이 없거나 거절이면 `[]`, SPEC §3.3), 승인 200은 `{review_id, approval_seq, memo_id}`, 취소 200은 `{review_id, approval_seq, status:"open"}`이다.
- 오류 응답은 SPEC.md §6의 `{"error":{"code","message","details","request_id"}}` 형식을 유지한다. 이 API가 쓰는 코드는 모두 SPEC.md §6 표에 있다: `RESOURCE_NOT_FOUND`, `CONFLICT_NOT_AWAITING_APPROVAL`, `CONFLICT_REVIEW_NOT_OPEN`, `CONFLICT_REVIEW_QUERY_LIMIT`, `SERVICE_UNAVAILABLE_GRAPH`, `SERVICE_UNAVAILABLE_DB`, `VALIDATION_CORP_NOT_FOUND`, `VALIDATION_AMBIGUOUS_CORP`, `VALIDATION_ERROR`. DB 연결은 `backend.db.connect_db()`를 쓴다.

## AC (Given-When-Then)

- **AC-01 · 서버 thread 생성**: GIVEN 로그인 사용자 A와 생성 요청 WHEN `POST /api/v1/reviews` THEN 저장된 thread는 `A:{review_id}`이고 본문의 user/thread 값은 없다.
- **AC-02 · 사용자 격리**: GIVEN A의 심사 건 WHEN B가 상세·질의·승인·취소 URL을 호출 THEN 각각 404이고 Repository/GraphRunner의 변경 호출은 0회다.
- **AC-03 · 목록·상세**: GIVEN A의 심사 건 2개와 B의 1개 WHEN A가 목록/상세 조회 THEN A 것만 보며, `awaiting_approval` 상세는 `pending_draft`를 포함한다.
- **AC-04 · 질의**: GIVEN A의 `open` 심사 건 WHEN A가 질문 POST THEN 서버가 저장된 회사·연도 및 서버 생성 thread로만 GraphRunner를 한 번 호출하고 결과를 200으로 돌려준다.
- **AC-04a · 심사 대상 고정**: GIVEN A회사·2025년 심사 건 WHEN 질문에 B회사 또는 다른 연도가 들어 있음 THEN Supervisor 검색 필터는 여전히 A회사·2025년이며 B회사 청크를 검색하지 않는다.
- **AC-05 · 질의 제한·상태**: GIVEN `query_count=50` 또는 `status!=open` WHEN 질문 POST THEN 409(`CONFLICT_REVIEW_QUERY_LIMIT` 또는 `CONFLICT_REVIEW_NOT_OPEN`)이며 GraphRunner 호출·카운트 증가·상태 전이는 없다.
- **AC-06 · 승인**: GIVEN A의 승인 대기 건 WHEN 올바른 decisions POST THEN `resume_approval()`만 한 번 호출하고 응답 승인자·메모는 A에 속한다.
- **AC-07 · 중복 승인**: GIVEN AC-06 성공 뒤 같은 요청 WHEN 다시 POST THEN 409, 메모 저장은 총 1건이다.
- **AC-08 · 취소**: GIVEN A의 승인 대기 건 WHEN `approvals:cancel` POST THEN `resume_approval()`이 cancel로 한 번 호출되고 메모 저장은 0건이다.
- **AC-09 · 입력·비밀 경계**: GIVEN UUID/본문이 잘못됐거나 클라이언트가 `thread_id`·`approved_by`를 포함 WHEN 요청 THEN 400이며 응답·로그에 입력 원문/비밀이 없다.
- **AC-10 · 상태 전이**: GIVEN A의 `open` 심사 건 WHEN 질문 → 승인 대기 → 승인을 차례로 호출 THEN 전이는 `open→running`, `running→awaiting_approval`, `awaiting_approval→saving`, `saving→open` 순서이고 승인은 409가 아닌 200이다. 답변·거절로 끝난 질의는 `running→open`이다. 실제 LangGraph(MemorySaver)와 가짜 저장소로 이 흐름을 시험한다.
- **AC-11 · 승인 대기 보호**: GIVEN 승인 대기(`awaiting_approval`) 심사 건 WHEN 새 질문을 POST THEN 409 `CONFLICT_REVIEW_NOT_OPEN`이고 그래프·검색은 다시 돌지 않으며 `pending_draft`가 그대로 남는다. 동시에 들어온 질의·승인은 현재 상태가 기대한 값일 때만 통과하는 전이(`transition_status`)로 한 요청만 진행한다.
- **AC-12 · 실패 복구**: GIVEN 질의 중 그래프 오류 또는 승인 처리 중 오류 WHEN 요청이 실패 THEN 심사 건은 `running`·`saving`에 갇히지 않고 각각 `open`·`awaiting_approval`로 돌아온다(승인 결정이 초안과 맞지 않으면 400).
