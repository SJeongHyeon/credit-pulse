# Spec - Approval Checkpoint

> 결론: 판정 초안은 **저장 직전에 interrupt로 멈추고**, 심사역의 결정으로 **Postgres 체크포인트에서 이어서 재개**해 한 번만 저장한다. 단일·멀티 그래프가 같은 승인 노드(`backend/graph/approval.py`)를 쓴다. 최초 계약·구현은 서정현(#89), D5 마무리(재개 값 형식·사전 검증·멀티 그래프·재시작 재개)는 박민영.
> 강의 근거: 2주05(checkpointer), 09(interrupt), 10(Command), 11(재실행 부작용·멱등성), 12(승인 흐름 설계), 16(승인 결정 종류).

## Why

- **페르소나**: 기업여신 심사역과 팀 개발자.
- **상황**: 그래프가 만든 판정 초안은 사람이 승인하거나 수정한 뒤에만 심사 메모로 저장해야 한다(가이드 '판단을 돕는 일: 최종 판정은 사람').
- **문제**: D4까지 `human_approval`·`save_memo`는 이벤트만 남겼다. 서버가 재시작되거나 같은 승인 요청이 재전송되면 승인 대기 상태와 저장 횟수를 보장할 수 없었다.
- **측정 지표**: 승인 대기 interrupt 1회, 같은 `approval_seq`의 메모 저장 1건, 다른 프로세스에서 재개 시 검색·작성 재실행 0회.

## Goal

- `human_approval`은 검증된 초안에서 `interrupt()`로 멈추고, `approval_prepare`가 남긴 `status=outcome=awaiting_approval`을 체크포인트에 보존한다.
- 재개는 `resume_approval()` 하나로만 하며, 재개 값을 그래프에 넘기기 **전에** 검증한다.
- `save_memo`는 승인 결정을 저장 함수에 넘기고 `(user_id, review_id, approval_seq)` 멱등키로 한 번만 저장한다. 승인자는 thread의 사용자다.
- `thread_id`는 `{user_id}:{review_id}`다.
- **Out of Scope**: HTTP 승인 API(`/api/v1/reviews/{id}/approvals`)와 화면 연결(D6), 실서비스 `review_memos` 표(D6), 고친 항목을 Store 규칙으로 저장(#98), 방치된 승인의 타임아웃 정책.

## What

### Happy Path

1. writer·verifier가 초안을 만든다.
2. `approval_prepare`: thread_id가 `user_id:review_id`인지 확인하고, `approval_seq`를 1 올리고, `status=outcome=awaiting_approval`을 남긴다(부작용 없음).
3. `human_approval`: `{review_id, approval_seq, draft_items}` payload로 `interrupt()`. 호출자는 정상 반환의 `__interrupt__`를 받는다.
4. 심사역 결정 → `resume_approval(graph, config, {"approval_seq", "decisions": [{item, verdict, reason}, ...]})`.
5. `resume_approval`이 대기 payload와 대조해 검증 → `Command(resume=...)` → `human_approval`이 결정(항목별 `edited` 표시)과 `status=saving`을 남긴다.
6. `save_memo`가 저장 함수를 멱등키로 부르고 `saved_memo_id`, `status=open`, `outcome=answered`를 남긴다.

### Edge Cases

| 상황 | 처리 방식 |
|---|---|
| checkpointer 없이 `approval_enabled=True` | 그래프를 만들지 않고 `ValueError`(승인 대기를 메모리에만 두지 않음) |
| thread_id가 `{user_id}:{review_id}`와 다름 | `ApprovalInputError`, 승인 대기를 만들지 않음 |
| 재개 값이 딕셔너리가 아님 / `approval_seq` 다름 / `action`이 `approve`·`cancel` 밖 / 초안에 없는 항목·빠진 항목·중복 / `verdict`가 3종 밖 / `reason`이 문자열 아님 | `resume_approval`이 그래프에 넘기기 전에 `ApprovalInputError`. 체크포인트는 그대로라 올바른 값으로 다시 재개할 수 있다 |
| 위 검증을 건너뛰고 잘못된 값을 `Command(resume=)`로 직접 넣음 | LangGraph가 재개 값을 먼저 기록해 그 승인 건이 막힌다(시험으로 확인). 그래서 노드 안 검증은 마지막 방어선이고, 호출자는 `resume_approval`만 쓴다 |
| 대기 중인 승인이 없는데 재개(재전송) | `NotAwaitingApproval` → API에서 409 `CONFLICT_NOT_AWAITING_APPROVAL` |
| `action=cancel` | 저장하지 않고 `status=open`, `outcome=answered` |
| 재개 값에 `approved_by`가 있음 | 무시한다. 저장 기록의 승인자는 thread의 `user_id` |
| 같은 멱등키로 저장 함수가 다시 불림 | 저장 함수가 기존 id를 돌려준다(DB `UNIQUE(review_id, approval_seq)`) |
| 프로세스 종료 뒤 새 프로세스에서 재개 | 같은 DB·thread_id의 체크포인트에서 `human_approval`부터 이어 간다. 검색·작성은 다시 돌지 않는다 |
| 평가 러너처럼 승인을 끈 실행 | 세 노드가 `:skip` 이벤트만 남기고 지나간다(58·64·62번 측정 경로) |

## How

- 노드·검증·입구: `backend/graph/approval.py` (`approval_prepare_node`, `human_approval_node`, `save_memo_node`, `validate_resume`, `resume_approval`, `pending_approval`, `add_approval_nodes`). `build.py`는 두 그래프에서 `add_approval_nodes`로 조립만 한다.
- 재개 값 형식은 승인 화면 계약(`docs/specs/approval-ui.md`)과 같다: 원본 초안의 모든 항목을 같은 순서로, 승인자는 보내지 않음. 고친 항목은 원본과 비교해 서버가 `edited`로 표시한다.
- 저장 함수 계약: `memo_saver(record, idempotency_key) -> memo_id`, `record = {user_id, review_id, approval_seq, approved_by, items}`. 같은 키에는 기존 id를 돌려준다.
- Postgres: `PostgresSaver.from_conn_string(url)`(라이브러리가 `autocommit=True, prepare_threshold=0, row_factory=dict_row`로 연결), 처음 1회 `setup()`. 시연·통합 시험은 `CHECKPOINT_DATABASE_URL`(시험용 DB)만 읽는다.
- interrupt payload는 JSON으로 직렬화되는 값만 담는다.

## AC (Given-When-Then)

키 없는 시험은 `tests/test_approval.py`(단일·멀티 모두), Postgres는 `tests/test_approval_checkpoint_postgres.py`.

- **AC-01 승인 대기**: GIVEN 가짜 검색기·작성기·MemorySaver WHEN 실행 THEN interrupt 1개, `approval_seq=1`, `next=("human_approval",)`, `status=outcome=awaiting_approval`, 저장 0회.
- **AC-02 재개 뒤 한 번 저장**: WHEN `resume_approval` THEN 저장 1건, 키 `(user, review, 1)`, `status=open`, 검색·작성 호출은 처음의 1회뿐.
- **AC-03 재전송**: WHEN 완료 뒤 다시 재개 THEN `NotAwaitingApproval`, 저장 1건 유지.
- **AC-04 수정·승인자**: WHEN 한 항목을 고치고 `approved_by`를 넣어 재개 THEN 그 항목만 `edited=True`, 승인자는 thread 사용자.
- **AC-05 취소**: WHEN `action=cancel` THEN 저장 0, `status=open`.
- **AC-06 잘못된 재개 값 7종**: THEN `ApprovalInputError`, 저장 0, 대기 유지, 올바른 값으로 다시 재개하면 저장 1.
- **AC-07 대화 격리**: thread_id 불일치는 거절, 두 사용자의 대기는 서로 영향이 없다.
- **AC-08 승인 끔**: interrupt 없음, `:skip` 3개, 저장 0. 체크포인터 없이 켜면 `ValueError`.
- **AC-09 재시작 재개**: GIVEN 시험용 Postgres WHEN 멈춤·재개·재전송·집계를 서로 다른 프로세스로 실행 THEN 재개 프로세스의 검색·작성 0회, 재전송은 `CONFLICT_NOT_AWAITING_APPROVAL`, 메모 1행(단일·멀티).
