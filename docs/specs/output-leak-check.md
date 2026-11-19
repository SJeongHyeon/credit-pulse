# 출력 유출 검사 Spec

## Why

- **페르소나**: 심사역(정상 사용자)과, 시스템 프롬프트·키를 응답으로 끌어내려는 사용자.
- **상황**: 질문 가드·규칙 가드·청크 신뢰 경계가 입력 쪽을 막지만, 새 표현이 뚫으면 모델이 `reason`에 시스템 지시문 조각이나 키 값을 적어 **사용자에게 그대로 나갈 수 있다**. 지금 유출 판정은 오프라인 스크립트(`scripts/run_redteam.py`의 `fail_markers`)가 사후에 할 뿐, 실제 응답 경로에는 마지막 문이 없다.
- **측정 지표**: 유출 시험 케이스 차단율, 정상 답변 오차단 수(0).

## Goal

- 키·접속 문자열 유형 6종과 시스템 프롬프트 지문이 든 초안 항목 **시험 10건 이상이 100% 가려진다**.
- 동결 평가셋 v1·v2의 정답 문장(`expected`) 전체에서 **오탐 0건**.
- LLM 호출 0회, 정상 응답은 바이트 단위로 이전과 같다.
- **Out of Scope**: 프롬프트 문구 변경, 응답 외 경로(로그·트레이스)의 비밀 마스킹(기존 규칙), 의미만 같은 바꿔 말하기 유출 탐지.

## What

### Happy Path
- 정상 초안은 그대로 통과한다. State·응답에 새 키가 생기지 않는다.

### 차단
1. 초안 항목의 `reason`·`item`·`evidence.quote`에 **비밀 패턴**(OpenRouter/OpenAI 형식 키, AWS 키, `Bearer` 토큰, 비밀번호가 든 DB 접속 URL, `api_key=값` 형태, 환경변수 이름 `OPENROUTER_API_KEY` 등)이나 **프롬프트 지문**(우리 시스템 지시문의 고유 문장)이 있으면 그 항목만 `needs_check`로 낮추고 `reason`을 고정 문구로 바꾸며 인용·근거를 비운다.
2. 검증 결과에 `failed_checks=["output_leak_suspected"]` 항목을 남긴다(기존 검증 기록과 같은 모양).

### Edge Case
- 한 항목이 걸려도 다른 항목은 그대로 둔다.
- 걸린 문자열 본문은 로그·검증 기록에 **남기지 않는다**(유형 이름만). 로그에 남기면 유출이 로그로 옮겨질 뿐이다.
- 프롬프트 지문은 `backend/services/llm.py`의 실제 문장에서 온다. 시험이 지문마다 llm.py 안에 존재함을 확인해, 지시문이 바뀌면 시험이 실패한다(지문이 낡아 조용히 무력화되는 것을 막는다).
- 공시 본문에 우연히 지문과 같은 문장이 있을 가능성은 평가셋·코퍼스 샘플로 0건임을 시험한다.

## How

- `backend/graph/output_guard.py`: `find_leaks(text) -> tuple[str, ...]`(유형 이름), `LEAK_REASON` 상수.
- `backend/graph/verifier.py`: `verify_draft_items`가 항목마다 검사하고, 걸리면 기존 하향 경로와 같은 모양(`needs_check`, 인용 비움)으로 낮춘다.
- 검증 기록 항목: `{"item", "chunk_id": "", "passed": False, "failed_checks": ["output_leak_suspected"]}`.
- SPEC §5.7에 한 줄 추가. State·API 계약 변경 없음.

## AC

- **AC-O1** GIVEN 비밀 패턴 6유형이 든 reason WHEN 검증 THEN 해당 항목이 `needs_check`·고정 reason·인용 0.
- **AC-O2** GIVEN 시스템 프롬프트 지문이 든 reason·quote WHEN 검증 THEN 같다.
- **AC-O3** GIVEN 항목 2개(유출 1, 정상 1) WHEN 검증 THEN 정상 항목은 이전 결과와 같다.
- **AC-O4** GIVEN 정상 초안 WHEN 검증 THEN 출력이 이전과 완전히 같다(검증 기록 포함).
- **AC-O5** GIVEN 평가셋 `expected` 전체 WHEN 검사 THEN 0건.
- **AC-O6** GIVEN 프롬프트 지문 목록 WHEN llm.py 소스와 대조 THEN 모든 지문이 소스에 존재한다.
- **AC-O7** GIVEN 유출 차단 WHEN 로그·검증 기록 THEN 걸린 문자열이 없다.
