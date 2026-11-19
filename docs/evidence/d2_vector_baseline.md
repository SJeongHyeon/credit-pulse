# D2 pilot 50 벡터 베이스라인 실행 증거

실행일: 2026-10-30
범위: FY2025 사업보고서 pilot 50사
상태: 임베딩·로컬 pgvector 적재·검색 스모크 완료, v1 30문항 평가는 대기

## 1. 기준선 설정

| 항목 | 값 |
|---|---|
| corpus_id | `corpus-c121ffd5dea5e9a0cfb237c9` |
| 모델 | `intfloat/multilingual-e5-small` |
| 차원·dtype | 384, float32 |
| 입력 상한 | 512 tokens |
| E5 접두사 | 문서 `passage:`, 질의 `query:` |
| 정렬 | 본문 길이, content SHA-256 |
| 전체 청크 | 128,607 |
| eligible 고유 본문 벡터 | 87,749 |
| 문서 | 50 |

대상은 `near_dup=false`, `superseded=false`인 청크이며 같은
`content_sha256` 본문은 한 번만 임베딩했다. 개별 청크는 적재 시 동일 본문 벡터를
연결한다.

## 2. 임베딩 실행과 재개

```powershell
python -m backend.rag.embedding build `
  --chunks data/chunks.pilot50.jsonl `
  --output-prefix data/embeddings/e5-small-pilot50 `
  --model intfloat/multilingual-e5-small `
  --batch-size 64 --max-length 512
```

- 이전 체크포인트: 38,784 / 87,749
- 재개 실행 처리: 48,965
- 재개 실행 시간: 3,664.311초
- 재개 실행 처리량: 13.363 rows/s
- 최종 완료: 87,749 / 87,749
- 중단 후 기존 완료분을 보존하고 다음 배치부터 이어서 처리했다.

## 3. 무결성·수치 검증

| 검증 | 결과 |
|---|---|
| chunks SHA-256 | `3f1faa6601049c43b165b9845dba35853dc9071f13f1721fcf3a5ce38113596a` |
| ids SHA-256 | `4963c0964e5ff97a9e441d5c5ebcac01fd4b6636323f10ccfa26d821ba6a0138` |
| vectors SHA-256 | `47b71b73cc8b855899249be5fe8b5b0b141026eaa7513c8c118ba4b2f699314f` |
| shape | `(87749, 384)` |
| NaN / Inf | 0 |
| L2 norm 최소 | 0.9999998212 |
| L2 norm 최대 | 1.0000001192 |
| L2 norm 평균 | 1.0 |

## 4. pgvector 적재

개발 PC의 다른 PostgreSQL과 충돌하지 않도록 이 프로젝트 DB는
`localhost:5433`을 사용했다.

```powershell
$env:DATABASE_URL='postgresql://app:app@localhost:5433/app'
python -m backend.rag.vector_store `
  --manifest data/manifest.pilot50.jsonl `
  --chunks data/chunks.pilot50.jsonl `
  --embedding-ids data/embeddings/e5-small-pilot50.ids.jsonl `
  --embedding-vectors data/embeddings/e5-small-pilot50.vectors.npy `
  --embedding-metadata data/embeddings/e5-small-pilot50.metadata.json
```

같은 명령을 두 번 실행한 뒤에도 다음 행 수가 유지됐다.

| 대상 | 1회 적재 | 2회 적재 |
|---|---:|---:|
| corpus_versions | 1 | 1 |
| 문서 | 50 | 50 |
| 청크 | 128,607 | 128,607 |

따라서 현재 upsert는 같은 코퍼스를 재실행해도 행 수가 늘어나지 않는다.

### 로컬 DB 용량

| 항목 | 측정값 |
|---|---:|
| 전체 DB | 783 MB |
| chunks 테이블+인덱스 | 775 MB |
| chunks 인덱스 | 255 MB |

현재 스키마는 검색 필터 뒤 HNSW를 바로 적용하기 위해 각 청크 행에 벡터를
저장한다. 동일 본문 벡터도 청크별로 복제되므로, 최종 250사 또는 제한된 운영
DB로 이전하기 전에는 벡터 분리 테이블·부분 코퍼스·인덱스 크기를 비교해 용량
설계를 확정해야 한다.

## 5. 실제 pilot 검색 스모크

질문: `채무보증과 우발부채 위험을 확인해줘`
필터: 금양 `corp_code=00106119`, `fiscal_year=2025`

### 장 필터 없음

1. 일반 신용위험 문단, score 0.8801305
2. `채무보증 현황: 해당사항 없음 / 채무인수약정: 해당사항 없음 / 그 밖의 우발채무: 해당사항 없음`, score 0.8798541
3. 우발채무 절 참조 문단, score 0.8783291

벡터 단독 검색은 정답성 높은 근거를 top 10에 포함했지만 일반 신용위험 문단을
근소한 차이로 먼저 배치했다. 이 사례는 D3의 BM25·리랭킹 비교 대상이다.

### `chapter=XI` 필터

정확한 채무보증·우발채무 근거가 같은 score 0.8798541로 1위가 됐다. 반환된
모든 결과는 요청한 회사·연도·장 필터를 만족했다.

## 6. 남은 게이트

- 평가 담당이 정답 청크를 보지 않고 v1 질문 30건을 작성해야 한다.
- 검색·데이터 담당이 질문을 받은 뒤 gold 청크를 판정해야 한다.
- v1 SHA-256과 기준일을 기록한 뒤에만 Recall@10·MRR을 계산한다.
- 따라서 이 문서는 전량 임베딩·DB·검색 실행 증거이며 D2 평가 게이트 완료
  증거는 아니다.
- 최종 250사 기준과 임베딩 모델·운영 DB가 확정되면 같은 명령으로 새
  `corpus_id`를 만들고 다시 측정한다.
