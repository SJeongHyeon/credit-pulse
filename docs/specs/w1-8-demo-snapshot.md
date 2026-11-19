# Spec — W1.8 데모 스냅샷 적재 뼈대

상태: **구현 완료** (2026-11-02). 아래 수치는 생성·적재 실측값이다.

## 결론

v1 평가셋 15개사의 청크 9,592개와 e5-small 벡터를 14.7MB의 gzip 파일로 커밋했고, compose의 일회성 `loader` 서비스가 기존 `load_pgvector()`로 적재한다. 적재는 약 9초, 캐시 없는 빌드를 포함한 기동은 48초(db 이미지는 받아 둔 상태)다. **적재만으로는 데모 검색이 되지 않는다.** 질의 임베딩은 별도 Spec이 필요하다(D7).

### 작성 당시 확인한 전제

| 확인 항목 | 결과 | 처리 |
|---|---|---|
| 커밋된 `data/manifest.100-b.jsonl` | 파서 v6, v7 청크와 코퍼스가 다름 | D2대로 재빌드 |
| 기존 `data/chunks.100-b-v7.jsonl` (`corpus-cf4281ff03f702da5ac1d298`) | 재빌드와 60,824행 모두 `chunk_id`·본문이 같고 `corpus_id`만 다름. 이 id는 현재 코드로 재현되지 않음 | 재현되는 재빌드본을 소스로 씀 |
| v7 임베딩 | 없었음 | D3대로 15개사만 생성 |
| `load_pgvector()` | 평문만 읽고, 건너뛰지 않으며, 전체가 한 트랜잭션. `bm25_tokens`는 `[]` | 수정 없이 재사용, 앞뒤를 `backend/rag/snapshot.py`가 맡음 |
| 이미지 | `backend/`, `data/`만 복사, `.dockerignore` 없었음 | 로더를 `backend/`에 두고 `.dockerignore` 추가 |

## Why

- **페르소나**: API 키 없이 새 clone에서 서비스를 띄워 보는 평가자
- **상황**: G2는 `docker compose up` 한 줄로 키 없이 10분 안에 떠야 한다. SPEC §5.1은 서비스·CI·데모가 스냅샷만 읽도록 정했다.
- **문제**:
  1. 새 clone의 DB에는 `chunks`가 비어 있어 검색할 데이터가 없다.
  2. 전체 코퍼스 산출물은 커밋 대상이 아니라 재현할 수 없다.
  3. 적재를 손으로 하면 G2의 "한 줄"이 깨진다.
- **측정 지표**: 새 clone에서 사람 개입 없이 `chunks` 9,592행 적재, `docker compose up` 후 `/health` 200까지 10분 이내

## Goal

- **해결 목표**: `docker compose up`만으로 15개사 스냅샷이 DB에 들어가고, 다시 올려도 재적재하지 않으며, 손상되면 조용히 넘어가지 않는다.
- **성공 기준**:
  - 적재 후 `chunks` 9,592행, `documents` 15행, 벡터 384차원
  - 두 번째 기동에서 적재를 건너뛰고 행 수가 그대로다
  - 키·`.env`·네트워크 없이 동작한다(이미지 빌드 제외)
  - 적재 소요 시간 실측: 약 9초
- **Out of Scope**:
  - 실제 수집과 OpenDART 호출
  - Supabase·운영 DB 적재
  - LLM 호출·녹화 응답
  - 데모 질의 임베딩과 검색 연결(D7), BM25 토큰 생성
  - 100사 전체 스냅샷, FY2024 전년판
  - `load_pgvector()` 변경, 평가셋 변경

## What

**Happy Path (생성, 키 소유자 로컬 1회)**

1. `evals/filters_v1.jsonl`에서 `corp_code` 15개를 읽는다.
2. 청크·manifest·임베딩에서 15개사만 골라 벡터를 다시 번호 매긴다.
3. `data/snapshot/`에 압축 파일과 `snapshot.json`을 쓴다.
4. 생성물을 커밋한다.

**Happy Path (기동, 평가자)**

1. `db`가 healthy가 된다.
2. `loader`가 `snapshot.json`과 파일 sha256을 검증한다.
3. 이미 적재돼 있으면 건너뛰고, 아니면 임시 폴더에 풀어 `load_pgvector()`를 호출한다.
4. 적재 행 수를 확인하고 exit 0으로 끝난다.
5. `api`가 뜬다.

**Edge Cases**

| # | 상황 | 처리 방식 |
|---|---|---|
| EC-01 | `data/snapshot/` 또는 `snapshot.json` 없음 | exit 1. `[snapshot] 없음: <경로>`와 생성 명령 안내 |
| EC-02 | 파일 하나가 없음 | exit 1. 파일 이름을 밝히고 DB는 건드리지 않음 |
| EC-03 | 압축 파일 또는 해제본의 sha256 불일치 | exit 1. 파일 이름과 기대·실제 해시 앞 12자리, "파일이 손상됐거나 snapshot.json과 다른 버전" 안내 |
| EC-04 | 이미 같은 스냅샷이 적재됨 | `corpus_id`, 해시 3개, 행 수가 모두 같으면 건너뜀, exit 0 |
| EC-05 | 같은 `corpus_id`인데 해시나 행 수가 다름 | exit 1. `docker compose down -v` 안내. 자동으로 덮어쓰지 않음 |
| EC-06 | 적재 중 실패 | 트랜잭션 전체 롤백, `corpus_versions`도 남지 않아 다음 기동에서 재시도. 무한 재시작 금지(`restart: "no"`) |
| EC-07 | `EMBED_DIM`이 스냅샷 차원과 다름 | 기존 `load_embedding_index()` 오류, exit 1 |
| EC-08 | 예전 볼륨이라 테이블이 없음 | `db/` SQL은 볼륨 생성 때만 실행된다. exit 1, `docker compose down -v` 안내 |
| EC-09 | 초기화 중 임시 서버에 연결 | 초기화용 임시 서버는 TCP를 열지 않으므로 `pg_isready -h 127.0.0.1`로 확인한다. 옛 healthcheck로 새 볼륨 3회를 시험했을 때 실패는 재현되지 않았고, 예방 조치로 유지한다 |
| EC-10 | `.env`의 `DATABASE_URL`이 운영 DB를 가리킴 | compose `environment`가 `env_file`을 덮어써 `db`로만 간다. 테스트로 고정 |

## How

### 스냅샷 파일 (`data/snapshot/`)

| 파일 | 내용 | 크기(실측, 바이트) |
|---|---|---:|
| `snapshot.json` | 메타데이터 | 1,871 |
| `manifest.jsonl.gz` | 15행 | 2,407 |
| `chunks.jsonl.gz` | 9,592행 | 2,306,705 |
| `embeddings.ids.jsonl.gz` | 고유 본문 8,543행 | 361,451 |
| `embeddings.vectors.npy.gz` | float32 8,543×384 | 12,055,270 |
| `embeddings.metadata.json` | `load_embedding_index()` 입력 형식 | 390 |

합계 약 14.7MB. "수십 MB" 기준보다 작다.

**줄바꿈**: `data/snapshot/`은 `.gitattributes`의 `-text`로 변환을 막는다. 이 폴더의 파일은 바이트 단위로 sha256을 검사하므로, Windows의 `autocrlf`가 줄바꿈을 바꾸면 적재가 실패한다.

**gzip**: 커밋하는 파일은 gzip으로 저장한다. `load_pgvector()`가 평문 경로를 요구하므로 로더가 임시 폴더에 푼 뒤 넘긴다. `mtime=0`으로 압축해 같은 입력이 같은 바이트가 되게 한다.

**sha256 기록**: `snapshot.json`에 압축본과 해제본의 해시를 모두 적는다. 압축본 해시는 적재 전 손상 검사, 해제본 해시는 `load_pgvector()`의 기존 검사(`chunks_sha256`, `ids_sha256`, `vectors_sha256`)와 연결된다.

```json
{
  "schema_version": 1,
  "corpus_id": "corpus-…",
  "source_corpus_id": "corpus-cf4281ff03f702da5ac1d298",
  "source_chunks_sha256": "ebb66a72…",
  "embedding_model": "intfloat/multilingual-e5-small",
  "dimension": 384,
  "corp_codes": ["00101220", "…"],
  "counts": {"documents": 15, "chunks": 9666, "unique_contents": 8613},
  "files": {
    "chunks.jsonl.gz": {"sha256": "…", "bytes": 0, "uncompressed_sha256": "…"}
  }
}
```

### 구성 요소

| 위치 | 역할 | 비고 |
|---|---|---|
| `scripts/build_demo_snapshot.py` | 생성 | 로컬 전용. 키 불필요 |
| `backend/rag/snapshot.py` | 검증·건너뛰기·압축 해제 후 `load_pgvector()` 호출 | 이미지에 `scripts/`가 없어 `backend/`에 둔다. `python -m backend.rag.snapshot --dir data/snapshot` |
| `docker-compose.yml` | `loader` 추가, `api`가 `service_completed_successfully`로 대기, healthcheck 수정, 33행의 낡은 주석 수정 | |
| `.dockerignore` | `data/corpus/`, `data/chunks*.jsonl`, `data/embeddings/`, `.env` 제외 | 신규. 원본 zip이 이미지에 들어가는 것을 막는다 |
| `.gitattributes` | `data/snapshot/** -text` | 줄바꿈 변환 방지 |
| `tests/test_demo_snapshot.py` | 작은 fixture로 검증 로직 시험 | 실제 임베딩 없이 소수 차원 벡터 사용 |
| `SPEC.md`, `README.md`, `data/README.md` | 해당 부분 갱신 | 같은 PR |

```yaml
# 예시. 구현 시 확정
loader:
  build: .
  command: ["python", "-m", "backend.rag.snapshot", "--dir", "data/snapshot"]
  environment:
    DATABASE_URL: postgresql://app:app@db:5432/app
    EMBED_DIM: ${EMBED_DIM:-384}
  depends_on:
    db:
      condition: service_healthy
  restart: "no"
```

### 멱등성

`corpus_versions`에서 `corpus_id`로 행을 찾고 `manifest_sha256`, `chunks_sha256`, `embeddings_sha256`과 `chunks` 실제 행 수를 비교한다. `load_pgvector()`가 전체를 한 트랜잭션으로 처리하므로 이 행이 있으면 적재가 끝난 것이다.

### 제약

- `load_pgvector()`와 CLI는 수정하지 않고 재사용한다.
- 코퍼스 문장은 데이터로만 다룬다.
- 재시도는 두지 않는다. 필요해지면 AGENTS.md 규칙대로 상한을 둔다.
- e5-small은 SPEC §8의 D3 미결정 항목이다. 모델이 바뀌면 스냅샷을 다시 만들어야 한다.

## 결정 결과

| # | 결정 | 채택 |
|---|---|---|
| D1 | 소스 코퍼스 | v7. 팀 확인이 남아 있다(`docs/evidence/w1_readiness_2026-11-02.md`) |
| D2 | v7 manifest | `ingest build` 재실행으로 생성. 소스 `corpus-d13be58ca72a1637407b3dd2`, 청크 sha256 `cdf40ce2…304e` |
| D3 | 임베딩 범위 | 15개사 고유 본문 8,543건 |
| D4 | `corpus_id` | 새 id `corpus-e62e7235cb8e892f55dbf261`. `chunk_id`는 유지, 부모는 `source_corpus_id`에 기록 |
| D5 | 저장 | gzip으로 저장소에 커밋. 재생성할 때마다 이력에 약 15MB가 쌓인다 |
| D6 | 스냅샷 오류 시 | `api`를 띄우지 않음 |
| D7 | 데모 질의 임베딩 | 이번 범위 밖, 별도 Spec |

## AC (Given-When-Then)

**AC-W18-01 · 빈 DB 자동 적재**
- GIVEN: 새 볼륨, 커밋된 `data/snapshot/`, `.env` 없음
- WHEN: `docker compose up`
- THEN: `loader` exit 0, `chunks` 9,592행·`documents` 15행, 벡터 차원 384, 그 뒤 `/health` 200

**AC-W18-02 · 멱등성**
- GIVEN: 적재가 끝난 DB
- WHEN: `loader`를 다시 실행
- THEN: `load_pgvector()`를 호출하지 않고 exit 0, 행 수 변화 없음

**AC-W18-03 · 스냅샷 없음**
- GIVEN: `snapshot.json` 또는 파일 하나가 없음
- WHEN: `loader` 실행
- THEN: exit 1, 없는 경로와 생성 명령을 출력, DB 변경 없음

**AC-W18-04 · sha256 불일치**
- GIVEN: `chunks.jsonl.gz`의 1바이트가 바뀜
- WHEN: `loader` 실행
- THEN: exit 1, 파일 이름과 기대·실제 해시 앞 12자리 출력, `load_pgvector()` 호출 없음

**AC-W18-05 · 해시 충돌 시 덮어쓰지 않음**
- GIVEN: 같은 `corpus_id`가 다른 해시로 적재돼 있음
- WHEN: `loader` 실행
- THEN: exit 1, `docker compose down -v` 안내, 기존 행 유지

**AC-W18-06 · 부분 추출 정합성**
- GIVEN: 3개사 fixture(chunks·manifest·임베딩)와 2개사 필터
- WHEN: `build_demo_snapshot.py` 실행
- THEN: 2개사 행만 남고, `chunk_id` 불변, 재번호된 벡터가 원본의 같은 `content_sha256` 벡터와 동일, manifest `chunks` 합이 청크 수와 일치

**AC-W18-07 · 결정성**
- GIVEN: 같은 입력
- WHEN: 생성을 두 번 실행
- THEN: 모든 파일의 sha256이 같다

**AC-W18-08 · 키·운영 DB 무관**
- GIVEN: 운영 DB를 가리키는 `DATABASE_URL`이 든 `.env`
- WHEN: `docker compose config`로 `loader` 환경을 확인
- THEN: `DATABASE_URL`의 호스트가 `db`이고, `loader` 실행에 API 키가 쓰이지 않는다
