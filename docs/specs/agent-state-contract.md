# Spec - Agent State and Node Contract

## Why

- **Persona**: 기업여신 심사역과 팀 개발자(서정현: 에이전트·평가 담당).
- **Situation**: 심사 질의는 범위 판정, 검색, 작성, 검증, 사람 승인, 저장을 거친다. 여러 담당자가 병렬로 구현하므로 상태와 노드 입출력이 먼저 고정되지 않으면 노드 간 상태 키가 어긋나거나 승인 뒤 저장이 중복될 수 있다.
- **Problem**: 현재 `docs/ARCHITECTURE.md`에는 출발점 그림만 있고, 구현 가능한 State 필드·노드 책임·전이 조건·재시도 상한이 확정되어 있지 않다.
- **Metric**: D4 단일 그래프 테스트에서 범위 밖 질의는 검색 호출 0회, 근거 부족 질의의 재검색은 최대 2회, 판정 초안은 저장 전 반드시 승인 대기 상태가 된다.

## Goal

- `backend/graph/state.py`와 `backend/graph/build.py` 구현에 사용할 단일 상태 계약을 확정한다.
- 노드별 입력·출력과 전이 조건을 문서화해 검색 담당과의 `retrieve()` 계약을 분리한다.
- **Success criteria**: AC 전체를 pytest로 옮길 수 있고, 경로·키·재시도 상한의 임의 해석이 남지 않는다.
- **Out of Scope**: 실제 DART 검색·LLM 호출을 사용하는 production 노드, Postgres checkpointer 연결, UI 승인 화면, 장기 기억(Store) 구현. W1.12의 주입된 stub 기반 제어 흐름은 별도 Spec으로 구현한다.
## What

### Happy Path

1. API가 `user_id`, `review_id`, 질문을 포함한 초기 State를 만든다.
2. `supervisor`가 회사·사업연도·체크리스트 항목을 추출하고 코퍼스 범위를 판정한다.
3. 범위 안이면 `retriever`가 `retrieve(question, filters, k)`를 호출하고 근거 충분 여부를 판정한다. 근거가 부족하면 `retriever` 내부에서 질의를 재작성하고 재검색한다.
4. `writer`가 판정 초안과 인용을 만들고, `verifier`가 인용·숫자·문서 내 지시문 경계를 검사한다.
5. 판정 항목이 있으면 `human_approval`에서 interrupt한다. 승인 요청이 오면 `save_memo`만 재개하고, 멱등키로 한 번만 저장한다.

### Edge Cases

| 상황 | 처리 |
|---|---|
| 코퍼스에 없는 회사·기간·도메인 | `supervisor`가 정해진 거절 코드와 함께 종료하며 `retriever`를 호출하지 않는다. |
| 공시하지 않는 항목 | 검색 결과가 0건이면 `ITEM_NOT_DISCLOSED`로 종료한다. 결과가 있으나 근거 충분성 판정에서 탈락하면 상한 안에서 질의 재작성·재검색하고, 상한까지 채점(회사·연도 검사·LLM 채점)을 통과한 근거가 0건이면 `ITEM_NOT_DISCLOSED`로 종료한다(사전 등록 60번). 근거가 1건이라도 남으면 `needs_check`로 답한다. |
| 재작성 질의가 직전 질의와 동일 | `retriever`는 상한 전이라도 재검색하지 않고 `writer`로 진행한다. |
| 재검색을 두 번 했는데도 근거 부족 | 총 검색 3회(최초 1회 + 재검색 2회) 뒤 남은 근거로 `writer`가 초안을 작성하고 부족 항목은 `needs_check`로 둔다. |
| 검증 실패 인용 | 해당 항목을 `needs_check`로 낮추고 실패 인용을 확정 판정에서 제거한다. `verifier`에서 `writer`로 최대 1회 돌아가 재작성·재검증하며, 다시 실패하면 추가 재작성 없이 하향 판정을 유지한다. |
| 승인 요청 재전송 | 같은 `approval_seq`는 저장을 중복 실행하지 않는다. |

## How

### State contract

`AgentState`는 다음 키를 사용한다. 코퍼스·모델별 값은 코드 상수로 고정하지 않고 의존성 주입 또는 DB 조회로 받는다.

| 영역 | 키 | 타입/의미 |
|---|---|---|
| 식별 | `user_id`, `review_id`, `thread_id` | 사용자·심사 건·체크포인트 식별자 |
| 입력 해석 | `question`, `corp_name`, `corp_code`, `fiscal_year`, `checklist_items` | 원 질문과 Supervisor가 manifest에서 확정한 대상 |
| 통제 | `outcome`, `refusal_reason`, `status` | `answered`/`refused`/`awaiting_approval` 및 상태 전이 결과 |
| 검색 | `retrieval_query`, `retrieval_rounds`, `filters`, `retrieved_chunks`, `retrieval_sufficient`, `retrieval_empty`, `rewrite_changed`, `target_required` | 검색 질의·회차·체크포인트 직렬화 가능한 필터·결과·충분성·0건·재작성 변경·대상 보완 필요 여부 |
| 생성·검증 | `draft_items`, `citations`, `verification`, `verification_rewrites` | 판정 초안과 근거 검증 결과, 검증 실패 후 재작성 횟수(초기 0, 최대 1) |
| 승인·저장 | `approval_seq`, `approval_decisions`, `saved_memo_id` | interrupt 재개 및 중복 저장 방지 |
| 관측 | `trace_id`, `node_events`, `errors` | Langfuse 및 디버그에 필요한 최소 이벤트 |

- 새 질의 턴 시작 시 `retrieval_query`, `filters`는 새 입력에서 만들고, `retrieved_chunks`, `draft_items`, `citations`, `node_events`, `errors`는 빈 목록, `verification`은 빈 결과, `retrieval_rounds`, `verification_rewrites`는 0으로 초기화한다. 턴별 필드는 누적 리듀서를 쓰지 않는다. 동일 턴의 승인 interrupt 재개 시에는 초기화하지 않는다.
- `refusal_reason`은 SPEC.md §3.3의 `OUT_OF_DOMAIN`, `CORP_NOT_IN_CORPUS`, `ITEM_NOT_DISCLOSED`, `PERIOD_NOT_IN_CORPUS`, `PRIOR_YEAR_NOT_IN_CORPUS`, `INJECTION_SUSPECTED`만 사용한다. `ITEM_NOT_DISCLOSED`는 검색 결과 0건이거나 재검색 상한까지 채점을 통과한 근거가 0건일 때만 사용한다(SPEC §3.3 '검색 후 근거 0'). 거절이 아닌 응답에서는 `None`으로 이전 거절 사유를 지운다.
- `filters`는 `{"corp_code": str, "fiscal_year": int}` 딕셔너리다. 실제 retriever adapter가 이를 `SearchFilters`로 바꿔 검색 호출 전에 적용한다. State에는 직렬화할 수 없는 객체를 넣지 않는다.
- 회사명 또는 사업연도를 확정하지 못하면 `target_required=True`, `outcome=answered`, 빈 초안·인용과 대상 보완 안내를 반환하고 retriever를 호출하지 않는다.

### Node contract and routing

| 노드 | 책임 | 성공 출력 | 다음 경로 |
|---|---|---|---|
| `supervisor` | manifest에서 대상 추출, 범위·도메인 판정 | 회사·코드·연도·필터, 거절 코드 또는 대상 보완 안내 | `retriever` 또는 종료 |
| `retriever` | 주입받은 stub 검색기를 호출하고 검색 회차·결과를 기록 | 후보 청크, 검색 회차 | `scorer` |
| `scorer` | 회사·연도 결정적 검사 후 선택적 LLM 일괄 채점으로 근거 충분성을 판정 | 남은 청크, `retrieval_sufficient`, `retrieval_empty` | 0건·충분 시 `writer`, 그 외 `rewriter` |
| `rewriter` | 회사·연도를 보존하고 코퍼스 지시문을 데이터로 취급해 후보 질의를 재작성·비교 | `retrieval_query`, `rewrite_changed` | 변경 시 `retriever`, 동일 시 `writer` |
| `writer` | LLM이 고른 이번 실행의 남은 청크 ID만으로 판정 초안·인용 작성. 근거 부족이면 `needs_check`와 고정 되묻기 문구·빈 인용을 반환 | `draft_items`, `citations`, `outcome`, `refusal_reason` | 거절 시 종료, 그 외 `verifier` |
| `verifier` | 인용·숫자·간접 프롬프트 인젝션 검증, 실패 항목 하향 및 재작성 횟수 제한 | 검증 결과, 필요 시 하향 판정 | 실패 시 `writer`로 최대 1회, 이후 `human_approval` 또는 종료 |
| `human_approval` | 승인 대기 State를 체크포인트에 보존하고 interrupt와 decisions를 검증한다. 메모 저장·규칙 갱신은 하지 않는다. | LangGraph interrupt payload | 외부 승인 후 `save_memo` |
| `save_memo` | 승인된 항목만 멱등 저장 | `saved_memo_id` | 종료 |

- LLM 호출은 오직 `backend/services/llm.py`를 통해 한다.
- `build_graph(retriever=None, checkpointer=None)`로 구성해 단위 테스트에서 가짜 검색기와 메모리 체크포인터를 주입한다.
- `retrieve()` 필터는 검색 호출 시점에 적용한다. 검색 후 결과를 잘라 우연한 거절을 만들지 않는다.
- 재시도·재작성·저장 모두 위의 고정 상한과 멱등키를 가진다.

## AC (Given-When-Then)

**AC-01 · 범위 밖 조기 거절**

- GIVEN: 코퍼스에 없는 회사 또는 기간을 담은 질의
- WHEN: `supervisor`가 State를 처리
- THEN: 정해진 `refusal_reason`과 `outcome=refused`를 내고 `retriever`는 0회 호출된다.

**AC-02 · 재검색 상한**

- GIVEN: 매 회 검색 결과는 1건 이상이나 근거 충분성 판정은 부족이고, 재작성 질의는 매번 달라지는 fixture
- WHEN: 그래프를 실행
- THEN: `retrieval_rounds`는 리터럴 3이고 질의 재작성 호출은 정확히 2회이며 네 번째 검색은 없다. 남은 근거로 `writer`가 초안을 작성하고 부족 항목의 `verdict=needs_check`이며, 근거 부족만으로 `outcome=refused`가 되지 않는다.

**AC-03 · 제자리걸음 방지**

- GIVEN: `retriever` 내부의 질의 재작성 결과가 직전과 정규화 후 같은 질의
- WHEN: 근거 부족 경로가 실행
- THEN: 추가 검색 없이 `writer`로 이동한다.

**AC-04 · 검증 실패 하향**

- GIVEN: 초안 인용의 숫자 또는 문장이 검색 청크와 다른 경우
- WHEN: `verifier`가 실행
- THEN: 해당 항목은 `확인필요`이고 확정 판정에 검증 실패 인용이 없다.

**AC-05 · 승인 대기와 멱등 저장**

- GIVEN: 검증을 마친 판정 항목과 아직 저장되지 않은 심사 건
- WHEN: 그래프가 `human_approval`에 도달하고 같은 승인 요청을 두 번 받음
- THEN: 첫 실행은 `awaiting_approval` interrupt로 멈추며, 재개 뒤 심사 메모는 정확히 1건만 저장된다.

**AC-06 · 검증 실패 후 재작성 상한**

- GIVEN: 최초 초안과 재작성 초안 모두 인용 검증에 실패하는 fixture
- WHEN: `writer`와 `verifier` 경로를 실행
- THEN: 검증 실패 후 `verifier` → `writer` 되돌림 횟수와 `verification_rewrites`를 리터럴 1과 비교해 같음을 확인한다. 최초 작성을 포함한 `writer` 호출은 2회, 검증은 2회이고, 실패 항목은 `needs_check`이며 확정 판정에 실패 인용이 없다.

**AC-07 · 검색 결과 0건과 충분성 탈락 구분**

- GIVEN: 범위 안 질의에 검색 결과가 0건인 fixture
- WHEN: `retriever`를 실행
- THEN: `outcome=refused`, `refusal_reason=ITEM_NOT_DISCLOSED`로 종료한다. 결과가 1건 이상인 충분성 탈락 fixture는 AC-02의 재검색 경로를 따르고, 상한까지 채점을 통과한 근거가 0건이면 `ITEM_NOT_DISCLOSED`로 종료한다(사전 등록 60번).
