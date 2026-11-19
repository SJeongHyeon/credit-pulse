# 서정현의 담당 범위와 기여

이 저장소는 팀 프로젝트의 공개용 사본이며 커밋 이력을 포함하지 않습니다. 아래 수치는 비공개 원본 저장소(`aihuman-7th/proj3-a`, 기준 커밋 `372ac28`, 2026-11-18)에서 2026-11-19에 센 값입니다. 외부에서 검증할 수 없으므로 면접에서 원본 화면으로 보여 드릴 수 있습니다.

## 1. 내가 만든 것

- 담당 축: LangGraph 에이전트(Supervisor, 검색·작성 서브에이전트, 승인 interrupt와 재개)와 그 평가.
- 원본 `main` 비병합 커밋 764건 중 203건입니다. author 이름이 둘이라 합산한 값입니다(`SJH` 189건, `SJeongHyeon` 14건). 커밋 수는 기여의 크기를 뜻하지 않습니다.
- Pull Request 65건(병합 62건, 닫음 3건). 원본이 비공개라 PR 링크는 걸지 않습니다.
- 상세 기록: [`docs/deliverables/portfolio-서정현.md`](docs/deliverables/portfolio-서정현.md), AI 도구 사용 기록 [`docs/deliverables/ai_tools_log.md`](docs/deliverables/ai_tools_log.md)

### 대표 PR과 코드 위치

PR 번호는 비공개 원본의 번호이며, 각 PR이 바꾼 파일을 원본에서 확인한 행만 적었습니다. 코드 위치는 이 사본의 경로입니다.

| PR | 내용 | 코드 위치 |
| --- | --- | --- |
| #35 | 그래프 라우팅 구성 | `backend/graph/build.py`, `state.py` |
| #71 | 단일 에이전트 v1 | `backend/graph/build.py`, `refusal.py`, `backend/services/llm.py` |
| #58 | 비관련 질문 거절 응답 | `backend/graph/refusal.py` |
| #79 | 보고서에 없는 반기보고서 요청 거절 | `backend/graph/refusal.py` |
| #80 | 인용 검증기 | `backend/graph/verifier.py`, `backend/graph/agents/writer_agent.py` |
| #89 | 승인 체크포인트와 재개 | `backend/graph/build.py` |
| #98 | 사용자별 심사 규칙 저장 | `backend/memory.py` |
| #114 | Store 기반 시맨틱 메모리 | `backend/memory.py`, `backend/graph/approval.py` |
| #115 | 심사 건 질의·승인 API | `backend/api/reviews.py`, `backend/main.py` |
| #149 | Gmail 인증코드 회원가입 | `backend/auth.py`, `backend/services/email_sender.py` |
| #54 | 리랭커 후보 수·배치 측정 | `backend/rag/rerank.py`, `evals/results/rerank_ko_*_latency_v1.json` |
| #169 | 프롬프트 인젝션 3겹 방어 | `backend/graph/injection_guard.py`, `output_guard.py` |
| #203 | 거절 시 참고 뉴스의 회사명 사후 필터 | `backend/services/news_search.py` |
| #235 | 문서 구조 그래프 검색(기본 꺼짐) | `backend/rag/document_graph.py`, `backend/retrieval_combo.py` |
| #239 | 목차 어휘 질의 번역(미채택) | `backend/rag/term_bridge.py` |

## 2. 팀 성과(내 단독 성과가 아님)

- 평가 수치, 테스트 수, 배포, 문제·시장 근거 문서는 팀의 결과물입니다.
- 평가셋 정답 판정과 결과 검토는 다른 팀원이 맡은 부분이 있습니다.

## 3. 측정 전 등록을 믿는 방법

원본에서 [`evals/PREREGISTER.md`](evals/PREREGISTER.md)가 구성 변경보다 먼저 커밋됐다는 사실은 원본의 `git log`로 확인됩니다. 이 사본은 이력이 없으므로 아래 값으로 같은 평가셋과 코드임을 대조하세요.

- 원본 기준 커밋: `372ac28f46577fadf88258cedd7bd4668282343c`
- 평가셋 v2 sha256(`evals/FROZEN.json`): `86b800542f112f5cbe4d1952826bfb685484d23255252e760a5c8a54989cf299`
