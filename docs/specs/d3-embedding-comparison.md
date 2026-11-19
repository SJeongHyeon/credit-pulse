# D3 Spec - 임베딩 모델 비교 (DB 없는 오프라인 평가)

## Why

- **페르소나**: 검색 품질을 숫자로 고르는 검색·데이터 담당자와, 결과를 검토하는 팀장
- **상황**: D2 기준선은 `intfloat/multilingual-e5-small`(384차원) 하나다. 가이드 ② RAG 품질은 "청킹과 임베딩 선택 근거: 둘 이상을 비교해 고른 이유"를 요구하고, 11/2 회의에서 피드백으로 임베딩 모델 테스트와 MRR·Recall 측정을 받았다.
- **문제**:
  1. 기존 embedder는 모든 모델에 E5 접두사(`passage:`/`query:`)와 mean pooling을 적용한다. KURE-v1·bge-m3에 그대로 쓰면 비교가 불공정하다.
  2. DB 컬럼이 384차원이라 1024차원 모델을 넣으려면 DB를 따로 만들어야 한다.
  3. 평가셋 질문을 검색해 원시 결과 파일을 만드는 실행기가 없다(채점기 `evaluation.py`만 있다).
  4. 큰 모델로 고유 청크 3만여 개를 CPU에서 전부 임베딩하면 몇 시간이 걸릴 수 있다(추정).
- **측정 지표**: v1 관련 문항의 Recall@10(정답 청크가 상위 10개에 하나라도 있는 비율, 즉 Hit@10), MRR@10, 임베딩 처리량(rows/s), 벡터 차원

## Goal

- e5-small, `nlpai-lab/KURE-v1`, `BAAI/bge-m3`(로컬 3종)과 `upstage/solar-embedding-2`(API 1종)를 **같은 코퍼스·같은 평가셋·같은 필터·같은 k**로 비교한다. Upstage는 키를 받은 뒤 추가하는 4번째 후보다.
- 모델별 풀링·접두사 규칙을 코드에 등록하고, 등록되지 않은 모델은 거절한다.
- DB 없이 메모리 코사인 검색으로 원시 결과와 지표를 만든다.
- 평가 질문에 나온 회사만 임베딩하는 옵션으로 비교 시간을 줄인다.

**성공 기준**

- 세 모델의 Recall@10·MRR@10과 처리량이 `evals/results/`에 파일로 남는다.
- 기존 E5 산출물의 설정 해시가 바뀌지 않는다(재임베딩 불필요).
- 필터는 검색 전에 걸리고, 정렬은 `(score DESC, chunk_id ASC)`로 pgvector 백엔드와 같다.
- 실험 전에 `evals/PREREGISTER.md`에 기대 범위를 적는다.

**Out of Scope**

- 메타데이터 머리말을 임베딩 입력에 붙이는 실험(다음 실험, 한 번에 하나만)
- BM25 하이브리드, 리랭커, pgvector 운영 적재와 `EMBED_DIM` 변경
- 최종 100사 재빌드와 Supabase 연결

## What

**Happy Path**

1. 평가셋 v1을 동결하고, 관련 문항마다 검색 필터(`corp_code`, `fiscal_year`, 선택 `chapter`)를 정한다.
2. 필터에 나온 회사의 청크만 세 모델로 임베딩한다(`--corp-codes`).
3. `offline_eval`이 모델마다 질문을 검색하고, 원시 결과 JSONL과 `.metrics.json`을 쓴다.
4. 세 모델의 지표와 처리량을 EVAL_REPORT와 PREREGISTER의 "측정 뒤" 칸에 적는다.

**Edge Cases**

| # | 상황 | 처리 방식 |
|---|---|---|
| EC-01 | 등록되지 않은 모델 이름 | 오류. 규칙을 추측하지 않는다 |
| EC-02 | 관련 문항에 필터 없음 | 오류 |
| EC-03 | 필터 없는 비관련 문항 | 빈 결과(Recall·MRR은 관련 문항만 계산) |
| EC-04 | 필터한 회사의 청크 벡터가 없음 | 오류(`--corp-codes` 누락 안내) |
| EC-05 | ids·vectors 파일이 metadata 해시와 다름 | 오류 |
| EC-06 | 질의 모델 차원 ≠ 산출물 차원 | 오류 |
| EC-07 | 점수 동점 | chunk_id 오름차순 |

## How

**모델 규칙 (`backend/rag/embedding.py`의 `MODEL_PROFILES`)**

| 모델 | 풀링 | 질의 접두사 | 문서 접두사 | 근거 |
|---|---|---|---|---|
| `intfloat/multilingual-e5-small` | mean | `query: ` | `passage: ` | D2 기준선 |
| `nlpai-lab/KURE-v1` | CLS | 없음 | 없음 | 모델 저장소 `1_Pooling/config.json`의 `pooling_mode_cls_token: true` |
| `BAAI/bge-m3` | CLS | 없음 | 없음 | M3Embedder 기본값 `pooling_method="cls"`, `query_instruction_for_retrieval=None` |
| `upstage/solar-embedding-2` | API | 질의 모델 `solar-embedding-2-query` | 문서 모델 `solar-embedding-2-passage` | Upstage Embeddings 문서(1024차원, 8k 토큰, 요청당 최대 100개). OpenAI 호환 API, `base_url=https://api.upstage.ai/v1`. 옛 별칭 `embedding-query/passage`는 2026-12-31 지원 종료 |

모든 벡터는 L2 정규화한다(API 응답도 받은 뒤 정규화). 로컬 모델 파일은 미리 받아 두고 `local_files_only=True`로 읽는다(각 약 2GB, G2 주의).

**Upstage(API) 후보의 조건**

- 외부 API 클라이언트 생성과 호출은 `backend/services/llm.py`의 `upstage_embeddings`만 한다(AGENTS.md 2번). `UpstageEmbedder`는 이 함수를 주입받아 배치 분할·차원 검사·정규화만 한다.
- 키는 `UPSTAGE_API_KEY` 환경변수에서만 읽는다. `embedding`·`offline_eval` CLI는 실행 위치(저장소 루트)의 `.env`를 읽되, 이미 설정된 환경변수는 덮어쓰지 않는다. 로그·예외·결과 파일에 키를 남기지 않는다.
- 요청에는 청크 본문(공개 공시)과 평가 질문만 실린다.
- 비용이 조 LLM 예산(OpenRouter 키 30달러)에 포함되는지, 단가는 Upstage 콘솔에서 확인한 뒤 실행한다(미확인).
- **채택 조건(측정 전 확정):** 가장 좋은 로컬 모델보다 MRR@10이 사전 등록한 차이 이상 높을 때만 채택한다. 채택하면 평가자에게 키가 없는 G2 데모 모드를 위해 질의 임베딩 대체 경로(로컬 모델 또는 녹화한 질의 벡터)를 같은 PR에서 만든다. 차이가 그보다 작으면 운영이 단순한 로컬 모델을 쓴다.

**필터 파일 (`--filters`, 문항 id별 한 줄)** — 평가셋을 고치지 않고 따로 둔다. 문항에 `filters` 필드가 있으면 그것을 먼저 쓴다.

```json
{"id": 1, "corp_code": "00126380", "fiscal_year": 2025, "chapter": "XI"}
```

**명령 (검색·데이터 담당 PC, 저장소 루트)**

```powershell
# 0) 처리량 먼저: 2,000행으로 속도 확인 (rows_per_second_this_run)
python -m backend.rag.embedding build --chunks data/chunks.100-b.jsonl `
  --output-prefix data/embeddings/kure-speed --model nlpai-lab/KURE-v1 --limit 2000

# 1) 평가 질문에 나온 회사만 모델별 임베딩 (같은 --corp-codes를 세 모델에 똑같이)
$CORPS = "00106119,00134963"   # 필터 파일의 corp_code 목록
python -m backend.rag.embedding build --chunks data/chunks.100-b.jsonl `
  --output-prefix data/embeddings/e5-v1corps --model intfloat/multilingual-e5-small --corp-codes $CORPS
python -m backend.rag.embedding build --chunks data/chunks.100-b.jsonl `
  --output-prefix data/embeddings/kure-v1corps --model nlpai-lab/KURE-v1 --corp-codes $CORPS
python -m backend.rag.embedding build --chunks data/chunks.100-b.jsonl `
  --output-prefix data/embeddings/bgem3-v1corps --model BAAI/bge-m3 --corp-codes $CORPS
# (키가 있을 때) Upstage API 후보
python -m backend.rag.embedding build --chunks data/chunks.100-b.jsonl `
  --output-prefix data/embeddings/upstage-v1corps --model upstage/solar-embedding-2 --corp-codes $CORPS

# 2) 모델별 검색 + 지표
python -m backend.rag.offline_eval --dataset evals/dataset_v1.jsonl --chunks data/chunks.100-b.jsonl `
  --embedding-prefix data/embeddings/kure-v1corps --filters evals/filters_v1.jsonl `
  --output evals/results/emb_kure_v1.jsonl
```

**제약**

- 비교는 모델 하나만 바꾼다. 코퍼스(`corpus_id`), 평가셋 해시, 필터, k=10, `max_length=512`를 고정한다.
- 원시 결과와 지표 파일은 커밋한다(가이드: 원시 결과를 저장소에). 벡터 파일(`data/embeddings/`)은 커밋하지 않는다.
- 평가셋 gold는 chunk_id다. 코퍼스를 다시 빌드하면 chunk_id가 바뀔 수 있으므로, 비교가 끝날 때까지 같은 코퍼스(B-100)를 쓴다.

## AC (Given-When-Then)

**AC-01 · 모델 규칙**
- GIVEN: e5-small, KURE-v1, bge-m3, 미등록 모델 이름
- WHEN: `model_profile` 호출
- THEN: 위 표의 규칙이 나오고 미등록 모델은 오류다.

**AC-02 · 기존 E5 산출물 호환**
- GIVEN: E5 설정으로 build
- WHEN: metadata를 확인
- THEN: `passage_prefix`는 `"passage: "`이고 `pooling`·`corp_codes` 키가 없어 기존 설정 해시와 같다.

**AC-03 · 회사 한정 임베딩**
- GIVEN: 두 회사의 청크와 `corp_codes={A}`
- WHEN: build
- THEN: A의 청크만 임베딩되고 metadata에 `corp_codes`가 기록된다. 빈 집합은 오류다.

**AC-04 · 검색 전 필터와 정렬**
- GIVEN: 다른 회사·근접 중복 청크가 더 높은 점수를 가진 코퍼스
- WHEN: 회사 필터로 검색
- THEN: 그 청크는 결과에 없고, 동점은 chunk_id 오름차순이다.

**AC-06 · Upstage API 후보**
- GIVEN: 가짜 OpenAI 호환 클라이언트
- WHEN: 문서·질의를 임베딩
- THEN: 문서는 passage 모델, 질의는 query 모델로 요청하고, 응답 순서와 관계없이 index 순으로 맞추며, 요청당 100개 이하로 나누고, L2 정규화된 1024차원 벡터를 돌려준다. 키가 없거나 차원이 다르면 오류다.

**AC-05 · 실패를 숨기지 않음**
- GIVEN: 필터 없는 관련 문항, 벡터가 빠진 회사, 변조된 ids 파일
- WHEN: 실행
- THEN: 각각 명시적 오류가 난다.
