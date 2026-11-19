# W3.10 Spec - 메타데이터 필터 검색 trace

> 2026-11-07: HNSW 인덱스를 빼고 정확 검색으로 바꿔 `SET LOCAL hnsw.iterative_scan`과 trace의 `iterative_scan` 칸은 없어졌다([pgvector-service-retrieval](pgvector-service-retrieval.md)). 필터·`result_count` trace는 그대로다.

## Why

- **페르소나**: 회사·사업연도 필터가 실제 검색에 적용됐는지 검토하는 RAG 품질 담당자
- **상황**: pgvector 검색은 SQL에서 메타데이터 필터와 `hnsw.iterative_scan`을 적용하지만, 실행 뒤 반환 건수를 trace에서 확인할 수 없다.
- **문제**: 검색 결과가 비었을 때 필터 범위가 좁아서인지 검색 품질 문제인지 실행 기록만으로 구분하기 어렵다.
- **측정 지표**: 검색 1회당 구조화 이벤트 1건, 이벤트의 `result_count`와 실제 반환 건수 일치

## Goal

- pgvector 검색 완료 시 corpus, 필터, 요청 개수, 실제 반환 개수, iterative scan 모드를 구조화된 이벤트로 전달한다.
- 질문 원문과 청크 본문은 trace 이벤트에 넣지 않는다.
- trace 수집기가 연결되지 않아도 기존 검색 동작과 반환 형식을 유지한다.

**성공 기준**

- 결과가 있거나 없는 검색 모두 `result_count`가 정확하다.
- 회사·연도와 선택 장·주석 필터가 이벤트에 기록된다.
- `SET LOCAL hnsw.iterative_scan = relaxed_order`가 검색 SQL보다 먼저 실행된다.

**Out of Scope**

- 검색 점수·순위·SQL 필터 조건 변경
- Langfuse SDK 연결과 원격 trace 전송
- 동결 평가셋, 사전 등록, 임베딩 또는 인덱스 재생성

## What

**Happy Path**

1. 호출자가 `PgVectorBackend`에 선택적 `trace_sink`를 주입한다.
2. 백엔드가 회사·연도·선택 필터를 SQL `WHERE`에 넣고 pgvector 검색을 수행한다.
3. 결과 정렬 후 `pgvector_search` 이벤트 한 건을 `trace_sink`로 전달한다.
4. 기존과 동일한 `ScoredChunk` 목록을 반환한다.

**Edge Cases**

| # | 상황 | 처리 방식 |
|---|---|---|
| EC-01 | 검색 결과 없음 | `result_count=0` 이벤트 전달 |
| EC-02 | 장·주석 필터 없음 | 해당 이벤트 필드는 `null` |
| EC-03 | trace 수집기 미주입 | 이벤트 전달을 생략하고 검색은 정상 수행 |
| EC-04 | trace 수집기 자체 오류 | 검색 성공을 깨지 않고 경고 로그만 기록 |

## How

```python
PgVectorBackend(
    database_url: str,
    corpus_id: str,
    query_embedder: QueryEmbedder,
    trace_sink: Callable[[dict[str, Any]], None] | None = None,
)
```

이벤트 형식:

```json
{
  "event": "pgvector_search",
  "corpus_id": "corpus-v7",
  "filters": {
    "corp_code": "00126380",
    "fiscal_year": 2025,
    "chapter": "NOTES",
    "note_no": null
  },
  "top_k": 10,
  "result_count": 7,
  "iterative_scan": "relaxed_order"
}
```

## AC

**AC-01 · 결과 수 trace**
- GIVEN: 필터와 일치하는 pgvector 결과 1건과 trace 수집기
- WHEN: 검색을 실행한다
- THEN: 실제 반환 건수와 같은 `result_count=1` 이벤트가 한 번 전달된다.

**AC-02 · 빈 결과 trace**
- GIVEN: 필터와 일치하는 결과가 없음
- WHEN: 검색을 실행한다
- THEN: 빈 목록을 반환하고 `result_count=0`을 전달한다.

**AC-03 · SQL 실행 순서**
- GIVEN: pgvector 검색 연결
- WHEN: 검색을 실행한다
- THEN: `SET LOCAL hnsw.iterative_scan = relaxed_order`가 SELECT보다 먼저 실행된다.

**AC-04 · 관측 실패 격리**
- GIVEN: 예외를 내는 trace 수집기
- WHEN: DB 검색은 성공한다
- THEN: 검색 결과는 그대로 반환되고 trace 오류는 경고 로그에만 남는다.
