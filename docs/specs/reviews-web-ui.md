# Spec - Reviews Web UI (심사 건 목록·생성 화면)

WBS W3.9(web_ui 뼈대: 로그인·심사 건 화면). 계약 근거: SDD §9.1·검증 규칙 매트릭스(`corp_query`, `fiscal_year`, `page_size`, `cursor`), SPEC.md §3(`reviews`).

## Why

- **페르소나**: 기업여신 심사역. 로그인한 뒤 어느 회사의 어느 사업연도를 심사할지 정해야 한다.
- **상황**: 질의·승인은 모두 "심사 건"(회사 + 사업연도 확정) 단위로 일어난다. 로그인 화면은 끝났지만 로그인 뒤에 갈 곳이 없다.
- **문제**:
  1. 로그인해도 심사 건을 만들거나 고를 화면이 없어 G1(평가용 계정으로 핵심 시나리오를 끝까지 보기)을 시작할 수 없다.
  2. 같은 이름의 회사가 여럿이면(동명·약칭) 심사 건 생성 때 한 번만 확정해야 하는데(SDD §5), 이를 고르는 화면이 없다.
  3. `/api/v1/reviews` 백엔드가 W6.1~4(11/5)에야 들어오므로, 화면은 계약만 보고 먼저 만들어 두어야 일정이 겹치지 않는다.
- **측정 지표**: 데모 계정으로 심사 건 생성 → 목록에서 확인하는 흐름을 자동화 테스트와 브라우저에서 각 1회 이상 확인한다. 화면에 비밀값·서버 오류 세부 정보 노출 0건.

## Goal

- **해결 목표**: 로그인한 사용자가 `/reviews`에서 내 심사 건 목록을 보고, 회사명(또는 corp_code)과 사업연도로 새 심사 건을 만든다.
- **성공 기준**:
  1. `GET /api/v1/reviews`의 `items`를 서버가 준 순서 그대로 표시하고, `next_cursor`가 있으면 「더 보기」로 이어 붙인다.
  2. 새 심사 건 생성(201) 뒤 응답의 심사 건을 목록 맨 위에 추가한다.
  3. 입력 검증 문구는 SDD 검증 규칙 매트릭스의 문구와 같다. 잘못된 입력은 요청을 보내지 않는다.
  4. 회사가 모호하면(422 `VALIDATION_AMBIGUOUS_CORP`) 후보 중 하나를 골라 같은 요청을 corp_code로 다시 보낸다.
  5. 비로그인(401)이면 로그인 화면(`/`)으로 보낸다.
  6. 이 화면의 자동화 테스트가 통과한다.
- **Out of Scope**: `/api/v1/reviews` 백엔드 구현(W6.1~4), 심사 건 상세·질의·승인·원문 보기·규칙 화면, 심사 종료(`:close`) 버튼, 디자인 시스템 도입, 인증 API 계약 변경. (로그인 직후 이동은 login-web-ui Spec의 「로그인 이동」으로 처리한다.)

## What

### Happy Path

1. 로그인한 사용자가 로그인 직후 이동(login-web-ui AC-02) 또는 `/reviews` 주소로 들어온다.
2. 화면이 `GET /api/v1/auth/me`로 세션을 확인해 상단에 사용자 이름을 보이고, 이어서 `GET /api/v1/reviews?page_size=20`으로 목록을 불러와 표시한다.
3. 사용자가 회사명(또는 8자리 corp_code)과 사업연도(2024·2025)를 고르고 「심사 건 만들기」를 누른다.
4. 화면이 `POST /api/v1/reviews`를 보내고, 201 응답의 심사 건을 목록 맨 위에 추가한 뒤 회사 입력칸을 비운다.
5. 사용자가 「로그아웃」을 누르면 `POST /api/v1/auth/logout` 호출 뒤 로그인 화면으로 이동한다.

### Edge Cases

| # | 상황 | 처리 방식 |
|---|---|---|
| EC-01 | 목록이 비어 있음 | 「아직 심사 건이 없습니다. 아래에서 새로 만들어 보세요.」를 보인다. |
| EC-02 | `next_cursor`가 있음 | 「더 보기」를 보이고, 누르면 `cursor`로 다음 쪽을 불러와 뒤에 이어 붙인다. 없으면 숨긴다. |
| EC-03 | 어느 API든 401 | 로그인 화면(`/`)으로 이동한다. |
| EC-04 | 422 `VALIDATION_AMBIGUOUS_CORP` | `error.details`의 후보(`corp_code`, `corp_name`)를 버튼 목록으로 보이고, 선택하면 그 corp_code로 같은 사업연도를 다시 요청한다. 후보가 없거나 형식이 다르면 일반 오류 문구를 보인다. |
| EC-05 | 422 `VALIDATION_CORP_NOT_FOUND` | 「코퍼스에 없는 회사입니다: {입력한 값}」을 보인다. 입력값은 화면이 가진 값이고 서버 본문이 아니다. |
| EC-06 | 400 `VALIDATION_ERROR`(선검증을 통과했는데 서버가 거절) | 「입력을 확인한 뒤 다시 시도해 주세요.」 |
| EC-07 | 네트워크 단절 | 「서버에 연결할 수 없습니다. 네트워크를 확인하고 다시 시도해 주세요.」 입력값은 지우지 않는다. |
| EC-08 | 5xx | 「서버에 문제가 있어 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.」 입력값은 지우지 않는다. |
| EC-13 | 429(요청 과다) | 「요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.」 목록 불러오기도 같은 문구를 쓰고 「다시 불러오기」를 보인다. 입력값은 지우지 않는다. |
| EC-09 | 그 밖의 응답(404 포함: 백엔드 미배포 시) | 목록은 「심사 건 목록을 불러오지 못했습니다.」+ 「다시 불러오기」, 생성은 「심사 건을 만들지 못했습니다. 잠시 후 다시 시도해 주세요.」 |
| EC-10 | 제출·더 보기를 연속으로 누름 | 요청 중에는 해당 버튼을 비활성화한다. |
| EC-11 | 알 수 없는 `status` 값 | 「알 수 없음」으로 표시하고 화면은 깨지지 않는다. |
| EC-12 | 회사명에 `<`, `&` 같은 문자 | 모두 글자 그대로 보인다(HTML로 해석하지 않는다). |

## How

- **화면 경로**: `GET /reviews`는 유효한 세션을 검증한 뒤 Jinja2 템플릿 `backend/templates/reviews.html`을 반환한다. 세션이 없거나 유효하지 않으면 안내가 있는 첫 화면으로 이동한다.
- **정적 자산**: `backend/static/reviews.js`, `backend/static/reviews.css`. 공통 바탕 스타일은 기존 `login.css`를 함께 불러온다.
- **API 사용**
  - 세션: `GET /api/v1/auth/me`(200/401).
  - 목록: `GET /api/v1/reviews?page_size=20[&cursor=<next_cursor>]`.
  - 생성: `POST /api/v1/reviews`, body `{"corp_query": string, "fiscal_year": integer}`.
  - 로그아웃: `POST /api/v1/auth/logout`(204).
- **응답 형태(제안 — SDD에 없어 백엔드 담당 확인이 필요하다)**
  - 심사 건: `{ "id", "corp_code", "corp_name", "fiscal_year", "status", "query_count", "created_at", "updated_at" }`. 필드는 SDD `reviews` 테이블과 `documents.corp_name`에서 가져왔다. 시각은 UTC ISO 8601.
  - 목록 200: `{ "items": [심사 건, ...], "next_cursor": string | null }`.
  - 생성 201: 심사 건 한 개.
  - 422 후보: `{ "error": { "code": "VALIDATION_AMBIGUOUS_CORP", "details": [ { "corp_code", "corp_name" } ], ... } }`.
  - 위 형태가 바뀌면 이 Spec과 `reviews.js`의 파싱만 고친다.
- **입력 검증(클라이언트 선검증, SDD 매트릭스와 같은 문구)**
  - `corp_query`: 앞뒤 공백 제거·연속 공백을 하나로 줄인 뒤 1~50자. 위반 시 「회사명 또는 corp_code(8자리)를 입력하세요」.
  - `fiscal_year`: 2024 또는 2025인 정수. 위반 시 「사업연도는 2024~2025만 가능합니다」.
- **표시**: 상태 라벨은 `open` 진행 가능 / `running` 처리 중 / `awaiting_approval` 승인 대기 / `saving` 저장 중 / `closed` 종료. 시각은 한국 시간(`Asia/Seoul`)으로 `YYYY-MM-DD HH:mm` 형식.
- **보안 제약**
  - 서버가 준 문자열은 모두 `textContent`로만 넣고 `innerHTML`은 쓰지 않는다.
  - 서버 오류의 `details`(후보 목록은 EC-04 한정)·`request_id`·내부 메시지는 화면에 출력하지 않는다.
  - 비밀번호·세션 토큰은 이 화면에서 다루지 않는다. 같은 출처 `fetch`와 `credentials: "same-origin"`만 쓴다.
- **변경 파일 범위**: `backend/main.py`(`GET /reviews` 한 줄), `backend/templates/reviews.html`, `backend/templates/login.html`(링크 한 개), `backend/static/reviews.js`, `backend/static/reviews.css`, `tests/test_reviews_web_ui.py`, `tests/reviews_web_ui.test.mjs`, 이 Spec.

## AC (Given-When-Then)

**AC-01 · 화면 제공**
- GIVEN: 유효한 세션이 있는 브라우저
- WHEN: `GET /reviews`
- THEN: 심사 건 목록 영역, 오류 영역(`role="alert"`), 회사 입력·사업연도 선택(2024, 2025)·제출 버튼, 로그아웃 버튼이 있는 HTML을 200으로 받는다. 로그인 화면에는 `/reviews`로 가는 링크가 있다.

**AC-02 · 목록 표시**
- GIVEN: 로그인 상태이고 목록 API가 심사 건 2개를 반환
- WHEN: 화면이 열림
- THEN: 서버 순서대로 회사명·사업연도·상태 라벨·질의 수·한국 시간 생성일을 보인다.

**AC-03 · 빈 목록**
- GIVEN: 목록 API가 `items: []`
- WHEN: 화면이 열림
- THEN: 빈 목록 안내 문구를 보이고 「더 보기」는 숨긴다.

**AC-04 · 더 보기**
- GIVEN: 응답에 `next_cursor`가 있음
- WHEN: 「더 보기」를 누름
- THEN: `cursor`를 붙여 요청하고 받은 항목을 뒤에 이어 붙이며, `next_cursor`가 null이면 버튼을 숨긴다.

**AC-05 · 생성 성공**
- GIVEN: 유효한 `corp_query`와 사업연도, 생성 API가 201
- WHEN: 제출
- THEN: 응답의 심사 건이 목록 맨 위에 추가되고 회사 입력칸이 비워진다. 요청 본문은 `{corp_query(정규화됨), fiscal_year(정수)}`다.

**AC-06 · 입력 선검증**
- GIVEN: 빈 입력(공백만 포함), 50자 초과, 허용 범위 밖 사업연도 중 하나
- WHEN: 제출
- THEN: 요청을 보내지 않고 SDD 매트릭스 문구를 보인다.

**AC-07 · 모호한 회사 선택**
- GIVEN: 생성 API가 422 `VALIDATION_AMBIGUOUS_CORP`와 후보 2개를 반환
- WHEN: 후보 하나를 선택
- THEN: 그 후보의 `corp_code`를 `corp_query`로, 같은 사업연도로 다시 요청하고 성공하면 AC-05와 같이 처리한다.

**AC-08 · 코퍼스에 없는 회사**
- GIVEN: 생성 API가 422 `VALIDATION_CORP_NOT_FOUND`
- WHEN: 응답을 받음
- THEN: 「코퍼스에 없는 회사입니다: {입력한 값}」을 보이고 서버 본문의 `details`·`request_id`는 보이지 않는다.

**AC-09 · 세션 만료**
- GIVEN: `me`·목록·생성·더 보기 중 어느 요청이든 401
- WHEN: 응답을 받음
- THEN: 로그인 화면(`/`)으로 이동한다.

**AC-10 · 오류 구분과 입력 유지**
- GIVEN: 생성 요청에서 fetch 예외, 5xx, 429, 그 밖의 응답 중 하나
- WHEN: 응답을 받음
- THEN: EC-07~09·EC-13의 해당 문구를 보이고, 제출 버튼을 다시 활성화하며, 입력한 회사명은 지우지 않는다. 문구에 입력값 외의 서버 정보는 없다.

**AC-11 · 목록 불러오기 실패**
- GIVEN: 목록 요청이 fetch 예외·5xx·404
- WHEN: 응답을 받음
- THEN: 목록 오류 문구와 「다시 불러오기」를 보이고, 누르면 목록을 다시 요청한다.

**AC-12 · HTML 비해석**
- GIVEN: 회사명이 `<img src=x onerror=alert(1)>`인 심사 건
- WHEN: 목록에 표시
- THEN: 글자 그대로 텍스트로 보이고 요소로 만들어지지 않는다.

**AC-13 · 로그아웃**
- GIVEN: `/reviews` 화면
- WHEN: 「로그아웃」을 누르고 API가 204
- THEN: 로그인 화면(`/`)으로 이동한다. 실패하면 이 화면에 머물며 로그아웃 실패 문구를 보인다.

**AC-14 · 사용자 이름 표시**
- GIVEN: `/api/v1/auth/me`가 200이고 본문에 문자열 `display_name`이 있음
- WHEN: 화면이 열림
- THEN: 상단에 「{display_name}님」을 텍스트로 표시한다. `display_name`이 없거나 문자열이 아니면 아무것도 표시하지 않고 화면은 그대로 동작한다.
