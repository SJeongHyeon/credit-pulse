# D1 OpenDART pilot 50 실행 증거

실행일: 2026-10-30  
담당: 류승민(검색·데이터)  
파서: `dart_xml_v5`  
코퍼스 ID: `corpus-c121ffd5dea5e9a0cfb237c9`

## 대상과 실행 조건

- OpenDART FY2025 유가증권시장 사업보고서 후보 872건을 조회했다.
- `(2025.12)` 결산, 회사별 최신 본문 공시를 사용했다.
- 원문 API에 본문이 없을 수 있는 `[첨부정정]`·`[첨부추가]` 공시는 후보에서 제외했다.
- 최종 업종별 250사 선정 전 파서 검증용 표본이다. 775개 적격 회사에서 `sha256(corp_code)` 정렬의 앞 50개를 결정적으로 골랐다.
- 원본 ZIP은 `data/corpus/`에만 두며 `.gitignore`로 저장소에서 제외한다.

## 결과

| 항목 | 결과 |
|---|---:|
| 대상 문서 | 50 |
| 파싱 성공 / 실패 | 50 / 0 |
| 의도하지 않은 잘림 | 0 |
| 전체 청크 | 128,607 |
| eligible 고유 청크 | 87,749 |
| 대체문자 U+FFFD | 0 |
| 1,000자 초과 청크 | 0 |
| 문서별 청크 최소 / 중앙값 / 최대 | 286 / 2,129.5 / 9,763 |
| II / NOTES / IV / XI | 19,939 / 93,336 / 10,054 / 5,278 |
| 표 행 청크 비율 | 95.8% |
| 청크 길이 최소 / 중앙값 / p95 / 최대 | 1 / 30 / 237 / 1,000 |
| 자동 원문 포함 대조 | 3 / 3 PASS |
| 자동 청크 포함 대조 | 3 / 3 PASS |
| 동일 설정 재실행 해시 | manifest / chunks / summary 모두 동일 |

G3 최소 조건인 서로 다른 문서 50건, 본문 SHA-256 기준 eligible 고유 청크 10,000건, 파서 1종을 pilot에서 충족했다. `eligible`은 `near_dup=false`이면서 `superseded=false`인 청크만 센 값이다.

같은 원문 스냅샷과 설정으로 build를 재실행한 뒤 manifest, chunks, summary의 SHA-256이 아래 기록값과 모두 일치해 결정성을 확인했다.

## 실제 데이터에서 수정한 결함

1. `[첨부정정]` 공시의 원문 API `status=014` 실패를 확인해 첨부 전용 공시를 후보에서 제외했다.
2. OpenDART `.xml`의 닫힘 태그 불일치 때문에 표준 XML 파서가 20/20 실패해, 외부 엔터티와 네트워크를 끈 `lxml` 복구 모드를 도입했다.
3. 닫히지 않은 `<P>`·`<TABLE>` 아래로 후속 SECTION이 들어가는 형식을 복구하고, 표가 이후 보고서 전체를 삼키지 않게 했다.
4. 대상 장 밖의 로마숫자 장과 III장 후속 고정 목차에서 추출 상태를 종료해 주석 범위 오염을 제거했다.
5. 블록×청크 제곱 탐색이던 보존 검사를 장·절별 선형 비교로 바꿔, 실측 한 문서 50.788초를 0.427초로 줄였다.
6. 원문 대조 샘플이 `해당사항 없음`, 단위 표기만 고르지 않도록 의미 있는 샘플을 우선하게 했다.

## 재현 명령

```powershell
python -m backend.rag.ingest build `
  --corpus-dir data/corpus `
  --manifest data/manifest.pilot50.jsonl `
  --chunks data/chunks.pilot50.jsonl `
  --summary data/ingest_summary.pilot50.json `
  --max-chars 1000 --overlap-chars 100 `
  --embedding-model unselected

python -m backend.rag.ingest validate `
  --manifest data/manifest.pilot50.jsonl `
  --chunks data/chunks.pilot50.jsonl `
  --summary data/ingest_summary.pilot50.json

python -m backend.rag.ingest verify `
  --corpus-dir data/corpus `
  --chunks data/chunks.pilot50.jsonl `
  --output docs/evidence/d1_source_verification.pilot50.md `
  --rcept-no 20260311004517 `
  --rcept-no 20260319001075 `
  --rcept-no 20260313000652
```

## 파일 SHA-256

| 파일 | SHA-256 |
|---|---|
| `data/targets.pilot50.csv` | `c8587a5ee588c2d1911c8f5f55f873633f879ac6258ad7138c002c02a83516d6` |
| `data/manifest.pilot50.jsonl` | `444a17cb250d588bdb6793f6ddfc1e0fb1009301fdf199fb6bbd7b0eb1206ca3` |
| `data/chunks.pilot50.jsonl` | `3f1faa6601049c43b165b9845dba35853dc9071f13f1721fcf3a5ce38113596a` |
| `data/ingest_summary.pilot50.json` | `efc0fc48fabf7bcc86e8977380c095ab0784a7a4edb485cd75510e3bbd0a270c` |
| `docs/evidence/d1_source_verification.pilot50.md` | `7ec93e381b95e103b5328fe5c60a61b88211e006aa99bbed12be2c183bee7025` |

## 남은 제한과 다음 작업

- 자동 대조 3건은 통과했지만 DART 화면과의 `사람 확인` 체크는 아직 남았다.
- 표 행 단위 계약 때문에 표 청크 비율이 95.8%, 중앙 길이가 30자다. D2 검색 평가에서 단위·헤더 등 저정보 행이 상위 결과를 오염하는지 측정한 뒤 필터 여부를 결정한다. 지금 임의 삭제하지 않는다.
- 전년도 30~50사를 아직 수집하지 않아 `near_dup=true`는 0건이다.
- 최종 250사 업종 분포와 정정공시 대체 기준은 팀 합의(OQ-004)가 필요하다. pilot 50을 최종 코퍼스로 간주하지 않는다.
- 임베딩 모델이 미확정이므로 현재 산출물의 설정은 `unselected`다. 모델 확정 후 새 `corpus_id`로 다시 빌드한다.

