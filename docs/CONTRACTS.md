# 팀 계약서

상태: D1 실측 반영 초안. 모델·DB·최종 대상 목록은 팀 승인 후 확정한다.

## 경로 소유권

| 역할 | 주 수정 경로 | 협의가 필요한 경로 |
|---|---|---|
| 검색·데이터(류승민) | `backend/rag/`, `data/`, `docs/specs/d1-*`, `docs/specs/d2-*` | `evals/` gold, 운영 DB 설정 |
| 에이전트(서정현) | `backend/graph/`, 검색 도구 adapter | `backend/rag/retrieve.py` 계약 변경 |
| 팀장·운영(박민영) | 배포·인증·통합·환경 설정 | 데이터 산출물 저장 위치, 최종 100사·전년도 30사 |

## 검색 함수 계약

```python
retrieve(
    question: str,
    *,
    filters: SearchFilters,
    top_k: int = 10,
    backend: RetrievalBackend,
) -> list[dict]
```

- 필수 필터: `corp_code`, `fiscal_year`
- 선택 필터: `chapter`, `note_no`
- `top_k`: 1~100. D2 기본 10, 에이전트용 최종값은 평가 후 확정한다.
- 필터는 랭킹 전에 적용한다. 검색 후 결과를 잘라 맞추지 않는다.
- 빈 결과는 정상적인 `[]`다. 백엔드 미연결·timeout·계약 위반은 빈 결과로 숨기지 않는다.
- 결과는 `chunk_id`, `corpus_id`, 접수번호, 회사, 연도, 장·절·주석·표, 본문 해시, 점수, rank, 검색 방식을 보존한다.
- `score`의 범위와 의미는 `retrieval_method`별로 다르므로 방식 간 절대값을 직접 비교하지 않는다.

## 의존 파일

| 용도 | 현재 파일 | 비고 |
|---|---|---|
| pilot 코퍼스 | `data/chunks.pilot50.jsonl` | 85MB. PR 포함 여부 팀 확인 필요 |
| pilot manifest | `data/manifest.pilot50.jsonl` | parser `dart_xml_v5` |
| pilot summary | `data/ingest_summary.pilot50.json` | corpus id 포함 |
| 평가셋 | `evals/dataset_v1.jsonl` | 현재 예시뿐이며 동결 전. 검색 담당이 임의 수정하지 않음 |
| 원시 검색 결과 | `evals/results/*.jsonl` | 모델·설정·corpus/eval 해시를 함께 기록 |

## 환경변수와 오프라인 규칙

- `DART_API_KEY`: 수집 배치에서만 사용. 서비스 런타임 사용 금지.
- `DATABASE_URL`: 로컬 compose 기본값 또는 운영 pgvector 연결 문자열.
- D2 기준 임베딩: `intfloat/multilingual-e5-small`, 384차원, 로컬 캐시 전용. D3에서 bge-m3·KURE 후보와 비교한다.
- 호스팅 임베딩을 선택할 때만 해당 공급자 키를 `.env`에 추가한다.
- `DEMO_MODE=1`은 키 없이 고정 스냅샷과 녹화 응답으로 동작해야 한다.

## 운영 코퍼스 교체 규칙

- Supabase에는 활성 `corpus_id` 한 벌만 유지한다. 새 코퍼스 적재·검증 후 `DELETE FROM corpus_versions WHERE corpus_id <> '<활성 corpus_id>';`를 실행하면 외래키 `ON DELETE CASCADE`로 이전 청크가 함께 정리된다.

## 아직 팀 승인이 필요한 결정

1. FY2025 최종 100사 업종별 목록과 정정공시 대체 기준
2. FY2024 비교 30사 목록
3. D3 최종 임베딩·리랭커 모델과 실행 위치. D2 기준선은 로컬 E5-small+pgvector로 고정
4. 전체 chunks 파일의 Git·릴리스·오브젝트 스토리지 중 저장 위치. 데모용 15개사는 `data/snapshot/`에 gzip으로 커밋했다(W1.8)
5. 검색 timeout, 에이전트 top-k, 거절 임계값
