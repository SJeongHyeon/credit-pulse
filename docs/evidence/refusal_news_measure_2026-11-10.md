# 거절 질문 뉴스 분기 측정 절차 (사전 등록 118번)


> **2026-11-11 고침**: main(104~117번 그래프)을 합치면서 사전 등록 번호를 103 → 118로 옮겼고(main의 103번과 겹침), 기준선을 병합한 main 커밋 `8b73517`로, 명령을 지금 러너에 맞게(`--redraft` 옵션은 없어짐) 바꿨다. 기준선과 새 그래프는 같은 시각에 짝지어 돌린다(외부 API 지연이 시간대마다 달라 따로 돌리면 응답 시간을 비교할 수 없다). 아직 측정하지 않았다.
환경(풀 코퍼스 데이터·OpenRouter·Voyage 키)이 있는 PC에서 이 순서대로 실행한다. 기대치는 `evals/PREREGISTER.md` 118번에 이미 적혀 있고, **측정 뒤에도 기대 칸은 고치지 않는다**(맞음/틀림 칸만 채운다). 코드는 측정 중에 바꾸지 않는다.

## 무엇을 재나

동결 v2 50건(`evals/dataset_v2.jsonl`)을 채택 구성(멀티 그래프·주제 판정 켬·redraft 켬·d3-55·gpt-4o-mini)으로 세 번 돈다. 뉴스 클라이언트는 호출 수만 세는 가짜라 **네이버 키는 필요 없다**. 실제 LLM을 부르므로 OpenRouter·Voyage 비용이 든다(회차당 약 $0.10).

| 회차 | 코드 | 옵션 | 판정하는 기대 |
|---|---|---|---|
| R0 기준선 | `8b73517`(worktree) | 없음 | (a) LLM 흔들림 폭 |
| R1 꺼짐 | 이 PR의 head | 없음 | (a) 꺼짐이면 기존과 같음 |
| R2 켜짐 | 이 PR의 head | `--news-search` | (b)(c)(d) 뉴스 호출·과잉 거절 |

## 준비

1. 저장소 루트에서 `pip install -r backend/requirements.txt` (`voyageai`, `kiwipiepy` 포함).
2. `.env`(git에 올라가지 않음)에 다음을 둔다. 키 값은 직접 입력하고 어디에도 붙여 넣지 않는다.
   ```
   OPENROUTER_API_KEY=
   VOYAGE_API_KEY=
   DEMO_MODE=0
   RETRIEVAL_COMBO=d3-55
   VECTOR_BACKEND=memory
   RETRIEVAL_CHUNKS_PATH=data/experiments/b100-v9-fixed_window/chunks.jsonl
   RETRIEVAL_EMBEDDING_PREFIX=data/embeddings/v9/oai-small-fixed1000-o100
   ```
3. 풀 코퍼스 파일이 있는지 확인한다(`data/README.md` 143행: GitHub에 올리지 않는 파일). corpus_id가 `corpus-ffc1830e50a11c57749b63b9`(v2 문항의 `corpus_version`)와 같아야 하고, 다르면 러너가 시작 직후 `GraphEvalError`로 멈춘다. 데모 스냅샷(`data/snapshot/`, 16사, 다른 corpus_id)으로는 이 측정을 하지 않는다.
4. `git log --oneline -1`이 이 PR의 head 커밋인지, `git status -s`에 코드 변경이 없는지 확인한다.

## 실행 (PowerShell, 저장소 루트)

```powershell
$common = @("--graph","multi","--domain-check","--node-timing",
  "--dataset","evals/dataset_v2.jsonl","--chunks","data/experiments/b100-v9-fixed_window/chunks.jsonl",
  "--retrieval-subset","evals/mappings/dataset_v2.retrieval23.jsonl",
  "--query-vectors","evals/results/v2_query_vectors_oai_small.npz",
  "--filters","evals/filters_v2.graph.domain.jsonl","--chunk-version","v9")

# 시험(5문항, 끝나면 w118_try* 삭제)
python -X utf8 scripts/run_graph_eval.py @common --news-search --limit 5 --output evals/results/graph50/w118_try.jsonl

# R2 켜짐 → R1 꺼짐
python -X utf8 scripts/run_graph_eval.py @common --news-search --output evals/results/graph50/w118_on_multi_d3-55_v9_v2.jsonl
python -X utf8 scripts/run_graph_eval.py @common --output evals/results/graph50/w118_off_multi_d3-55_v9_v2.jsonl

# R0 기준선: 8b73517 worktree에서 같은 명령(출력은 이 저장소의 절대경로로)
git worktree add ..\proj3-a-base 8b73517
New-Item -ItemType Junction -Path ..\proj3-a-base\data -Target (Resolve-Path data)
Copy-Item .env ..\proj3-a-base\.env
Push-Location ..\proj3-a-base
python -X utf8 scripts\run_graph_eval.py @common --output "<이 저장소 절대경로>\evals\results\graph50\w118_base_multi_d3-55_v9_v2.jsonl"
Pop-Location
git worktree remove ..\proj3-a-base
```

R0의 `summary.json` `git_commit`이 `8b73517…`로 기록됐는지 확인한다.

## 끝난 직후 점검

R2 `summary.json`의 `news_search` 블록이 다음을 만족하면 정상이다. 어긋나도 중단하지 말고 그대로 기록한다.

- `client_calls == news_calls_total`, `max_news_calls_per_question <= 1`, `max_news_item_count <= 5`
- `relevant_with_news_ids == []` (관련 질문 뉴스 호출 0)
- `news_called_ids`의 개수 12~16 (사전 등록 (c))

## 기록 (측정한 사람이 한다)

1. `evals/PREREGISTER.md` 118번 행의 "측정 뒤 맞음/틀림" 칸(지금 `(측정 전)`)에 (a)~(d)를 항목별로 맞음/틀림과 실제 숫자로 적는다. "지켜볼 회귀 위험"의 17번(회사명이 든 취미 질문) 분류 결과도 문항 번호로 적는다. 기대와 다르면 다르다고 쓴다.
2. `EVAL_REPORT.md`에 "뉴스 검색 분기" 절을 더한다(결과 파일 이름, 켜짐-꺼짐-기준선 지표 표, 달라진 문항 목록). 점수가 오르지 않았으면 오르지 않았다고 쓴다.
3. 결과 파일 6개(`w118_on|off|base_*.jsonl`, `*.summary.json`)와 위 두 문서를 커밋한다. 메시지: `eval: 뉴스 검색 분기 측정 결과 기록`.

## 주의

- 동결 파일(`evals/FROZEN.json`의 v1·v2)은 수정하지 않는다.
- `.env`, 키, 터미널에 키가 찍힌 부분을 커밋·로그·메신저에 남기지 않는다.
- 러너는 supervisor 추출이 필터 파일과 다르면 중단한다(비용이 이미 나간 뒤일 수 있다). 먼저 `--limit 5`로 확인한다.
- 켜짐 실행에서는 회사명이 없는 질문(31·32번)에도 `classify_domain`을 먼저 부르므로, 이 문항의 결과 변화(과잉 거절 여부)를 (d)에서 따로 본다.
- 이 측정 전까지 이 기능은 "완료"가 아니다. 서비스(`backend/api/runtime.py`)에는 켜지 않았다(플래그 기본 꺼짐).
