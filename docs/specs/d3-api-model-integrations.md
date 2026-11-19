# Spec — D3 OpenAI 임베딩·Voyage 리랭커 연결

## Why

- **페르소나**: 같은 동결 평가셋으로 검색 모델을 비교하는 검색 담당자
- **상황**: 로컬 임베딩 3종과 Upstage, 로컬 리랭커 2종은 연결돼 있지만 OpenAI 임베딩과 Voyage 리랭커는 팀장 PC에서 측정할 공통 코드가 없다.
- **문제**: API 키를 공유하지 않으면서도 입력 순서, 차원, 모델명과 점수 대응을 측정 전에 검증해야 한다. `text-embedding-3-large` 기본 3,072차원은 pgvector HNSW `vector` 인덱스 한도 2,000을 넘는다.
- **측정 지표**: 사전등록 9번의 Recall@10·MRR@10, Voyage 재정렬의 Recall@10·MRR@10·네트워크 지연·토큰 비용

## Goal

- `text-embedding-3-small`과 `text-embedding-3-large` 1,536·3,072차원을 기존 오프라인 임베딩 빌드·평가 CLI에서 선택할 수 있다.
- `rerank-3`과 legacy 비교군 `rerank-2.5`를 기존 `Reranker` 계약으로 선택할 수 있다.
- 키가 없는 개발 환경에서는 외부 요청 없이 가짜 응답 테스트가 통과한다.
- API 응답의 순서·개수·차원이 계약과 다르면 결과를 쓰지 않고 실패한다.
- 실제 호출 전에 `--dry-run`으로 토큰 수·공식 단가 기준 예상 비용을 확인한다.
- 같은 설정으로 완료된 벡터 산출물이 있으면 API 키가 없어도 재호출하지 않는다.

**Out of Scope**: 실제 API 측정, 키 저장·공유, 최종 모델 채택, pgvector 재적재, 동결 평가셋 수정, Voyage 후보 수·batch 실험(사전등록 10번)

## What

**Happy Path**

1. 호출자는 `OPENAI_API_KEY` 또는 `VOYAGE_API_KEY`를 환경변수로만 제공한다.
2. OpenAI 임베딩 응답은 `index`로 입력 순서를 복구하고 L2 정규화해 기존 산출물 형식으로 저장한다.
3. Voyage 응답은 관련도순 응답의 `index`를 입력 문서 순서의 점수 배열로 복구한다.
4. 기존 평가 실행기는 API 구현도 로컬 구현과 같은 인터페이스로 사용한다.

**Edge Cases**

- 키 없음: 키 이름만 포함한 오류를 내고 값은 출력하지 않는다.
- 빈 입력: 외부 API를 호출하지 않고 빈 배열을 반환한다.
- 응답 index 중복·누락·범위 초과, 개수·차원 불일치, NaN·Inf·0 벡터: 명시적 오류로 중단한다.
- `text-embedding-3-large`: 기본 비교는 `dimensions=1536`, 차원 단독 비교 프로필은 `dimensions=3072`를 요청한다.
- 1,536·3,072차원은 설정 해시가 반드시 달라야 하며, 같은 출력 경로의 산출물을 서로 재사용하지 않는다.
- 완료된 ids·vectors·progress·metadata가 모두 있고 설정 해시가 같음: 비용 0으로 건너뛴다. 일부만 있거나 설정이 다르면 호출 전에 오류로 중단한다.

## How

- 외부 클라이언트 생성과 호출은 `backend/services/llm.py`의 `openai_embeddings`, `voyage_rerank`에만 둔다.
- 임베딩 프로필:
  - `openai/text-embedding-3-small`: API 모델 `text-embedding-3-small`, 1,536차원
  - `openai/text-embedding-3-large`: API 모델 `text-embedding-3-large`, `dimensions=1536`
  - `openai/text-embedding-3-large-3072`: API 모델 `text-embedding-3-large`, `dimensions=3072`
- API 프로필의 실제 `api_model`과 `dimension`을 설정 해시에 직접 포함한다.
- OpenAI는 한 요청을 최대 100개로 제한한다. 프로젝트 청크 상한에서 API의 총 토큰 상한을 넘기지 않도록 공식 최대 배열 크기보다 보수적으로 둔다.
- Voyage 프로필은 `voyage/rerank-3`, `voyage/rerank-2.5`다. 모든 후보 점수를 받아 기존 코드가 안정적으로 정렬하도록 `top_k=None`, `truncation=True`로 호출한다.
- OpenAI dry-run은 공식 권장 `cl100k_base` 토크나이저로 입력 토큰을 세고 2026-11-03 공식 단가(3-small $0.02/1M, 3-large $0.13/1M)를 적용한다. 단가 확인 URL·확인일과 30달러 예산 통과 여부를 함께 출력한다.
- Voyage dry-run은 공식 식 `(질의 토큰 × 문서 수) + 문서 토큰 합`을 적용한다. 검색 때문에 다른 API가 호출되지 않도록 필터 안의 문서를 로컬 tokenizer로 세고 토큰 수가 가장 큰 N개로 비용 상한을 계산하며, 무료 크레딧은 차감하지 않는다.
- Voyage SDK는 `timeout=60`, `max_retries=0`으로 만든다. 실패는 숨기거나 1차 순서로 대체하지 않는다.
- `voyageai`·`tiktoken`은 실험용 `requirements-ml.txt`에만 둬 서비스 기본 이미지에서 제외한다.

```powershell
python -m backend.rag.embedding build --chunks data/chunks.jsonl `
  --output-prefix data/embeddings/openai-large --model openai/text-embedding-3-large --dry-run

python -m backend.rag.embedding build --chunks data/chunks.jsonl `
  --output-prefix data/embeddings/openai-large-3072 `
  --model openai/text-embedding-3-large-3072 --dry-run

python -m backend.rag.offline_eval --dataset evals/dataset_v1.jsonl `
  --chunks data/chunks.jsonl --filters evals/filters_v1.jsonl `
  --reranker voyage/rerank-3 --candidate-k 50 --dry-run
```

## AC

**AC-01 · OpenAI 요청 계약**
- GIVEN: 역순의 가짜 임베딩 응답
- WHEN: 두 모델을 호출
- THEN: 모델명·1,536차원 요청이 전달되고 벡터는 index 순서로 복구된다.

**AC-02 · 임베딩 빌드 호환**
- GIVEN: 가짜 OpenAI 임베딩 함수
- WHEN: 문서·질의를 인코딩
- THEN: 100개 단위로 나뉘며, 개수·차원을 검사하고 L2 정규화한다.

**AC-03 · Voyage 요청 계약**
- GIVEN: 관련도순으로 섞인 가짜 Voyage 응답
- WHEN: 재정렬 점수를 요청
- THEN: `rerank-3` 또는 `rerank-2.5`, `top_k=None`, `truncation=True`가 전달되고 점수는 입력 문서 순서로 복구된다.

**AC-04 · 비밀값·경계**
- GIVEN: 키가 없거나 잘못된 API 응답
- WHEN: API 어댑터를 호출
- THEN: 외부 요청 없이 실패하고 오류에 키 값이 없으며, RAG 모듈은 외부 SDK를 직접 import하지 않는다.

**AC-05 · 비용 dry-run**
- GIVEN: OpenAI 임베딩 입력 또는 Voyage 평가 입력과 API 키가 없는 환경
- WHEN: `--dry-run` 실행
- THEN: 외부 추론 API와 결과 파일 쓰기 없이 토큰 수·단가·예상 비용·30달러 예산 통과 여부를 출력한다.

**AC-06 · 완료 산출물 재사용**
- GIVEN: 같은 설정 해시의 완료된 ids·vectors·progress·metadata
- WHEN: 같은 출력 경로로 임베딩 build를 다시 실행
- THEN: API 키 없이 `processed_this_run=0`, `skipped_existing=true`로 끝난다.

**AC-07 · OpenAI 3-large 차원 분리**
- GIVEN: 같은 입력의 1,536차원과 3,072차원 프로필
- WHEN: 가짜 임베딩 응답 또는 `--dry-run`으로 실행
- THEN: API 요청 차원과 출력 shape가 각각 맞고 설정 해시는 다르며, 토큰 단가와 예상 비용은 같다.
