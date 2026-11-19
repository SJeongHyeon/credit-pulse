# 데이터

G3: **서로 다른 청크 1만 개 이상, 서로 다른 문서 50건 이상**(복제와 변형 제외), 문서 파싱 1종 이상. 2만 개 이상을 권장합니다.

## 폴더

| 위치 | 내용 |
|---|---|
| `corpus/` | 수집한 원본 ZIP/XML. 로컬 전용이며 **저장소에 커밋하지 않습니다** |
| `manifest.jsonl` | 문서와 청크 목록. 인덱스를 만들 때마다 갱신합니다 |
| `chunks.jsonl` | 검색용 청크와 출처 메타데이터, 본문 SHA-256 |
| [`g3/chunks.100-b-v9.jsonl.gz`](g3/README.md) | 평가자 재집계용 FY2025 100사 전체 청크 압축본과 검증 방법 |
| `ingest_summary.json` | 성공·실패 문서 수와 전체·고유 청크 수 |

수집한 날짜와 출처 주소를 manifest에 기록하세요. 평가용 데모에는 팀이 별도로 승인한 파싱 스냅샷만 사용하며, 원본 ZIP 재배포 여부는 팀 합의 전까지 금지합니다.

## manifest 형식 (한 줄에 하나)

```json
{"doc_id": "20260312000123", "corpus_id": "corpus-...", "source_url": "https://...", "collected_at": "2026-11-02", "parser": "dart_xml_v7", "chunks": 214, "chars_original": 53210, "chars_kept": 53210, "separator_counts": {"blank_lines": 0, "line_breaks": 120, "sentence_marks": 340, "paragraph_elements": 250, "section_elements": 18, "table_rows": 60}, "superseded": false, "truncated": false}
```

원문이 길어서 잘라 넣었다면 `chars_original`과 `chars_kept`가 다르고 `truncated`가 true여야 합니다. 얼마나 잘랐는지 숨기면 파싱 대조가 의미가 없어집니다.

청크 단위 기록이 필요하면 `chunks.jsonl`을 따로 두고 `doc_id`와 `chunk_id`, 위치, 본문 해시(`chunk_sha256`)를 적으세요. 청크 수의 합이 1만을 넘고 **서로 다른 `chunk_sha256`이 1만 개 이상**이라는 것이 G3의 증거입니다. 정정공시처럼 본문이 같은 문서는 해시가 같으므로 한 개로 셉니다.

## 임베딩 시간과 비용의 기준 (참고)

DART 공시 11,022청크(평균 762토큰) 기준입니다. 조 코퍼스에서는 반드시 100건으로 먼저 재 보세요.

| 방식 | 시간 또는 비용 |
|---|---|
| 로컬 KURE-v1 (Apple MPS, 배치 16) | 361초 (30.5청크/초). CPU 전용은 측정하지 않았습니다 |
| OpenAI text-embedding-3-small API | 1만 청크 한 번에 약 $0.15 ($0.02/1M 토큰). 매일 전체를 다시 하면 한 달 약 $4.6 |

매번 전체를 다시 임베딩하지 말고 본문 해시가 바뀐 청크만 다시 하세요.

## 저장소 크기 주의

벡터는 1024차원이면 1만 개에 약 41MB입니다. 본문과 manifest는 저장소에 두고, 벡터는 `db/` 아래 SQL로 적재하는 방법을 팀과 상의하세요.

## D1 OpenDART 파이프라인

수집 키는 `.env`의 `DART_API_KEY`에만 둡니다. 키가 들어간 URL은 저장하거나 로그에 남기지 않습니다. 서비스 런타임과 데모 모드는 OpenDART를 호출하지 않습니다.

```bash
# 1. FY2025 사업보고서 후보 목록(제출일 범위는 팀이 확정)
python -m backend.rag.ingest list \
  --begin-date 20260301 --end-date 20260430 \
  --fiscal-year 2025 --output data/candidates.jsonl

# 2-A. 후보 회사의 공개 기업개황·업종코드 캐시
python -m backend.rag.ingest profiles \
  --candidates data/candidates.jsonl \
  --output data/company_profiles.jsonl \
  --request-interval-seconds 0.5

# 2-B. 금융업 제외·업종 비례 FY2025 100사 후보안
python -m backend.rag.ingest select \
  --candidates data/candidates.jsonl \
  --profiles data/company_profiles.jsonl \
  --output data/targets.proposed.100.csv \
  --summary data/targets.proposed.100.summary.json \
  --count 100 --seed 20261030

# 2-C. 팀이 승인한 후보안만 data/targets.csv로 확정한 뒤 원문 수집
# 필수 필드: rcept_no,corp_code,corp_name,fiscal_year
# 선택 필드: report_type,filed_at,superseded
python -m backend.rag.ingest collect \
  --targets data/targets.csv --corpus-dir data/corpus

# 3. 키 없이 파싱·청킹·manifest 재생성
python -m backend.rag.ingest build \
  --corpus-dir data/corpus \
  --manifest data/manifest.jsonl \
  --chunks data/chunks.jsonl \
  --summary data/ingest_summary.json \
  --max-chars 1000 --overlap-chars 100 \
  --embedding-model unselected

# 4. JSON Schema, 해시, 문서-청크 참조, 집계 검증
python -m backend.rag.ingest validate \
  --manifest data/manifest.jsonl \
  --chunks data/chunks.jsonl \
  --summary data/ingest_summary.json

# 5. 원문 대조 3건 증거 문서 생성
python -m backend.rag.ingest verify \
  --corpus-dir data/corpus \
  --chunks data/chunks.jsonl \
  --output docs/evidence/d1_source_verification.md

# 6. G3 eligible 고유 청크 SHA-256 목록 생성
python -m backend.rag.ingest hashes \
  --chunks data/chunks.jsonl \
  --output data/unique_chunk_hashes.txt
```

전체 수집 전에 1사, 원문 대조용 3사, seed 20사, pilot 50사 순서로 실행합니다. 파서와 청커의 상세 계약은 `docs/specs/d1-dart-ingest.md`를 따릅니다.

`targets.proposed.100.csv`는 팀 승인 전까지 후보안입니다. `profiles`는 같은 출력 파일이 있으면 완료된 회사는 건너뛰며, API 키와 키가 포함된 URL을 저장하지 않습니다.

`corpus_id`는 대상 문서·원본 해시·수집일·`superseded`와 parser·청킹·임베딩 설정을 함께 해시합니다. 같은 입력과 설정은 같은 ID를 만들고, 설정이 달라지면 다른 ID를 만듭니다. G3 고유 청크는 `near_dup=false`이면서 `superseded=false`인 본문 SHA-256만 셉니다.

## D2 로컬 벡터 베이스라인

D2 기준선은 캐시된 `intfloat/multilingual-e5-small`을 사용합니다. 본문은 `passage:`, 질의는 `query:` 접두사를 붙이고 mean pooling 후 L2 정규화합니다. 같은 `content_sha256`은 한 번만 임베딩하고 개별 청크 적재 시 재사용합니다.

```powershell
python -m backend.rag.embedding build `
  --chunks data/chunks.pilot50.jsonl `
  --output-prefix data/embeddings/e5-small-pilot50 `
  --model intfloat/multilingual-e5-small `
  --batch-size 64 --max-length 512
```

배치마다 `*.progress.json`을 원자적으로 갱신하므로 같은 명령으로 중단 지점부터 재개합니다. 벡터와 모델 캐시는 대용량이므로 Git에 커밋하지 않습니다.

로컬 pgvector가 다른 프로젝트의 5432 포트와 충돌하면 다음처럼 이 프로젝트만 5433에 띄웁니다.

```powershell
$env:POSTGRES_PORT="5433"
docker compose up -d db
$env:DATABASE_URL="postgresql://app:app@localhost:5433/app"
```

임베딩 완료 후 적재:

```powershell
python -m backend.rag.vector_store `
  --manifest data/manifest.pilot50.jsonl `
  --chunks data/chunks.pilot50.jsonl `
  --embedding-ids data/embeddings/e5-small-pilot50.ids.jsonl `
  --embedding-vectors data/embeddings/e5-small-pilot50.vectors.npy `
  --embedding-metadata data/embeddings/e5-small-pilot50.metadata.json
```

이 모델은 D2 기준선이다. bge-m3·KURE 등 후보 비교와 최종 모델 선택은 같은 평가셋으로 D3에서 한 번에 하나씩 수행합니다.

## 데모 스냅샷

`data/snapshot/`은 평가(v2) 검색 대상 16개사(`data/snapshot_targets.jsonl`)의 v9 청크와 채택 조합 임베딩(OpenAI 3-small, 1,536차원)입니다. 평가에 쓴 벡터를 그대로 담아, compose에서도 측정과 같은 검색 입력을 씁니다. `docker compose up`이 `loader` 서비스로 자동 적재하며, 이미 적재돼 있으면 건너뜁니다. 적재 쪽 상세는 `docs/specs/w1-8-demo-snapshot.md`를 보세요. `.env`나 API 키는 필요 없습니다.

서비스(Cloud Run)는 100사 전체 청크와 벡터를 씁니다. 이 파일들(`data/experiments/b100-v9-fixed_window/`, `data/embeddings/v9/`)은 GitHub에 올리지 않고 배포 때 이미지에만 넣습니다(README 배포 절).

다시 만들 때는 v9 청크와 그 임베딩이 있는 로컬에서 실행합니다(API 키 필요 없음). 중간 산출물은 커밋되지 않는 곳에 둡니다. 11/6 생성 결과는 문서 16, 청크 4,942, 고유 본문 4,932, corpus_id `corpus-a833986c7423168eae523176`(부모 `corpus-ffc1830e50a11c57749b63b9`, 본문은 커밋된 manifest와 같음, `docs/evidence/g3_v9_manifest_100-b.md`)입니다.

```powershell
python -X utf8 scripts/build_demo_snapshot.py select `
  --chunks data/experiments/b100-v9-fixed_window/chunks.jsonl `
  --manifest data/experiments/b100-v9-fixed_window/manifest.jsonl `
  --filters data/snapshot_targets.jsonl `
  --work-dir data/embeddings/snapshot-work
python -X utf8 scripts/build_demo_snapshot.py pack `
  --work-dir data/embeddings/snapshot-work `
  --embedding-prefix data/embeddings/v9/oai-small-fixed1000-o100
```

`loader`가 "다른 내용으로 적재돼 있습니다"라고 하면 `docker compose down -v`로 볼륨을 지우고 다시 올리세요.

`verify`의 자동 PASS는 파싱 문장이 원문과 청크에 포함됐다는 뜻입니다. 최종 원문 대조는 생성된 문서에서 담당자가 DART 원문 화면을 직접 보고 `사람 확인`을 체크해야 완료됩니다.

`hashes`는 `near_dup=false`, `superseded=false`인 청크의 본문 SHA-256을 검증하고 중복 제거한 뒤 정렬해 저장합니다. 줄 수가 G3 고유 청크 수이며, 명령 출력의 `chunks_sha256`과 `output_sha256`으로 같은 입력에서 같은 목록이 생성됐는지 확인할 수 있습니다.
