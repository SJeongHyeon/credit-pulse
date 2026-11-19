# Spec - 메인 화면(CREDIT PULSE) 디자인

> 2026-11-10 변경: 기업 목록과 보유 수에 관한 아래의 데모 스냅샷 15·16곳 기준 및 AC-14·AC-49의 숫자는 [서비스 코퍼스 목록 스펙](landing-service-catalog.md)으로 대체한다. 데모 검색 스냅샷 16곳은 별도로 유지한다.

## Why

- **페르소나**: 기업여신 심사역이 웹 브라우저로 서비스에 접속한다. 팀은 서비스 이름을 CREDIT PULSE로 정했고 로고 파일이 있다.
- **상황**: 로그인 화면과 심사 건 화면은 기능 검증용 기본 스타일(파란 버튼, 흰 상자)이라 서비스의 정체성이 보이지 않는다. 첫 화면에는 로그인 폼만 있어 비어 보이고, 이 서비스가 어떤 기업 정보를 다루는지 알 수 없다.
- **문제**: 첫 화면에서 어떤 서비스인지, 금융 업무에 쓰는 신뢰할 만한 도구인지 알기 어렵고, 두 화면의 색·글꼴·버튼이 제각각이다.
- **측정 지표**: 로그인 화면과 심사 건 화면 모두에서 로고(CREDIT PULSE)가 보이고, 본문 글자 색 대비가 WCAG AA(4.5:1) 이상이며, 폭 360px에서 가로 스크롤이 없다. 첫 화면에서 보유 기업을 넘겨 보고 검색할 수 있다. 이 항목을 자동화 테스트와 브라우저 스크린샷에서 각각 1회 이상 확인한다.

## Goal

- **해결 목표**: 로그인 화면(`/`)을 메인 화면으로 바꾼다. 화면 왼쪽에는 큼직한 보유 기업 정보 슬라이드와 그 아래 아직 정해지지 않은 기능 칸(더미)을 둔다. 오른쪽에는 위쪽이 오른쪽으로 밀린 평행사변형 신용카드 모양 카드(CREDIT PULSE 로고), 그 아래 검색, 그 아래 작은 로그인 폼을 둔다. 배경은 기업의 깔끔한 느낌으로 채우고, 심사 건 화면까지 금융 업무용 차분한 색·글꼴로 통일한다.
- **성공 기준**:
  1. `GET /`가 정보 슬라이드, 검색, 기능 칸, 카드(로고 포함), 로그인 폼을 한 화면에 제공한다.
  2. 정보 슬라이드는 보유 기업(데모 스냅샷의 15곳)을 이전·다음 버튼과 점으로 넘겨 볼 수 있고, 검색은 그 목록을 거른다.
  3. 로고 이미지는 배경이 투명한 PNG 두 장(남색, 흰색)으로 제공된다.
  4. 두 화면이 같은 색 토큰(남색 `#00274c` 중심)과 글꼴 목록을 쓴다.
  5. 외부 글꼴·CDN·이미지 주소를 쓰지 않는다.
  6. 기존 로그인·심사 건 동작과 보안 기준은 바뀌지 않는다(기존 테스트 전부 통과).
- **Out of Scope**: 다크 모드, 로그인 주소 변경(`/login` 신설), 로고 원본 수정, 기존 JavaScript(`login.js`, `reviews.js`) 동작 변경, 새 프레임워크, 기업 정보·검색용 서버 API(이 화면은 정적 사본을 쓴다), 기능 칸의 실제 동작, 심사 건 화면의 기능 변경. 카드에는 실제 카드 번호나 개인정보처럼 보이는 문구를 넣지 않는다.

### 정한 사항 (사용자 요청과 기본안)

- 메인 화면은 새 주소 없이 `/`의 로그인 화면과 통합한다. 로그인 직후에는 `/reviews`로 이동하고, 이미 로그인한 사용자가 `/`에 오면 첫 화면에 머문다([첫 화면 이동 스펙](home-review-navigation.md)).
- 배치는 사용자가 그린 스케치를 따르되 사용자 요청으로 조정했다. 왼쪽: 정보 슬라이드(크게) → 기능 칸 6개. 오른쪽: 카드 → 검색 → 로그인(작게). 카드는 스케치의 평행사변형처럼 위쪽이 오른쪽으로 밀리게(`skewX` 음수) 기울인다. 회전은 쓰지 않는다.
- 검색 칸은 오른쪽 열의 카드와 로그인 사이에 두고, 왼쪽 정보 슬라이드를 거른다(검색 칸 위치 해석: "로그인 창 위").
- 스케치의 "로그인 막대"는 이메일·비밀번호 입력 칸 아래의 로그인 버튼으로 구현한다. 로그인 폼은 항상 카드 아래에 보이고, 로그인 동작(JavaScript)은 바꾸지 않는다.
- 정보 슬라이드의 기업은 데모 스냅샷(`data/snapshot`)에 든 15곳이며, 이름·corp_code·사업연도·공시일을 정적 파일(`landing-data.js`)로 복사해 둔다. 서버 API가 생기면 교체한다. 이 화면은 로그인 전에도 보이므로 공개된 공시 정보(기업명, corp_code, 공시일)만 쓴다.
- 슬라이드 아래에는 기업 수만큼 점을 만들지 않고 가로 진행 막대 하나를 둔다. 이전·다음 버튼과 자동 전환에 따라 노란 채움의 끝이 현재 순번을 가리킨다.
- 검색은 이 15곳 안에서 기업명 또는 corp_code로 거르는 화면 안 기능이다. 서버로 요청을 보내지 않는다.
- 기능 칸 6개(재무 요약, 공시 검색, 리스크 점검, 보고서, 승인 현황, 설정)는 이름만 있는 비활성 버튼이다. 눌러도 아무 일도 일어나지 않고 「준비 중」이 표시된다.
- 색·글꼴 통일은 로그인 폼과 심사 건 화면까지 적용한다. 팀 이름 철자는 `CREDIT PULSE`다(로고 파일 기준).

## What

### Happy Path

1. 비로그인 사용자가 `/`에 접속한다.
2. 「보유 기업 정보」 카드에서 첫 기업(이름, corp_code, 사업연도·공시일)이 보인다. 이전·다음 버튼으로 기업을 바꾸면 `3 / 100` 같은 위치와 카드 아래 가로 진행 막대가 함께 바뀐다.
3. 그 아래에 기능 칸 6개가 있다. 비활성이고 「준비 중」이 표시된다.
4. 오른쪽에는 남색 신용카드 모양 카드가 위쪽이 오른쪽으로 밀린 평행사변형으로 보이고, 안에 흰색 CREDIT PULSE 로고, 금색 칩, 얇은 맥박선, 문구 「기업여신 심사 지원」이 있다.
5. 카드 아래 검색 칸에 기업명이나 corp_code 일부를 입력하면 왼쪽 슬라이드가 일치하는 기업만 보여 준다.
6. 그 아래에 작은 「기업여신 심사」 로그인 폼(이메일·비밀번호·로그인 버튼)이 있다.
7. 배경은 옅은 청회색 바탕 위의 등각(아이소메트릭) 기하 패턴으로 채워져 비어 보이고, 패널은 반투명 유리처럼 그 위에 떠 있다.
8. 로그인하면 기존처럼 `/reviews`로 이동한다. `/reviews` 상단 바에 남색 로고, 사용자 이름, 로그아웃 버튼이 보이고 본문은 같은 색 체계의 카드로 보인다.
9. 좁은 화면에서는 카드·검색·로그인이 위, 정보·기능 칸이 아래로 쌓인다.

### Edge Cases

| # | 상황 | 처리 방식 |
|---|---|---|
| EC-01 | 로고 이미지를 불러오지 못함 | 카드는 남색 배경을 유지하고 대체 텍스트 「CREDIT PULSE」가 흰 글자로 보인다. |
| EC-02 | 좁은 화면(폭 360px) | 1열로 쌓고 카드 폭은 24rem 이하, 가로 스크롤이 없다. |
| EC-03 | 모션 줄이기 설정 | 카드 기울기 전환과 슬라이드 이동 효과가 없다. |
| EC-04 | JavaScript가 꺼짐 | 카드와 안내 문구가 보이고, 기존처럼 로그인은 할 수 없다. 정보 슬라이드는 비어 있다. |
| EC-05 | 카드 기울기 때문에 모서리·그림자가 잘림 | 카드를 둘러싼 영역에 위아래 여유를 두어 잘리지 않게 한다(스크린샷으로 확인). |
| EC-06 | 아주 긴 사용자 이름 | 기존처럼 줄바꿈해 카드 밖으로 넘치지 않는다. |
| EC-07 | 로고 파일을 같은 이름으로 교체 | 템플릿·CSS 수정 없이 바뀐 로고가 보인다(가로세로 비율은 900:158 유지 권장). |
| EC-08 | 오류·상태 문구가 길어짐 | 기존 `role="alert"`·`role="status"` 영역을 유지하고 색 대비를 지킨다. |
| EC-09 | 검색 결과가 없음 | 슬라이드·진행 막대·위치를 비우고 「검색 결과가 없습니다.」를 보인다. 이전·다음 버튼은 비활성이다. |
| EC-10 | 검색 결과가 1곳 | 그 기업만 보이고 이전·다음 버튼은 비활성이다. |
| EC-11 | 검색어를 지움 | 15곳 전체로 돌아가 첫 기업부터 보인다. |
| EC-12 | 마지막 기업에서 다음, 첫 기업에서 이전 | 반대쪽 끝으로 순환한다. |
| EC-13 | 검색어에 HTML처럼 보이는 글자(`<b>`) | 글자 그대로 검색에만 쓰이고 화면에 HTML로 해석되어 나타나지 않는다. |
| EC-14 | 검색 칸에서 Enter | 페이지가 새로 고쳐지지 않고 현재 입력으로 거른다. |

## How

- **변경·추가 파일**: `backend/templates/login.html`, `backend/templates/reviews.html`, `backend/static/login.css`(공통 색 토큰·입력·버튼), `backend/static/landing.css`(메인 화면 전용), `backend/static/reviews.css`, `backend/static/landing.js`(정보 슬라이드·검색 컨트롤러), `backend/static/landing-data.js`(기업 15곳 정적 사본), `backend/static/credit-pulse-logo.png`(남색), `backend/static/credit-pulse-logo-white.png`(흰색), `tests/test_login_web_ui.py`, `tests/test_reviews_web_ui.py`, `tests/landing_web_ui.test.mjs`. `login.js`, `reviews.js`, `backend/main.py`는 바꾸지 않는다.
- **구조**: `login.html`은 `.landing` 안에 `.brand-column`(카드 + 검색 + 로그인 `main`)과 `.info-column`(정보 슬라이드·기능 칸)을 둔다. 문서 순서는 로그인이 먼저다(읽는 순서, 좁은 화면 순서). 넓은 화면에서는 CSS `order`로 정보 열을 왼쪽에 놓는다.
- **컨트롤러**: `landing.js`는 `createLandingController({ document, companies })`를 내보낸다. 서버·브라우저 객체를 직접 쓰지 않고 `document`를 주입받아 가짜 DOM으로 테스트한다. 슬라이드 내용은 `createElement`와 `textContent`로만 만든다.
- **슬라이드 효과**: 이동할 때마다 새 `article.slide` 요소로 바꿔 CSS 키프레임(옆에서 들어옴)이 자동으로 재생된다. 모션 줄이기 설정에서는 끈다.
- **색 토큰**(`login.css`의 `:root`): 남색 `#00274c`, 남색 700 `#0b3b6f`, 남색 100 `#dbe5f1`, 남색 50 `#eef3f9`, 바탕 `#fcf8f4`, 표면 `#ffffff`, 글자 `#1b2533`, 보조 글자 `#566273`, 선 `#d9dee6`, 입력 선 `#aab5c4`, 포커스 `#2f6fb3`, 오류 `#b42318`. 남색과 바탕색은 로고 파일에서 추출한 값이다.
- **글꼴**: `"Pretendard", "Noto Sans KR", "Malgun Gothic", system-ui, sans-serif`. 웹 글꼴을 내려받지 않는다(설치된 글꼴만 사용).
- **배경**: 메인 화면 `body`에 옅은 청회색 CSS 그라디언트를 깔고, 화면 전체에 등각 큐브 패턴과 큰 반투명 면을 그린 인라인 SVG(`.page-bg`, `aria-hidden="true"`, 이미지 파일·외부 주소 없음)를 둔다. 심사 건 화면 배경은 기존 바탕색을 유지한다.
- **레퍼런스 스타일(유리·타이포)**: 정보 패널·로그인 폼·기능 칸은 반투명 흰색 + `backdrop-filter` 블러 + 얇은 흰 테두리의 유리 패널로 만든다. 기업명은 얇은(300) 큰 글씨, 작은 라벨(보유 기업 정보·순번·corp_code·준비 중)은 모노스페이스 대문자 + 넓은 자간, 포인트 색은 어두운 금색(`--accent`, #8f5b00) 하나만 쓴다. 새 색(`--mist`, `--accent`)도 글자 대비 4.5:1을 지킨다.
- **로고 자산**: 제공받은 로고의 배경을 제거해 투명 PNG로 만들었다(폭 900px). 흰색 판은 카드 위에서 쓴다.
- **파비콘**: 첫 화면, 회원가입, 심사 건 목록·상세, 승인 현황의 `head`에서 공통 템플릿 `_favicon.html`을 사용한다. 제공된 `favicon_pulse` 아이콘과 웹 매니페스트를 `backend/static/favicon/`에서 제공한다.
- **카드**: CSS로만 그린다(`transform: skewX(-14deg)`로 위쪽을 오른쪽으로 민 평행사변형, 그라디언트, 그림자). 로고·문구도 같이 기운다. 장식 요소(칩, 맥박선)는 `aria-hidden="true"`이고 로고 이미지의 `alt`가 카드의 접근 가능한 이름이다.
- **크기**: 로그인 폼과 검색 칸은 최대 폭 22rem으로 카드 아래에 가운데 맞춤한다(이전보다 작게). 정보 슬라이드는 한 장의 높이를 12rem 이상으로 키운다.
- **반응형**: 폭 52rem 이하에서 1열로 바꾼다.
- **보안 제약 유지**: 서버 문자열은 `textContent`로만 넣고 `innerHTML`을 쓰지 않는 기존 규칙은 그대로다. 비밀번호·토큰 보관 금지도 그대로다.

## AC (Given-When-Then)

**AC-01 · 메인 화면 구성**
- GIVEN: 비로그인 브라우저
- WHEN: `GET /` 요청
- THEN: 200 HTML에 `alt="CREDIT PULSE"` 로고 이미지가 든 카드(`credit-card`), 검색 폼, 로그인 폼(`login-form`), 정보 슬라이드, 기능 칸이 함께 있고, 문서 순서는 카드 → 검색 폼 → 로그인 폼이며 정보 열(`info-column`)은 그 뒤에 온다. 칩·맥박선은 `aria-hidden="true"`다. 기존 로그인 화면 AC(login-web-ui)의 요소 id와 문구는 유지된다.

**AC-02 · 로고 자산 제공**
- GIVEN: 서버가 실행 중
- WHEN: `/static/credit-pulse-logo.png`, `/static/credit-pulse-logo-white.png` 요청
- THEN: 둘 다 200 `image/png`이고 알파 채널이 있는 PNG(배경 투명)다. 템플릿의 `width`·`height` 속성은 파일 크기와 같다.

**AC-03 · 로고 비율 유지**
- GIVEN: 카드 안 로고 이미지
- WHEN: CSS가 로고 폭을 비율(%)로 지정
- THEN: `height: auto`가 함께 지정되어 이미지가 늘어나지 않는다.

**파비콘 · 화면 공통**
- GIVEN: 첫 화면, 회원가입, 심사 건 목록·상세, 승인 현황
- WHEN: 각 화면의 HTML과 파비콘 링크를 요청
- THEN: 모든 화면이 동일한 파비콘을 가리키고, 아이콘과 매니페스트를 200으로 받는다.

**AC-04 · 외부 의존 없음**
- GIVEN: 로그인·심사 건 화면과 모든 CSS·JavaScript 파일
- WHEN: 응답 본문을 검사
- THEN: 같은 서버 `/static/` 주소를 빼고 `http://`, `https://` 주소가 없다(외부 글꼴·CDN·이미지 없음).

**AC-05 · 카드 모양과 모션 배려**
- GIVEN: `landing.css`
- WHEN: 내용을 검사
- THEN: `.credit-card`에 음수 `skewX(`(위쪽이 오른쪽으로 밀린 평행사변형, 사용자가 그린 방향) 변환이 있고 회전(`rotate`)은 없으며, `prefers-reduced-motion: reduce`에서 전환·슬라이드 효과를 끈다.

**AC-06 · 배치와 좁은 화면 대응**
- GIVEN: `landing.css`
- WHEN: 내용을 검사하고 폭 390px 브라우저로 확인
- THEN: 넓은 화면에서 정보 열은 왼쪽(`order: 1`), 카드·로그인 열은 오른쪽(`order: 2`)이다. 폭 52rem 이하에서는 1열이며 카드·로그인 열이 먼저(`order: 0`) 온다. 브라우저에서 가로 스크롤이 없고 카드 모서리·그림자가 잘리지 않는다(스크린샷 확인).

**AC-07 · 글자 색 대비**
- GIVEN: `login.css`의 색 토큰
- WHEN: 글자·배경 짝의 WCAG 대비를 계산
- THEN: 글자/바탕(글자·표면, 글자·바탕, 보조 글자·표면, 보조 글자·바탕), 버튼(흰색·남색), 제목(남색·표면), 보조 버튼(남색·남색 50), 오류(오류색·표면, 오류색·바탕)가 모두 4.5:1 이상이다.

**AC-08 · 심사 건 화면 통일**
- GIVEN: `GET /reviews`
- WHEN: HTML 검사
- THEN: 머리 영역에 `alt="CREDIT PULSE"` 로고가 있고 `<h1>`보다 앞에 온다. 사용자 이름(`current-user`)과 로그아웃 버튼 순서, 기존 id는 유지된다. 로그인·심사 건 화면의 `<title>`에 CREDIT PULSE가 들어간다.

**AC-09 · 기존 동작 회귀 없음**
- GIVEN: 이 변경 뒤
- WHEN: 전체 `pytest`와 `node --test tests/*.test.mjs` 실행
- THEN: 기존 테스트가 모두 통과한다(`login.js`·`reviews.js`는 바뀌지 않는다).

**AC-10 · 정보 슬라이드**
- GIVEN: 기업 목록 N곳(N≥2)과 초기화된 화면
- WHEN: 처음 화면, 「다음」, 「이전」 또는 자동 전환
- THEN: 처음에는 첫 기업의 이름·`corp_code {코드}`·`{연도} 사업보고서 · 공시 {YYYY-MM-DD}`와 위치 `1 / N`이 보인다. 「다음」은 다음 기업, 「이전」은 이전 기업으로 바꾸고 마지막에서 다음은 첫 기업, 첫 기업에서 이전은 마지막 기업으로 순환한다. 진행 막대는 하나이고 노란 채움 너비는 `(index + 1) / N × 100%`다(첫 기업 1%, 마지막 100%, N=100). 막대의 접근성 값은 현재 순번과 N을 나타낸다. 슬라이드 영역은 `aria-live="polite"`다.

**AC-11 · 기업 검색**
- GIVEN: 초기화된 화면
- WHEN: 검색 칸에 입력(또는 검색 버튼·Enter)
- THEN: 기업명(대소문자 무시) 또는 corp_code에 입력(앞뒤 공백 제거, 연속 공백 하나로)이 포함된 기업만 슬라이드에 남고 위치는 `1 / {결과 수}`가 된다. 입력을 지우면 전체 목록으로 돌아간다. 폼 제출은 페이지를 새로 고치지 않는다.

**AC-12 · 검색 결과 없음과 1곳**
- GIVEN: 일치하는 기업이 없는 검색어
- WHEN: 검색
- THEN: 슬라이드·진행 막대·위치가 비고 「검색 결과가 없습니다.」가 보이며 이전·다음이 비활성이다. 결과가 1곳이면 이전·다음이 비활성이고 진행 막대는 100%다.

**AC-13 · 기능 칸(더미)**
- GIVEN: `GET /`
- WHEN: HTML 검사
- THEN: 기능 칸 6개(재무 요약, 공시 검색, 리스크 점검, 보고서, 승인 현황, 설정)가 모두 `disabled` 버튼이고 각각 「준비 중」이 표시된다.

**AC-14 · 기업 데이터가 스냅샷과 일치**
- GIVEN: `landing-data.js`와 `data/snapshot/manifest.jsonl.gz`, `snapshot.json`
- WHEN: 두 쪽의 기업 목록을 비교
- THEN: corp_code 집합이 같고 각 기업의 이름·사업연도·공시일이 같다. corp_code는 8자리 숫자이며 중복이 없다.

**AC-15 · 배경**
- GIVEN: `GET /`
- WHEN: HTML·CSS 검사
- THEN: `body`에 `landing-page` 클래스가 있고, `aria-hidden="true"`인 `.page-bg` 안에 등각 기하 패턴 SVG(`<polygon>` 6개 이상)가 있으며 `.page-bg`는 화면 전체(`position: fixed; inset: 0`)를 덮는다. CSS에 그라디언트 배경이 있고 이미지 파일·외부 주소를 쓰지 않는다.

**AC-16 · 안전한 출력**
- GIVEN: `landing.js`
- WHEN: 소스를 검사하고 `<b>` 같은 검색어·특이한 기업명으로 실행
- THEN: `innerHTML`, `insertAdjacentHTML`, `outerHTML`을 쓰지 않고, 기업 정보와 검색어는 `textContent`로만 화면에 들어간다.

**AC-17 · 로그인·검색 크기와 슬라이드 크기**
- GIVEN: `landing.css`
- WHEN: 내용을 검사
- THEN: 오른쪽 열의 로그인 폼과 검색 칸 최대 폭이 24rem 이하이고, 정보 슬라이드 한 장(`.slide`)의 최소 높이가 11rem 이상이다.

**AC-18 · 유리 패널**
- GIVEN: `landing.css`
- WHEN: `.info-panel`, `.brand-column .login-page`, `.feature-tile` 규칙을 검사
- THEN: 세 규칙 모두 반투명 흰색 배경(`rgba(255, 255, 255, …)`)과 `backdrop-filter: blur(…)`를 가진다.

**AC-19 · 타이포그래피와 포인트 색**
- GIVEN: `landing.css`, `login.css`
- WHEN: 내용을 검사
- THEN: `.slide-name`의 글자 굵기는 300이고, `.info-title`·`.slide-position`·`.slide-code`·`.feature-note`는 모노스페이스 글꼴(`var(--mono)`)과 대문자(`text-transform: uppercase`)를 쓴다. `--accent`, `--mist` 색이 정의되어 있고 (ink·muted·navy·accent)/mist, accent/surface 글자 짝의 대비가 4.5:1 이상이다.

**AC-20 · 촘촘한 간격**
- GIVEN: `landing.css`
- WHEN: 내용을 검사
- THEN: `.landing`의 최대 폭이 64rem 이하이고 열 사이 간격(`gap`)의 최댓값이 2rem 이하이며, 카드 무대(`.brand-stage`)의 아래쪽 안쪽 여백이 2rem 이하이다. 오른쪽 열의 검색 칸 아래 간격은 0.5rem 이하이다.

**AC-21 · 터치 크기와 상호작용 피드백 (UI/UX 점검 반영)**
- GIVEN: `landing.css`, `login.css`
- WHEN: 내용을 검사
- THEN: 오른쪽 열의 입력칸·버튼 최소 높이가 2.75rem(44px) 이상이다. 버튼은 `cursor: pointer`와 0.3초 이하의 색 전환(`transition`)을 가지고, `prefers-reduced-motion: reduce`에서는 전환이 꺼진다.

**AC-22 · 두 화면이 공유하는 테마**
- GIVEN: `GET /`, `GET /reviews`, `GET /static/theme.css`
- WHEN: HTML·CSS 검사
- THEN: 두 화면 모두 `theme.css`를 불러오고 `body`에 `glass-page` 클래스가 있으며 `aria-hidden="true"`인 `.page-bg` 안에 등각 패턴 SVG(`<polygon>` 6개 이상)가 있다. 그라디언트 배경, `--mono` 글꼴 변수, 화면 전체를 덮는 `.page-bg`(`position: fixed; inset: 0`)는 `theme.css`가 정의한다. 메인 화면의 AC-15·AC-19 배경·모노 글꼴 기준도 이 파일을 기준으로 검사한다.

**AC-23 · 심사 건 화면 유리 스타일**
- GIVEN: `reviews.css`
- WHEN: 내용을 검사
- THEN: `.app-bar`와 `.reviews-page`는 반투명 흰색 배경 + `backdrop-filter: blur(…)`, `.review-item`은 반투명 흰색 배경을 가진다. `.reviews-page h1`의 굵기는 300이고 `.review-code`·`.review-meta`는 모노스페이스(`var(--mono)`) 대문자이다.

---

## 개정 2 · 히어로 랜딩 (IPO FX 형태 레퍼런스)

**Why**: 카드·2열 배치보다 흰 바탕의 상단 메뉴 바 + 큰 헤드라인 + 알약 버튼이 금융 기업다운 차분한 첫인상을 준다. 사용자가 카드를 없애고 로고를 기준으로 만들자고 했다.

**정한 사항(사용자 선택)**: 로그인은 상단 버튼 → 팝업, 보유 기업 슬라이더·검색·기능 칸은 히어로 아래 섹션, 배경은 거의 흰색 + 은은한 그림자.

**바뀌는 것**: 신용카드·오른쪽 로그인 열·등각 큐브 배경을 없앤다. 포인트 색(금색)·유리 패널·모노 라벨·기업 데이터·슬라이더·검색·더미 기능 칸 동작은 그대로다. 로고는 흰색 변형 파일을 더는 쓰지 않는다.

### 대체 관계
| 이전 AC | 상태 |
|---|---|
| AC-01(구성), AC-05(카드 기울기), AC-06(2열 배치), AC-17(로그인·검색 크기), AC-20(촘촘한 간격) | **대체됨** — 아래 AC-24~AC-28이 기준이다. |
| AC-15(배경), AC-18(유리 패널), AC-21(터치 크기), AC-22(공유 테마) | **수정됨** — 큐브 패턴 대신 옅은 기하 면(배경)과 히어로의 금색·은색 도형, 선택자는 `.login-dialog`·`.pill`로 바뀐다. |

**AC-24 · 상단 바와 히어로**
- GIVEN: `GET /`
- WHEN: HTML 검사
- THEN: `credit-card`와 `credit-pulse-logo-white.png`가 없다. `header.site-bar`에 `alt="CREDIT PULSE"` 로고(`/static/credit-pulse-logo.png`, 900x158)와 `aria-label="주요 메뉴"` 내비(`#companies`, `#features` 앵커, 대상 id 존재), `#open-login` 버튼(`type="button"`, `aria-haspopup="dialog"`, `aria-controls="login-dialog"`)이 있다. `<h1>`은 한 개이고 상단 바 뒤에 오며, 히어로 안에 `#hero-login` 버튼과 `#companies`로 가는 링크가 있다.

**AC-25 · 로그인 팝업**
- GIVEN: `GET /`와 `landing.js`
- WHEN: HTML 검사, 가짜 DOM으로 동작 검사
- THEN: `#login-dialog`는 처음에는 닫혀 있고 `aria-labelledby="login-title"`로 제목을 연결한다. 안에 `#login-error`, `#login-status`, `#login-form`(처음 hidden), `#login-close`(`type="button"`, `aria-label="닫기"`)가 있다. `#open-login`·`#hero-login`을 누르면 `showModal()`로 연다. 배경 클릭·터치와 Esc로는 창이 유지되며, 사용자가 취소하려면 `×` 버튼을 누른다. `showModal`이 없는 브라우저에서는 `open` 속성으로 연다.

**AC-26 · 섹션 순서**
- GIVEN: `GET /`
- WHEN: HTML 검사
- THEN: 히어로 → `#companies` → `#features` 순서이고, `#companies`에 검색 폼과 슬라이더가, `#features`에 비활성 기능 칸 6개가 있다.

**AC-27 · 히어로 스타일**
- GIVEN: `landing.css`
- WHEN: 내용을 검사
- THEN: `.hero h1`의 굵기는 600 이상, `.pill`은 `border-radius: 999px`에 최소 높이 2.75rem 이상이며 `.pill-dark`는 남색 배경·`.pill-light`는 흰 배경이다. 52rem 이하에서 `.hero`는 1열이 된다. `.slide`의 최소 높이는 11rem 이상이다.

**AC-28 · 로고 중심 비주얼과 배경**
- GIVEN: `GET /`, `theme.css`
- WHEN: HTML·CSS 검사
- THEN: `.hero-visual`의 큰 로고 이미지는 장식이라 `alt=""`이고 `height: auto`로 비율을 지키며, 로고 패널 아래에 금색·은색 도형 SVG(`.hero-objects`, `linearGradient` 2개 이상)가 있다. `.page-bg`의 SVG는 `<polygon>` 6개 이상이고 화면 전체를 덮는다(스크롤해도 내용 뒤로 도형이 비치지 않도록 도형은 히어로에만 둔다). `.glass-page` 배경은 그라디언트이다.

**AC-29 · 로그인 팝업 스타일**
- GIVEN: `landing.css`
- WHEN: 내용을 검사
- THEN: `.login-dialog`는 반투명 흰색 + 블러, 최대 폭 24rem 이하이고 `.login-dialog::backdrop` 규칙이 있으며 `.login-dialog button`의 최소 높이는 2.75rem 이상이다.

**AC-30 · 심사 건 화면을 메인과 같은 모양으로**
- GIVEN: `reviews.css`
- WHEN: 내용을 검사
- THEN: 심사 건 화면의 버튼(`.reviews-page button`, `.reviews-user button`)과 입력칸(`.reviews-page input`, `.reviews-page select`)은 `border-radius: 999px`(알약)에 최소 높이 2.75rem 이상이다. `.review-item`은 그림자(`box-shadow`)로 패널과 구분되고, "새 심사 건 만들기"(`.create-section`)는 윗줄 대신 반투명 흰색 배경·16px 이상 둥근 모서리·그림자를 가진 별도 카드이다.

**AC-31 · 슬라이드 자동 넘김**
- GIVEN: `landing.js`(가짜 타이머·가짜 DOM)와 `GET /`
- WHEN: 초기화 후 시간이 흐르거나 사용자가 조작
- THEN:
  - 5초마다 다음 기업으로 넘어가고(순환), 결과가 1곳 이하이면 넘어가지 않는다. 자동 넘김 중에는 `#slide-viewport`의 `aria-live`가 `off`, 멈춰 있으면 `polite`이다.
  - 슬라이드 영역(`#info-panel`)에 마우스를 올리거나 키보드 포커스가 들어오면 멈추고, 벗어나면 다시 시작한다.
  - `#slide-toggle` 버튼(처음에는 `hidden`, JS가 보여 줌)은 "일시정지"/"재생"을 오가며, 직접 멈춘 상태에서는 마우스·포커스가 벗어나도 다시 시작하지 않는다.
  - 이전·다음·점·검색 조작을 하면 5초를 처음부터 다시 센다.
  - "동작 줄이기"(`prefers-reduced-motion: reduce`)가 켜져 있으면 처음에는 멈춘 상태("재생" 버튼)이고, 버튼으로만 시작할 수 있다.

---

## 개정 3 · 어두운 책상 사진 배경과 유리 카드

**Why**: 개정 2의 흰 바탕은 깔끔하지만 금융 심사 서비스의 무게감과 서비스만의 인상이 약했다. 팀이 정한 시안(어두운 배경 위 큰 헤드라인, 알약 버튼, 반투명 유리 카드)과 CREDIT PULSE 로고, 책상 위 재무 보고서 사진을 쓰기로 했다.

**정한 사항(사용자 선택)**
- 배경은 정지 사진(`desk-bg.webp`) + CSS 모션으로 한다. 22MB 영상은 저장소에 넣지 않는다.
- 마우스 반응을 위한 새 JS 파일 `bg-motion.js`만 추가한다. `login.js`·`landing.js`·`reviews.js`는 바꾸지 않는다.
- 적용 순서: 메인(`/`) → 심사 건(`/reviews`). 심사 건 상세·승인 화면은 해당 브랜치가 main에 들어온 뒤 별도로 맞춘다.

**바뀌는 것**: 화면 전체가 어두운 톤이 된다. 밝은 유리(흰색 반투명)를 어두운 유리(`--glass-bg`)로, 남색 중심 팔레트를 밝은 글자(`--on-dark`)와 금색 포인트(`--gold`)로 바꾼다. 로고는 새 CREDIT PULSE(900x158 → 1200x213)로 교체하고 흰색 판을 다시 쓴다. 히어로의 큰 로고 패널, 금색·은색 도형, 윗줄 라벨, 특징 칩은 두지 않는다. 기업 데이터, 슬라이더, 검색, 더미 기능 칸, 로그인 동작은 그대로다.

### 대체 관계
| 이전 AC | 상태 |
|---|---|
| AC-02, AC-24(로고 파일·크기, 흰색 판 금지) | **수정됨** — AC-32 |
| AC-15, AC-22, AC-28(큐브·폴리곤 배경, 큰 로고 패널, 도형) | **대체됨** — AC-33, AC-34 |
| AC-18, AC-23, AC-29(흰색 반투명 + 블러) | **수정됨** — 어두운 유리 토큰(`var(--glass-bg…)`)과 블러 변수를 쓴다. |
| AC-27(`.pill-dark` 남색, `.pill-light` 흰색, `.hero` 1열) | **수정됨** — `.pill-light`는 `var(--on-dark)`, 히어로는 단일 열이고 1열 전환은 `.section-grid`가 맡는다. |
| AC-19(포인트 색 `--accent`) | **수정됨** — 어두운 화면의 포인트는 `--gold`다. `--accent`, `--mist`와 AC-07의 밝은 팔레트 짝은 로그인 폼 기본 스타일을 위해 유지한다. |
| AC-30(`.create-section` 흰색 반투명) | **유지** — 값은 `rgba(255, 255, 255, …)`로 같고 어두운 배경 위에서 옅은 유리로 보인다. |

**AC-32 · 새 로고**
- GIVEN: 서버가 실행 중
- WHEN: `/static/credit-pulse-logo.png`, `/static/credit-pulse-logo-white.png` 요청, `/`·`/reviews` HTML 검사
- THEN: 둘 다 200 `image/png`, 알파 채널이 있는 PNG다. 두 화면의 상단 로고는 흰색 판을 쓰고 `width="1200" height="213"`이 파일 크기와 같으며 `alt="CREDIT PULSE"`다.

**AC-33 · 움직이는 사진 배경**
- GIVEN: `GET /`, `GET /reviews`, `theme.css`
- WHEN: HTML·CSS 검사
- THEN: `body`에 `glass-page` 클래스가 있고 `aria-hidden="true"`인 `.page-bg` 안에 `img.bg-photo`(`/static/desk-bg.webp`, `alt=""`)와 `bg-shade`, `bg-light`, `bg-spot` 층이 있다. `.page-bg`는 `position: fixed; inset: 0`이다. `@keyframes bg-drift`, `bg-sweep`이 있고 `prefers-reduced-motion: reduce`에서 `animation: none`이다. 외부 주소는 없다.

**AC-34 · 마우스 반응**
- GIVEN: `bg-motion.js`
- WHEN: 소스를 검사
- THEN: 포인터 위치를 CSS 변수(`--mx`, `--my`, `--spot`)로만 전달한다. `prefers-reduced-motion`과 마우스가 없는 기기(`hover: hover`)를 확인해 해당하면 아무것도 하지 않는다. `innerHTML`, `fetch(`를 쓰지 않는다.

**AC-35 · 어두운 화면의 글자 대비**
- GIVEN: `theme.css`의 `--on-dark`, `--on-dark-muted`, `--gold`
- WHEN: 어두운 유리 바탕(`#0e1014`)과의 WCAG 대비를 계산
- THEN: 세 색 모두 4.5:1 이상이다. 글자 아래에는 `.bg-shade`가 왼쪽을 더 어둡게 눌러 사진의 밝은 부분과 겹치지 않게 한다.

**AC-37 · 읽기 쉬운 기업 정보 글꼴**
- GIVEN: `landing.css`, `reviews.css`
- WHEN: 내용을 검사
- THEN: 보유 기업 패널의 라벨·순번·corp_code·준비 중 표시와 심사 건 목록의 코드·메타 정보는 모노스페이스·대문자 변환·넓은 자간 없이 본문 글꼴로 쓴다(한글이 모노스페이스에서 어색하게 보인다). 숫자는 `tabular-nums`다. `.info-title`은 1rem 이상, 슬라이드의 corp_code·공시 정보는 1.125rem 이상이고 기업명은 굵기 300을 유지한다. 이 항목이 AC-19, AC-23의 모노스페이스 대문자 기준을 대체한다.

**AC-38 · 자체 호스팅 명조 글꼴**
- GIVEN: `GET /`, `GET /reviews`, `/static/fonts.css`, `/static/fonts/`
- WHEN: HTML·CSS·파일 검사
- THEN: 두 화면이 `fonts.css`를 불러오고 글꼴 파일을 `preload`한다. `fonts.css`는 `"Noto Serif KR"`(가변 굵기 200~900, `font-display: swap`)을 같은 서버의 woff2로 정의한다. woff2는 필요한 글자(ASCII, Latin-1, 일반 문장부호, KS X 1001 한글 2350자, 호환 자모)만 남긴 사본으로 1.5MB 미만이고, 원본 라이선스(SIL OFL 1.1)를 `fonts/LICENSE.txt`로 함께 둔다. 전역 글꼴 스택(`login.css`의 `:root`)은 `"Noto Serif KR"`로 시작해 `Batang`, `serif` 순으로 대체한다. 외부 글꼴 서비스는 쓰지 않는다(AC-04).

**AC-36 · 히어로 구성**
- GIVEN: `GET /`
- WHEN: HTML 검사
- THEN: 히어로의 `<h1>`은 크게 쓴 서비스 이름 `CREDIT PULSE`와 그 아래 작은 글씨 부제(`.hero-sub`, 「공시 근거로 답하는 기업여신 심사」)로 이루어진다. 히어로에는 `<h1>` 하나, 설명(`.hero-lead`), `#hero-login`(`.pill-light`)과 `#companies` 링크 둘만 있다. 윗줄 라벨(`.eyebrow`), 기울임 강조(`<em>`), 특징 칩(`.hero-chips`), 버튼 화살표(`.pill-arrow`), `.hero-visual`은 없다. 설명은 근거 출처(사업보고서)와 승인 후 저장이라는 사실만 쓰고 연도 표기는 쓰지 않는다. 로그인 버튼 문구는 「로그인」이다.

**Out of Scope**: 다크/라이트 전환, 영상 파일 재생, 심사 건 상세·승인 화면의 디자인 반영, 상태별 배지 색(`reviews.js` 변경 필요).

---

## 개정 4 · 정보 가독성 (고딕 본문, 대비, 반응형)

**Why**: 개정 3은 분위기는 잡았지만 정보를 읽기에는 부족했다. 본문·카드·목록까지 모두 명조라 위계가 약하고, 카드가 밝은 책 사진 위에 놓이면 보조 글자·금색·배지 글자의 대비가 AA(4.5:1)에 못 미친다(최악 조건에서 2.6~3.5:1). 375px에서는 상단 사용자 이름이 두 줄로 갈라지고 메인 설명이 밝은 사진 위에 카드 없이 놓여 흐리다.

**정한 사항(사용자 선택)**
- 어두운 유리 테마는 유지한다. 정보 영역은 고딕(Pretendard), 큰 제목과 로고 근처는 세리프(영문 Playfair Display, 한글 Noto Serif KR)로 쓴다. 시안 7종을 화면으로 비교해 사용자가 고른 짝이다.
- 이 개정의 적용 범위는 메인(`/`)과 심사 건(`/reviews`)이었다. 상세·승인 화면의 후속 변경은 [상세 화면 시각 정렬 스펙](review-detail-visual-alignment.md)을 따른다.
- 상태 배지 색은 계속 보류한다. `reviews.js`가 상태를 글자로만 넣고 `data-*`·클래스를 주지 않아 CSS 선택자로 구분할 수 없고, JS는 바꾸지 않는다.
- 외부 CDN·외부 URL 금지, JS·API·서버 코드 불변, 기존 id·class 유지는 그대로다.

### 대체 관계
| 이전 AC | 상태 |
|---|---|
| AC-35(세 글자색 4.5:1, 바탕 `#0e1014`) | **강화됨** — AC-41: 사진의 가장 밝은 부분 위 최악 조건에서도 4.5:1 |
| AC-18, AC-23(유리 패널은 블러) | **수정됨** — 스크롤되는 큰 면(`.reviews-page`)과 기능 칸은 블러를 쓰지 않는다(AC-40). 상단 바·정보 패널·로그인 창은 유지 |
| AC-38(전역 글꼴 `Noto Serif KR`, `login.css` `:root`) | **유지** — 명조는 제목용으로 남고 `:root`는 상세 화면을 위해 그대로 둔다. 고딕은 AC-39 |

**AC-39 · 글꼴 역할과 자체 호스팅 고딕**
- GIVEN: `GET /`, `GET /reviews`, `GET /reviews/{id}`, `/static/fonts.css`, `/static/theme.css`, `/static/fonts/`
- WHEN: HTML·CSS·파일 검사
- THEN: `/`·`/reviews`의 `<body>`는 `glass-page ui-sans` 클래스이고 고딕 woff2(`/static/fonts/CreditPulseSans-subset.woff2`)를 `preload`한다. `/`는 영문 제목용 `CreditPulseSerif-subset.woff2`도 `preload`한다. `fonts.css`는 `"Noto Serif KR"`, `"Credit Pulse Sans"`(Pretendard 부분집합, 굵기 300~700), `"Credit Pulse Serif"`(Playfair Display 부분집합, 굵기 400~700)를 같은 서버 woff2로 `font-display: swap`과 함께 정의하고 외부 주소를 쓰지 않는다. 고딕·Playfair woff2는 AC-38과 같은 글자 범위(ASCII, Latin-1, 문장부호, KS X 1001 한글 2350자, 호환 자모, `₩`·`㈜`·화살표)만 남긴 사본으로 각각 1.5MB 미만이고, `fonts/LICENSE.txt`가 세 글꼴의 SIL OFL 1.1 고지와 라이선스 전문을 담는다. 원본에 예약 글꼴 이름이 있는 Pretendard와 Playfair Display는 부분집합이 수정본이므로(OFL: 미리 만든 부분집합은 수정본) 파일·패밀리 이름을 바꿨고, 고지에는 원본의 저작권·예약 이름 선언을 원본 그대로 적는다(Playfair Display의 예약 이름은 `Playfair`가 아니라 `Playfair Display`). 이름 외의 글꼴 데이터와 저작권·상표·라이선스 메타데이터는 원본 그대로다. `theme.css`는 `:root`에 `--font-serif`(`"Credit Pulse Serif"`, `"Noto Serif KR"`, … `serif` 순)와 `--font-sans`(`"Credit Pulse Sans"`, `"Malgun Gothic"`, … `sans-serif` 순)를 정의하고 `.ui-sans`가 `font-family: var(--font-sans)`와 `word-break: keep-all`을 쓴다. 큰 제목(`.hero h1`, `.section-title`, `.slide-name`, `.reviews-page h1`, `.dialog-head h2`)은 `var(--font-serif)`다. 후속 변경으로 상세 화면에도 `ui-sans`를 적용한다([상세 화면 시각 정렬 스펙](review-detail-visual-alignment.md)).

**AC-40 · 표면 위계와 블러**
- GIVEN: `theme.css`, `landing.css`, `reviews.css`
- WHEN: 내용을 검사
- THEN: `--glass-bg`는 알파 0.82, `--glass-bg-strong`(로그인 창)은 0.92다. `--shadow-tint`(어두운 갈색 계열)가 있고 카드 그림자는 순수 검정 `rgba(0, 0, 0, …)`만 쓰지 않고 이 색조를 쓴다. `.feature-tile`과 `.reviews-page`에는 `backdrop-filter`가 없다(스크롤되는 면에 블러를 걸지 않는다). `.app-bar`, `.info-panel`, `.login-dialog`는 `var(--glass-bg…)`와 블러를 유지한다. 유리 배경을 쓰는 규칙은 모두 `var(--glass-bg`로 시작한다.

**AC-41 · 최악 조건의 글자 대비**
- GIVEN: `theme.css`의 `--glass-bg`(0.82), `--on-dark`, `--on-dark-muted`, `--gold`, 오류색 `#ffb4a8`, 배지 글자색
- WHEN: 사진의 가장 밝은 부분(`#f7cca4`) 위에 `--glass-bg`를 얹고 그 위에 호버 겹침(`rgba(255, 255, 255, 0.12)`)을 더한 바탕과의 WCAG 대비를 계산
- THEN: 위 글자색 모두 4.5:1 이상이다. 카드 안 배지 글자는 호버 겹침과 배지 바탕(`rgba(227, 184, 102, 0.16)`)까지 더한 바탕에서도 4.5:1 이상이다.

**AC-42 · 글자 변수와 줄바꿈**
- GIVEN: `theme.css`, `landing.css`, `reviews.css`
- WHEN: 내용을 검사
- THEN: `theme.css` `:root`에 `--text-sm`(0.9375rem), `--text-base`(1rem), `--text-lg`(1.0625rem), `--leading-body`(1.7), `--leading-tight`(1.35), `--space-card-y`·`--space-card-x`(카드 안 위아래·좌우 여백)가 있고 `.review-meta`, `.review-code`, `.section-lead`, `.feature-note`, `.slide-toggle`의 글자 크기·줄간격과 `.review-item`의 안쪽 여백이 이 변수를 쓴다. 본문 최소 글자는 0.9375rem(보조 라벨 포함, 슬라이드 일시정지 버튼 포함) 이상이다. `.ui-sans`는 `word-break: keep-all`이고 `h1`, `h2`, `.review-title`은 `text-wrap: balance`다.

**AC-43 · 기능 칸 배치**
- GIVEN: `GET /`, `landing.css`
- WHEN: HTML·CSS 검사
- THEN: `.feature-tile` 6개와 「준비 중」 표시는 그대로이고, 넓은 화면(`min-width: 52rem`)에서는 4열 격자이고 첫 번째·네 번째 칸이 2칸을 차지해 6칸이 빈칸 없이 2+1+1 / 2+1+1로 채워진다. 그보다 좁으면 3열, 40rem 이하에서는 2열이다. 열 너비는 `minmax(0, 1fr)`다.

**AC-44 · 모션과 눌림**
- GIVEN: `theme.css`, `landing.css`, `reviews.css`
- WHEN: 내용을 검사
- THEN: `--ease-spring: cubic-bezier(0.16, 1, 0.3, 1)`이 `:root`에 있고 `.pill`, `.feature-tile`, `.review-item`, `.slide-nav`의 전환이 이 값을 쓴다. `.pill:active`, `.reviews-page button:active`는 `transform: scale(0.98)`이다. `prefers-reduced-motion: reduce`에서는 전환·눌림 변환이 없다.

**AC-45 · 375/768/1440px 반응형**
- GIVEN: `theme.css`, `landing.css`, `reviews.css`
- WHEN: 내용을 검사(실제 폭은 스크린샷으로 따로 확인)
- THEN: `.glass-page`는 `min-height: 100vh` 뒤에 `min-height: 100dvh`를 한 번 더 쓴다. `#current-user`는 `white-space: nowrap`과 `text-overflow: ellipsis`로 한 줄에 머물고, 40rem 이하에서는 상단 바의 로고(높이 1.5rem)와 간격을 줄여 375px에서 사용자 이름(「홍길동님」)이 잘리지 않는다. `.review-code`는 `white-space: nowrap`이라 코드 숫자가 중간에서 끊기지 않는다. `.review-meta`의 각 항목(`> span`)은 `white-space: nowrap`이라 줄 안에서 끊기지 않고 `gap`으로 항목 단위로만 줄을 바꾼다. `.slide-progress`는 카드 너비를 채우는 가로 막대이며 `.slide-progress-fill`은 너비 변경을 부드럽게 전환한다. 52rem 이하에서 `.bg-shade`는 위아래 두 지점 모두 알파 0.75 이상으로 눌러 사진의 밝은 부분 위 글자가 읽히게 한다. `.section`의 위아래 여백은 `clamp(2rem, 7vh, 4.5rem)` 이상이다.

**AC-46 · 첫 화면은 히어로만**
- GIVEN: `landing.css`
- WHEN: 내용을 검사(실제 높이는 스크린샷으로 따로 확인)
- THEN: 모든 폭에서 `.hero`는 `display: flex`, `align-items: center`이고 `min-height`가 `max(30rem, calc(100dvh - var(--site-bar-h)))`다(`100vh` 대체값을 먼저 둔다). `--site-bar-h`(5.25rem)는 `.hero`가 읽을 수 있도록 상위인 `.glass-page`에 정의한다. 따라서 높은 화면에서도 보유 기업 패널(`#info-panel`)은 첫 화면 아래에서 시작하고 스크롤해야 보인다. 이전의 52rem 이하 `min-height: 0`(AC-27)은 이 규칙으로 대체한다.

**AC-47 · 히어로 문구 위치**
- GIVEN: `landing.css`
- WHEN: 내용을 검사(실제 위치는 스크린샷으로 따로 확인)
- THEN: `.hero`가 `--hero-lift`(넓은 화면 `28vh`, 52rem 이하 `12vh`)를 정의하고 아래 안쪽 여백에 이 값을 더한다. 문구 덩어리(`.hero-copy`)의 가운데는 첫 화면 높이의 약 40% 지점, 즉 배경 책의 가운데 높이에 놓여 로고와의 거리가 너무 멀지 않다. 위쪽 여백은 그대로다.

**AC-48 · 아래로 스크롤 안내**
- GIVEN: `GET /`, `landing.css`
- WHEN: HTML·CSS 검사
- THEN: 히어로 안에 `aria-hidden="true"`인 `.scroll-cue`(글자 「아래로 스크롤」과 금색 가는 선 `.scroll-cue-line`)가 하나 있고 첫 화면 맨 아래 가운데에 놓인다. 누를 수 없고(`pointer-events: none`, 링크·버튼 아님) 선은 `transform`·`opacity`만으로 움직이며 `--ease-spring`을 쓴다. `prefers-reduced-motion: reduce`에서는 `animation: none`이다. 히어로의 링크는 여전히 `#companies` 하나뿐이다(AC-36).

**AC-49 · 핵심 수치 띠**
- GIVEN: `GET /`, `landing.css`, `hero-stats.js`, `landing-data.js`
- WHEN: HTML·CSS·소스 검사
- THEN: 히어로 안, 스크롤 안내 앞에 `ul.hero-stats`(`aria-label="핵심 수치"`)가 있고 `li` 네 칸이 「15곳 · 보유 기업」 「6항목 · 체크리스트」 「2025 · 사업보고서 기준」 「승인 후 · 심사역이 확인한 내용만 저장」이다. 숫자는 확인되는 사실만 쓴다. 기업 수와 사업연도는 HTML에 미리 적어 두고(`#stat-companies`, `#stat-year`) 새 파일 `hero-stats.js`가 `landing-data.js`의 목록에서 세어 같은 값으로 채운다(사업연도는 목록 전체가 한 해일 때만). 목록이 비었거나 형식이 틀리면(항목 중 하나라도 객체가 아니거나 `fiscal_year`가 정수가 아니면 목록 전체를) HTML의 값을 그대로 둔다. 기업 수와 사업연도 모두 바꾸지 않는다. HTML에 적힌 값은 `landing-data.js`와 일치하고 항목 수 6은 SPEC §3.1의 체크리스트 항목 수와 같다. 띠는 `var(--glass-bg)` 바탕에 위아래 금색 얇은 선을 두고 칸 사이는 `var(--glass-line)` 세로선이다. 숫자는 `var(--font-serif)`, 설명은 고딕이며 `--text-*` 변수를 쓴다. 넓고 높은 화면(`min-width: 52rem` 그리고 `min-height: 42rem`)에서는 `position: absolute`로 첫 화면 아래쪽에 놓이고, 52rem 이하이거나 높이 42rem 이하에서는 `position: static`으로 문구 아래에 쌓인다. 40rem 이하에서는 2열이다. 판정 초안 카드는 넣지 않는다.

**Out of Scope**: 상세·승인 화면 디자인, 상태별 배지 색(JS 변경 필요), 스크롤 진입 애니메이션, 판정 초안 미리보기 카드, 로딩 스켈레톤, 노이즈 질감, 기능 칸 문구 변경.

---

## 개정 5 · 로고 인트로 (첫 방문)

**Why**: 첫 화면이 비어 보이고 서비스 이름의 인상이 약하다. 흩어진 로고 조각이 합쳐지며 들어가는 짧은 인트로로 첫인상을 만든다. 연출은 시안 A·B·C를 화면으로 비교해 사용자가 A(조각 합쳐짐 + 은은한 금색)를 골랐다.

**정한 사항(사용자 선택)**
- 연출은 A안: 로고 PNG를 16×3 조각으로 나눠 흩어진 조각이 날아와 합쳐지고, 조립이 끝나면 로고 글자 모양을 따라 은은한 금색이 번졌다가 흰색으로 돌아온다. 금색 픽셀은 쓰지 않는다.
- 브라우저 세션당 1회, 길이 약 2.8초(금색 번짐 시간 포함), 클릭·키로 건너뛸 수 있다.
- 메인(`/`)에만 적용한다. 심사 건·상세 화면과 기존 JS(`login.js`, `landing.js` 등)·API·서버 코드는 바꾸지 않는다. 새 파일은 `intro.css`, `intro.js`다.
- 붙여 받은 React/Next/Tailwind 컴포넌트는 쓰지 않는다(스택 변경 금지, 외부 이미지 주소 금지). 격자 조각, 섞은 무작위 시차, 모션 줄이기 생략이라는 방식만 가져와 순수 CSS/JS로 만든다.

**AC-50 · 인트로 구조와 연출**
- GIVEN: `GET /`, `intro.css`, `intro.js`
- WHEN: HTML·CSS·소스 검사
- THEN: `<body>` 맨 앞에 `aria-hidden="true"`인 `#intro.intro`가 있고 `data-logo`가 흰색 로고(`/static/credit-pulse-logo-white.png`) 주소이며 안에 `.intro-stage`가 있다. `intro.css`는 `.intro`를 평소 `display: none`, `html.intro-pending .intro`일 때 `display: grid`, `position: fixed; inset: 0`으로 두고 `.intro-stage`는 가로세로 비율 `1200 / 213`의 16×3 격자다. 외부 주소와 CSS 안의 `url(`은 없고(로고는 JS가 넣는 변수 `--intro-logo`), 금색은 `var(--gold)`를 쓴다. `intro.js`는 조각 48개(16×3)를 만들어 각자 무작위 위치·회전·크기에서 `transform`·`opacity`만으로 모이게 하고(`filter`·`top`·`left`·`width`·`height` 애니메이션 없음), 조립이 끝나면 조각을 통짜 로고(`.intro-full`)로 바꿔 이음선을 없앤 뒤 `.intro-gold`(로고 모양 마스크 + `var(--gold)`)를 투명도 0 → 0.6 → 0으로 움직인다. 전체 2800ms, 마지막 500ms는 오버레이 페이드아웃이고 곡선은 `--ease-spring`과 같은 `cubic-bezier(0.16, 1, 0.3, 1)`이다. 조각의 무작위 시차는 칸 순서를 섞어 0~450ms로 고르게 나눈다.

**AC-51 · 노출 조건과 건너뛰기**
- GIVEN: `GET /`, `intro.js`
- WHEN: HTML·소스 검사
- THEN: `<head>`의 인라인 스크립트가 sessionStorage 키 `credit-pulse-intro-seen`이 없고 `prefers-reduced-motion: reduce`가 아닐 때만 `<html>`에 `intro-pending`을 붙여 첫 페인트부터 오버레이가 덮게 하고, 6000ms 뒤에는 어떤 경우에도 이 클래스를 뗀다(JS 실패 대비). `intro.js`는 같은 조건을 다시 확인하고 재생을 시작할 때 키를 기록해 새로고침해도 다시 나오지 않게 하며(저장소를 못 쓰면 재생하지 않는다), 오버레이 클릭·아무 키는 약 150ms 페이드로 건너뛴다. 로고 주소는 `/static/`으로 시작할 때만 쓴다. `innerHTML`, `fetch(`를 쓰지 않는다. `/reviews`·`/reviews/{id}` HTML에는 인트로가 없다. JS를 쓰지 못하면(`noscript`) 인트로는 나오지 않는다.

**Out of Scope**: 시안 B·C안(금색 픽셀 연출), 매번·영구 노출, 메인 외 화면 적용, 소리, 로딩 진행 표시, 인트로 사용자 설정(끄기 스위치).


---

## 개정 6 · 2페이지 구성과 스크롤 전환

**Why**: 보유 기업(검색·슬라이드)과 기능(6칸)이 두 섹션으로 쪼개져 2페이지가 산만하고, 1페이지에서 2페이지로 넘어갈 때 화면이 뚝 끊긴다. 둘을 한 장면으로 합치고, 스크롤을 내리면 2페이지로 부드럽게 미끄러지게 한다. 구조는 시안 D안, 스크롤은 시안 B안(한 칸씩 미끄러짐 + 올라오기 + 배경 어두워짐)을 사용자가 골랐다.

**정한 사항(사용자 선택)**
- 2페이지는 한 화면을 채운다. 맨 위의 「보유 기업」 제목은 화면에서 숨기고(스크린 리더용 제목으로는 남김), 설명 문장은 작게 유지한다. 가운데에 기업 정보 슬라이드(조금 키움), 바로 아래 검색창, 그 아래에 기능 칸이 한 줄로 자동으로 흐른다.
- 별도 「기능」 섹션은 없앤다. 상단 내비의 「기능」 링크는 이 줄(`#features`)로 이어진다.
- 1↔2페이지 이동은 휠 한 번에 부드럽게 미끄러진다. 새 파일 `page-glide.js`만 추가하고 기존 JS(`landing.js` 등)·API·서버 코드는 바꾸지 않는다.
- 모션 줄이기에서는 미끄러짐, 전환 효과, 기능 줄의 흐름을 모두 끈다.

### 대체 관계
| 이전 AC | 상태 |
|---|---|
| AC-13(기능 칸 6개, 모두 disabled, 「준비 중」) | **유지** — 칸은 그대로이고 위치만 2페이지의 흐르는 줄이다(AC-53) |
| AC-26(히어로 → `#companies` → `#features` 순서) | **수정됨** — `#features`는 `#companies` 안의 기능 줄이다(AC-52) |
| AC-27(`.section-grid`의 1열 전환) | **수정됨** — `.section-grid`를 없앴다(AC-52) |
| AC-17·AC-37(슬라이드 크기 `min-height` 11rem 이상, 코드·공시 1.125rem 이상) | **유지** — 13rem, 1.625rem으로 더 크다(AC-52) |
| AC-43(기능 칸 4열 비대칭 격자) | **대체됨** — AC-53 |

**AC-52 · 2페이지 구조**
- GIVEN: `GET /`, `landing.css`
- WHEN: HTML·CSS 검사
- THEN: `<body>`에 `landing` 클래스가 있다. `#companies`(`aria-labelledby="companies-title"`) 안에 `h2#companies-title.section-title.visually-hidden`(「보유 기업」), `p.section-lead`, `.stage-core`(그 안에 `#info-panel`, `#company-search`가 이 순서), `#features.feature-row`가 이 순서로 있다. `.section-grid`, `.section-intro`, `.feature-grid`, 별도 `#features` 섹션은 없다. 내비의 「기능」 링크는 `href="#features"`다. `#companies`는 `min-height: 100vh`에 이어 `100dvh`, `display: grid`, `align-content: center`다. `.stage-core`는 `width: min(46rem, 100%)`, `.slide-name`은 `clamp(2.75rem, 6.5vw, 5.25rem)`(굵기 300 유지), `.slide`와 `.slide-viewport`의 `min-height`는 13rem, `.slide-code`·`.slide-meta`는 1.625rem이다. `.section-lead`는 `var(--text-sm)`이고 가운데 정렬이다. JS가 쓰는 id·class는 그대로다.

**AC-53 · 기능 줄(자동 흐름)**
- GIVEN: `GET /`, `landing.css`
- WHEN: HTML·CSS 검사
- THEN: `#features`는 `.feature-row`(`role="group"`, `aria-label="준비 중인 기능"`)이고 안의 `.feature-track`에 `ul.feature-list`가 둘 있다. 첫 목록은 기능 칸 6개(재무 요약, 공시 검색, 리스크 점검, 보고서, 승인 현황, 설정)가 모두 `disabled` 버튼(`.feature-tile`)이고 각각 「준비 중」이 표시되며, 둘째 목록은 끊김 없는 순환용 복사본이라 `aria-hidden="true"`다. 트랙은 `animation: feature-flow 36s linear infinite`로 `transform: translateX`만 움직이고, 마우스를 올리거나 포커스가 들어오면 `animation-play-state: paused`다. 양끝은 `mask-image`로 부드럽게 사라진다. `prefers-reduced-motion: reduce`에서는 애니메이션이 없고 복사본이 숨겨지며 칸이 줄바꿈되어 가운데 정렬된다. 칸 스타일은 `.feature-tile`(AC-18, AC-40) 그대로이고 블러를 쓰지 않는다.

**AC-54 · 1↔2페이지 미끄러짐**
- GIVEN: `GET /`, `page-glide.js`
- WHEN: 소스·동작 검사
- THEN: 마우스·트랙패드 휠을 1페이지에서 아래로 한 번 굴리면 `#companies` 위치까지, 2페이지 맨 위에서 위로 한 번 굴리면 맨 위까지 1100ms 동안 `easeInOutCubic`(시작과 끝이 부드러움)으로 미끄러진다. 위치는 프레임마다 `scrollTo({ top, left: 0, behavior: "instant" })`로 정해 CSS `scroll-behavior: smooth`와 겹치지 않게 하고, 이동 중 들어오는 휠은 막는다(`preventDefault`). 2페이지 안에서의 스크롤은 기본 동작이다. 다음은 가로채지 않는다: 모션 줄이기, `ctrl` 키가 눌린 휠(확대), 열린 `dialog`가 있을 때, `|deltaY|`가 4 미만인 미세한 휠. 키보드·터치·스크롤바는 기본 동작이다. 이동 중 `pointerdown`·`keydown`·`touchstart`가 오면 중단한다. `wheel` 리스너는 `{ passive: false }`이고 `innerHTML`, `fetch(`, 외부 주소를 쓰지 않는다.

**AC-55 · 스크롤 연동 전환 효과**
- GIVEN: `landing.css`
- WHEN: 내용을 검사
- THEN: 모두 `@supports (animation-timeline: view())`와 `@media (prefers-reduced-motion: no-preference)` 안에 있어 지원하지 않거나 모션 줄이기면 효과만 빠진다. `.hero`의 `view-timeline: --hero`로 `.hero-copy`·`.hero-stats`·`.scroll-cue`가 같은 구간(`exit 10% exit 70%`)에서 함께 위로 흐려지며 사라진다. `#companies`의 `view-timeline: --page-two`로 `.section-lead`, `.stage-core`, `.feature-row`가 서로 다른 구간으로 아래에서 올라오며 나타난다. `body.landing .page-bg::after`(`#080605`)는 투명도 0 → 0.7이 `animation-timeline: scroll(root)`, `animation-range: 0 90vh`로 연결되어 내려갈수록 사진이 가라앉는다. 움직이는 속성은 `translate`(transform 계열)와 `opacity`뿐이다. `.scroll-cue`가 이미 `transform`을 쓰므로 개별 속성 `translate`를 써서 위치가 틀어지지 않게 한다.

**Out of Scope**: 키보드·터치로 미끄러지기, 전체 관성 스크롤(시안 C), 스냅(시안 A), 커튼 효과, 기능 칸을 눌렀을 때의 동작, 기능 칸 호버 안내.


---

## 개정 7 · 2페이지 탭형 무대와 자동 전환

**Why**: 2페이지의 기업 정보, 검색, 기능이 한 화면에 모두 나열돼 위계가 약하다. 왼쪽 큰 번호 목록과 오른쪽 무대로 나누면 한 번에 하나씩 또렷하게 보인다. 붙여 받은 컴포넌트(번호가 붙은 큰 글자 목록과 이미지 전환)의 구성을 따르되 순수 CSS/JS로 새로 만든다. 전환은 시안 B(올라오기 + 차례로), 보기는 자동 시연(탭이 스스로 돌아감)을 사용자가 골랐다.

**정한 사항(사용자 선택)**
- 2페이지는 탭형이다. 01 보유 기업(기업 정보 슬라이드) / 02 기업 검색 / 03 기능(준비 중인 6칸). 별도 흐르는 기능 줄은 없앤다.
- 탭을 바꿀 때 커튼 없이 올라오기 계열로 전환하고(B안), 탭은 자동으로 차례로 바뀐다. 자동으로 바뀌는 화면이므로 사용자가 멈출 수 있어야 한다.
- 새 파일은 `stage-tabs.js`다. 기존 JS(`landing.js`, `login.js`, `reviews.js` 등)·API·서버 코드는 바꾸지 않고, JS가 쓰는 id·class(`#info-panel`, `#company-search`, `#slide-*`)는 그대로 둔다.
- 한 탭이 머무는 시간(6초)은 정하지 않은 값이라 제안값이다. 상수 하나(`AUTO_MS`)로 바꿀 수 있다.

### 대체 관계
| 이전 AC | 상태 |
|---|---|
| AC-13(기능 칸 6개, 모두 disabled, 「준비 중」) | **유지** — 칸은 03 패널 안에 있다(AC-56) |
| AC-26(섹션 순서) | **수정됨** — `#companies` 안의 탭 구조다(AC-56) |
| AC-52(기업 정보·검색·기능 줄의 세로 구성) | **대체됨** — AC-56 |
| AC-53(자동으로 흐르는 기능 줄) | **대체됨** — 03 패널의 정지된 6칸(AC-56) |
| AC-55(스크롤 연동 전환 효과) | **수정됨** — 올라오는 대상이 `.stage-nav`, `.stage-body`다. 나머지(1페이지 사라짐, 배경 어두워짐)는 그대로 |

**AC-56 · 탭형 구조**
- GIVEN: `GET /`, `landing.css`
- WHEN: HTML·CSS 검사
- THEN: `#companies` 안에 `.stage-wrap`(좁은 화면에서는 한 열)이 있고, `.stage-nav`(`ul[role="tablist"][aria-orientation="vertical"][aria-label="보유 기업 화면 선택"]`)와 `.stage-body`가 이 순서로 있다. 탭은 `button.stage-tab[role="tab"]` 3개(보유 기업, 기업 검색, 기능)로 번호 `01`~`03`(`.stage-num`)·제목(`.stage-title`)·설명(`.stage-sub`)을 담고, 첫 탭만 `aria-selected="true"`이며 각 탭의 `aria-controls`가 같은 순서의 패널 id(`stage-panel-0`, `stage-panel-1`, `features`)다. `.stage-body` 안에 `.stage-panel[role="tabpanel"][aria-labelledby]` 3개가 있다. 패널 0은 `#info-panel`(속의 `#slide-*` 포함), 패널 1(`.stage-search`)은 `h3`, `.section-lead`, `#company-search`, 예시 안내(`.stage-hint`, 「LG화학 · 00356361」은 보유 기업 데이터에 실제로 있는 값), 패널 2(`#features`)는 `.feature-tile` 6개(모두 `disabled` 버튼, 각각 「준비 중」)와 「아직 준비 중인 기능입니다.」다. 탭 목록 아래에 처음에는 `hidden`인 `#stage-toggle`이 있다. `h2#companies-title`은 화면에서 숨겨진 채 남는다. `.stage-core`, `.feature-row`, `.feature-track`, `.feature-list`, `.feature-grid`와 별도 `#features` 섹션은 없다. 내비의 「기능」 링크는 `href="#features"`다. `<head>`의 인라인 스크립트가 `<html>`에 `js` 클래스를 붙이며(첫 페인트부터 탭 레이아웃으로 보이게 한다), CSS는 이 클래스가 없으면(JS가 꺼져 있으면) 탭 목록을 숨기고 패널 셋을 모두 쌓아 보여 준다(JS가 없어도 내용은 보인다). `html.js`일 때는 패널이 한 칸(`grid-area: 1 / 1`)에 겹쳐 있고 `data-active`인 것만(전환 중에는 `data-leaving`도) 보인다. 숨은 패널은 `visibility: hidden`이라 낭독기에도 읽히지 않는다. `.stage-wrap`은 `grid-template-columns: minmax(0, 25rem) minmax(0, 1fr)`, `.stage-title`은 `var(--font-serif)`, 활성 탭만 투명도 1이고 번호가 금색이다. `#companies`의 한 화면 채우기, 기업명 크기, 슬라이드 크기는 AC-52를 유지한다.

**AC-57 · 탭 전환(올라오기 + 차례로)**
- GIVEN: `stage-tabs.js`
- WHEN: 소스·동작 검사
- THEN: 탭을 바꾸면 나가는 패널은 투명도 1 → 0과 위로 20px(300ms)로 사라지고, 들어오는 패널은 투명도 0 → 1(280ms, 100ms 지연)로 나타나며 그 안의 `[data-rise]` 요소가 아래 44px에서 700ms 동안 120ms + 60ms × 순번의 지연으로 하나씩 올라온다. 곡선은 `--ease-spring`과 같은 `cubic-bezier(0.16, 1, 0.3, 1)`이고 움직이는 속성은 `transform`·`opacity`뿐이다. 전환 중에는 두 패널이 겹쳐 보이고(`data-leaving`), 끝나면 애니메이션을 지우고 정리한다. 탭 표시(`aria-selected`)는 가리키는 즉시 바뀌고 패널은 전환이 끝난 뒤 맞추므로, 빠르게 여러 번 바꿔도 마지막으로 가리킨 탭에서 멈춘다. 조작은 마우스 올리기(마우스만, 110ms 머문 뒤), 클릭, 포커스, 방향키·Home·End이고, 검색 폼을 제출하면 01로 돌아가며, `a[href="#features"]` 클릭은 03을 연다. 모션 줄이기에서는 애니메이션 없이 바로 바뀐다.

**AC-58 · 자동 전환(자동 시연)**
- GIVEN: `stage-tabs.js`
- WHEN: 소스·동작 검사
- THEN: 탭은 `AUTO_MS`(6000ms)마다 01 → 02 → 03 → 01 순서로 자동으로 바뀐다. 다음 경우에는 멈춘다: 마우스가 `.stage-wrap` 위에 있을 때, 키보드 포커스(`:focus-visible`)가 `.stage-wrap` 안에 있을 때(검색 입력 중 포함. 마우스로 누른 버튼에 남은 포커스는 멈춤 이유가 아니므로 「재생」을 눌러도 계속 멈춰 있지 않는다), 문서가 숨겨졌을 때, 2페이지가 35% 미만으로 보일 때, 사용자가 `#stage-toggle`로 멈췄을 때. 직접 멈춤(`userPaused`)과 그 밖의 멈춤은 따로 관리하고, 직접 고르면 시간을 처음부터 다시 센다. `#stage-toggle`은 JS가 준비되면 탭 목록 아래에 표시되는 검은색 토글이다. 옆의 「자동 전환」 문구가 화면과 스크린 리더에서 같은 이름으로 유지되며, `role="switch"`와 `aria-checked`로 켜짐 상태를 알린다. 켜짐은 노란색 ON 글자와 오른쪽 노브, 꺼짐은 회색 OFF 글자와 왼쪽 노브로 표시한다. 토글은 사용자 설정(`userPaused`)을 표시하며 마우스·포커스·화면 가시성에 따른 임시 멈춤 중에도 그 설정은 유지된다. 키보드 포커스에는 금색 윤곽이 있다. 모션 줄이기에서는 멈춘 채로 시작한다(`userPaused`). 자동으로 바뀌는 동안 `.stage-body`의 `aria-live`는 `off`다. `innerHTML`, `fetch(`, 외부 주소를 쓰지 않는다. 슬라이드 자동 넘김(5초, AC-17)은 그대로 따로 동작한다.

**AC-59 · 긴 기업명도 한 줄**
- GIVEN: `landing-data.js`(가장 긴 이름은 「삼성에피스홀딩스」 8자), `landing.css`, `slide-fit.js`
- WHEN: CSS·소스 검사(실제 폭은 화면으로 따로 확인)
- THEN: `html.js`일 때 `.slide-name`은 `white-space: nowrap`, `overflow-wrap: normal`이라 한 줄에 놓인다. 새 파일 `slide-fit.js`가 이름이 넘칠 때만 `font-size`를 인라인 px로 줄여 사용 가능한 폭(`clientWidth` − 좌우 패딩)에 맞추고(0.5px 단위로 내림, 이름이 짧아 넘치지 않으면 CSS의 기본 크기 그대로), 하한은 28px이다. 하한에서도 넘치면 `white-space: normal`로 되돌려 줄바꿈을 허용한다. 슬라이드가 바뀔 때(`#slide-viewport`의 자식 추가), 창·패널 크기가 바뀔 때(`ResizeObserver`, 없으면 `resize`), 글꼴이 로드된 뒤(`document.fonts.ready`)마다 다시 맞춘다. `landing.js`·`landing-data.js`는 바꾸지 않으며 `innerHTML`, `fetch(`, 외부 주소를 쓰지 않는다. 순수 함수 `fitFontSize`는 `{ fontPx, availableWidth, neededWidth, minPx }`를 받아 `{ fontPx, fits }`를 돌려준다.

**Out of Scope**: 터치 스와이프로 탭 바꾸기, 탭마다 머무는 시간 설정 화면, 키보드 단축키, 03 패널의 칸을 눌렀을 때의 동작, 시안 A·C·D 전환.


---

## 개정 8 · 「기능」 탭(03)의 칸 동작

상태: **승인됨**(안내 문장은 「로그인 후 사용 가능합니다.」로 확정). 이후 추가: 링크 칸을 누르면 로그인 창을 열고 로그인 뒤 누른 칸의 도착지로 이동(AC-64·65, 사용자 요청).

### Why

03 패널의 6칸은 모두 「준비 중」이라 눌러도 아무 일이 없다. 그런데 이 제품에는 이미 심사 건 목록·질의(`/reviews`, `/reviews/{id}`)와 판정 규칙 관리(`/reviews`의 「내 판정 규칙」)가 있어서, 칸 일부는 새 API 없이 그 화면으로 이어 줄 수 있다. 반면 일부 칸은 만들 수 없는 기능이다. 사용자와의 질의응답으로 무엇을 동작시킬지 정했다.

확인한 사실:
- 랜딩(`/`)은 로그인한 사용자를 `/reviews`로 바로 보낸다(`login.js`). 03 패널은 **로그인하지 않은 사람만** 본다. 따라서 칸 안에 로그인해야 알 수 있는 데이터(승인 대기 건수 등)를 띄울 수 없고, 동작하는 칸은 로그인 뒤 화면으로 가는 입구다. 해당 화면이 401을 받으면 로그인 화면으로 보내진다(`reviews-web-ui` EC-03).
- 재무 요약은 SPEC.md가 재무비율·등급·부도 예측을 범위 밖으로 두고 재무 데이터 API도 없어 **불가**다.
- 공시 검색은 검색이 심사 질의 안에서만 돌고 단독 API(`/api/v1/chunks/{id}` 포함)가 없고, 보고서는 `review_memos`를 읽는 API가 없어 둘 다 **새 API가 필요한 별도 스펙 대상**이다.

### Goal

- 03 패널의 칸을 6개에서 5개로 줄이고(재무 요약 제거), 그중 3개(리스크 점검, 승인 현황, 내 판정 규칙)를 링크로 동작시킨다. 공시 검색·보고서는 `disabled`와 「준비 중」을 그대로 둔다.
- 새 API는 만들지 않는다. 새 화면은 `/approvals` 하나이고 별도 스펙(`approvals-web-ui.md`)으로 나눈다.
- 로그인하지 않은 사람이 링크 칸을 누르면 `/reviews`가 401로 `/`에 되돌려 보내는 어색한 화면 전환 대신, 랜딩에서 로그인 창을 바로 연다(AC-64). 로그인에 성공하면 누른 칸의 도착지로 간다(AC-65). 이를 위해 새 파일 `feature-login.js`만 랜딩에 더하고 `login.js`는 이동 도착지를 고르는 부분만 고친다. 랜딩 계열 JS의 `fetch(` 금지 테스트는 그대로다(데이터를 불러오는 코드는 `/approvals` 화면의 `approvals.js`에만 있다).
- **Out of Scope**: 재무 요약, 공시 검색·보고서 동작(별도 스펙), 칸 안의 건수·상태 표시, 로그인한 사용자가 랜딩을 보게 하는 변경(`login.js` 리다이렉트 변경), 계정·알림 등 다른 설정, 칸 순서 변경, 03 패널 격자 열 수 변경, 그 밖의 곳(상단·히어로 로그인 버튼 등)에서 로그인했을 때의 도착지 변경(항상 `/reviews`).

### What

| 칸(순서) | 동작 | 도착지 | 데이터 |
|---|---|---|---|
| 공시 검색 | 준비 중(`disabled`) | 없음 | 없음 |
| 리스크 점검 | 링크 | `/reviews`(심사 건을 만들고 질의) | 없음(정적 링크) |
| 보고서 | 준비 중(`disabled`) | 없음 | 없음 |
| 승인 현황 | 링크 | `/approvals`(새 화면, `approvals-web-ui.md`) | 없음(정적 링크) |
| 내 판정 규칙(이전 이름 「설정」) | 링크 | `/reviews#rules-section` | 없음(정적 링크) |

- 동작하는 칸의 보조 글자는 「준비 중」 대신 도착지를 알리는 짧은 문구다: 리스크 점검 「심사 건 만들기·질의」, 승인 현황 「승인 대기 건 모아 보기」, 내 판정 규칙 「판정 규칙 보기·삭제」.
- 패널 아래 안내 문장 「아직 준비 중인 기능입니다.」는 「로그인 후 사용 가능합니다.」로 바꾼다. 로그인하면 사라진다: 로그인한 사용자는 랜딩에서 `/reviews`로 이동하므로(`login.js`) 문장이 보이는 것은 로그인하지 않은 때뿐이며, 이를 위해 JS를 더하지 않는다.
- 5칸은 기존 격자에서 3열이면 3+2, 2열이면 2+2+1로 채워진다. 마지막 줄이 비는 것은 이번에 손대지 않는다.
- 비로그인 사용자가 동작하는 칸을 누르면 화면이 넘어가지 않고 로그인 창이 열린다. 로그인에 성공하면 누른 칸의 도착지(`/reviews`, `/approvals`, `/reviews#rules-section`)로 이동한다. 창을 닫으면 도착지 기억은 지워지고, 이후 다른 곳에서 로그인하면 `/reviews`로 간다. JS가 꺼져 있으면 링크가 그대로 열려 `/reviews`가 401로 `/`에 되돌려 보낸다.

### How

- `backend/templates/login.html`의 `.stage-features`에서 「재무 요약」 `li`를 지우고 「설정」을 「내 판정 규칙」으로 바꾼다. 동작하는 칸 3개는 `<button disabled>`를 `<a class="feature-tile" href="…">`로 바꾸고 `li[data-rise]`, `.feature-name`, `.feature-note`는 그대로 둔다. 준비 중인 2칸은 현재 마크업 그대로다.
- `backend/static/landing.css`에 `a.feature-tile`(글자색 상속, `text-decoration: none`, `cursor: pointer`)과 `a.feature-tile:focus-visible`(금색 윤곽)만 더한다. 칸의 호버·`transform`·`opacity`는 기존 규칙을 쓴다. 랜딩은 `reviews.css`를 불러오지 않고 `login.css`에는 `a` 전역 규칙이 없다. 전역 `button` 규칙(`login.css`)은 `a`에 적용되지 않으므로 링크 칸은 영향이 없고, 준비 중인 칸(`button:disabled`)은 기존 `.feature-tile:disabled` 규칙이 그대로 덮는다.
- 새 파일 `backend/static/feature-login.js`(`login.html` 맨 아래에 `type="module"`로 추가)는 `#features`의 `a.feature-tile` 클릭에서 `preventDefault()`하고, 도착지가 허용 목록(`/reviews`, `/approvals`, `/reviews#rules-section`)에 있을 때만 `sessionStorage`의 `login-next`에 저장한 뒤 `#open-login` 버튼을 누른다(`landing.js`의 로그인 창 열기를 그대로 쓴다). `#login-dialog`의 `close` 이벤트에서 `login-next`를 지운다. 저장소를 쓸 수 없으면(예외) 저장 없이 로그인 창만 연다. `login.js`의 `createLoginController`는 선택 인자 `storage`를 받고, 이동할 때 `login-next`를 읽어 허용 목록에 있으면 그곳으로, 아니면 `/reviews`로 보내며 읽은 값은 지운다. 이동 중 문구(「심사 건 화면으로 이동하는 중…」)는 바꾸지 않는다.
- `stage-tabs.js`, `landing.js`, 모든 API·서버 코드는 바꾸지 않는다(`login.js`는 위 이동 도착지 부분만). 단 `/approvals`를 위해 `backend/main.py`에 라우트 한 개를 더한다(`approvals-web-ui.md` How).
- 테스트: `tests/test_login_web_ui.py`의 6칸 전제(라벨 목록, `len(tiles) == 6`, `준비 중 >= 6`, 패널 2의 `disabled` 버튼 6개와 `준비 중</span>` 6개, 안내 문장)를 이 개정의 5칸 기준으로 고친다. 이 파일은 CRLF이므로 줄바꿈을 바꾸지 않고, 스테이징은 `git -c core.autocrlf=false add`로 한다. `tests/stage_tabs.test.mjs`, `tests/landing_web_ui.test.mjs`는 6칸에 의존하지 않는지 확인하고 의존하면 고친다.

### 대체 관계
| 이전 AC | 상태 |
|---|---|
| AC-13(기능 칸 6개, 모두 disabled, 「준비 중」) | **대체됨** — 5칸, 3칸은 링크(AC-60) |
| AC-43(기능 칸 4열 비대칭 격자) | 개정 6에서 이미 대체됨. 이번에도 건드리지 않음 |
| AC-56의 「`.feature-tile` 6개(모두 `disabled` 버튼, 각각 「준비 중」)와 「아직 준비 중인 기능입니다.」」 | **수정됨** — 패널 2(`#features`)는 AC-60의 5칸과 새 안내 문장이다. 탭·패널 구조의 나머지는 그대로 |
| AC-53의 칸 이름 목록(재무 요약 … 설정) | 개정 7에서 이미 대체됨 |
| AC-57·AC-58(전환·자동 전환)·`#stage-toggle` | **그대로** |

### AC (Given-When-Then)

**AC-60 · 03 패널의 5칸**
- GIVEN: `GET /`
- WHEN: HTML 검사
- THEN: `#features`(`.stage-panel`) 안의 `.stage-features`에 `li[data-rise]`가 5개이고 이름은 순서대로 공시 검색, 리스크 점검, 보고서, 승인 현황, 내 판정 규칙이다. 「재무 요약」과 「설정」이라는 칸 이름은 없다. 공시 검색·보고서는 `<button class="feature-tile" type="button" disabled>`이고 각각 `.feature-note`가 「준비 중」이다. 그 밖의 칸은 `<a class="feature-tile">`이고 `href`가 각각 `/reviews`, `/approvals`, `/reviews#rules-section`이며 `.feature-note`는 각각 「심사 건 만들기·질의」, 「승인 대기 건 모아 보기」, 「판정 규칙 보기·삭제」다. 03 탭의 설명(`#stage-tab-2 .stage-sub`)은 「심사 건·승인 현황·판정 규칙」이다(이전 「아직 준비 중인 기능」은 사실과 달라져 바꿨다). 안내 문장(`p.stage-hint`)은 「로그인 후 사용 가능합니다.」다. 「준비 중」 글자는 정확히 두 곳(두 칸)에만 있다.

**AC-61 · 링크 칸의 스타일과 우선순위**
- GIVEN: `landing.css`, `login.css`
- WHEN: CSS 검사
- THEN: `a.feature-tile`은 `box-sizing: border-box`(`button`의 기본값과 같아야 `width: 100%`가 패딩 때문에 넘치지 않는다), 글자색이 `var(--on-dark)`(상속 밑줄 없음 `text-decoration: none`), `cursor: pointer`이고 `a.feature-tile:focus-visible`에 금색 윤곽(`outline`)이 있다. 링크 칸의 `min-height`는 준비 중 칸과 같은 `5.5rem`이다(`.stage-features a.feature-tile`). 링크 칸의 배경·테두리·반경·그림자는 `.feature-tile`(AC-18, AC-40)의 값을 그대로 쓰며 `backdrop-filter`가 없다. `.feature-tile:disabled`(준비 중 칸)의 규칙은 바뀌지 않았다. `login.css`의 전역 `button`·`button.secondary` 규칙은 `a.feature-tile`에 적용되지 않는다(새 `button`을 만들지 않았으므로 우선순위 충돌이 없다).

**AC-62 · 접근성·모션·기존 계약**
- GIVEN: `GET /`, `stage-tabs.js`, `landing.css`
- WHEN: HTML·CSS·소스 검사
- THEN: 링크 칸은 키보드로 Tab 순서에 들어가고 Enter로 열린다. 준비 중인 칸은 여전히 `disabled`라 순서에 들어가지 않는다. `prefers-reduced-motion: reduce`에서 칸의 전환·`transform`은 없다. 움직이는 속성은 `transform`·`opacity`뿐이다. `#info-panel`, `#company-search`, `#slide-*`, `#stage-toggle`, `a[href="#features"]`는 그대로이고 `stage-tabs.js`·`landing.js`에는 변경이 없으며 `innerHTML`, `fetch(`, 외부 주소가 새로 생기지 않았다. 랜딩 HTML에 더해진 JS는 `feature-login.js` 하나다(AC-64).

**AC-63 · 이동(실제 화면)**
- GIVEN: 로컬 서버, 로그인하지 않은 브라우저
- WHEN: 03 탭에서 「리스크 점검」, 「승인 현황」, 「내 판정 규칙」을 차례로 누름
- THEN: 화면이 넘어가지 않고 로그인 창이 열린다(AC-64). 로그인에 성공하면 각각 `/reviews`, `/approvals`, `/reviews#rules-section`으로 이동하고 `/reviews#rules-section`은 「내 판정 규칙」 섹션 위치로 열린다(자동 테스트가 아닌 브라우저 확인 항목이며 보고에서 확인한 것과 못 한 것을 구분한다).

**AC-64 · 링크 칸을 누르면 로그인 창**
- GIVEN: `GET /`(로그인하지 않음), `feature-login.js`
- WHEN: `#features`의 `a.feature-tile`을 클릭(마우스·Enter)
- THEN: 기본 이동은 막히고(`preventDefault`) `#open-login`이 눌려 `#login-dialog`가 열린다. 도착지가 허용 목록(`/reviews`, `/approvals`, `/reviews#rules-section`)에 있을 때만 `sessionStorage`의 `login-next`에 저장하며, 목록에 없는 `href`나 저장소 예외면 저장 없이 로그인 창만 연다. 준비 중인 칸(`button:disabled`)은 영향이 없다. `#login-dialog`가 닫히면(`close`) `login-next`는 지워진다. `login.html`은 `feature-login.js`를 `type="module"`로 불러오고, 파일에는 `innerHTML`, `fetch(`, `http`, `eval(`이 없다. JS가 없으면 `href`가 그대로 동작한다.

**AC-65 · 로그인 후 도착지**
- GIVEN: `login.js`의 `createLoginController({ document, fetch, navigate, storage })`
- WHEN: 로그인에 성공하거나 이미 로그인된 세션을 확인해 이동할 때
- THEN: `storage`의 `login-next`가 허용 목록에 있으면 그 값으로, 없거나 허용 목록 밖이면(예: 외부 주소·`//host`) `/reviews`로 `navigate`하고 읽은 값은 지운다. `storage`를 주지 않거나 읽기가 예외면 `/reviews`다. 기존 이동 문구와 비밀번호 비우기 동작은 그대로다.
