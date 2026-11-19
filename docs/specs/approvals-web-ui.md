# Spec - Approvals Web UI (승인 현황 화면 `/approvals`)

상태: **승인됨**(구현 후 화면을 보고 수정 예정). 화면 구성(문구·배치·색)은 사용자가 화면을 본 뒤 고치기로 했으므로, 아래 AC 중 「화면 구성」으로 표시한 것은 확인 후 바뀔 수 있다. 계약 근거: `docs/specs/review-api.md`(`GET /api/v1/reviews`), `docs/specs/reviews-web-ui.md`(목록 표시·오류 문구), SPEC.md §3 `reviews.status`. 랜딩 03 패널의 「승인 현황」 칸이 이 화면으로 이어진다(`main-page-design.md` 개정 8).

## Why

- **페르소나**: 기업여신 심사역. 질의로 만든 판정 초안을 승인·수정해야 심사 메모가 저장된다.
- **상황**: 승인 대기(`awaiting_approval`) 건이 `/reviews` 목록에 다른 상태와 섞여 있어 어느 건이 내 손을 기다리는지 한눈에 찾기 어렵다.
- **문제**:
  1. 랜딩(`/`)은 로그인한 사용자를 `/reviews`로 바로 보낸다(`login.js`). 그래서 랜딩에 승인 대기 건수를 띄울 수 없고, 승인 현황은 로그인 뒤의 별도 화면이어야 한다.
  2. `GET /api/v1/reviews`에는 `status` 필터가 없다. 상태별로 모아 보려면 화면이 받은 목록에서 걸러야 한다.
- **측정 지표**: 자동화 테스트로 상태 선택·빈 상태·오류 경로를 확인하고, 로컬 브라우저(1440px·375px)에서 1회씩 확인한다. 화면에 서버 오류 세부 정보·비밀값 노출 0건.

## Goal

- **해결 목표**: 로그인한 사용자가 `/approvals`에서 상태 「승인 대기」·「진행 가능」·「처리 중」 중 하나를 골라 그 상태의 내 심사 건만 모아 보고, 건을 눌러 `/reviews/{id}`로 이동한다.
- **성공 기준**:
  1. 처음에는 「승인 대기」가 선택돼 있다.
  2. 세 상태 버튼에 각각 건수가 보이고, 목록은 선택한 상태의 건만 보인다.
  3. 목록은 `GET /api/v1/reviews?page_size=50`을 `next_cursor`로 최대 5쪽(250건)까지 받아 화면에서 거른 것이다. 서버 순서를 유지한다.
  4. 비로그인(401)이면 로그인 화면(`/`)으로 보낸다.
  5. 이 화면의 자동화 테스트가 통과한다.
- **Out of Scope**: 승인·취소 자체(`/reviews/{id}`에서 한다), 서버 `status` 필터·새 API, 「저장 중」·「종료」 상태 보기, 정렬·검색·건수 뱃지의 실시간 갱신(자동 새로고침), 랜딩 칸 안의 건수 표시, 로그인 뒤 `/approvals`로 되돌아오기(로그인 이동은 login-web-ui 그대로 `/reviews`), 알림, 심사 건 생성(`/reviews`에서 한다), 백엔드 API 변경.

## What

### Happy Path

1. 사용자가 랜딩의 「승인 현황」 칸 또는 `/approvals`로 들어온다.
2. 화면이 `GET /api/v1/auth/me`로 사용자 이름을 보이고, `GET /api/v1/reviews?page_size=50`을 `next_cursor`가 없을 때까지(최대 5쪽) 이어 받는다.
3. 「승인 대기」「진행 가능」「처리 중」 버튼과 각 건수를 보이고, 승인 대기 건 목록을 보인다.
4. 사용자가 다른 상태 버튼을 누르면 받아 둔 데이터에서 그 상태의 건만 다시 보인다(다시 요청하지 않는다).
5. 목록의 각 건은 회사명·사업연도·상태·질의 수·생성 시각과 `/reviews/{id}` 링크를 가진다.
6. 「로그아웃」은 `POST /api/v1/auth/logout` 뒤 `/`로 이동한다. 「심사 건 전체 보기」는 `/reviews`로 이동한다.

### Edge Cases

| # | 상황 | 처리 방식 |
|---|---|---|
| EC-01 | 선택한 상태의 건이 0개 | 상태별 문구: 승인 대기 「승인 대기 중인 심사 건이 없습니다.」, 진행 가능 「진행 가능한 심사 건이 없습니다.」, 처리 중 「처리 중인 심사 건이 없습니다.」 |
| EC-02 | 5쪽(250건)을 받고도 `next_cursor`가 남음 | 「최근 250건만 확인했습니다. 전체는 심사 건 화면에서 확인하세요.」와 `/reviews` 링크를 보이고, 건수 옆에도 「이상」을 붙인다(예: 3건 이상) |
| EC-03 | 어느 API든 401 | 로그인 화면(`/`)으로 이동 |
| EC-04 | 네트워크 단절 | reviews-web-ui EC-07 문구, 「다시 불러오기」 |
| EC-05 | 5xx | reviews-web-ui EC-08 문구, 「다시 불러오기」 |
| EC-06 | 429 | reviews-web-ui EC-13 문구, 「다시 불러오기」 |
| EC-07 | 그 밖의 응답(404 포함) | 「승인 현황을 불러오지 못했습니다.」 + 「다시 불러오기」 |
| EC-08 | 여러 쪽 중 중간 쪽에서 실패 | 이미 받은 쪽은 버리고 위 오류 문구를 보인다(일부만 보여 건수가 틀리는 것을 막는다) |
| EC-09 | 알 수 없는 `status` | 세 상태 어디에도 넣지 않는다(건수에도 포함하지 않는다) |
| EC-10 | 회사명에 `<`, `&` 같은 문자 | 글자 그대로 보인다 |
| EC-11 | 「다시 불러오기」를 연속으로 누름 | 요청 중에는 비활성화 |
| EC-12 | JavaScript 꺼짐 | `<noscript>` 안내(reviews.html과 같은 문구) |

## How

- **경로·파일**: `GET /approvals`는 `backend/templates/approvals.html`을 반환한다(쉘 HTML은 공개, 인증은 API가 한다). 새 파일은 `backend/templates/approvals.html`, `backend/static/approvals.js`, `backend/static/approvals.css`, `tests/test_approvals_web_ui.py`, `tests/approvals_web_ui.test.mjs`다. **서버 코드 변경은 `backend/main.py`에 `GET /approvals` 라우트 한 개를 더하는 것뿐**이다(reviews-web-ui가 `/reviews`를 더한 것과 같은 방식). 이 변경은 승인이 필요하다.
- **API 사용(모두 기존)**: `GET /api/v1/auth/me`, `GET /api/v1/reviews?page_size=50[&cursor=]`, `POST /api/v1/auth/logout`. 새 API 없음.
- **JS**: `approvals.js`는 `createApprovalsController({ document, fetch, navigate })`를 내보낸다(`reviews.js`와 같은 주입 구조라 `node --test`로 시험한다). `fetch(`는 이 파일에서만 쓰고 같은 출처·`credentials: "same-origin"`이다. 랜딩 계열 JS(`landing.js`, `stage-tabs.js` 등)의 `fetch(` 금지 테스트는 그대로다. 서버가 준 문자열은 `textContent`로만 넣고 `innerHTML`·외부 주소·`eval`을 쓰지 않는다. 서버 오류의 `details`·`request_id`는 화면에 내지 않는다.
- **상태 선택(화면 구성)**: `role="group"`(`aria-label="상태 선택"`)의 버튼 3개이고 선택된 것은 `aria-pressed="true"`다. 건수는 버튼 글자 안에 넣는다(예: 「승인 대기 3」). 목록이 바뀌면 `#approvals-status`(`role="status"`)에 「승인 대기 3건」을 알린다. 키보드는 Tab·Enter·Space로 충분하다.
- **표시**: 상태 라벨은 reviews.js와 같다(`open` 진행 가능 / `running` 처리 중 / `awaiting_approval` 승인 대기). 시각은 `Asia/Seoul`의 `YYYY-MM-DD HH:mm`이다. 링크 글자는 승인 대기에서만 「승인하러 가기」, 그 외는 「열기」다.
- **스타일**: `login.css`, `theme.css`, `reviews.css`를 불러온 뒤 `approvals.css`를 불러온다. `login.css`와 `reviews.css`에는 전역 `button` 규칙과 `button.secondary` 규칙이 있어 새 버튼의 색을 덮어쓰므로, 상태 선택 버튼과 「다시 불러오기」의 색·테두리는 `.approvals-page`로 범위를 좁힌 규칙으로 정하고 실제 계산된 값을 브라우저에서 확인한다. `.review-item` 계열 목록 모양은 `reviews.css`를 재사용한다. 움직이는 속성은 `transform`·`opacity`뿐이고 `prefers-reduced-motion: reduce`에서는 전환이 없다. 키보드 포커스에 금색 윤곽(`:focus-visible`)이 있다.
- **변경하지 않는 것**: `reviews.html`, `reviews.js`, `reviews.css`, `login.js`, 모든 `/api/v1/**`, 기존 id·class.

## AC (Given-When-Then)

- **AC-01 · 라우트**: GIVEN 앱 WHEN `GET /approvals` THEN 200 HTML이고 `<title>`이 「CREDIT PULSE - 승인 현황」, `h1`이 「승인 현황」이며, `login.css`→`theme.css`→`reviews.css`→`approvals.css` 순서로 불러오고 `approvals.js`를 `type="module"`로 불러온다. `innerHTML`·외부 주소가 템플릿에 없다.
- **AC-02 · 기본 선택·건수**: GIVEN 심사 건이 승인 대기 2, 진행 가능 3, 처리 중 1, 저장 중 1, 종료 1인 응답 WHEN 화면이 열림 THEN 「승인 대기 2」「진행 가능 3」「처리 중 1」 버튼이 있고 승인 대기가 `aria-pressed="true"`이며 목록에는 승인 대기 2건만 서버 순서로 있다. 저장 중·종료·알 수 없는 상태는 어느 건수에도 없다.
- **AC-03 · 상태 전환**: GIVEN AC-02 WHEN 「진행 가능」을 누름 THEN 추가 요청 없이 진행 가능 3건만 보이고 `aria-pressed`가 옮겨 가며 `#approvals-status`가 「진행 가능 3건」이다.
- **AC-04 · 항목과 링크**: GIVEN 승인 대기 건 WHEN 목록을 봄 THEN 회사명·사업연도·「승인 대기」·질의 수·생성 시각(`Asia/Seoul`)이 있고 `a[href="/reviews/{id}"]`의 글자는 「승인하러 가기」다. 진행 가능·처리 중 건의 링크 글자는 「열기」다.
- **AC-05 · 여러 쪽**: GIVEN 첫 쪽에 `next_cursor`가 있음 WHEN 화면이 열림 THEN 이어서 `cursor`로 요청해 `next_cursor`가 없을 때까지 합치고, 요청은 최대 5번이며 매번 `page_size=50`이다.
- **AC-06 · 250건 초과**: GIVEN 5쪽을 받고도 `next_cursor`가 남음 WHEN 화면이 열림 THEN EC-02 문구와 `/reviews` 링크가 보이고 건수에 「이상」이 붙으며 6번째 요청은 없다.
- **AC-07 · 빈 상태**: GIVEN 선택한 상태의 건이 0개 WHEN 목록을 봄 THEN EC-01의 해당 문구가 보이고 목록은 비어 있다.
- **AC-08 · 401**: GIVEN `auth/me` 또는 `reviews`가 401 WHEN 화면이 열림 THEN `/`로 이동하고 오류 문구나 목록은 남지 않는다.
- **AC-09 · 오류 문구**: GIVEN 네트워크 예외·5xx·429·404 WHEN 화면이 열림 THEN EC-04~EC-07의 문구와 「다시 불러오기」가 보이고 서버 본문·`details`·`request_id`는 화면에 없다. 중간 쪽 실패면 받은 건도 보이지 않는다(EC-08).
- **AC-10 · 안전한 출력**: GIVEN 회사명이 `<img src=x onerror=1>&` WHEN 목록을 그림 THEN 글자 그대로 보이고 `innerHTML`·`eval`·외부 주소가 `approvals.js`에 없다.
- **AC-11 · 접근성·모션**: GIVEN `approvals.css`와 템플릿 WHEN 검사 THEN 상태 선택은 `role="group"`·`aria-label`·`aria-pressed`이고, 건수 알림은 `role="status"`이며, 오류는 `role="alert"`다. `:focus-visible` 윤곽이 있고 `prefers-reduced-motion: reduce`에서 전환이 없으며 움직이는 속성은 `transform`·`opacity`뿐이다.
- **AC-12 · 버튼 색 우선순위**: GIVEN `login.css`·`reviews.css`·`approvals.css` WHEN 상태 선택 버튼과 「다시 불러오기」의 규칙을 검사 THEN `.approvals-page` 범위 규칙이 전역 `button`·`button.secondary`보다 우선하도록 선택자 구체성이 높고, 선택된 버튼과 선택되지 않은 버튼의 배경·글자색이 서로 다르다(실제 값은 브라우저에서 확인).
- **AC-13 · 로그아웃·이동**: GIVEN 로그인 상태 WHEN 「로그아웃」을 누름 THEN `POST /api/v1/auth/logout` 뒤 `/`로 이동하고, 실패하면 reviews.js와 같은 문구를 보인다. 「심사 건 전체 보기」는 `/reviews` 링크다.
