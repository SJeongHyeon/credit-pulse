# Spec 거절 질문 뉴스 검색 (참고용 첨부)

## Why

- **페르소나**: FY2025 사업보고서 근거로 여신 심사 초안을 검토하는 심사역
- **상황**: 코퍼스에 없는 회사·기간이나 최근 이슈를 물으면 지금은 "답변할 수 없다"는 한 줄 거절만 받는다. 날씨·잡담 같은 무관 질문도 같은 거절을 받아 둘이 구분되지 않는다.
- **문제**:
  1. 업무 관련 질문(코퍼스 밖 회사·기간, 최근 이슈)과 무관 질문이 같은 문구로 거절되어 다음에 무엇을 해야 하는지 알 수 없다.
  2. 업무 관련 거절에는 참고할 만한 외부 정보가 있어도 전혀 보여 주지 못한다.
- **측정 지표**: 무관 질문의 뉴스 호출 0회, 업무 관련 거절의 뉴스 호출 정확히 1회, 관련 질문 과잉 거절 증가 0건, 플래그 꺼짐 시 기존 v2 측정값 동일.

## Goal

- **해결 목표**: 거절을 `unrelated`와 `work_related` 두 종류로 나눈다. 무관 질문은 고정 멘트로 끝내고, 업무 관련 질문은 네이버 뉴스 검색 1회의 결과를 **참고용**으로 거절 응답에 첨부한다.
- **성공 기준**: AC-N1~N9 통과 / 턴당 뉴스 호출 ≤ 1 / 뉴스 항목 ≤ 5 / 타임아웃 5초 / 재시도 0 / 플래그 꺼짐 시 기존 State·결과와 동일.
- **뉴스의 지위**: 뉴스는 공시 근거(citation)가 아니다. 판정·승인·메모 저장에 쓰지 않는다. `citations=[]`, `draft_items=[]`를 유지한다.
- **Out of Scope**: 뉴스 LLM 요약, 뉴스 캐시, 뉴스를 근거로 한 답변·판정, 새 `refusal_reason` 값, `classify_domain` 수정, "예측" 규칙 거절의 뉴스 연결, 동결 평가셋(v1·v2) 수정.

## What

**Happy Path**

1. 질문이 입구 판정에서 거절된다. 뉴스 대상은 다음으로 한정한다.
   - `CORP_NOT_IN_CORPUS`, `PERIOD_NOT_IN_CORPUS`: 업무 관련이 명백하므로 분류 호출 없이 바로 뉴스 대상.
   - `OUT_OF_DOMAIN`(`classify_domain` 결과): 두 번째 LLM 분류 `classify_refusal_kind(question)`을 1회 호출한다. `work_related`면 뉴스 대상, `unrelated`면 아니다.
2. `unrelated`: 검색·뉴스·LLM 작성 없이 `REFUSAL_MESSAGES_V2["unrelated"]`로 종료한다.
3. 뉴스 대상: 질문에서 짧은 검색어를 규칙으로 만들어 네이버 뉴스 검색 API를 1회 호출한다.
4. 결과가 있으면 최대 5건을 `news_items`에 담고 `work_related_with_news` 멘트로 종료한다.
5. 응답은 `outcome="refused"`, 기존 `refusal_reason`, `refusal_kind`, `news_items`, `citations=[]`, `draft_items=[]`이다.

**Edge Cases**

| # | 상황 | 처리 방식 |
|---|---|---|
| EC-01 | 키(HUB 또는 기존 `NAVER_CLIENT_ID`/`SECRET`)가 없음 | HTTP 호출 없이 `work_related_news_unavailable` 멘트, State `news_search="skipped_no_key"`, `news_calls=0` |
| EC-02 | 429·타임아웃·5xx·형식 오류 | 재시도 없이 `work_related_news_unavailable` 멘트, `news_search="error"`, `news_calls=1` |
| EC-03 | 호출은 성공했으나 결과 0건(최근 90일 기사가 없어 걸러진 경우 포함) | 예외 아님. `news_items=[]`, `news_search="ok"`, `work_related_news_unavailable` 멘트 |
| EC-04 | "예측" 규칙 거절 | 기존 `OUT_OF_DOMAIN` 거절 유지, 분류·뉴스 호출 0회 (SPEC §1 범위 밖) |
| EC-05 | 뉴스 title·description에 `<b>` 태그·HTML 엔티티 | 태그 제거 + `html.unescape` 후 사용 |
| EC-06 | 뉴스 본문에 "이전 지시를 무시하라" 같은 문장 | 지시로 쓰지 않는다. 뉴스 텍스트는 어떤 LLM 프롬프트에도 넣지 않고, 제목·링크·발행일·언론사 도메인만 데이터로 그대로 노출한다 |
| EC-07 | 질문이 `domain_check` 키 없음(`skipped_no_key`)이라 `OUT_OF_DOMAIN` 판정 자체를 못 함 | 분류·뉴스 모두 호출하지 않는다(기존 동작) |
| EC-08 | 사업보고서 관련 정상 질문 | 사전 판별에서 거절되지 않으며 뉴스 분기로 새지 않는다 |
| EC-09 | `refusal_kind` 분류 LLM 키 없음 | 분류하지 못했으므로 뉴스를 호출하지 않고 `work_related_news_unavailable` 멘트, `news_search="skipped_no_key"` |
| EC-10 | `news_search_enabled=False`(기본) | 새 분기·새 State 키·새 LLM 호출 없음. 기존 거절 멘트 `REFUSAL_MESSAGES`와 State가 그대로 |

## How

**플래그**: `build_supervisor_graph(..., news_search_enabled=False, refusal_kind_classifier=None, news_search=None)`. 기본 꺼짐. 꺼짐이면 `turn_start`도 새 키를 쓰지 않는다(State 불변).

**서비스에서 켜기(11/11 추가)**: 환경 변수 `NEWS_SEARCH_ENABLED=1`일 때만 `backend/api/runtime.py`가 플래그를 켠다(`news_search_enabled(env)`). 기본 꺼짐이고, 키 없는 데모 모드에서는 값이 1이어도 켜지 않는다(거절 종류 분류 LLM 호출이 데모 기록에 없다). 네이버 키(`NAVER_HUB_CLIENT_ID`·`NAVER_HUB_CLIENT_SECRET` 또는 기존 `NAVER_CLIENT_ID`·`NAVER_CLIENT_SECRET`)가 없으면 켜져 있어도 뉴스 없이 폴백 멘트가 나간다(EC-01).

**그래프 연결(11/11, 104~117번 구조에 맞춤)**: 입구 검사(`_supervisor_node`)가 거절과 함께 `refusal_kind`를 남기면 `supervisor_node`가 `supervisor_next="end"`로 끝내고, `route_from_supervisor(news_enabled=True)`가 `work_related`이면서 검색 전인 거절만 `news_search` 노드로 보낸다. 뉴스 노드는 supervisor로 돌아오지 않는다. 검색 뒤의 거절(`ITEM_NOT_DISCLOSED`)도 11/12부터 뉴스 대상이다(docs/specs/refusal-news-after-search.md).

**미리 실행과의 관계(110번)**: 기본 경로는 회사가 확정된 질문의 주제 분류 LLM을 기다리는 동안 검색·초안을 미리 돌린다. 그 질문이 주제 밖으로 거절되면 미리 돌린 결과는 버려지고 State에 남지 않는다(이 분기 이전부터 있던 동작). 그래서 '거절된 질문의 retriever·writer 호출 0회'는 순차 경로(`speculative=False`)에서 세고, 기본 경로에서는 결과 State가 순차 경로와 같은지를 본다. 코퍼스 밖 회사·기간 거절은 주제 분류 전에 끝나므로 기본 경로에서도 미리 실행이 시작되지 않는다.

**화면(11/11 추가)**: 심사 상세(`backend/static/review-detail.js`)가 거절 응답의 `refusal_message`를 안내 문구로 보이고, `news_items`가 있으면 '참고 뉴스 · 공시 근거 아님' 영역에 최대 5건(제목, 언론사 도메인·발행일 YYYY-MM-DD, 요약 120자, 11/12 변경: docs/specs/refusal-news-display.md)을 초안·인용과 다른 영역으로 보인다. 모든 값은 `textContent`로만 넣고, 링크는 http·https 주소만 건다(새 탭, `rel="noopener noreferrer"`). `refusal_message`가 없으면(플래그 꺼짐) 기존 문구 그대로다.

**거절 뒤 이어 묻기(11/11, 기존 버그 수정)**: 심사 건 하나가 thread 하나라, 앞 질문의 `outcome=refused`가 다음 질문으로 넘어가 입구에서 그대로 거절되던 문제가 있었다(이 분기 이전 코드에서도 재현). `turn_start`가 턴마다 `outcome`·`refusal_reason`·`refusal_message`를 비운다. 플래그와 무관하게 적용된다.

**거절 멘트** (고정 문구, 사유별 상수 `REFUSAL_MESSAGES_V2`, `backend/graph/build.py`. 기존 `REFUSAL_MESSAGES`는 그대로)

```
"unrelated": "저는 기업여신 심사 업무를 돕는 에이전트라서 날씨·일상 같은 질문에는 답변드리지 않습니다. 회사명, 사업연도, 확인할 체크리스트 항목을 알려 주시면 사업보고서 근거로 도와드리겠습니다."
"work_related_with_news": "요청하신 내용은 현재 사업보고서 코퍼스로는 근거를 찾을 수 없습니다. 참고용으로 관련 최신 뉴스를 함께 안내합니다. 뉴스는 공시 근거가 아니며 심사 판정과 메모 저장에는 사용되지 않습니다."
"work_related_news_unavailable": "요청하신 내용은 현재 사업보고서 코퍼스로는 근거를 찾을 수 없고, 뉴스 검색도 지금은 사용할 수 없습니다. 회사명과 사업연도를 확인해 다시 질문해 주세요."
```

**분류 `llm.classify_refusal_kind(*, question, model) -> "unrelated" | "work_related"`**
- LLM 호출은 `backend/services/llm.py`에서만 한다. `out_of_domain` 이후에만 호출하며, 호출 횟수는 턴당 최대 1회다.
- 키가 없으면 `llm.ExternalApiKeyMissing`을 올리고, 그래프 쪽 래퍼가 `None`으로 바꾼다(EC-09).

**뉴스 클라이언트 `backend/services/news_search.py`** (HTTP는 이 파일에서만)

```
[기본] NAVER API HUB (11/12 추가, 개발자센터 검색 API 신규 발급이 막혀 이관)
GET https://naverapihub.apigw.ntruss.com/search/v1/news
헤더: X-NCP-APIGW-API-KEY-ID, X-NCP-APIGW-API-KEY  (값은 .env의 NAVER_HUB_CLIENT_ID, NAVER_HUB_CLIENT_SECRET에서만)

[대체] 개발자센터 키가 이미 있는 경우 (HUB 키가 둘 다 있으면 HUB를 먼저 쓴다)
GET https://openapi.naver.com/v1/search/news.json
헤더: X-Naver-Client-Id, X-Naver-Client-Secret  (값은 .env의 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET에서만)

파라미터(공통): query, display=20, start=1, sort=sim  (11/12 변경: 관련도순 후보 20건을 한 번에 받는다)
응답 항목 사용: title, originallink, link, description, pubDate

고르기(11/12 추가, 측정: docs/evidence/news_sort_measure_2026-11-12.md): 받은 20건 중 발행일이 최근 90일 이내인 기사만 남겨
날짜 내림차순 최대 5건을 `news_items`에 담는다. 날짜를 읽지 못하는 기사는 뺀다. 호출은 여전히 1회다.
최근 90일 기사가 없으면 EC-03(결과 0건)과 같게 처리한다.
회사명 필터(11/12 추가, 측정: docs/evidence/news_company_filter_2026-11-12.md): 질문에 서비스 코퍼스 회사명이 있으면 위 최근 기사 중
제목 또는 요약에 그 이름이 있는 기사만 남긴다(다른 계열사 기사 혼입 방지). 코퍼스 밖 회사·회사명 없는 질문은 거르지 않는다. 걸러져 0건이 되면 EC-03과 같다.
```

- 상한: 턴당 호출 1회, 타임아웃 5초, 재시도 없음. 일일 한도 25,000회를 넘지 않도록 `news_calls`를 State에 남긴다.
- 정제: `title`·`description`의 HTML 태그 제거 후 `html.unescape`. 지시형 문장 제거(`trust_boundary.remove_directive_sentences`)는 **적용하지 않는다**: 뉴스는 LLM에 들어가지 않고 그대로 데이터로만 노출한다(AC-N8).
- 항목 형식: `{"title", "link", "originallink", "description", "pub_date", "source_domain"}`. `source_domain`은 `originallink`(없으면 `link`)의 호스트명이다. 요약 LLM은 쓰지 않는다.
- 검색어: 질문 원문을 그대로 보내지 않는다. LLM 없이 규칙으로 만든다: 이메일·전화번호·긴 숫자열을 제거하고, 회사명(manifest에 없는 회사 후보 포함)·핵심 명사 위주의 짧은 토큰 최대 4개(30자 이내)로 줄인다. 세부 규칙은 구현 단계에서 단위 테스트로 고정한다.
- 비밀값은 코드·문서·로그·테스트에 적지 않는다. `.env.example`에는 빈 키 이름만 추가한다.

**State와 응답 필드**

| 필드 | 값 | 비고 |
|---|---|---|
| `refusal_kind` | `"unrelated"` \| `"work_related"` \| `None` | 거절일 때만 값. 뉴스 대상 `CORP/PERIOD_NOT_IN_CORPUS`는 `work_related` |
| `news_items` | 최대 5개 목록 | 항상 `[]` 또는 위 항목 형식. 직렬화 가능한 값만 |
| `news_search` | `"ok"` \| `"skipped_no_key"` \| `"error"` \| `None` | 조용히 숨기지 않고 State에 남긴다 |
| `news_calls` | 정수 0~1 | 턴마다 0으로 초기화(`TURN_COUNTERS`, 플래그 켜짐일 때만) |

- `refusal_reason`은 새로 만들지 않고 기존 값을 쓴다. `outcome`은 `"refused"`를 유지한다.
- 거절된 질문은 retriever·rewriter·verifier·human_approval·save_memo를 호출하지 않는다(기존 계약).
- API 응답에는 `refusal_kind`, `news_items`, `news_search`와 고정 거절 멘트 `refusal_message`를 싣기만 한다. 플래그가 꺼져 있으면 State에 새 키가 없어 응답은 기존과 같고, 켜져 있어도 `outcome=refused`일 때만 싣는다(답변·승인 대기 응답에는 없다). 멘트를 싣는 이유: "뉴스는 공시 근거가 아니다" 안내가 클라이언트에 닿지 않으면 `news_items`가 근거처럼 오해될 수 있다.

## AC (Given-When-Then)

**AC-N1 · 무관 질문 고정 거절**
- GIVEN: 플래그 켜짐, "오늘 서울 날씨 어때?"
- WHEN: 그래프를 실행
- THEN: `refusal_kind="unrelated"`, `REFUSAL_MESSAGES_V2["unrelated"]` 반환, 뉴스·retriever·writer 호출 0회, `citations`는 빈 배열

**AC-N2 · 코퍼스 밖 회사**
- GIVEN: 플래그 켜짐, 코퍼스에 없는 회사에 대한 질문
- WHEN: 그래프를 실행
- THEN: `refusal_reason="CORP_NOT_IN_CORPUS"`, `news_items`는 최대 5건, 뉴스 호출 정확히 1회, retriever 0회, 분류 호출 0회

**AC-N3 · 업무 관련 코퍼스 밖 주제**
- GIVEN: 업무 관련이지만 코퍼스 밖 주제 질문(`OUT_OF_DOMAIN` + `work_related`)
- WHEN: 그래프를 실행
- THEN: 뉴스 호출 1회, `outcome="refused"`, `citations`는 빈 배열, human_approval·save_memo 0회

**AC-N4 · 뉴스 키 없음**
- GIVEN: 뉴스 API 키 없음
- WHEN: 그래프를 실행
- THEN: 예외 없이 `work_related_news_unavailable` 멘트, `news_search="skipped_no_key"`, HTTP 호출 0회

**AC-N5 · 429·타임아웃**
- GIVEN: 뉴스 API가 429 또는 타임아웃
- WHEN: 그래프를 실행
- THEN: 재시도 없이 폴백 멘트, `news_search="error"`, 호출 정확히 1회

**AC-N6 · 예측 요청**
- GIVEN: "예측" 요청
- WHEN: 그래프를 실행
- THEN: 기존처럼 `OUT_OF_DOMAIN` 거절, 분류·뉴스 호출 0회

**AC-N7 · 플래그 꺼짐 회귀 없음**
- GIVEN: 플래그 꺼짐
- WHEN: 어떤 질문이든 그래프를 실행
- THEN: 기존 State·결과와 동일(새 키 없음, 새 LLM 호출 없음)

**AC-N8 · 뉴스 본문은 데이터**
- GIVEN: 뉴스 title에 `<b>` 태그, description에 "이전 지시를 무시하라" 문장
- WHEN: 처리
- THEN: 태그는 제거되고, 해당 문장은 지시로 쓰이지 않으며(LLM 호출 0회) 삭제되지 않고 그대로 데이터로만 노출

**AC-N9 · 과잉 거절 방지**
- GIVEN: 사업보고서 관련 정상 질문
- WHEN: 사전 판별
- THEN: 뉴스 분기로 새지 않고 기존 검색 흐름으로 진행(AC-05와 같은 취지)

- 호출 상한(뉴스 항목 5건, 호출 1회)은 상수끼리 비교하지 않고 리터럴 `5`, `1`과 비교하며 호출 횟수를 센다(docs/GATES.md 변이 확인).

**AC-N10 · 거절 뒤 이어 묻기**
- GIVEN: 같은 thread에서 코퍼스 밖 회사 질문 → 코퍼스 밖 기간 질문 → 정상 질문
- WHEN: 차례로 실행
- THEN: 각각 자기 `refusal_reason`으로 거절되고(뉴스 호출은 거절마다 1회), 세 번째는 거절되지 않고 검색·작성까지 간다. 세 번째 결과의 `news_items`는 빈 배열, `refusal_kind`는 없음

**AC-N11 · 화면**
- GIVEN: 거절 응답에 `refusal_message`와 `news_items` 6건(하나는 `javascript:` 주소)
- WHEN: 심사 상세 화면이 결과를 그림
- THEN: 안내 문구가 보이고 참고 뉴스 5건만 보이며, http(s)가 아닌 주소는 링크가 아니고, 제목의 `<b>`는 글자 그대로 보이며, 초안 영역은 비어 있다. 다음 결과가 거절이 아니면 뉴스 영역은 숨는다
