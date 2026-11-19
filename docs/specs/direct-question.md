# Spec — 회사 선택 없이 바로 질문 (2026-11-13)

> 결론: 심사 목록 화면에서 회사·사업연도를 고르는 폼을 없애고 **질문만 적어 보낸다**. 서버가 질문에서 회사·연도를 찾아 심사 건을 만들고, 찾지 못하면 심사 건 없이 거절이나 되묻기를 돌려준다. DB 구조는 바꾸지 않는다.

## Why

- **페르소나**: 기업여신 심사역, 그리고 배포 주소로 평가셋 50건을 넣어 보는 평가자
- **상황**: 로그인한 뒤 질문을 하나 넣어 답과 근거를 본다.
- **문제**:
  - 회사·연도를 먼저 골라 심사 건을 만들어야만 질문할 수 있었다. 코퍼스에 없는 회사·기간을 묻는 질문과 회사 이름이 없는 질문은 **넣을 방법이 없었다**.
  - 그래서 배포 주소로는 평가셋 50건 중 34건만 보낼 수 있었고(EVAL_REPORT 8장), 거절 지표의 분모가 로컬 평가(25)와 달랐다(10).
  - 11/13 점검 의견: 회사를 고르게 하지 말고 바로 질문하게 할 것. 지금 구조로는 평가셋 50건을 다 넣을 수 없다.
- **측정 지표**: 배포 주소로 보낼 수 있는 평가셋 문항 34 → 50건.

## Goal

- **해결 목표**: 질문 문장 하나만으로 답·거절·되묻기를 받는다. 승인·메모 저장은 지금의 심사 건 흐름을 그대로 쓴다.
- **성공 기준**: 평가셋 50건이 모두 HTTP 200으로 끝난다. 회사가 정해지는 질문은 승인 대기까지 가서 심사 건 화면에서 승인할 수 있다. 거절로 끝난 질문은 빈 심사 건을 남기지 않는다.
- **Out of Scope**: DB 스키마 변경(심사 건의 회사·연도는 필수 그대로), 심사 건 상세 화면의 추가 질문 흐름, 심사 건 만들기 API(`POST /api/v1/reviews`) 삭제(스크립트·시험이 쓰므로 남긴다. 화면에서만 뺀다), 랜딩 화면의 '새 심사 건 만들기' 링크 문구.

## What

### API: `POST /api/v1/queries`

요청 `{"question": "<1~1000자>"}` 또는 `{"question": "<되물음에 대한 답>", "clarifies": "<되물음을 받은 원래 질문>"}`(11/17, 사전 등록 162번). 로그인 필요(없으면 401). 질의 한도(분당 5회, 하루 50회, 비용 상한)는 심사 건 질의와 같다.

| 질문 | 서버 동작 | 응답 |
|---|---|---|
| 회사·연도가 하나로 정해진다 | 그 대상으로 심사 건을 만들고 그래프를 돌린다. 그래프에는 대상을 주지 않아 질문에서 다시 찾게 한다(회사 비교 질문은 회사별로 검색) | 승인 대기면 `outcome=awaiting_approval`, `draft[]`, `review={id, corp_name, fiscal_year}`. 심사 건이 남는다 |
| 위와 같지만 거절로 끝난다(근거 없음, 공시 안 됨, 주제 밖) | 방금 만든 심사 건을 지운다 | `outcome=refused`, `refusal_reason`, `review=null` |
| 대상이 정해지지 않는다(코퍼스 밖 회사·기간, 접수연도만 있음, 사업연도 둘 이상, 회사 이름 없음) | 심사 건 없이 그래프를 돌린다. 그래프는 입구에서 거절하거나 되묻고 끝난다 | `outcome=refused`+`refusal_reason`, 또는 `outcome=answered`+`target_required=true`. 둘 다 `refusal_message`(안내 문구), `review=null` |
| 외부 호출 실패 | 방금 만든 심사 건을 지운다 | 기존 질의와 같은 오류(502·503·504) |
| `clarifies`가 있다(되물음에 대한 답) | 답이 회사·연도만 적은 짧은 글이면 "답 + 원래 질문"으로 합쳐 위 표의 처음부터 다시 처리한다(입구 검사·주제 판정·검색을 모두 다시 거친다). 답에 물음표가 있거나 80자를 넘으면 새 질문으로 보고 합치지 않는다. 합친 글이 1,000자를 넘으면 그래프를 부르지 않는다 | 위 행들과 같다. 길이 초과는 422 `VALIDATION_ERROR` |

- 응답의 나머지 필드(`applied_rules[]`, 거절 뉴스 확장 필드)는 `POST /api/v1/reviews/{id}/queries`와 같다.
- 대상 찾기는 그래프 입구와 같은 함수를 같은 순서로 쓴다(`classify_question` → 사업연도 개수 → `resolve_target`). 그래서 '대상이 정해졌다'고 본 질문이 그래프에서 다른 대상이 되지 않는다.
- 심사 건 없이 돌린 질문이 승인 대기까지 가면 이어 갈 곳이 없으므로 오류로 멈춘다(위 순서가 같아 일어나지 않아야 한다. 방어용).
- 하루 한도: 심사 건이 생기는 질문은 그 심사 건의 한도(20회)에, 심사 건 없이 끝나는 질문은 사용자별 칸 `direct:<user_id>`(20회)에 센다.

### 화면: 심사 목록(`/reviews`)

- '새 심사 건 만들기'(회사 입력, 사업연도 선택, 후보 선택)를 없애고 '질문하기'(질문 입력 1~500자, 버튼)를 둔다.
- '심사 건' 제목 아래 질문하기, 심사 건 내역, 내 판정 규칙 순으로 배치한다. 내역 제목 행에 정렬과 전체 삭제를 둔다.
- 질문 입력 아래 '질문 예시'에 단기차입금·주요 소송·지급보증을 둔다. 예시를 선택하면 입력란에 문장을 채우고 포커스를 옮긴다. 사용자가 수정한 뒤 질문하기를 눌러 전송한다. 입력란은 상세 화면과 같은 어두운 배경과 금색 포커스 표시를 쓴다.
- 단기차입금 예시는 `data/demo/questions.jsonl`의 오뚜기 질문을 쓴다. 주요 소송과 지급보증 예시는 `evals/results/service/svc_r6_direct.jsonl`에서 각각 근거를 포함한 승인 대기 초안이 나온 v2 문항 3번(GS리테일)과 36번(한화투자증권)을 쓴다.
- 응답에 `review`가 있으면 그 심사 건 화면으로 이동한다(승인 대기 초안이 거기에 보인다).
- `review`가 없으면 이 화면에 안내 문구를 보인다: 서버의 `refusal_message`가 있으면 그것, 없으면 거절 사유별 고정 문구. 거절 응답의 참고 뉴스는 제목 링크로 최대 5건(http·https만 링크).
- 서버 오류 본문은 화면에 쓰지 않는다(상태 코드별 고정 문구).

## How

- `backend/api/reviews.py`: `queries_router`, `ask_directly`, `LangGraphReviewRunner.ask_without_review`, 질의 본문을 `_ask_in_review`·`_guarded_graph_call`로 나눠 두 엔드포인트가 함께 쓴다.
- `backend/api/runtime.py`: `question_target_resolver(corpus_catalog)`를 `ReviewRuntime.resolve_target`에 넣는다.
- `backend/templates/reviews.html`, `backend/static/reviews.js`, `backend/static/reviews.css`
- `scripts/run_service_eval.py --direct`: 평가셋 50건을 모두 `POST /api/v1/queries`로 보낸다.
- LLM 지시문·그래프·검색은 바꾸지 않는다. 그래프에 넘기는 입력이 로컬 평가(`run_graph_eval.py`)와 같아진다(대상 없이 질문만).

## AC (Given-When-Then)

- **AC-01**: GIVEN 코퍼스에 있는 회사와 사업연도가 든 질문 WHEN `POST /api/v1/queries` THEN 심사 건 1건이 그 회사·연도로 생기고 응답에 `review.id`가 있으며 상태가 `awaiting_approval`이다. (`tests/test_direct_query_api.py`, `tests/test_review_runtime.py`)
- **AC-02**: GIVEN AC-01의 질문이 거절로 끝남 WHEN 응답 THEN `review`는 null이고 만든 심사 건이 지워져 있다.
- **AC-03**: GIVEN 코퍼스 밖 회사·기간 또는 회사 이름이 없는 질문 WHEN `POST /api/v1/queries` THEN 심사 건을 만들지 않고 `refusal_reason` 또는 `target_required`와 `refusal_message`를 돌려준다.
- **AC-04**: GIVEN 외부 호출이 실패 WHEN 응답 THEN 만든 심사 건이 지워지고 기존과 같은 오류 코드다.
- **AC-05**: GIVEN 같은 사용자가 1분에 6번 질문 WHEN 6번째 THEN 429.
- **AC-06**: GIVEN 심사 목록 화면 WHEN 연다 THEN 회사 입력·사업연도 선택이 없고 질문 입력만 있다. (`tests/test_reviews_web_ui.py`)
- **AC-07**: GIVEN 질문을 보냄 WHEN 응답에 `review`가 있다 THEN 그 심사 건 화면으로 이동하고, 없으면 안내 문구를 이 화면에 텍스트로 보인다. (`tests/reviews_web_ui.test.mjs`)
- **AC-09**(162번): GIVEN 되물음(`target_required`)을 받은 질문과 회사 이름만 적은 답 WHEN `clarifies`와 함께 `POST /api/v1/queries` THEN 서버는 "답 + 원래 질문"으로 대상을 찾고 그래프를 돌린다. 답이 새 질문이면 합치지 않고, 합친 글이 1,000자를 넘으면 422다. 화면은 되물음을 받은 다음 질문 한 번만 `clarifies`를 붙인다. (`tests/test_direct_query_api.py`, `tests/reviews_web_ui.test.mjs`)
- **AC-08**: GIVEN `run_service_eval.py --direct` WHEN 실행 THEN 평가셋 50건을 모두 보내고, 서비스가 만든 심사 건의 승인 대기를 취소한다. (`tests/test_service_eval.py`)

## 남는 것

- 심사 건 없이 돌린 질문은 체크포인트에 한 번 쓰고 버리는 스레드(`direct-…`)를 남긴다(입구에서 끝나 크기는 작다).
- 랜딩 화면 패널의 링크 문구는 '새 심사 건 만들기' 그대로다(같은 위치의 질문하기로 연결된다).
- 배포 주소에서 50건을 잰 값(11/13, 리비전 00051): 50건 모두 HTTP 200, 정답 근거 23/25·오답 근거 0·올바른 거절 24/25·과잉 거절 0·환각 위험 0, 검색 문항 p50 5.6초·p95 6.9초(EVAL_REPORT 8장, `evals/results/service/svc_r6_direct.*`). 측정 지표 34 → 50건을 채웠다.
