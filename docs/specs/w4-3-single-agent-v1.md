# W4.3 Spec - D4 단일 에이전트 v1

## Why

- **페르소나**: FY2025 사업보고서 근거로 체크리스트 초안을 만드는 기업여신 심사역.
- **상황**: W1.12 그래프는 주입한 stub의 검색·채점·재작성 경로만 검증한다. 실제 D4 기준선에는 대상 추출, 결정적 근거 검사, LLM 채점·재작성, 근거 기반 초안이 필요하다.
- **문제**: 회사·연도 필터 없이 검색하면 타사·다른 기간 청크가 섞이고, "해당 사항 없음" 근거를 LLM이 무관으로 버린 25번 회귀가 있다.
- **측정 지표**: 키 없이 그래프 테스트에서 정상 경로, 사전 거절, 총 검색 3회 상한, 동일 재작성 정지, 대상 불일치 제거, "해당 사항 없음" 유지, 0건 `ITEM_NOT_DISCLOSED`를 재현한다. W4.7의 동결 평가셋 측정은 박민영이 별도로 실행한다.

## Goal

- manifest 기반 대상 추출과 필수 `SearchFilters`를 포함한 D4 단일 에이전트 v1을 만든다.
- 충분성은 대상 일치 결정적 검사와 선택적 LLM 일괄 채점으로 판단한다.
- writer는 검색된 근거에서만 `draft_items`와 `citations`를 만든다.
- **성공 기준**: AC의 키 없는 pytest가 통과하고, 재검색은 최초 1회와 재검색 2회를 합쳐 총 3회를 넘지 않는다.
- **Out of Scope**: `backend/rag/` 수정, 실제 평가셋 전체 호출, verifier 변경, 사람 승인·메모 저장의 실제 구현, 임베딩·리랭커 조합 선택.

## What

### Happy Path

1. supervisor는 `CorpusCatalog`가 manifest에서 읽은 회사명·`corp_code`·회사별 사업연도로 질문의 대상을 확정해 State의 `corp_code`, `fiscal_year`, `filters`에 넣는다.
   - 회사는 질문 전체에서 manifest 이름을 찾고 긴 이름을 우선한다. 연도가 없으면 회사별 연도가 하나인 경우에만 확정한다.
2. retriever 주입 함수는 확정한 필터를 포함한 State로 검색을 수행한다.
3. scorer는 청크 메타데이터의 회사·연도를 결정적으로 검사한 뒤, 켜져 있으면 한 라운드의 후보를 한 번의 LLM 호출로 일괄 채점한다.
4. 부족하면 rewriter가 대상 문자열을 보존한 검색 질의를 새로 만들고, 변경됐을 때만 상한 안에서 재검색한다.
5. writer는 남은 청크에서 항목별 근거를 선택해 `chunk_id`를 포함한 인용과 판정 초안을 만든다. 근거가 부족한 항목은 `needs_check`다.

### Edge Cases

| 상황 | 처리 방식 |
|---|---|
| 회사 또는 사업연도를 확정하지 못함 | 검색 0회, `outcome=answered`, 빈 초안·인용, 대상 보완 안내를 반환한다. |
| 회사가 manifest에 없음 | `CORP_NOT_IN_CORPUS`로 사전 거절한다. |
| 회사는 있으나 해당 연도가 없음 | `PERIOD_NOT_IN_CORPUS`로 사전 거절한다. |
| 체크리스트가 비어 있음 | 질문을 단일 항목으로 작성한다. |
| 재검색 결과가 0건 | 앞 라운드의 검증된 후보를 유지한다. |
| 결과 청크의 회사·연도가 대상과 다름 | scorer 1단에서 제외한다. |
| LLM 채점이 꺼짐 | 결정적 검사 결과만으로 충분성을 판단한다. |
| "해당 사항 없음" 청크 | 질문 대상·항목에 맞으면 LLM 채점에서 relevant로 유지한다. |
| 검색 원시 결과가 0건 | writer가 `ITEM_NOT_DISCLOSED`로 종료한다. |
| LLM 재작성에 코퍼스 지시문이 포함됨 | 코퍼스 문장은 데이터로만 제공하며, 프롬프트에 지시로 따르지 않는다고 명시한다. |

## How

- `MAX_RETRIEVAL_ROUNDS = 3` 하나로 검색 상한을 정의한다. 테스트는 상수가 아니라 리터럴 총 검색 `3`, 재검색 `2`와 호출 수를 확인한다.
- `CorpusCatalog`는 manifest의 `corp_name`, `corp_code`, `fiscal_year`, `report_type`을 읽고 회사별 대상 정보를 보존한다. 회사 목록과 코드, 점수 임계값을 그래프 코드에 고정하지 않는다.
- supervisor는 `{"corp_code", "fiscal_year"}` 필터를 State에 저장한다. 실제 retriever adapter는 이를 `SearchFilters`로 변환해 검색 계약을 지키며, `backend/rag/retrieve.py`는 수정하지 않는다.
- scorer의 1단은 `chunk["corp_code"] == state["corp_code"]` 및 `chunk["fiscal_year"] == state["fiscal_year"]`다. 리랭커 절대 점수 임계값은 주입값이 있을 때만 적용한다.
- scorer의 2단은 `llm_grader(chunks, question, checklist_items, model)` 주입 함수로 한 라운드 전체를 한 번에 채점한다. 기본 judge 모델은 28번 결과에 따라 `openai/gpt-4.1`이고, LLM 채점은 설정으로 끈다.
- rewriter와 writer의 LLM 호출은 `backend/services/llm.py`의 `openrouter_structured`만 통한다. 기본 생성 모델은 `openai/gpt-4o-mini`이고 judge 모델과 같으면 구성 오류로 낸다. 가짜 LLM을 주입해 키 없이 시험한다.
- rewriter는 `corp_code`, `fiscal_year`와 원 질문의 회사·기간 표현을 입력으로 받아 보존한다. 코퍼스 문장은 명령이 아니라 인용할 데이터임을 별도 system 지시로 준다.
- writer는 선택한 청크의 `chunk_id`, `rcept_no`, `chapter`, `note_no`, `content`을 인용 후보로 쓰며, 근거가 없는 checklist item은 빈 인용의 `needs_check`로 둔다. verifier는 변경하지 않는다.
- `node_events`는 기존 경로 기록을 보존하고 대상 미확정·채점 단계·LLM 비활성 분기를 추가한다.

## AC (Given-When-Then)

**AC-01 · 대상 확정과 필수 필터**
- GIVEN: manifest에 `오뚜기`, `00000001`, FY2025가 있다.
- WHEN: `오뚜기의 FY2025 사업보고서 차입금`을 실행한다.
- THEN: retriever는 `corp_code=00000001`, `fiscal_year=2025` 필터를 가진 State를 정확히 1회 받는다.

**AC-02 · 대상 미확정**
- GIVEN: 회사 또는 사업연도가 없는 질문이다.
- WHEN: supervisor를 실행한다.
- THEN: retriever 호출은 0회이고 빈 초안·인용과 대상 보완 안내를 반환한다.

**AC-03 · 재검색 상한과 동일 질의 정지**
- GIVEN: 항상 부족한 채점기와 매번 다른 재작성 fixture 또는 공백만 다른 재작성 fixture다.
- WHEN: 그래프를 실행한다.
- THEN: 전자는 총 검색 `3`, 재작성 `2`회이고 후자는 검색 `1`, 재작성 `1`회다.

**AC-04 · 2단 채점**
- GIVEN: 대상 일치·불일치 청크와 가짜 일괄 LLM 채점기가 있다.
- WHEN: scorer를 실행한다.
- THEN: 불일치 청크는 LLM에 전달되지 않고, 일치한 "해당 사항 없음" 청크는 relevant면 남는다.

**AC-05 · 근거 기반 writer**
- GIVEN: 일치하고 relevant인 청크와 checklist item이 있다.
- WHEN: writer를 실행한다.
- THEN: 초안 인용에는 해당 `chunk_id`가 있고, 근거 없는 항목은 `needs_check`다.

**AC-06 · 검색 0건**
- GIVEN: 대상이 확정됐고 retriever가 빈 목록을 반환한다.
- WHEN: 그래프를 실행한다.
- THEN: `outcome=refused`, `refusal_reason=ITEM_NOT_DISCLOSED`다.
