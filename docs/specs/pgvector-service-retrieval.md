# Spec — 운영 벡터 검색을 메모리에서 Supabase pgvector로 전환

상태: 결정 반영(2026-11-07). 3번 결정 중 1(corpus_id ffc1)·2(HNSW 없이 정확 검색)를 팀장이 확정했다. 사전 등록 90번과 함께 본다.

## Why

- **페르소나**: 배포 담당(팀장)과, 평가자 계정으로 서비스를 쓰는 심사역
- **상황**: 운영 검색은 `build_retriever()`가 이미지 안 `data/service/`(chunks 70MB + 벡터 196MB)를 메모리에 올려 코사인을 계산한다. 가이드 4-2는 벡터 DB로 Supabase pgvector를 권고하고, `docs/GATES.md` G3에 "C4 벡터 적재·활성 버전 전환"이 남아 있다.
- **문제**:
  1. 벡터 DB를 쓴다고 설계(AGENTS.md 기술 스택)해 놓고 운영은 파일을 쓴다. G3 증거가 비어 있다.
  2. 이미 있는 `PgVectorBackend`·`load_pgvector()`(D2 E5 때 작성)는 서비스에 연결된 적이 없고, 서비스 구성(d3-55, 연결 풀, 100사)과 맞지 않는 곳이 있다(아래 How 표).
- **측정 지표**: 같은 질의·필터에서 메모리와 pgvector의 1차 벡터 검색 상위 50개가 같은 문항 수(목표 23/23), Supabase 적재 후 DB 크기(무료 500MB 이내).

## Goal

- **해결 목표**: `VECTOR_BACKEND=pgvector`일 때 RRF의 벡터 쪽만 Supabase pgvector에서 검색하고, 결과는 메모리 백엔드와 같다.
- **성공 기준**
  - v2 `filters_v2.jsonl` 23문항(질의 벡터는 `evals/results/v2_query_vectors_oai_small.npz` 재사용, API 호출 0)에서 벡터 1차 상위 50개 id 집합 일치 23/23, 순서 일치 23/23(점수 차 1e-6 미만끼리 자리바꿈은 따로 표시하고 허용)
  - 기본값(`VECTOR_BACKEND` 없음 = memory)에서 기존 pytest 전체 통과, 데모 모드(G2) 동작 그대로
  - 로컬 pgvector에 100사 적재 후 `pg_total_relation_size('chunks')` 실측값을 기록하고, Supabase 무료 500MB(인덱스 포함, 출처 아래) 안에 들어간다
  - `prepare_supabase.py --service-corpus`를 두 번 실행해도 `chunks` 행 수·`corpus_versions` 행이 같고, 활성 corpus_id 한 벌만 남는다
- **Out of Scope**
  - BM25를 DB로 옮기기(`bm25_tokens` 컬럼은 계속 빈 배열). 한 번에 하나만 바꾼다
  - 이미지에서 `data/service/embeddings.*`(196MB) 빼기, 메모리 절감 측정
  - 리랭커·생성 모델·그래프 변경, Voyage 리랭커가 들어가는 v2 50건 전체 재측정(팀장이 따로 결정)
  - FY2024 30사 적재, 연간 갱신 자동화
  - 데모 모드(`DEMO_MODE=1`, 스냅샷 16사)를 pgvector로 바꾸기

## What

**Happy Path**
1. 배포 담당이 로컬에서 `DATABASE_URL_DIRECT`(직접 연결 5432)를 셸 환경변수로 주고 `python -X utf8 scripts/prepare_supabase.py --service-corpus`로 계획을 본 뒤 `--apply`로 적재한다.
2. 스크립트가 서비스 chunks·벡터·manifest의 corpus_id와 SHA-256을 대조하고 `load_pgvector()`로 적재한다. 적재·검증이 끝나면 다른 corpus_id 행을 지운다(CONTRACTS 운영 코퍼스 교체 규칙).
3. Cloud Run에 `VECTOR_BACKEND=pgvector`, `RETRIEVAL_CORPUS_ID=<활성 corpus_id>`를 추가해 재배포한다.
4. 기동 시 `build_retriever()`가 DB의 `corpus_versions` 행과 이미지 안 chunks 파일(BM25용)을 대조하고, 검색 대상 회사 목록을 DB에서 읽는다.
5. 질의마다 벡터 쪽은 연결 풀에서 연결을 빌려 SQL 한 번, BM25 쪽은 메모리. RRF 이후는 지금과 같다.

**Edge Cases**

| # | 상황 | 처리 방식 |
|---|---|---|
| EC-01 | `VECTOR_BACKEND`가 memory·pgvector가 아닌 값 | `RetrievalComboError`, 허용값을 메시지에 담음 |
| EC-02 | pgvector인데 `DATABASE_URL`·`RETRIEVAL_CORPUS_ID`가 비어 있음 | `RetrieverNotConfigured`, 빈 변수 이름을 메시지에 담음. 메모리로 넘어가지 않음 |
| EC-03 | DB에 `RETRIEVAL_CORPUS_ID` 행이 없음 / `embedding_model`·`vector_dimension`이 조합과 다름 / `chunks_sha256`이 `RETRIEVAL_CHUNKS_PATH` 파일과 다름 | 기동 시 `RetrievalComboError`(BM25와 벡터가 다른 코퍼스를 보게 되므로) |
| EC-04 | DB의 회사 목록과 chunks 파일의 회사 목록이 다름 | 기동 시 `RetrievalComboError` |
| EC-05 | 적재되지 않은 회사로 검색 | 지금처럼 `CorpNotIndexed`(목록은 DB에서 읽은 것) |
| EC-06 | 검색 중 DB 연결 실패·timeout | 예외를 그대로 올림(빈 결과로 숨기지 않음). API 응답 코드는 기존 오류 처리 경로를 따른다 |
| EC-07 | 필터에 맞는 청크가 없음 | 정상 `[]` |
| EC-08 | 적재 manifest의 corpus_id와 chunks 파일의 corpus_id가 다름 | `load_pgvector()`가 `VectorStoreError`(지금은 검사 없이 manifest 값으로 덮어씀) |
| EC-09 | 벡터 산출물에 `input_header`가 있음(머리말 임베딩) | `load_pgvector()`가 `VectorStoreError`. DB에는 본문 기준 벡터만 넣는다 |
| EC-10 | 같은 본문(SHA 같음)이라 점수가 같은 청크가 50위 경계에 걸침 | SQL `ORDER BY` 둘째 키 `chunk_id`로 메모리와 같은 쪽을 고름 |

## How

### 1. 현황 비교(코드 수정 없이 확인한 것)

| 항목 | 메모리 `InMemoryVectorBackend` | `PgVectorBackend`(지금) | 맞출 것 |
|---|---|---|---|
| 벡터 키 | `embedding_key(chunk, input_header)` | 적재 때 `content_sha256`으로 찾음 | 서비스 벡터 `input_header=null`이라 같음. 다른 값이면 적재 거부(EC-09) |
| 점수 | `matrix @ query`(float32 내적) | `1 - (embedding <=> q)` = 코사인 유사도 | 문서 벡터 노름 0.99999988~1.00000013(5,000개 표본 실측), 질의는 정규화 검사함 → 사실상 같음. 부동소수 차이는 비교로 확인 |
| 정렬·동점 | 전체 후보 `(-score, chunk_id)` 정렬 후 자름 | SQL `ORDER BY 거리 LIMIT k` 후 파이썬에서 `(-score, chunk_id)` | SQL에서 자를 때 동점 처리가 없음 → `ORDER BY 거리, chunk_id`로 고침(EC-10). 서비스 chunks에 본문 중복 14건 있음 |
| 정확/근사 | 정확(전수) | HNSW 인덱스를 타면 근사(`ef_search` 기본 40, 필터는 인덱스 스캔 뒤 적용) | 아래 3번 결정 |
| 필터 | corp_code·fiscal_year·chapter·note_no(None이면 조건 없음), near_dup·superseded 제외 | SQL WHERE에 같은 조건 + `corpus_id` | 같음 |
| retrieval_method | `offline_cosine:{모델}` | `"vector_e5_small_cosine"` 고정 | `pgvector_cosine:{모델}`로(최종 결과에는 RRF 방식명이 덮어씀) |
| 연결 | 없음 | 검색마다 `psycopg.connect` + `register_vector` | 풀에서 빌림(아래 2번) |
| 행 접근 | — | `row[13]` 위치 접근 | 런타임 풀은 `row_factory=dict_row`라 깨짐 → 커서에 `tuple_row` 지정 |
| 회사 목록 | 벡터 metadata `corp_codes` | 없음 | DB에서 `SELECT DISTINCT corp_code` |

`load_pgvector()`에서 본 것: corpus_id를 manifest에서만 읽어 모든 청크에 씀(EC-08), `batch_size`는 받기만 하고 안 씀(한 트랜잭션 COPY), 문서 행은 `ON CONFLICT DO UPDATE`로 덮어씀.

### 2. 코퍼스·크기

| 항목 | 값 |
|---|---|
| 서비스 chunks | `data/service/chunks.jsonl`, 31,967청크(고유 본문 31,953), 100사, FY2025, near_dup·superseded 0 |
| chunks의 corpus_id | `corpus-ffc1830e50a11c57749b63b9`(평가 v9 실험 코퍼스, 벡터 metadata `chunks_sha256=f523a7a9…`) |
| 커밋된 `data/manifest.jsonl`의 corpus_id | `corpus-4867b25d560356df13ab7886`(본문 같음, `collected_at`만 달라 ID가 다름, `docs/evidence/g3_v9_manifest_100-b.md`) |
| 같은 corpus_id의 manifest | `data/experiments/b100-v9-fixed_window/manifest.jsonl`(ffc1, 100건, 청크 합계 31,967) |
| 스키마 제약 | 본문 최대 1,000자(≤1,200), 장 II·IV·XI·NOTES만, (rcept_no, ordinal) 중복 0, note_no 최대 3자 → 통과 예상 |

Supabase 크기 추정(실측 아님, 로컬 적재 후 `pg_total_relation_size`로 확인):

| 구성 | 추정 |
|---|---:|
| 벡터 31,967 × (1536×4+8 B) | 약 197MB |
| 본문(UTF-8, 압축 전) | 약 53MB |
| 그 밖의 컬럼·행 헤더 | 약 10MB |
| btree 인덱스 5개(PK·UNIQUE·필터·sha·rcept) | 약 15MB |
| **HNSW 인덱스**(벡터를 한 벌 더 저장 + 이웃 목록) | 약 200~230MB |
| 합계: HNSW 있음 / 없음 | **약 475~505MB / 약 275MB** |

**실측(11/7, 로컬 PostgreSQL 16.15·pgvector 0.8.6, HNSW 없음)**: 빈 DB에 처음 적재하면 `chunks` 313.5 MiB(btree 인덱스 14.2 MiB)·DB 321.5 MiB로, 추정(약 275MB)보다 약 40MB 컸다. 데모 스냅샷이 있는 DB에 적재한 뒤 스냅샷을 지우면 `chunks` 360MB·DB 368MB. 같은 코퍼스를 다시 upsert하면 표가 커져서(두 번 더 돌리자 666MB) 적재 스크립트는 이미 적재된 코퍼스를 건너뛴다.

여기에 새 프로젝트 기본 40~60MB와 체크포인터·Store 표가 더해진다. Supabase 무료는 "500 MB database size per project", 크기에는 "data, indexes, and materialized views"가 들어간다(<https://supabase.com/pricing>, <https://supabase.com/docs/guides/platform/manage-your-usage/database-size>). **HNSW를 두면 한도를 넘을 가능성이 크다.**

### 3. 결정(팀장 확인 필요, 추천을 앞에)

1. **적재 corpus_id: ffc1(추천)** — 서비스 chunks·벡터·평가(66번 이후)·데모 스냅샷 원본이 모두 ffc1이다. 적재 manifest로 `data/experiments/b100-v9-fixed_window/manifest.jsonl`을 쓴다. 대안 4867은 chunks 파일 SHA가 바뀌어 벡터 metadata와 어긋난다. 영향: `documents` 100행의 `collected_at`이 실험 코퍼스 값(11/1)으로 덮어써진다.
2. **HNSW 인덱스: 운영에서 쓰지 않음(추천)** — 회사·연도 필터 뒤 후보는 회사당 54~1,934청크(중앙값 259)라 btree `(corpus_id, corp_code, fiscal_year)`로 좁힌 뒤 전수 계산이 충분하다. pgvector는 인덱스 없이 "exact nearest neighbor search, which provides perfect recall"이고, HNSW는 "filtering is applied after the index is scanned"라 근사·누락이 생긴다(<https://github.com/pgvector/pgvector>). 크기도 약 230MB 준다. 바꿀 곳: `db/02_schema.sql`의 `idx_chunks_embedding` 줄 삭제 + `prepare_supabase.py`가 `DROP INDEX IF EXISTS`(설정 파일 변경이라 따로 확인받음). 그러면 `SET LOCAL hnsw.iterative_scan`도 필요 없어 지운다. 대안(HNSW 유지)은 크기 한도와 근사 차이를 감수해야 한다.
3. **질의 벡터 전달: `%s::vector` 텍스트 캐스트** — `register_vector`는 연결마다 `TypeInfo.fetch`를 4번 한다(pgvector 0.5.1 `psycopg/register.py`). 풀 연결을 빌릴 때마다 부르면 질의당 왕복 4번이 늘어서, `pgvector.Vector(q).to_text()` 문자열을 캐스트한다(float32 값은 짧은 반올림 표기로 그대로 복원됨, 비교로 확인).

### 4. 코드

- `backend/retrieval_combo.py`
  - `RetrievalSettings`에 `vector_backend: str = "memory"`, `corpus_id: str | None = None`, `database_url: str | None = None`
  - `settings_from_env`: `VECTOR_BACKEND`(기본 memory), pgvector면 `DATABASE_URL`·`RETRIEVAL_CORPUS_ID` 필수(EC-02), memory일 때만 `RETRIEVAL_EMBEDDING_PREFIX` 필수
  - `build_retriever(settings, *, embedder=None, reranker=None, pool=None)`: pgvector면 벡터 산출물을 읽지 않고 `PgVectorBackend(pool=..., corpus_id=..., query_embedder=..., retrieval_method=f"pgvector_cosine:{모델}")`, 기동 검사(EC-03·04), 회사 목록은 DB
- `backend/rag/vector_store.py`(검색·데이터 담당 소유 경로 → PR에서 리뷰 요청)
  - `PgVectorBackend`: `pool` 또는 `database_url` 중 하나, `retrieval_method` 인자, `ORDER BY embedding <=> q, chunk_id`, `cursor(row_factory=tuple_row)`, 텍스트 캐스트
  - `load_pgvector()`: chunks의 corpus_id = manifest corpus_id 검사(EC-08), `input_header` 거부(EC-09)
- `backend/api/runtime.py`: `build_retriever(..., pool=pool)`로 같은 풀을 넘김, `missing_runtime_env`가 pgvector면 `RETRIEVAL_CORPUS_ID`를 요구하고 `RETRIEVAL_EMBEDDING_PREFIX`는 요구하지 않음
- `scripts/prepare_supabase.py`: `--service-corpus` 옵션. 이때 3단계(데모 스냅샷 16사)를 건너뛰고 서비스 100사를 `load_pgvector()`로 적재 → 행 수·`corpus_versions` 검증 → `DELETE FROM corpus_versions WHERE corpus_id <> %s`. 기본(옵션 없음)은 계획만 출력, `--apply`일 때만 바꿈
- `scripts/compare_vector_backends.py`(새 파일): 로컬 pgvector에 같은 산출물 적재 → 23문항 × 두 백엔드 `search(top_k=50)` → `evals/results/vector_backend_parity_v2.jsonl`(문항별 id 집합 일치율·순위 변화·최대 점수 차)과 `.summary.json`. 질의 벡터는 캐시만 쓰고 없으면 오류(API 호출 안 함)

트랜잭션 확인: 풀은 `autocommit=True`이고, psycopg 3.3.6 `Connection.transaction()`은 바깥 트랜잭션일 때 `BEGIN`…`COMMIT`을 보낸다(`psycopg/transaction.py` `_get_enter_commands`). 결정 2를 따르면 `SET LOCAL`이 없어져 단일 SELECT라 트랜잭션 블록도 필요 없다. HNSW를 유지하면 `SET LOCAL`을 같은 `transaction()` 안에 두어야 트랜잭션 풀러(6543)에서 같은 서버 연결에 적용된다.

### 5. 제약

- 키·DB 주소·비밀번호는 저장소에 적지 않는다. Supabase 적재와 Cloud Run 반영은 팀장이 직접 실행한다.
- 동결 평가셋(`evals/dataset_*.jsonl`, `FROZEN.json`) 수정 금지. 비교는 리랭커를 부르지 않는다.
- 상한·차원 테스트는 리터럴과 비교한다(1536, 50 등).

## AC (Given-When-Then)

**AC-01 · 기본값은 메모리**
- GIVEN: `VECTOR_BACKEND`가 없는 env
- WHEN: `settings_from_env(env)`
- THEN: `vector_backend == "memory"`, 기존처럼 `RETRIEVAL_EMBEDDING_PREFIX`가 필수

**AC-02 · 잘못된 값 거부**
- GIVEN: `VECTOR_BACKEND="faiss"`
- WHEN: `settings_from_env(env)`
- THEN: `RetrievalComboError`, 메시지에 `memory`와 `pgvector`

**AC-03 · pgvector 설정 누락**
- GIVEN: `VECTOR_BACKEND="pgvector"`, `RETRIEVAL_CORPUS_ID` 없음
- WHEN: `settings_from_env(env)`
- THEN: `RetrieverNotConfigured`, 메시지에 `RETRIEVAL_CORPUS_ID`. memory 백엔드로 만들어지지 않음

**AC-04 · 코퍼스 불일치로 기동 거부**
- GIVEN: DB `corpus_versions.chunks_sha256`이 `RETRIEVAL_CHUNKS_PATH` 파일 SHA와 다름(가짜 풀)
- WHEN: `build_retriever(settings, pool=가짜풀)`
- THEN: `RetrievalComboError`

**AC-05 · 검색 결과 계약**
- GIVEN: 로컬 pgvector에 작은 고정 코퍼스(두 회사, 동점 본문 포함) 적재
- WHEN: `retrieve(q, filters=회사A·2025·chapter=NOTES, top_k=5, backend=PgVectorBackend)`
- THEN: 모든 결과가 `ScoredChunk` 검증 통과, rank 1..n, 회사A·NOTES만, 동점은 `chunk_id` 오름차순, `retrieval_method == "pgvector_cosine:openai/text-embedding-3-small"`(리터럴)
- DB가 없으면 이 시험은 skip하지 않고 표시(`-m pgvector`)해 따로 돌린다

**AC-06 · 메모리와 같은 결과**
- GIVEN: AC-05와 같은 코퍼스를 메모리 백엔드에도 넣음
- WHEN: 같은 질의 벡터·필터로 두 백엔드 `search(top_k=50)`
- THEN: id 순서가 같음

**AC-07 · 미적재 회사**
- GIVEN: pgvector 모드, DB 회사 목록에 없는 corp_code
- WHEN: retriever 호출
- THEN: `CorpNotIndexed`

**AC-08 · 적재 corpus_id 검사**
- GIVEN: manifest corpus_id ≠ chunks corpus_id
- WHEN: `load_pgvector(...)`
- THEN: `VectorStoreError`, DB에 아무 행도 쓰지 않음

**AC-09 · 멱등 적재와 한 벌 유지**
- GIVEN: 로컬 DB에 다른 corpus_id 한 벌이 있음
- WHEN: `prepare_supabase.py --service-corpus --apply`를 두 번
- THEN: `corpus_versions`에 활성 1행, `chunks` 31,967행(두 번째 실행 후에도 같음)

**AC-10 · 서비스 비교(v2 23문항)**
- GIVEN: 로컬 pgvector에 서비스 100사 적재, 질의 벡터 캐시
- WHEN: `scripts/compare_vector_backends.py`
- THEN: 사전 등록 90번 기대와 대조해 결과 파일에 기록. 다르면 원인을 적고 운영 적용은 팀장이 결정

**변이 확인**: `ORDER BY` 둘째 키 `chunk_id`를 지우면 AC-05의 동점 시험이, `near_dup = false` 조건을 지우면 필터 시험이 실패해야 한다. 결과를 `docs/GATES.md` 변이 표에 적는다.
