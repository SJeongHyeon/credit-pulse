# Spec - D5 Supervisor + 서브에이전트 분리 (A3)

> 결론: D4 단일 그래프(`build_graph`)의 노드 함수를 **그대로 재사용**해 검색 에이전트·작성/검증 에이전트 두 서브그래프로 묶고, Supervisor가 선별 인계로 부른다. 바꾸는 것은 **구조 하나**뿐이라 v2 50건에서 단일(사전 등록 56번)과 같은 결과가 나와야 하고, E5(58번)는 구조 효과만 잰다.
> 근거 강의: 3주15(Supervisor를 StateGraph로 직접), 3주16(서브에이전트 컨텍스트 격리, 선별 인계), 2주17~19(검색 판단·재작성 상한·라우팅). 정본은 SPEC.md·agent-state-contract.md다.

## Why

- **페르소나**: 팀 개발자(박민영 A3, 서정현 A4)와 E5 비교를 검토하는 평가자.
- **상황**: G4는 "Supervisor와 서브에이전트 2개 이상, 역할과 컨텍스트를 나눈다"를 요구한다. 지금은 `build_graph` 하나가 supervisor~save_memo를 모두 같은 `AgentState`로 공유한다.
- **문제**:
  1. 모든 노드가 State 전체를 본다. 작성기는 검색 질의·재작성 회차까지 받을 수 있어, 무엇이 작성 품질에 쓰였는지 경계가 없다.
  2. 단일과 멀티를 비교할 때 노드·지시문·모델을 같이 바꾸면 구조 효과를 분리할 수 없다(SPEC §7, P16 R7).
- **측정 지표**: v2 50건 응답 지표 5개(정답 근거 인용·오답 근거·과잉 거절·올바른 거절·환각 위험), 문항별 outcome·refusal_reason·인용 chunk_id·검색 호출 수의 단일 대비 일치 수, 비용, 문항 시간 p50.

## Goal

- `build_supervisor_graph(...)`가 `build_graph`와 같은 인자로 컴파일되고, 가짜 검색기·가짜 LLM으로 v2 50건을 돌렸을 때 단일과 **50/50 문항** outcome·refusal_reason·인용 chunk_id·검색 호출 수가 같다.
- 서브에이전트는 아래 인계 계약 밖의 키를 입력으로 받지 않는다(시험으로 확인).
- 검색 상한(총 3회)·제자리걸음 정지·사전 거절 시 서브에이전트 0회 호출이 멀티에서도 유지된다.
- **Out of Scope**:
  - 노드 함수·지시문·모델·검색 조합(`d3-55`)·LLM 채점(꺼짐) 변경. 전부 56번과 같다.
  - `human_approval`·`save_memo`·checkpointer·interrupt(서정현 A4 브랜치). 멀티 그래프는 지금의 stub 노드를 같은 자리에 둔다.
  - 주제 밖 판정(59 후보), 채점 '부족'(60 후보), 비교 질문 분리(63 후보), 웹 검색.
  - LLM 기반 라우팅. Supervisor의 라우팅은 지금처럼 코드(결정적)다.
  - 기존 `build_graph`(단일) 수정.

## What

### Happy Path

1. `supervisor`(지금 노드 그대로): 회사·연도 추출, 없는 회사·다른 기간·보고서 종류 사전 거절, 대상 미확정 안내.
2. 범위 안이면 Supervisor가 **검색 인계 입력**만 골라 `search_agent`를 부른다.
3. `search_agent`(서브그래프): `retriever → scorer → (rewriter → retriever)* → END`. 상한·제자리걸음·대상 유실·0건 유지 규칙은 지금 코드 그대로.
4. Supervisor가 검색 결과를 받아 **작성 인계 입력**을 골라 `writer_agent`를 부른다.
5. `writer_agent`(서브그래프): `writer → verifier → END`. 거절(`ITEM_NOT_DISCLOSED`)이면 verifier 없이 END.
6. Supervisor가 결과를 취합해 거절이면 END, 아니면 `human_approval → save_memo → END`(지금 stub 그대로).

```
START → supervisor ─┬─ 거절·대상 미확정 ─────────────────────────────→ END
                    └→ search_agent [retriever→scorer⇄rewriter]
                         → writer_agent [writer→verifier] ─┬─ 거절 → END
                                                            └→ human_approval → save_memo → END
```

~~Supervisor가 각 서브에이전트 뒤에 다시 `supervisor` 노드로 돌아오는 hub 구조는 쓰지 않는다.~~ **11/6 변경(사전 등록 74번)**: 중간 점검 피드백과 사이트 D5 조건('고정 순서 불인정')에 따라 hub 구조로 바꾼다. 서브에이전트는 끝나면 `supervisor`로 돌아오고, supervisor가 `route_from_supervisor(state)`(`backend/graph/supervisor_flow.py`, 조건부 엣지)로 다음을 고른다. 1단계(74번)는 판단 규칙이 위 고정 순서와 같은 결과를 내게 해 구조만 바꾼다. 입구 판정은 첫 방문 한 번만, 방문 상한 7(SPEC §4), 턴마다 카운터를 비우는 `turn_start` 노드를 둔다. supervisor가 기대는 State 필드: `outcome`, `target_required`, `supervisor_turns`, `search_calls`, `writer_calls`. 이벤트(`node_events`)는 단일 그래프와 같게 두고, supervisor의 결정 기록은 `supervisor_trace`에 따로 남긴다.

```
START → turn_start → load_rules → supervisor ◀──────────────┐
                                   ├─ 거절·되묻기 → END        │
                                   ├─ 검색 전 → search_agent ──┤
                                   ├─ 작성 전 → writer_agent ──┘
                                   ├─ 작성 거절 → END
                                   └─ 판정 있음 → approval_prepare → human_approval → save_memo → END
```

인계 선별은 그대로 어댑터 노드(`search_agent`, `writer_agent`)가 한다.

**2단계 2a(사전 등록 75번, 80번에서 rewriter 위치 옮김, `requery_enabled`, 기본 꺼짐)**: 작성 결과가 작성기 스스로 낸 근거 부족(needs_check 중 verifier 하향이 아닌 것)이거나 작성 단계 `ITEM_NOT_DISCLOSED` 거절이면, 총 검색 3회 안에서 supervisor가 search_agent를 다시 부른다. 역할은 셋으로 나눈다: writer는 '무엇을 못 찾았는지' 판단(needs_check의 reason, 공시 안 됨), supervisor는 '다시 할지 끝낼지' 결정(코드 규칙, LLM 호출 없음), search_agent는 '어떻게 다시 찾을지' 실행. 검색 인계 함수(`search_handoff`)가 writer 결과에서 '못 찾은 것'을 만들어 `requery_feedback`으로, 직전 검색 질의를 `last_query`로 넘긴다. search_agent는 피드백이 있으면 rewriter → retriever → scorer, 없으면 지금처럼 retriever부터 돈다. 재작성이 제자리걸음·대상 유실이면 retriever 없이 끝나고(`rewrite_changed=False`), supervisor는 그때 결과로 끝낸다. 재검색을 켜면 검색 에이전트 안의 채점 뒤 재작성 루프는 뺀다(`inner_rewrite=False`). 질의를 다루는 곳을 search_agent 하나로 모은 이유: 작성 에이전트는 계약상 검색 질의·재작성 이력을 받지 않고(격리), supervisor는 결정만 한다(3주16 선별 인계).

**2단계 2b(사전 등록 78번, `redraft_enabled`, 82번부터 멀티 그래프 기본 켜짐)**: verifier가 reason 숫자 불일치(`reason_number_or_unit_not_in_chunk`)로 하향하면, supervisor가 `redraft` 노드로 보낸다. redraft는 verifier가 기록한 `missing_numbers`로 피드백 문장을 만들어 인계 키 `writer_feedback`에 싣고, 같은 청크로 작성 에이전트를 한 번 더 부른 뒤 supervisor로 돌아온다(검색 없음, 질문당 1회). 다시 검색해도 숫자 불일치는 고쳐지지 않으므로 2a(재검색)와 방아쇠를 나눈다. 방문 상한 7은 더 일하는 경로만 막고, 남은 방문으로 끝낼 수 있는 일만 고른다(redraft는 1방문, 재검색은 검색·작성 2방문).

```
supervisor ─ 근거 부족/작성 단계 거절 + 검색 3회 미만 ─→ requery ─ 바뀜 ─→ search_agent → supervisor → writer_agent → supervisor
                                                          └ 제자리걸음 ─→ supervisor(그때 결과로 끝냄)
```

### Edge Cases

| # | 상황 | 처리 |
|---|---|---|
| EC-01 | 없는 회사·다른 기간·보고서 종류 | supervisor가 거절, 서브에이전트 0회 호출(검색 0회) |
| EC-02 | 회사·연도 미확정 | `target_required=True`, 서브에이전트 0회 호출 |
| EC-03 | 근거 부족이 계속됨 | 검색 에이전트 안에서 총 3회 뒤 종료, 작성은 needs_check |
| EC-04 | 재작성 질의가 직전과 같음 | 검색 에이전트 안에서 추가 검색 없이 종료 |
| EC-05 | 재작성이 회사·연도를 잃음 | `rewriter:target_lost`, 추가 검색 없이 종료 |
| EC-06 | 재검색 결과 0건 | 이전 결과 유지(지금 `_retriever_node` 규칙) |
| EC-07 | 검색 결과 0건 | 작성 에이전트가 `ITEM_NOT_DISCLOSED`로 거절, verifier·승인 노드 없음 |
| EC-08 | 서브에이전트에 계약 밖 키가 섞임 | 어댑터가 계약 키만 골라 넘기므로 들어가지 않음(시험 ②) |

## How

### 파일과 소유

| 파일 | 내용 |
|---|---|
| `backend/graph/agents/__init__.py` | 빈 파일 |
| `backend/graph/agents/search_agent.py` | `SearchInput`·`SearchOutput`·`SearchState` TypedDict, `build_search_agent(retriever, scorer, rewriter, batch_grader, llm_grading_enabled)` → compile된 서브그래프 |
| `backend/graph/agents/writer_agent.py` | `WriterInput`·`WriterOutput`·`WriterState` TypedDict, `build_writer_agent(draft_writer)` → compile된 서브그래프 |
| `backend/graph/build.py` | `build_supervisor_graph(...)` **추가만**. 조립(노드 등록·엣지)과 어댑터만 두고, 노드 로직은 기존 `_supervisor_node`·`_retriever_node`·`_scorer_node`·`_rewriter_node`·`_writer_node`·`_event_node`를 import해 재사용. 기존 `build_graph`는 바꾸지 않는다 |

- 서브그래프는 `StateGraph(내부State, input_schema=입력, output_schema=출력)`으로 만들고 checkpointer 없이 compile한다(부모 그래프의 checkpointer만 쓴다). 어댑터 노드 안에서 `subgraph.invoke(골라낸 입력)`으로 부른다(3주16 선별 인계). 부모와 State를 공유하는 "노드로 서브그래프 직접 추가" 방식은 쓰지 않는다. 그 방식은 같은 키를 자동으로 공유해 계약을 코드로 강제할 수 없다.
- 기본 LLM 함수(재작성·작성·채점)를 만드는 부분은 `build_graph`와 같은 `llm.py` 함수·같은 모델 인자를 쓴다. 생성 모델 = judge 모델이면 `ValueError`(지금 검사 유지).
- `node_events`: 서브그래프는 빈 목록에서 시작하고, 어댑터가 부모 목록 뒤에 이어 붙인다. 서브그래프 안 노드 이름이 같아 **이벤트 순서가 단일과 같다**(`supervisor:retrieve, retriever:round=1, scorer:write, writer, verifier, human_approval, save_memo`). 러너의 `supervisor_record`·`rewrites` 집계를 고치지 않아도 된다.
- 노드 이름: SPEC §5.3의 6개는 그대로 쓰고(서브그래프 안 `retriever`·`writer`·`verifier`), 부모 그래프에 `search_agent`·`writer_agent`만 더한다.

### 선별 인계 계약

| 방향 | 넘기는 키 | 비고 |
|---|---|---|
| Supervisor → 검색 | `question`, `corp_code`, `corp_name`, `fiscal_year`, `checklist_items`, `filters`, `comparison_targets`(63번: 비교 질문이면 회사 2~3곳) | `retrieval_query`는 넘기지 않는다. 검색 에이전트가 `question`을 공백 정규화해 1회차 질의로 쓴다(지금 supervisor가 하는 계산과 같다). `retrieval_rounds`는 0에서 시작 |
| 검색 → Supervisor | `retrieved_chunks`, `retrieval_rounds`, `retrieval_sufficient`, `retrieval_empty`, `node_events` | 재작성 질의·`rewrite_changed`는 검색 에이전트 안에만 남는다 |
| Supervisor → 작성 | `question`, `checklist_items`, 채택 청크(`retrieved_chunks` = scorer가 남긴 청크), **`retrieval_sufficient`, `retrieval_empty`**, `domain_check`(72번) | 굵은 두 키는 아래 '확인 필요 1'. `domain_check`는 주제 판정 결과로, 작성기의 '공시 안 됨'과 함께 볼 때만 거절에 쓴다 |
| 작성 → Supervisor | `outcome`, `refusal_reason`, `draft_items`, `citations`, `node_events` (verifier 켜면 `verification` 추가) | |
| 넘기지 않음 | 대화 전체(`user_id`·`review_id`·`thread_id`·이전 턴 값), 다른 에이전트 중간값(`retrieval_query`, `rewrite_changed`, 재작성 이력, `target_required`, `refusal_message`) | |

### verifier

- 작성 에이전트의 verifier 노드는 단일 그래프와 같은 `build._verifier_node`(→ `backend/graph/verifier.py`의 `verify_draft_items`, PR #80) 하나다. 멀티 전용 verifier 코드와 `verifier_enabled` 스위치는 두지 않는다. 단일·멀티가 같은 검사를 받으므로 구조 동등성(AC-01)이 verifier를 켠 상태에서도 성립한다.
- E5(58번)는 #80 머지 전 main으로 잰다(그때는 둘 다 verifier가 이벤트만 남긴다).

### 러너

- `scripts/run_graph_eval.py --graph single|multi`(기본 `single`). `single`은 지금 코드 경로 그대로라 기존 결과 재현에 영향이 없다. summary에 `graph` 값을 남긴다.

## AC (Given-When-Then)

**AC-01 · 구조 동등성**
- GIVEN: 가짜 검색기(질의·필터로 결정적 청크 반환)·가짜 재작성·가짜 작성기, 동결 v2 50문항
- WHEN: 같은 입력을 `build_graph`와 `build_supervisor_graph`에 넣는다
- THEN: 50문항 모두 outcome·refusal_reason·인용 chunk_id 목록·검색 호출 수·`node_events`가 같다.

**AC-02 · 인계 계약**
- GIVEN: 계약 밖 키(`user_id`, `review_id`, `retrieval_query`, `rewrite_changed`, `target_required` 등)를 채운 초기 State
- WHEN: 멀티 그래프를 실행하고 각 서브에이전트가 받은 입력을 기록한다
- THEN: 검색 에이전트 입력 키 ⊆ {question, corp_code, corp_name, fiscal_year, checklist_items, filters}, 작성 에이전트 입력 키 ⊆ {question, checklist_items, retrieved_chunks, retrieval_sufficient, retrieval_empty, domain_check}(domain_check는 72번).

**AC-03 · 검색 상한과 제자리걸음**
- GIVEN: (a) 항상 부족·매번 다른 재작성 (b) 부족·같은 재작성
- WHEN: 멀티 그래프 실행
- THEN: (a) 검색 리터럴 3회·재작성 2회, (b) 검색 1회이고 writer로 간다.

**AC-04 · 사전 거절은 서브에이전트 0회**
- GIVEN: 없는 회사·다른 기간·반기보고서·대상 미확정 질문
- WHEN: 멀티 그래프 실행
- THEN: 검색·작성 서브에이전트 호출 0회, 검색기 호출 0회.

**AC-05 · verifier 공통**
- GIVEN: 이번 검색 결과에 없는 chunk_id 인용, 또는 인용 청크에 없는 금액·비율을 reason에 쓴 초안
- WHEN: 단일과 멀티 그래프를 실행
- THEN: 그 항목은 `needs_check`, 인용은 빈 목록. 통과한 항목은 그대로. 두 그래프의 `draft_items`·`verification`이 같다.

## 확인 필요

1. **작성 인계에 `retrieval_sufficient`·`retrieval_empty` 추가.** 지금 `_writer_node`가 이 두 값으로 분기한다(0건이면 `ITEM_NOT_DISCLOSED` 거절, 부족이면 needs_check). 넘기지 않으면 노드 함수를 고치거나 그 분기를 Supervisor로 옮겨야 해서 "구조만 바꾼다"를 어긴다.
2. **`retrieval_rounds`를 검색 인계에 넣지 않음.** 턴마다 0에서 시작한다(상태 계약 '새 질의 턴 시작 시 0'). 단일은 초기 State에 `retrieval_rounds>=3`이 있으면 검색을 건너뛰는데(`test_preexhausted_retrieval_state_never_calls_a_fourth_retriever`), 멀티는 이 경우 다시 검색한다. 러너는 늘 0으로 시작하므로 E5 결과에는 영향이 없다.
