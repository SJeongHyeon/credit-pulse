# D2 Spec - 로컬 E5 벡터 베이스라인과 pgvector 적재

## Why

- **페르소나**: DART 근거 검색의 재현 가능한 기준선을 만드는 검색·데이터 담당자
- **상황**: pilot 50은 128,607청크이며 동일 본문 SHA-256 기준 87,749개다. 임베딩·DB 적재를 중단 후 재개할 수 있어야 한다.
- **문제**:
  1. 모든 중복 청크를 다시 임베딩하면 시간과 저장 공간을 낭비한다.
  2. 로컬 CPU 전량 임베딩은 1시간 이상 걸려 중간 실패 복구가 필요하다.
  3. 모델·입력·설정 해시가 없으면 평가 결과를 재현할 수 없다.
- **측정 지표**: 고유 본문 수, 처리량, 재개 성공, 벡터 차원·정규화, DB 적재 건수, Recall@10·MRR

## Goal

- `intfloat/multilingual-e5-small`을 D2 기준선으로 사용한다.
- `near_dup=false`, `superseded=false`인 본문 SHA-256마다 passage 벡터를 한 번 생성한다.
- E5 규칙에 따라 문서는 `passage:`, 질의는 `query:` 접두사를 붙이고 L2 정규화한다.
- `.npy` 벡터·id JSONL·metadata JSON·progress JSON을 만들어 중단 후 재개한다.
- 로컬 pgvector의 문서·청크·임베딩을 멱등 적재한다.

**성공 기준**

- 벡터 384차원, L2 norm 1±1e-5
- 같은 입력·모델·설정으로 재실행 시 기존 완료분을 건너뜀
- 입력 해시나 설정이 달라진 progress 재사용 거절
- content SHA-256 기준 벡터 수가 eligible 고유 본문 수와 일치
- DB 재적재 시 행 수가 늘어나지 않음
- 앱 기동 시 `EMBED_DIM`과 DB `chunks.embedding VECTOR(n)` 차원이 다르면 명시적으로 실패

**Out of Scope**

- bge-m3·KURE 비교, 리랭커, BM25·RRF(D3)
- 최종 FY2025 100사와 FY2024 비교 30사 코퍼스
- Supabase 운영 연결(D6)
- 실제 v1 평가셋 작성·동결(평가 담당과 공동 작업)

## What

**Happy Path**

1. chunks JSONL을 읽고 eligible 본문을 SHA-256으로 중복 제거한다.
2. 길이순·해시순으로 결정적으로 정렬해 padding 낭비를 줄인다.
3. 배치마다 벡터 memmap을 flush하고 progress를 원자적으로 갱신한다.
4. 완료 후 입력·id·벡터 SHA-256, 처리량, 설정을 metadata에 기록한다.
5. 문서·청크를 pgvector에 upsert하고 동일 content hash의 벡터를 연결한다.

**Edge Cases**

| # | 상황 | 처리 방식 |
|---|---|---|
| EC-01 | 모델 캐시 없음 | 네트워크 자동 다운로드 없이 명확한 오류 |
| EC-02 | 같은 content hash에 다른 본문 | 해시 충돌/손상으로 중단 |
| EC-03 | progress 설정과 현재 입력 불일치 | 재개 거절, `--overwrite` 명시 필요 |
| EC-04 | 배치 중 프로세스 종료 | 마지막 완료 배치 다음부터 재개 |
| EC-05 | 벡터에 NaN/Inf 또는 norm 오류 | 저장 전 중단 |
| EC-06 | DB 재적재 | PK·unique upsert로 행 수 불변 |
| EC-07 | Docker/DB 미기동 | 임베딩 파일은 보존하고 DB 단계만 실패 |

## How

```powershell
python -m backend.rag.embedding build `
  --chunks data/chunks.pilot50.jsonl `
  --output-prefix data/embeddings/e5-small-pilot50 `
  --model intfloat/multilingual-e5-small `
  --batch-size 64 --max-length 512
```

산출물:

```text
data/embeddings/e5-small-pilot50.ids.jsonl
data/embeddings/e5-small-pilot50.vectors.npy
data/embeddings/e5-small-pilot50.progress.json
data/embeddings/e5-small-pilot50.metadata.json
```

제약:

- 모델은 `local_files_only=True`, CPU 기준으로 로드한다.
- `sentence_transformers`는 현재 PC의 손상된 pyarrow import를 거치므로 사용하지 않는다. `transformers + torch` mean pooling을 사용한다.
- 벡터는 content hash별 한 번 저장하고, 개별 chunk는 content hash로 이를 참조한다.
- 대용량 벡터 파일은 커밋하지 않는다.

## AC

**AC-01 · eligible 중복 제거**
- GIVEN: 같은 본문 2개와 near_dup·superseded 청크
- WHEN: 임베딩 대상을 생성
- THEN: 정상 고유 본문 1개만 남는다.

**AC-02 · 결정적 순서**
- GIVEN: 입력 JSONL 순서가 다른 같은 청크 집합
- WHEN: 임베딩 대상을 생성
- THEN: 길이·본문 해시 순서와 id 산출물이 같다.

**AC-03 · 배치 저장과 재개**
- GIVEN: 2개 배치 중 첫 배치까지 완료된 progress
- WHEN: 같은 설정으로 재실행
- THEN: 두 번째 배치만 실행하고 최종 metadata를 만든다.

**AC-04 · 설정 불일치 차단**
- GIVEN: 다른 모델·max length·입력 해시의 progress
- WHEN: 재개
- THEN: 기존 벡터에 이어 쓰지 않고 오류를 낸다.

**AC-05 · 정규화 벡터**
- GIVEN: 모델 출력
- WHEN: 저장 전 검증
- THEN: shape가 `(N,384)`이고 모든 행의 norm이 1±1e-5다.
