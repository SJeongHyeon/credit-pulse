# W1.12 Spec - Stub 검색 기반 그래프 라우팅·재작성 흐름

## Why

- **페르소나**: 기업여신 심사 에이전트를 구현·검증하는 팀 개발자
- **상황**: 현재 그래프는 노드 등록과 순차 실행만 가능하며, 검색 결과에 따라 경로를 바꾸지 못한다.
- **문제**:
  1. 범위 밖 질의도 검색으로 진행될 수 있다.
  2. 근거 부족 시 재작성·재검색 경로와 상한을 검증할 수 없다.
  3. 실행 후 어떤 노드와 분기를 거쳤는지 확인할 수 없다.
- **측정 지표**: 가짜 의존성으로 정상·재작성·상한·거절 경로를 pytest에서 결정적으로 재현한다.

## Goal

- stub 검색으로 `supervisor → retriever → scorer → rewriter → writer` 조건부 흐름을 실행한다.
- 충분한 근거는 재작성 없이 writer로 보낸다.
- 근거 부족은 서로 다른 질의일 때만 재작성·재검색하며, 검색은 최대 3회로 제한한다.
- 모든 노드와 분기 결정을 `node_events`에 순서대로 기록한다.
- 기존 `build_graph(retriever=None, checkpointer=None)` 호출은 계속 동작한다.

**Out of Scope**

- 실제 DART 검색, 임베딩, pgvector 연결
- LLM 호출과 프롬프트 구현
- 실제 기업·기간 추출과 도메인 분류
- 인용 검증, 사람 승인 interrupt, 메모 저장의 실제 구현
- Langfuse 전송

## What

### Happy Path

1. `supervisor`는 초기 State가 `outcome=refused`이면 즉시 종료하고, 아니면 검색 경로로 보낸다.
2. `retriever`는 주입된 stub 검색기를 호출하고 `retrieval_rounds`를 1 증가시킨다.
3. `scorer`는 주입된 stub 채점기로 근거 충분 여부를 판단한다.
4. 근거가 충분하면 `writer`로 이동한다.
5. 근거가 부족하고 검색 횟수가 3회 미만이면 `rewriter`가 질의를 재작성한 뒤 retriever로 되돌아간다.
6. `writer`는 `retrieval_sufficient=False`이면 아래 근거 부족 규칙을 적용한다. 거절이면 END로 종료하고, 그 외에는 기존 verifier·human_approval·save_memo stub 경로를 유지한다.

### Edge Cases

| 상황 | 처리 방식 |
|---|---|
| `outcome=refused` 초기 State | retriever를 호출하지 않고 END로 이동 |
| 검색 결과가 비어 있음 | scorer 결과에 따라 writer 또는 재작성 경로로 이동 |
| 근거 부족이 계속됨 | 최초 검색 1회 + 재검색 2회, 총 3회 후 writer의 근거 부족 경로로 이동 |
| 재작성 질의가 직전 질의와 동일 | 추가 검색 없이 writer의 근거 부족 경로로 이동 |
| retriever가 `None`을 반환 | 기존 호환성을 위해 검색 결과를 비워 두고 기본 scorer는 충분으로 처리 |
| node event 기록 | 기존 이벤트를 지우지 않고 뒤에 새 이벤트를 추가 |

## How

- `build_graph`는 기존 인자를 유지하고 선택 인자를 추가한다.

```python
build_graph(
    retriever=None,
    checkpointer=None,
    scorer=None,
    rewriter=None,
)
```
- 조건부 엣지 제어 상태는 `retrieval_sufficient: bool`, `rewrite_changed: bool`이며, 각각 채점 결과와 재작성 질의 변경 여부를 나타낸다.

- `retriever(state)`는 청크 목록을 반환할 수 있다. 반환값이 `None`이면 기존 `retrieved_chunks`를 유지한다.
- `scorer(state)`는 `True`(충분) 또는 `False`(부족)를 반환한다. 미주입 기본 scorer는 기존 stub 호환성을 위해 `True`를 반환한다.
- `rewriter(state)`는 새 검색 질의 문자열을 반환한다.
- `retrieval_rounds`는 retriever 진입 직전에 증가한다.
- 최대 검색 횟수는 리터럴 `3`으로 테스트한다.
- 재작성 질의는 공백 정규화 후 직전 `retrieval_query`와 비교한다.
- 같은 질의면 `rewriter:unchanged` 이벤트를 기록하고 writer로 이동한다.
- `retrieval_sufficient: bool`은 마지막 scorer 결과, `rewrite_changed: bool`은 정규화한 재작성 질의의 변경 여부다.
- **writer의 근거 부족 규칙(검색 상한·동일 질의 종료 모두 적용)**: `retrieval_sufficient=False`이면 일반 답변·확정 판정(`applicable`, `not_applicable`)을 생성하지 않는다. `retrieved_chunks`가 빈 목록이면 `outcome=refused`, `refusal_reason=ITEM_NOT_DISCLOSED`, `draft_items=[]`, `citations=[]`로 종료한다. 근거가 남아 있으면 SPEC.md §4에 따라 부족 항목의 `verdict=needs_check`, `reason="근거 부족: 확인할 공시 항목이나 기간을 구체화해 주세요."`, 항목의 `citations=[]`로 되묻고 `outcome=answered`로 둔다. 이 경우 이전 턴의 거절 사유는 제거한다. stub이 근거 문장이나 수치를 만들어 인용하지 않는다.
- 위 구분은 SPEC.md §3.3의 `ITEM_NOT_DISCLOSED`(검색 후 근거 0건일 때만)와 §4의 재검색 소진 시 `needs_check` 규칙을 따른다. 승인·저장 노드는 계속 stub이며 실제 메모를 저장하지 않는다.
- `node_events`는 다음 형식으로 누적한다.

```text
supervisor:retrieve
supervisor:refuse
retriever:round=1
scorer:write
scorer:rewrite
rewriter:round=1
rewriter:unchanged
writer
```

- `docs/specs/agent-state-contract.md`의 노드 계약 표를 갱신해 scorer, rewriter의 책임과 조건부 전이를 명시한다.
- LLM 호출은 추가하지 않는다.

## AC (Given-When-Then)

**AC-01 · 범위 밖 조기 종료**

- GIVEN: `outcome=refused`와 유효한 `refusal_reason`이 있는 State
- WHEN: 그래프를 실행
- THEN: retriever 호출은 0회이고 `node_events`에 `supervisor:refuse`가 기록된다.

**AC-02 · 충분한 근거의 정상 경로**

- GIVEN: 충분하다고 판정하는 scorer fixture
- WHEN: 그래프를 실행
- THEN: retriever는 1회 호출되고 `supervisor → retriever → scorer → writer` 순서 이벤트가 기록된다.

**AC-03 · 재작성 후 성공**

- GIVEN: 첫 scorer는 부족, 두 번째 scorer는 충분이며 rewriter가 다른 질의를 반환
- WHEN: 그래프를 실행
- THEN: retriever는 2회 호출되고 rewriter는 1회 호출되며 writer로 이동한다.

**AC-04 · 재검색 상한**

- GIVEN: 항상 부족으로 판정하고 매번 다른 질의를 반환하는 fixture
- WHEN: 그래프를 실행
- THEN: retriever는 정확히 3회, rewriter는 정확히 2회 호출되고 네 번째 검색은 없다.

**AC-05 · 동일 질의 재작성 방지**

- GIVEN: scorer가 부족을 반환하고 rewriter가 기존 질의와 같은 문자열을 반환
- WHEN: 그래프를 실행
- THEN: retriever는 1회만 호출되고 `rewriter:unchanged`를 기록한 뒤 writer로 이동한다.

**AC-06 · 기존 stub 호환성**

- GIVEN: 기존처럼 retriever와 checkpointer만 주입
- WHEN: 최소 State로 그래프를 실행
- THEN: 그래프가 컴파일·실행되고 retriever는 1회 호출된다.

**AC-07 · 근거 부족 종료 시 일반 답변 금지**

- GIVEN: 항상 `False`인 scorer와 (a) 매번 다른 질의를 반환해 검색 3회 상한에 도달하는 rewriter 또는 (b) 공백 정규화 뒤 동일 질의를 반환하는 rewriter
- WHEN: 각 종료 경로를 빈 검색 결과와 청크 1건·체크리스트 항목 1개가 남은 결과로 각각 실행한다
- THEN: (a)는 검색 3회·재작성 2회, (b)는 검색 1회·재작성 1회이며 추가 검색이 없다. 두 경로 모두 빈 결과는 `outcome=refused`, `refusal_reason=ITEM_NOT_DISCLOSED`, 빈 초안·인용으로 END에 도달한다. 청크가 남은 경우는 `outcome=answered`, 거절 사유 없음, 항목의 `verdict=needs_check`, 위 고정 되묻기 문구와 빈 인용을 반환한다. 확정 판정·생성한 근거·일반 답변 및 실제 메모 저장은 없다.
