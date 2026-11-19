# Spec Langfuse 트레이스 계층 (멀티에이전트 구조가 트리로 보이게)

- 작성: 2026-11-11 (11/11 피드백). 기록만 더한다. 그래프의 노드·순서·분기·모델·지시문·상한은 바꾸지 않는다.
- 구현: `backend/services/tracing.py`, 시험: `tests/test_trace_tree.py`

## Why

- **페르소나**: 트레이스로 "어느 에이전트가 어떤 호출을 했는지" 확인하려는 조원·심사자.
- **상황**: 멀티에이전트(Supervisor + 검색·작성 서브에이전트)로 구현했는데 Langfuse 트레이스 왼쪽 트리에는 질문 아래에 `chat`·`rerank`·`embedding`이 같은 층에 나열된다.
- **문제**:
  1. 기록 지점이 질문 전체(`question`)와 외부 호출(`external_call`) 두 곳뿐이라 2층이다. 에이전트·노드 층이 없다.
  2. LLM 호출 이름이 호출 종류(`chat`)라 주제 분류·판단·채점·작성을 구분할 수 없다.
  3. 미리 실행(110~113번)한 검색·초안은 시작한 자리(주제 분류·채점 도중) 기준으로 기록돼, 단계별 시간을 나눠 볼 수 없다(EVAL_REPORT 8장 한계).
- **측정 지표**: 트리의 부모-자식 관계가 에이전트 그래프 그림(`docs/diagrams/agent_graph.py`)의 칸과 같다. 기록을 켜도 그래프 결과가 같다.

## Goal

- **해결 목표**: 질문 → supervisor → 에이전트 → 노드 → 외부 호출의 계층을 Langfuse에 남긴다.
- **성공 기준**
  - 정상·재검색·다시 쓰기·입구 거절·미리 실행 후 버림 경로의 트리가 아래 '목표 트리'와 같다(시험으로 확인).
  - 미리 실행한 호출도 `search_agent`·`writer_agent` 아래에 있고, 고아 관측(부모 없는 관측)이 0개다.
  - 기록을 켠 결과와 끈 결과의 State가 같다(`node_events`·`outcome` 포함 전체 비교).
  - 관측 하나를 여닫는 비용이 1ms 미만이고, 요청 경로에 동기 네트워크 호출이 없다.
- **Out of Scope**: 단일 그래프(`build_graph`)의 계층화, 노드 입출력 State 전체 기록, 프롬프트·청크 본문 기록, `scripts/node_timing.py`(`node_ms`) 변경, LangChain 콜백 핸들러 도입.

## What

**목표 트리**

```
질문(eval | review-query | review-approval)
├ load_rules                         규칙 의미 검색의 embedding이 이 아래
└ supervisor                         (agent) 입구 검사·다음 일 판단
   ├ classify_domain                 (generation)
   ├ search_agent                    (agent)  round, speculative, discarded
   │  ├ retriever                    (retriever) └ embedding, rerank
   │  ├ scorer
   │  └ grader                       └ grade_search_round (generation, τ 미달이면 없음)
   ├ decide_next                     (generation)
   ├ writer_agent                    (agent)  attempt, redraft, speculative, discarded
   │  ├ draft_normal                 discarded(쓰지 않았으면 true)
   │  │  ├ writer                    └ write_drafts (generation)
   │  │  ├ verifier
   │  │  └ report
   │  └ draft_strict | draft_redraft (같은 구조)
   ├ news_search                     (tool, 뉴스 분기가 켜졌을 때만)
   └ approval                        └ approval_prepare, human_approval, save_memo
```

**이름 규칙**

- 노드 관측 이름 = 그래프의 실제 노드 이름. 묶음 관측은 `supervisor`, `approval`.
- 초안 관측: `draft_normal`(보통 지시), `draft_strict`(엄격 지시, 112번), `draft_redraft`(Supervisor가 지시한 다시 쓰기). 초안 하나는 작성 서브그래프(`writer → verifier → report`) 실행 한 번이다.
- LLM 호출 관측 이름 = `llm.py`의 함수 이름(`classify_domain`, `classify_refusal_kind`, `decide_next`, `grade_search_round`, `grade_chunks`, `rewrite_query`, `write_drafts`, `judge_answers`, `llm_rerank_scores`). 호출 종류는 관측 타입(generation·embedding)과 `metadata.kind`(chat·embedding·rerank)에 남는다. 임베딩·리랭크 관측 이름은 그대로 `embedding`·`rerank`다.

**메타데이터**

| 관측 | 키 | 뜻 |
|---|---|---|
| search_agent | `round` | 몇 번째 검색인가(1~3) |
| writer_agent | `attempt`, `redraft` | 몇 번째 작성인가, Supervisor가 지시한 다시 쓰기인가 |
| search_agent·writer_agent | `speculative` | 미리 실행(110~113번)으로 시작됐는가 |
| search_agent·writer_agent·draft_* | `discarded` | 결과를 쓰지 않았는가(재검색으로 버린 초안, 고르지 않은 초안, 입구 거절로 버린 검색) |
| LLM 호출(이중 요청일 때만) | `hedged`, `discarded` | 같은 요청을 두 번 보냈는가, 답을 쓰지 않은 쪽인가 |
| human_approval | `interrupted` | 승인 대기로 멈췄는가(오류가 아니다) |

**Edge Case**

| # | 상황 | 처리 |
|---|---|---|
| EC-01 | Langfuse 키 없음·`LANGFUSE_TRACING_ENABLED=false` | 모든 기록 함수가 아무 일도 하지 않는다. langfuse를 import하지도 않는다 |
| EC-02 | 미리 시작한 검색이 입구 거절로 버려짐 | `search_agent`(speculative, discarded)로 남는다. 그때 미리 쓰던 초안도 `writer_agent`(discarded)로 남는다 |
| EC-03 | 재검색으로 청크가 바뀌어 미리 쓴 초안을 못 씀 | 버린 `writer_agent`(speculative, discarded)와 새 `writer_agent`가 둘 다 남는다 |
| EC-04 | 쓰지 않은 엄격 초안·이중 요청의 늦은 쪽이 답이 나간 뒤에 끝남 | 관측을 열어 두었다가 끝났을 때 실제 끝난 시각으로 닫는다 |
| EC-05 | 아무도 가져가지 않은 미리 실행(마지막 질문) | 질문이 끝날 때 discarded로 닫는다 |
| EC-06 | 사람 승인 interrupt | `human_approval`에 `interrupted=true`. 재개는 `review-approval` 트레이스에 `human_approval`·`save_memo`가 남는다 |
| EC-07 | Langfuse 기록 중 예외 | 로그만 남기고 노드 결과·예외는 그대로다 |
| EC-08 | 질문 트레이스 밖의 호출(스크립트 단독) | 노드·에이전트 관측은 만들지 않고, 외부 호출 관측은 이전처럼 남는다 |

## How

- **방식**: 노드·에이전트마다 관측을 직접 여닫는다(강의 P19, 3주11의 중첩 방식). LangChain 콜백 핸들러는 쓰지 않는다. 핸들러는 ① 트리가 LangGraph 실행 모양(supervisor가 방문마다 형제로 나열)이 되고 ② 미리 실행 호출이 시작한 노드 아래로 가며 ③ 노드 입출력 State(청크 본문 포함)를 통째로 남긴다(11/11 langfuse 5.1.0 실측).
- **부모 지정**: Langfuse의 '현재 컨텍스트'에 맡기지 않는다. `tracing.py`가 ContextVar로 '지금의 부모 관측'을 들고 다니고, 자식은 항상 `부모.start_observation()`으로 만든다. 스레드에는 `contextvars.copy_context()`로 넘어간다(기존 방식 그대로).
- **미리 실행의 부모**: 미리 시작하는 검색·초안은 `tracing.open_agent()`가 지금 자리와 무관하게 supervisor 관측 아래에 연다. 어댑터 노드가 그 결과를 쓰면 손잡이를 이어받고, 쓰지 않으면 `discarded=true`로 닫는다.
- **늦게 닫기**: langfuse 5.1.0에서 `end()` 뒤의 `update()`는 반영되지 않는다(실측). 그래서 '일이 끝남'과 '쓰임·버림이 정해짐'이 둘 다 된 뒤에 메타데이터를 쓰고 `end(end_time=실제 끝난 시각)`으로 닫는다.
- **바뀌는 파일**: `backend/services/tracing.py`(계층), `backend/services/llm.py`(호출 이름 데코레이터, `_first_of_two`의 이중 요청 표시), `backend/graph/build.py`·`agents/search_agent.py`·`agents/writer_agent.py`·`approval.py`(기록 래퍼), `backend/graph/speculation.py`(손잡이 전달). `question()`·`score()`·`flush()`의 사용법과 `llm.py`의 사용량 기록은 그대로다.
- **제약**: 프롬프트·청크 본문·초안 문장은 남기지 않는다(질문, 결과, 청크 id, 개수, 판정, 모델, 토큰, 비용, 시간만. P19 R7). 비용은 공급자 실측값(R4), 캐시 토큰은 더하지 않는다(R5).

## AC

- **AC-01** Given 가짜 Langfuse 클라이언트와 가짜 에이전트, When 정상 질문 1건을 돌리면, Then 관측 경로 집합이 목표 트리와 같고 `search_agent`·`writer_agent`가 `speculative=true, discarded=false`, `draft_strict`가 `discarded=true`다.
- **AC-02** Given 재검색을 고르는 Supervisor, When 돌리면, Then `search_agent` 관측이 2개이고 `round`가 1, 2다.
- **AC-03** Given 재검색으로 청크가 바뀌는 검색기, When 돌리면, Then 버린 `writer_agent`(speculative, discarded)와 쓴 `writer_agent`가 모두 supervisor 아래에 있고 `write_drafts` 관측이 4개다.
- **AC-04** Given 검증에 실패하는 작성기(순차 경로), When 돌리면, Then `writer_agent`가 2개이고 두 번째가 `attempt=2, redraft=true`이며 자식이 `draft_redraft`다.
- **AC-05** Given 주제 분류가 범위 밖, When 돌리면, Then 미리 시작한 검색·초안이 discarded로 남고 `approval`은 없다.
- **AC-06** Given 같은 입력, When 기록을 켜고 끈 두 번을 돌리면, Then 최종 State가 같다.
- **AC-07** Given 실제 langfuse SDK와 메모리 exporter(네트워크 없음), When 정상 질문 1건을 돌리면, Then 루트가 하나이고 모든 관측의 부모가 같은 트레이스 안에 있다.
- **AC-08** Given 이중 요청이 일어나는 느린 호출, When 돌리면, Then 같은 이름의 관측이 2개 남고 늦은 쪽만 `discarded=true`다.
- **AC-09** Given 키 없음, When 기록 함수를 부르면, Then 아무 일도 하지 않고 langfuse 클라이언트를 만들지 않는다.
