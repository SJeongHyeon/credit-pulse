# 문서 그래프 검색: 구현 검증과 남은 실측

기준 브랜치 `origin/main`의 `d2195c5`에서 `feat/graphrag` worktree를 만들었다. 설계와 채택 조건은 `docs/specs/graph-retrieval.md`, 변경 전 기대는 `evals/PREREGISTER.md`의 150번에 있다. Microsoft GraphRAG의 개체 추출·커뮤니티 요약 전체를 구현한 것이 아니라, DART 원문 메타데이터의 연속 청크를 탐색하는 **제한적 문서 그래프 검색**이다. 최종 답에는 기존처럼 원문 청크만 인용한다.

## 키 없이 확인한 것

- `python -m pytest -q tests/test_document_graph.py tests/test_retrieval_combo.py -p no:cacheprovider`: 38 passed. 최초 실패는 `document_graph` 부재, 검색 설정의 그래프 필드 부재, 후보 한도 0과 작은 리랭커 한도 초과였다. 각 원인을 순서대로 수정했다.
- `data/g3/chunks.100-b-v9.jsonl.gz`를 읽어 그래프를 생성했다: 원문 청크 31,967개 → 노드 31,967개·무방향 인접 간선 23,755개, 이 PC에서 0.834초. 이 파일은 현재 서비스 코퍼스와 `corpus_id`가 달라 응답 성능 측정에 사용하지 않았다.
- 전체 `python -m pytest -q -p no:cacheprovider`: 기존 `tests/test_upstream_errors.py::test_voyage_errors_become_llm_errors` 1건만 `ModuleNotFoundError: voyageai`, 나머지 1,418 passed·8 skipped. 이 PC에 `backend/requirements.txt`가 요구하는 `voyageai==0.5.0`이 설치되지 않은 환경 문제다. 코드를 바꾸어 시험을 우회하지 않았다.
- 해당 1건을 제외한 전체 시험: `python -m pytest -q -k 'not test_voyage_errors_become_llm_errors' -p no:cacheprovider` → 1,420 passed·8 skipped·1 deselected. 이 명령은 아래 변경의 최종 검토 때 다시 실행한다.
- OpenRouter 키만 있을 때 임베딩 생성이 잘못 거절되던 검사를 수정했다(`93d5d36`). 먼저 실패 테스트에서 `OPENAI_API_KEY가 없습니다`를 재현한 뒤 수정했다. 관련 테스트 105 passed, 전체 테스트 1,423 passed·8 skipped(실제 API 키는 테스트 프로세스에서만 비우고 `.env`는 변경하지 않음), 동결 해시 변경 없음.

## 실제 검색·응답 전후 측정: 완료

키 소유자가 이 worktree에 `.env`를 만들고, 같은 v9 코퍼스의 서비스 청크(`C:\Users\home\Desktop\proj3-a\data\service\chunks.jsonl`, SHA-256 `f523a7a9575f1ee5d4457e4c150b88e48525fb7cd5cdbca222dae6943494b62b`)를 `data/experiments/b100-v9-fixed_window/chunks.jsonl`에 하드 링크했다. 임베딩 접두 경로는 `C:\Users\home\Desktop\proj3-a\data\service\embeddings`이며 메타데이터의 청크 해시·모델(`openai/text-embedding-3-small`)·평가셋 `corpus_id`가 일치한다. Windows BM25 토큰표도 같은 청크로 만들었다(31,967 청크·31,953 항목). 이 경로들은 로컬 준비 상태이며 Git 추적 대상이 아니다. 키·토큰·전체 프롬프트를 결과나 이 문서에 붙이지 않는다.

보충 평가셋의 초기 두 기준선 실행은 Voyage 직접 리랭크의 HTTP 429로 중단됐다. 결제수단 등록 뒤 새 키로 한 문항(54번)을 성공시킨 다음, 아래 네 전체 실행을 순서대로 완료했다. 실패한 실행은 출력 파일을 만들지 않았고 아래 결과에 포함하지 않는다.

```powershell
# 먼저 .env 또는 현재 PowerShell 환경에 RETRIEVAL_COMBO=d3-55,
# RETRIEVAL_CHUNKS_PATH, RETRIEVAL_EMBEDDING_PREFIX,
# RETRIEVAL_BM25_TOKENS_PATH와 필요한 API 키를 소유자가 설정한다.
# 아래 두 경로는 같은 v9 산출물이어야 한다.
$env:RETRIEVAL_CHUNKS_PATH = 'C:\Users\home\.codex\worktrees\graphrag\proj3-a\data\experiments\b100-v9-fixed_window\chunks.jsonl'
Test-Path $env:RETRIEVAL_CHUNKS_PATH
Test-Path 'C:\Users\home\Desktop\proj3-a\data\service\embeddings.vectors.npy'

$env:GRAPH_RETRIEVAL_ENABLED = 'false'
python -X utf8 scripts/run_graph_eval.py --graph multi --domain-check --node-timing --dataset evals/paraphrase_v2_plain.jsonl --supplementary --chunks $env:RETRIEVAL_CHUNKS_PATH --filters evals/filters_v2.graph.domain.jsonl --chunk-version v9 --output evals/results/graph50/graph150_paraphrase_baseline.jsonl

$env:GRAPH_RETRIEVAL_ENABLED = 'true'
python -X utf8 scripts/run_graph_eval.py --graph multi --domain-check --node-timing --dataset evals/paraphrase_v2_plain.jsonl --supplementary --chunks $env:RETRIEVAL_CHUNKS_PATH --filters evals/filters_v2.graph.domain.jsonl --chunk-version v9 --output evals/results/graph50/graph150_paraphrase_enabled.jsonl

$env:GRAPH_RETRIEVAL_ENABLED = 'false'
python -X utf8 scripts/run_graph_eval.py --graph multi --domain-check --node-timing --dataset evals/dataset_v2.jsonl --chunks $env:RETRIEVAL_CHUNKS_PATH --retrieval-subset evals/mappings/dataset_v2.retrieval23.jsonl --query-vectors evals/results/v2_query_vectors_oai_small.npz --filters evals/filters_v2.graph.domain.jsonl --chunk-version v9 --output evals/results/graph50/graph150_v2_baseline.jsonl

$env:GRAPH_RETRIEVAL_ENABLED = 'true'
python -X utf8 scripts/run_graph_eval.py --graph multi --domain-check --node-timing --dataset evals/dataset_v2.jsonl --chunks $env:RETRIEVAL_CHUNKS_PATH --retrieval-subset evals/mappings/dataset_v2.retrieval23.jsonl --query-vectors evals/results/v2_query_vectors_oai_small.npz --filters evals/filters_v2.graph.domain.jsonl --chunk-version v9 --output evals/results/graph50/graph150_v2_enabled.jsonl
```

모든 실행은 종료 코드 0으로 끝났고 결과 파일에 설정·커밋·해시·문항별 검색 후보가 남아 있다. 동결 해시는 실행 뒤에도 불변이다.

| 지표 | 그래프 끔 | 그래프 켬 | 변화 |
|---|---:|---:|---:|
| 일상어 23건 Hit@10 | 19/23 | 20/23 | +1 (60번 회수) |
| 일상어 MRR@10 / nDCG@10 | 0.672 / 0.476 | 0.710 / 0.531 | +0.038 / +0.055 |
| 일상어 정답 / 오답 인용 | 18 / 5 | 18 / 4 | 0 / -1 |
| 동결 v2 Hit@10 | 23/23 | 23/23 | 0 |
| 동결 v2 Recall@10 | 0.926 | 0.937 | +0.011 |
| 동결 v2 MRR@10 / nDCG@10 | 0.799 / 0.740 | 0.796 / 0.743 | -0.002 / +0.003 |
| 동결 v2 정답 / 오답 인용 | 24 / 0 | 23 / 1 | -1 / +1 |
| 동결 v2 올바른 거절 / 과잉 거절 / 환각 위험 | 24 / 0 / 0 | 24 / 0 / 0 | 0 / 0 / 0 |
| 알려진 비용 | $0.326 | $0.219 | -$0.108 |
| 검색 노드 p50 / p95 | 769ms / 1,984ms | 712ms / 2,765ms | -57ms / +781ms |

일상어의 기존 누락 54·58·60·64번 중 60번만 회수했다. 동결 v2에서 새 검색 누락은 없었고, 그래프 후보 133개를 원문 청크 메타데이터와 대조한 결과 회사·연도·장·주석 필터 위반은 0건이었다. 리랭커 입력은 최대 50개 이하였고, 그래프가 늘린 후보도 원문 청크만 가리킨다.

그러나 동결 v2 62번은 그래프 모드에서 정답 인용이 사라져 `chunk-8f7406ce4f58e2acb591e6b3`, `chunk-542c3335902f48229f3c0c76`만 인용했고, 응답 지표상 오답 인용 1건이 되었다. 이 실험은 검색 회수 기준과 필터 기준은 통과했지만, 응답 품질이 악화했다. 따라서 `GRAPH_RETRIEVAL_ENABLED=false` 기본값을 유지한다. 성과는 제한적 문서 그래프가 한 일상어 누락 근거를 회수한 실측으로만 기록하며, 서비스 기본 경로 개선이라고 주장하지 않는다.

## 원문 대조 결과 (2026-11-13)

DART 공시 뷰어(`dart.fss.or.kr`)의 원문 주석 페이지를 직접 받아, 결과 파일의 인용과 서비스 청크(`data/service/chunks.jsonl`)를 대조했다. 대상은 LG화학 사업보고서(접수번호 20260313001195)와 KG케미칼 사업보고서(20260323001613)이다. 모델이나 검색은 다시 실행하지 않았다.

**60번 (LG화학 연결 사채 잔액·1년 내 만기분): 회수 근거가 원문에 실제로 있다.**
- 원문 연결주석 14 「차입금」의 **당기** 사채 표(단위 백만원)에 `사채계_유동 5,222,013`, `사채계_비유동 15,002,881`이 있다. 합계 20,224,894(계산값)는 평가셋 expected와 같다.
- 그래프 켠 실행의 인용 `chunk-869d5c3a…`는 이 당기 표의 해당 행이다.
- 그래프 끈 기준선은 `chunk-a12de7e9…`를 인용해 `3,726,481` / `12,984,178`로 답했다. 이 값은 원문 **전기** 표의 `사채계` 행이다. 기준선의 오답은 기간을 잘못 잡은 것이고, 그래프가 당기 표를 회수해 바로잡았다.
- 판정: 회수는 정당하다.

**62번 (KG케미칼 연결 차입금+사채 합계): 그래프 켠 인용은 오답이다.**
- 원문 기준 정답은 주석 21 「차입금」 당기 합계 1,698,370,521과 주석 6 「범주별 금융상품」의 사채 129,487,727의 합 1,827,858,248(천원)이다.
- 그래프 켠 실행은 `chunk-8f7406ce…`(주석 4)를 인용해 1,890,444,828을 답했다. 이 값은 "차입금, 사채 및 리스부채"이다. 원문 주석 6에서 1,698,370,521 + 129,487,727 + 리스부채 62,586,580 = 1,890,444,828로 정확히 맞아, 리스부채가 포함된 다른 정의의 합계임을 확인했다. 다른 인용 `chunk-542c3335…`(주석 38 현금흐름표)는 질문과 관련 없다.
- 두 인용 모두 정답 청크(`chunk-2c4b3c55…`, `chunk-cd70eae0…`)가 아니므로, 오답 인용 1건이라는 위 판정이 맞다.
- 부수 발견: 그래프 끈 기준선은 정답 청크 `chunk-cd70eae0…`를 인용했지만, 사채를 58,033,808로 적었다. 이 값은 주석 38 현금흐름표의 사채 항목이고 원문 주석 6의 사채 잔액(129,487,727)과 다르다. 기준선도 합계를 맞추지 못했고 "확인 필요"로 답했다. 지표의 "정답 인용"은 청크 일치 여부만 본 것이므로, 기준선의 24/0이 숫자까지 맞았다는 뜻은 아니다.

**결론:** 60번 회수는 사실이고 62번 오답 인용도 사실이므로, 위 결론(`GRAPH_RETRIEVAL_ENABLED=false` 유지)과 일치한다. 성공 기준 두 가지를 모두 충족했다.

한계: 원문은 공시 뷰어 화면을 직접 읽었고 화면 캡처는 남기지 않았다. 사람이 위 접수번호의 해당 주석(LG화학 연결주석 14, KG케미칼 연결주석 4·6·21)을 열어 한 번 더 확인하면 증거가 완성된다.
