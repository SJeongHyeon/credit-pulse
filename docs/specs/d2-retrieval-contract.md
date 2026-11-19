# D2 Spec - 검색 계약과 베이스라인 평가 계산

## Why

- **페르소나**: DART 검색 결과를 에이전트·평가 담당자에게 넘기는 검색·데이터 담당자
- **상황**: 임베딩 모델과 운영 DB는 아직 미확정이지만, 세 역할이 병렬 작업하려면 검색 입력·출력과 평가 형식은 먼저 고정해야 한다.
- **문제**:
  1. 필터를 검색 후에 적용하면 결과가 굶어 거절 지표가 부풀 수 있다.
  2. 검색 방식마다 score 의미가 달라 숫자만으로 비교할 수 없다.
  3. Recall@k와 MRR 계산 형식이 없으면 같은 결과를 재현할 수 없다.
- **측정 지표**: 계약 AC 테스트 통과, 필터 위반 탐지, 고정 fixture의 Recall@k·MRR 일치

## Goal

- `question + filters + top_k` 입력과 `ScoredChunk[]` 출력을 타입으로 고정한다.
- 회사·연도·장·주석 필터는 검색 백엔드가 랭킹 전에 적용하고, 계약 계층이 결과 위반을 탐지한다.
- 결과에 `score`, `rank`, `retrieval_method`, `corpus_id`와 출처 메타데이터를 보존한다.
- 동결 평가셋과 원시 검색 결과에서 Recall@k·MRR을 결정적으로 계산한다.

**성공 기준**

- 잘못된 query·top_k·필터·rank·필터 불일치를 명시적 오류로 거절
- 동일 fixture에서 동일 JSON 지표 생성
- 관련 문항과 비관련 문항 수를 분리해 기록
- 기존 동결 평가셋을 수정하지 않음

**Out of Scope**

- 임베딩 모델 선택·다운로드·API 호출
- pgvector 적재와 인덱스
- BM25·RRF·리랭킹 구현과 거절 임계값
- v1 질문 작성·gold 청크 판정·동결
- 생성 답변의 인용률·거절·환각 평가

## What

**Happy Path**

1. 호출자가 질문과 필수 `corp_code`, `fiscal_year`, 선택 `chapter`, `note_no`, `top_k`를 전달한다.
2. 주입된 검색 백엔드가 필터를 랭킹 전에 적용해 `ScoredChunk` 목록을 반환한다.
3. 계약 계층이 개수·rank·메타데이터 필터 일치를 검증한다.
4. 평가 실행기는 `dataset_v1.jsonl`의 gold와 원시 결과의 chunk id를 비교해 Recall@k·MRR을 기록한다.

**Edge Cases**

| # | 상황 | 처리 방식 |
|---|---|---|
| EC-01 | 검색 백엔드가 연결되지 않음 | 빈 결과로 숨기지 않고 `RetrieverNotConfigured` |
| EC-02 | 질문이 공백 | 계약 오류 |
| EC-03 | `top_k`가 1 미만 또는 100 초과 | 계약 오류 |
| EC-04 | 백엔드가 다른 회사·연도 결과 반환 | 후처리로 버리지 않고 계약 오류 |
| EC-05 | 결과 rank가 1부터 연속이 아님 | 계약 오류 |
| EC-06 | 평가 결과에 중복 id 또는 알 수 없는 id | 평가 입력 오류 |
| EC-07 | 관련 문항의 gold가 비어 있음 | 평가 입력 오류 |
| EC-08 | 비관련 문항 | Recall·MRR 분모에서 제외하고 건수만 별도 기록 |

## How

```python
SearchFilters(
    corp_code: str,
    fiscal_year: int,
    chapter: str | None = None,
    note_no: str | None = None,
)

retrieve(
    question: str,
    *,
    filters: SearchFilters,
    top_k: int = 10,
    backend: RetrievalBackend,
) -> list[dict]
```

결과 필수 필드:

```json
{
  "chunk_id": "chunk-...",
  "corpus_id": "corpus-...",
  "rcept_no": "202603...",
  "corp_code": "00126380",
  "corp_name": "회사명",
  "fiscal_year": 2025,
  "chapter": "II",
  "section_path": ["II. 사업의 내용"],
  "note_no": null,
  "table_title": null,
  "ordinal": 1,
  "content": "근거 본문",
  "content_sha256": "64자리",
  "score": 0.82,
  "rank": 1,
  "retrieval_method": "vector"
}
```

평가 원시 결과 한 줄:

```json
{"id": 1, "retrieved_chunks": ["chunk-a", "chunk-b"], "latency_ms": 12.3}
```

재현 명령:

```bash
python -m backend.rag.evaluation \
  --dataset evals/dataset_v1.jsonl \
  --results evals/results/vector_v1.jsonl \
  --k 10 \
  --output evals/results/vector_v1.metrics.json
```

## AC

**AC-01 · 정상 계약**
- GIVEN: 필터와 일치하는 rank 1..k 결과
- WHEN: `retrieve` 호출
- THEN: 출처와 점수 필드를 보존한 dict 목록을 반환한다.

**AC-02 · 사전 필터 강제**
- GIVEN: corp_code 또는 fiscal_year가 다른 결과를 반환하는 백엔드
- WHEN: `retrieve` 호출
- THEN: 결과를 버려 빈 목록으로 만들지 않고 계약 위반 오류를 낸다.

**AC-03 · 입력 상한**
- GIVEN: 공백 질문 또는 top_k 0/101
- WHEN: `retrieve` 호출
- THEN: 백엔드를 호출하기 전에 계약 오류를 낸다.

**AC-04 · 평가 지표**
- GIVEN: 관련 2건 중 하나는 gold가 2위, 하나는 미검색이고 비관련 1건
- WHEN: k=10으로 계산
- THEN: Recall@10=0.5, MRR@10=0.25이며 관련/비관련 건수를 분리한다.

**AC-05 · 평가 입력 무결성**
- GIVEN: 중복 결과 id, 알 수 없는 id, 관련 문항의 빈 gold
- WHEN: 평가 계산
- THEN: 조용히 제외하지 않고 입력 오류를 낸다.
