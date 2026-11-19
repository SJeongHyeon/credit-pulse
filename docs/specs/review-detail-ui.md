# Spec - Review Detail UI (심사 건 상세·질의 화면)

> 상세 화면의 로고·글꼴·카드 색과 승인 항목 제목 배치는 [시각 정렬 스펙](review-detail-visual-alignment.md)을 따른다. 아래의 초기 밝은 카드·모노스페이스 표시 지침보다 우선한다.

심사 건 목록(reviews-web-ui) 다음 화면. 계약 근거: SPEC.md §3.1~3.3(항목·판정·응답), §3.5(심사 건 상태), §4(질의 50건 상한), §6(API 목록·오류 본문). 요청·응답 상세는 SDD §9를 따르지만 이 저장소에는 없어 아래 "응답 형태(제안)"으로 먼저 만든다.

## Why

- **페르소나**: 기업여신 심사역. 심사 건(회사 + 사업연도)을 정한 뒤 체크리스트 6개 항목을 공시 근거로 확인한다.
- **상황**: 목록 화면에서 심사 건을 만들 수는 있지만 질의를 보내고 답을 볼 화면이 없다. 이 화면이 제품의 핵심 장면(질문 → 근거 있는 초안)이다.
- **문제**:
  1. 질의를 보낼 곳이 없어 G1(데모 계정으로 핵심 시나리오 끝까지 보기)을 진행할 수 없다.
  2. 답을 믿으려면 항목별 판정과 인용 원문(장·주석번호·인용문·검증 결과)을 한 화면에서 봐야 한다. 거절(`refused`)은 오류가 아니라 정상 응답이라 이유를 알려 줘야 한다.
  3. 백엔드(`/api/v1/reviews/{id}`, `/queries`)가 아직 `main`에 없어 계약만 보고 먼저 만든다.
- **측정 지표**: 질의 보내기 → 답·판정·인용 확인 흐름을 자동화 테스트와 브라우저(가짜 응답)에서 각 1회 이상 확인. 화면에 비밀값·서버 오류 세부 정보 노출 0건.

## Goal

- **해결 목표**: 로그인한 사용자가 `/reviews/{id}`에서 심사 건 정보를 보고 질문을 보내 답(`answer`)·항목별 판정(`draft[]`)·인용을 확인한다.
- **추가 질문**: 같은 회사·사업연도의 심사 건에서 질문과 응답을 보낸 순서대로 보여 준다. 대화는 화면을 열어 둔 동안 유지되며, 다시 열면 현재 승인 대기 질문과 초안을 복원한다. 이전 답변을 새 질의에 합치지 않는다. 열린 건은 기존 질의 API로 초안을 만들고, 승인 대기 중에는 `queries:follow-up`으로 별도 분석을 받아 기존 초안을 유지한다. 심사역은 답변을 참고해 초안을 직접 고치거나 승인한다. 승인으로 종료된 건은 새 질문을 받지 않는다.

### 추가 답변의 판정과 인용 표시

- **Why** — 심사역이 추가 답변에서 공시 인용 확인과 금액의 시점 확인을 구분할 수 있어야 한다.
- **Goal** — 당기·전기 비교 회귀 사례에서 불확실한 금액을 확정 표시하지 않는다. 적용 범위는 승인 대기 중 추가 답변과 상세 화면의 인용 배지다.
- **What** — 추가 답변이 확인을 요청하면 `확인 필요`로 표시한다. 사업연도·당기·전기 판별이 불명확하다는 이유가 나오면 금액을 보류한 이유와 인용을 보여 준다. 인용 배지에는 `인용 검증 통과`처럼 검증 대상을 적는다.
- **How** — `queries:follow-up` 결과의 항목 사유를 검사해 응답 직전에 판정을 보정한다. 당기 말만 물었을 때 인용의 명시된 전기 구간에만 나온 금액도 보류한다. 저장된 승인 초안과 데모 재생 데이터는 그대로 둔다. 여러 체크리스트 항목에 걸쳐 정식 항목을 특정할 수 없으면 추가 답변 제목을 사용한다.
- **AC** — 승인 대기 중 첫 답변이 있는 상태에서 당기·전기 구분을 확인해 달라는 추가 답변을 받으면 `확인 필요`와 금액 보류 안내, `인용 검증 통과` 배지를 표시하고 첫 초안을 유지한다.
- **성공 기준**:
  1. 목록에서 심사 건을 누르면 상세 화면으로 이동한다.
  2. 질문을 보내면 `outcome`에 따라 답변 / 거절 사유 / 승인 대기 안내 중 하나를 보인다.
  3. `draft[]`를 항목 이름·판정 라벨·이유·인용(장, 주석번호, 인용문, 검증 통과 여부)으로 보인다.
  4. 질의 중·`awaiting_approval`·`closed` 상태에서는 질문을 보낼 수 없다.
  5. 비로그인(401)이면 로그인 화면으로, 없는 심사 건(404)이면 안내 문구를 보인다.
  6. 이 화면의 자동화 테스트가 통과한다.
- **Out of Scope**: 승인·취소 화면과 `approvals` 호출(다음 작업), 인용 원문 전체 보기(`/chunks`), 심사 종료(`:close`), 메모 되돌리기, 규칙 관리, 질의 이력 목록(API 미확정), 백엔드 구현, 디자인 시스템 변경.

## What

### Happy Path

1. 사용자가 목록에서 심사 건을 눌러 `/reviews/{id}`로 들어온다.
2. 화면이 `GET /api/v1/auth/me`로 세션을 확인하고, `GET /api/v1/reviews/{id}`로 회사명·사업연도·상태·질의 수를 보인다.
3. 사용자가 질문을 입력하고 「질문하기」를 누른다.
4. 화면이 `POST /api/v1/reviews/{id}/queries`를 보낸다. 요청 중에는 버튼을 막고 「분석 중…」을 보인다.
5. 200 응답의 `outcome=answered`이면 답변과 항목별 판정·인용을 보이고, 질의 수를 1 늘린다.

### Edge Cases

| # | 상황 | 처리 방식 |
|---|---|---|
| EC-01 | `outcome=refused` | HTTP는 200이다. 「답변할 수 없는 질문입니다」와 `refusal_reason`별 안내 문구를 보인다(아래 표). 판정 목록은 비운다. |
| EC-02 | `outcome=awaiting_approval` | 답변과 판정 초안을 보이고 「승인 대기 중입니다. 승인 화면은 준비 중입니다.」를 보인다. 질문 입력은 막는다. |
| EC-03 | 알 수 없는 `outcome`·`refusal_reason`·`verdict`·`item` | 「알 수 없음」 계열 문구로 보이고 화면은 깨지지 않는다. |
| EC-04 | 인용 `verification.passed=false` | 「검증 실패」 표시와 함께 `failed_checks`의 코드는 보이지 않고 개수만 보인다. |
| EC-05 | 빈 질문(공백만)·500자 초과 | 요청을 보내지 않고 안내 문구를 보인다. |
| EC-06 | 심사 건 상태에 따라 질문 방식이 달라짐 | `open`은 일반 질의, `awaiting_approval`은 추가 질의를 보낸다. `running`·`saving`·`closed`에서는 전송을 막는다. |
| EC-07 | `query_count`가 50 이상 | 질문 입력을 막고 「질의 한도(50건)에 도달했습니다」를 보인다. |
| EC-08 | 401 | 로그인 화면(`/`)으로 이동한다. |
| EC-09 | 404 | 「심사 건을 찾을 수 없습니다」와 목록으로 가는 링크를 보인다. 다른 사용자의 건도 같다. |
| EC-10 | 409(상태 충돌)·429 | 409는 「지금은 질문할 수 없는 상태입니다. 화면을 새로 불러옵니다」 후 심사 건을 다시 불러온다. 429는 「요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.」 |
| EC-11 | 네트워크 단절·5xx·그 밖의 응답 | 각각 목록 화면과 같은 문구. 입력한 질문은 지우지 않는다. |
| EC-12 | 질문·답변·이유·인용문에 `<`, `&` | 글자 그대로 보인다(HTML로 해석하지 않는다). |
| EC-13 | 질의를 연속으로 누름 | 요청 중에는 버튼을 비활성화한다. |
| EC-14 | `applied_rules`가 비어 있거나, 없거나, 배열이 아님 | 규칙 영역을 숨긴다. 답변·판정 표시는 그대로. |
| EC-15 | `outcome=refused` | 규칙 영역을 숨긴다(거절에는 초안이 없음). |
| EC-16 | 규칙의 `item`이 알 수 없는 값, 지시에 `<`·`&` | 「알 수 없음」 라벨, 지시는 글자 그대로 보인다. |

`refusal_reason` 안내 문구:

| 값 | 문구 |
|---|---|
| `OUT_OF_DOMAIN` | 기업여신 심사 체크리스트와 관련 없는 질문입니다. |
| `CORP_NOT_IN_CORPUS` | 이 회사의 공시가 아직 수집되지 않았습니다. |
| `ITEM_NOT_DISCLOSED` | 해당 항목에 대한 공시 근거를 찾지 못했습니다. |
| `PERIOD_NOT_IN_CORPUS` | 요청한 사업연도의 공시가 수집되지 않았습니다. |
| `PRIOR_YEAR_NOT_IN_CORPUS` | 비교할 전년도 공시가 수집되지 않았습니다. |

항목 라벨: `litigation` 소송, `guarantee_collateral` 보증·담보, `customer_concentration` 거래처 집중, `fx_raw_material` 환율·원자재, `borrowing_structure` 차입 구조, `contingent_liability` 우발부채. 판정 라벨: `applicable` 해당, `not_applicable` 비해당, `needs_check` 확인필요.

## How

- **화면 경로**: `GET /reviews/{review_id}`는 `backend/templates/review_detail.html`을 반환한다(쉘 HTML은 공개, 인증은 API가 판단). `review_id`는 템플릿에 넣지 않고 화면 JS가 `location.pathname`에서 읽는다(UUID 형식이 아니면 404 안내).
- **정적 자산**: `backend/static/review-detail.js`(컨트롤러 팩토리 `createReviewDetailController({document, fetch, navigate, reviewId})`), `backend/static/review-detail.css`, 공통 `login.css`·`theme.css`. 목록 화면(`reviews.js`)은 각 심사 건 제목을 `/reviews/{id}` 링크로 바꾼다.
- **API 사용**
  - 세션: `GET /api/v1/auth/me`.
  - 심사 건: `GET /api/v1/reviews/{id}` → 목록의 심사 건과 같은 객체 하나.
  - 질의: `POST /api/v1/reviews/{id}/queries`, body `{"question": string}` → SPEC §3.3 응답(`request_id, review_id, outcome, refusal_reason, answer, draft[], applied_rules[], trace`).
- **응답 형태**: 질의 요청 필드는 `question`(화면에서 최대 500자)이다. 질의 후 상세를 다시 불러 `status`·`query_count`를 갱신한다. 작성기에 넘긴 규칙(SPEC §4 상위 5개)은 「이번 초안에 참고한 내 규칙」으로 표시한다. 첫 초안은 `applied_rules` 응답이나 `pending_applied_rules` 체크포인트 조회값을 상단 승인 카드에 표시한다. 추가 질문의 `applied_rules`는 해당 분석 결과에 표시되며 화면을 다시 열면 이전 추가 답변과 함께 사라진다.
- **입력 검증**: `question`은 앞뒤 공백 제거·연속 공백 하나로 줄인 뒤 1~500자. 위반 시 「질문을 1~500자로 입력하세요」.
- **보안 제약**: 서버가 준 문자열은 모두 `textContent`로만 넣고 `innerHTML`은 쓰지 않는다. `request_id`·`trace`·`failed_checks` 코드·서버 오류 본문은 출력하지 않는다. 같은 출처 `fetch`와 `credentials: "same-origin"`만 쓴다. 인용문(`quote`)은 길이 제한 없이 보이되 접힘(`<details>`) 안에 둔다.
- **디자인**: 기존 토큰·글래스 패널·pill 버튼(`login.css`, `theme.css`, `reviews.css` 스타일)을 그대로 쓴다. 판정은 색만으로 구분하지 않고 글자 라벨을 함께 보인다. 판정 배지 글자·배경 대비는 4.5:1 이상. 터치 대상 최소 2.75rem. 320px에서 가로 스크롤이 없다.
- **변경 파일 범위**: `backend/main.py`(`GET /reviews/{review_id}` 한 개), `backend/templates/review_detail.html`, `backend/static/review-detail.{js,css}`, `backend/static/reviews.js`(링크), `tests/test_review_detail_web_ui.py`, `tests/review_detail_web_ui.test.mjs`, `tests/reviews_web_ui.test.mjs`(링크 확인 한 건), 이 Spec. (참고한 규칙 표시, AC-17·18) `backend/templates/review_detail.html`, `backend/static/review-detail.{js,css}`, `tests/review_detail_web_ui.test.mjs`, `tests/test_review_detail_web_ui.py`.

## AC (Given-When-Then)

**AC-01 · 화면 제공**
- GIVEN: 브라우저
- WHEN: `GET /reviews/{임의 id}`
- THEN: 심사 건 정보 영역, 오류 영역(`role="alert"`), 질문 입력·제출 버튼, 답변·판정 결과 영역, 목록으로 가는 링크가 있는 HTML을 200으로 받는다. 외부 리소스·인라인 스크립트가 없다.

**AC-02 · 목록에서 이동**
- GIVEN: 목록에 심사 건이 표시됨
- WHEN: 회사명 링크를 봄
- THEN: 링크 주소가 `/reviews/{id}`다(id는 서버 값 그대로, 텍스트로만 조립되어 요소로 해석되지 않는다).

**AC-03 · 심사 건 정보**
- GIVEN: 심사 건 API 200
- WHEN: 화면이 열림
- THEN: 회사명·사업연도·상태 라벨·질의 수(`n / 50`)·한국 시간 생성일을 보이고 사용자 이름을 상단에 보인다.

**AC-04 · 답변 표시**
- GIVEN: 질의 응답 `outcome=answered`, `draft` 2개
- WHEN: 질문을 보냄
- THEN: 요청 본문은 `{question(정규화됨)}`다. `answer`와 항목별 라벨·판정 라벨·이유를 서버 순서대로 보이고, 심사 건 정보를 다시 불러 질의 수를 갱신한다.

**AC-05 · 인용 표시**
- GIVEN: 판정 하나에 인용 2개(검증 통과 1, 실패 1)
- WHEN: 결과 표시
- THEN: 인용마다 장·주석번호·`rcept_no`를 보이고 인용문은 접힘 안에 텍스트로 둔다. 통과/실패 라벨이 글자로 보이며 실패에는 실패 개수만 보인다.

**AC-06 · 거절 응답**
- GIVEN: HTTP 200, `outcome=refused`, 각 `refusal_reason` 값
- WHEN: 응답을 받음
- THEN: 표의 안내 문구를 보이고 판정 목록은 비어 있다. 알 수 없는 사유는 일반 거절 문구를 보인다. 오류 영역(`role="alert"`)은 쓰지 않는다.

**AC-07 · 승인 대기**
- GIVEN: `outcome=awaiting_approval`
- WHEN: 응답을 받음
- THEN: 답변·판정 초안을 보이고 승인 대기 안내를 보이며 질문 입력이 막힌다. 승인 호출은 하지 않는다.

**AC-08 · 질문 불가 상태**
- GIVEN: 심사 건 `status`가 `open`이 아니거나 `query_count ≥ 50`
- WHEN: 화면이 열림
- THEN: 입력과 버튼이 비활성화되고 이유 문구를 보인다. 제출해도 요청이 나가지 않는다.

**AC-09 · 입력 선검증**
- GIVEN: 공백만 있는 질문 또는 500자 초과
- WHEN: 제출
- THEN: 요청을 보내지 않고 「질문을 1~500자로 입력하세요」를 보인다.

**AC-10 · 세션 만료와 없는 건**
- GIVEN: 어느 요청이든 401 / 심사 건 요청이 404
- WHEN: 응답을 받음
- THEN: 401은 `/`로 이동한다. 404는 안내 문구와 목록 링크를 보이고 질문 영역은 숨긴다.

**AC-11 · 오류 구분과 입력 유지**
- GIVEN: 질의 요청에서 fetch 예외, 5xx, 429, 409, 그 밖의 응답
- WHEN: 응답을 받음
- THEN: EC-10·11의 해당 문구를 보이고 버튼을 다시 활성화하며 입력한 질문은 지우지 않는다(409는 심사 건을 다시 불러온다). 서버 본문의 `details`·`request_id`는 보이지 않는다.

**AC-12 · 심사 건 불러오기 실패**
- GIVEN: 심사 건 요청이 fetch 예외·5xx·그 밖의 응답
- WHEN: 응답을 받음
- THEN: 오류 문구와 「다시 불러오기」를 보이고 누르면 다시 요청한다.

**AC-13 · HTML 비해석**
- GIVEN: 질문·답변·이유·인용문·회사명이 `<img src=x onerror=alert(1)>`
- WHEN: 표시
- THEN: 글자 그대로 텍스트로 보이고 요소로 만들어지지 않는다.

**AC-14 · 알 수 없는 값**
- GIVEN: 알 수 없는 `outcome`·`item`·`verdict`·`status`
- WHEN: 표시
- THEN: 「알 수 없음」으로 보이고 화면은 깨지지 않는다.

**AC-15 · 스타일·접근성**
- GIVEN: 화면 CSS
- WHEN: 검사
- THEN: 글래스 패널·pill 버튼·모노 라벨은 다른 화면과 같은 토큰을 쓰고, 판정 배지 대비 ≥ 4.5:1, 버튼·입력 높이 ≥ 2.75rem, `prefers-reduced-motion`을 따른다. 320~1280px에서 가로 스크롤이 없다(브라우저 확인).

**AC-16 · 비밀·외부 리소스**
- GIVEN: 이 화면의 HTML·CSS·JS
- WHEN: 검사
- THEN: 외부 URL·`innerHTML`·`eval`·비밀값이 없다. LLM 호출 코드가 없다.

**AC-17 · 참고한 규칙 표시**
- GIVEN: 질의 응답의 `applied_rules` 또는 승인 대기 상세의 `pending_applied_rules`에 규칙 2개
- WHEN: 분석 결과를 표시하거나 승인 대기 초안을 다시 연다
- THEN: 첫 초안의 규칙은 상단 승인 카드에, 추가 질문의 규칙은 해당 분석 결과에 표시한다. 「이번 초안에 참고한 내 규칙」 아래에 항목 라벨·지시를 서버 순서대로 텍스트로 보이고 「내 판정 규칙에서 관리」 링크(`/reviews#rules-section`)를 둔다.

**AC-18 · 규칙 없음·거절**
- GIVEN: `applied_rules: []`, 필드 없음, 또는 `outcome=refused`
- WHEN: 결과 표시
- THEN: 규칙 영역이 숨겨지고 나머지 표시는 AC-04·AC-06과 같다.
