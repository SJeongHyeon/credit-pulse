"""사용자 질문 속 프롬프트 인젝션 방어, 특히 멀티턴(점진적 수위 상승) 공격 대비.
Spec: docs/specs/multi-turn-injection-defense.md

왜 코드(규칙+누적 점수)인가
  - LLM 판정기는 비싸고 흔들리며, 공격 대상인 LLM에게 공격 여부를 묻는 꼴이 된다. 이 가드는 LLM을 부르지 않는다.
  - 멀티턴 공격은 턴마다 임계값 아래로 쪼개 넣는다. 그래서 한 턴 점수가 아니라 대화 누적 위험도를 본다.

동작(질문 한 건마다)
  1. 정규화: 전각→반각(NFKC), 제로폭·제어 문자 제거, 키릴·그리스 유사 문자 치환, 소문자. 난독화 우회를 줄인다.
  2. 신호 스캔: 정규화 문장과 '공백·기호를 모두 뺀 문장' 두 곳에서 구문 단위로 찾는다(낱말 단독은 보지 않는다).
  3. 누적: risk = 이전 risk x 0.7 + 이번 점수. 깨끗한 턴이 이어지면 회복한다.
  4. 차단: risk >= 60 이면서 이번 턴에 신호가 있을 때. 직전에 막힌 질문을 표현만 바꿔 다시 보내면 +50.

상태(injection_risk)는 AgentState에 실려 같은 thread의 checkpointer로 턴을 넘어 유지된다.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

# ── 점수표(Spec '신호와 점수'의 정본) ─────────────────────────────────────────────────────────
HARD, MEDIUM, SOFT = 100, 40, 12
RETRY_AFTER_BLOCK = 50
DECAY = 0.7  # 턴마다 이전 위험도에 곱한다
BLOCK_RISK = 60
WATCH_RISK = 20
RETRY_OVERLAP = 0.5  # 직전 차단 질문과 겹침 비율(글자 2-gram)
HISTORY_TURNS = 5
LAST_BLOCKED_CHARS = 200

# ── 1. 정규화 ──────────────────────────────────────────────────────────────────────────────
_INVISIBLE = dict.fromkeys(
    map(ord, "​‌‍‎‏⁠⁡⁢⁣﻿­͏‪‫‬‭‮⁦⁧⁨⁩"),
    None,
)
# 한글 질문에 섞일 일이 거의 없는 유사 문자만 치환한다(키릴 а→a 등).
_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "і": "i", "ѕ": "s", "ј": "j", "ԁ": "d", "ӏ": "l",
    "ο": "o", "ν": "v", "ρ": "p", "α": "a", "ε": "e", "ι": "i", "κ": "k", "τ": "t", "υ": "u",
})


def normalize_text(text: str) -> str:
    """난독화를 풀어 비교하기 쉬운 문장으로 만든다(소문자, 공백 하나로)."""

    # 130번: 소문자로 바꾼 뒤 유사 문자를 한 번 더 치환한다(대문자 키릴·그리스 문자는 소문자가 된 뒤에야 표에 걸린다)
    text = unicodedata.normalize("NFKC", str(text)).translate(_INVISIBLE).translate(_CONFUSABLES).lower().translate(_CONFUSABLES)
    text = "".join(ch if (ch in "\n\t" or unicodedata.category(ch) != "Cc") else " " for ch in text)
    return " ".join(text.split())


def squash_text(normalized: str) -> str:
    """글자 사이 공백·기호('무 시', 'i-g-n-o-r-e')를 모두 지운 문장."""

    return re.sub(r"[\W_]+", "", normalized)


# ── 2. 신호 사전 ───────────────────────────────────────────────────────────────────────────
# 패턴은 '구문'이다. 도메인 낱말("지시", "규칙", "무시")이 단독으로 걸리지 않게 짝을 맞춘다.
# 넓힐 때는 tests/test_injection_guard.py의 평가셋 오탐 시험이 0건인지 먼저 본다.
_LEAK_TARGET = (
    r"(?:시스템\s*(?:프롬프트|지시|메시지)|system\s*(?:prompt|message|instructions?)|내부\s*(?:지시|지침|프롬프트|설정)|"
    r"초기\s*(?:지시|지침|프롬프트|설정)|숨겨진\s*(?:지시|지침|프롬프트)|"
    r"너(?:에게|한테)\s*주어진\s*(?:지시|지침|규칙|프롬프트)|developer\s*message)"
)
_LEAK_VERB = r"(?:출력|알려|보여|공개|말해|전문|그대로|복사|반복|번역|요약해|유출|print|show|reveal|repeat|leak|dump|tell)"
_SECRET = (
    r"(?:api\s*키|api\s*key|apikey|환경\s*변수|비밀\s*번호|비밀\s*키|시크릿|secret|password|"
    r"액세스\s*토큰|access\s*token|인증\s*토큰|\.env|openrouter_api_key)"
)
_SECRET_VERB = r"(?:출력|알려|보여|공개|말해|붙여|넣어|유출|print|show|reveal|leak|dump)"

_VERDICT = r"(?:not_applicable|applicable|needs_check|해당\s*없음|특이\s*사항\s*없음|문제\s*없음)"

# (이름, 등급 점수, 패턴들, 공백 제거 문장에서도 찾을지)
SIGNALS: list[tuple[str, int, list[str], bool]] = [
    # ── hard: 한 번에 차단 ──
    ("override_instructions", HARD, [
        r"(?:이전|위|앞|기존|지금까지|모든|상기)\s*(?:의\s*)?(?:모든\s*)?(?:지시|지침|명령|규칙|프롬프트|설정|제약|제한)\w*[^.\n]{0,12}?"
        r"(?:무시|잊어|잊고|따르지\s*마|취소|해제|버려|벗어나)",
        r"(?:지시|명령|지침|규칙)(?:를|을|은|는)?\s*(?:모두\s*)?(?:무시|잊어|잊고)",
        r"(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|the\s+|your\s+)*(?:previous|prior|above|earlier|system|safety)?\s*"
        r"(?:instructions?|rules?|prompts?|guidelines?|constraints?)",
    ], True),
    ("prompt_leak", HARD, [
        rf"{_LEAK_TARGET}[^.\n]{{0,25}}{_LEAK_VERB}",
        rf"{_LEAK_VERB}[^.\n]{{0,25}}{_LEAK_TARGET}",
    ], True),
    ("secret_request", HARD, [
        rf"{_SECRET}[^.\n]{{0,20}}{_SECRET_VERB}",
        rf"{_SECRET_VERB}[^.\n]{{0,20}}{_SECRET}",
    ], True),
    ("verdict_force", HARD, [
        rf"(?:모든|전\s*항목|전부|각)\s*(?:항목|건|판정|verdict)[^.\n]{{0,12}}{_VERDICT}[^.\n]{{0,6}}(?:으로|로)?\s*"
        r"(?:하|적|표시|판정|처리|고정|출력)",
        r"근거(?:와|에)?\s*(?:상관|관계)\s*없이|근거\s*없이\s*(?:도\s*)?(?:판정|판단|답|결론)",
        r"verdict\s*(?:를|을|는|은)?\s*(?:not_applicable|applicable|needs_check)\s*(?:으로|로)",
    ], True),
    ("citation_forge", HARD, [
        r"chunk[_\s-]?id[^.\n]{0,30}(?:넣어|추가|조작|위조|바꿔|임의|만들어|끼워)",
        r"(?:넣어|추가|조작|위조|바꿔|임의|만들어|끼워)[^.\n]{0,30}chunk[_\s-]?id",
        r"(?:가짜|허위|위조)\s*(?:인용|출처|chunk|근거)",
    ], True),
    # ── medium: 단독은 통과, 쌓이면 차단 ──
    ("role_hijack", MEDIUM, [
        r"(?:너는|넌|당신은)\s*이제", r"(?:지금부터|이제부터)\s*(?:너|넌|당신)",
        r"you\s+are\s+now", r"act\s+as\s+(?:a|an|if)", r"pretend\s+(?:to\s+be|you)",
        r"do\s+anything\s+now|dan\s+mode",
        r"개발자\s*모드|developer\s*mode|god\s*mode|탈옥|jail\s*break",
        r"역할(?:을|를)?\s*(?:바꿔|변경|전환)",
    ], True),
    ("policy_bypass", MEDIUM, [
        r"(?:제한|제약|필터|검열|가드레일)\s*(?:을|를)?\s*(?:해제|풀어|끄고|무력화|우회|없애)",
        r"(?:제한|제약|필터|검열)\s*없이\s*(?:답|대답|응답|말|출력|알려|작성)",
        r"(?:거절|거부)\s*(?:하지\s*마|하지\s*말|금지)|거절\s*하면\s*안",
        r"without\s+(?:any\s+)?(?:restrictions?|filters?|limits?|refus)", r"bypass",
        r"(?:규칙|제한|정책)(?:을|를)?\s*(?:어기|깨|벗어나)",
    ], True),
    ("authority_claim", MEDIUM, [
        r"(?:나는|저는|난|제가|내가)\s*(?:이\s*시스템의?\s*)?(?:관리자|개발자|운영자|시스템\s*관리|감독관|보안\s*담당|admin|developer)",
        r"(?:관리자|개발팀|운영팀|보안팀|본사)\s*(?:의|로부터)?\s*(?:승인|허가|허락|권한|지시)(?:을|를)?\s*(?:받|있)",
        r"(?:테스트|디버그|디버깅|유지보수|관리자|개발)\s*모드",
    ], True),
    ("encoded_exec", MEDIUM, [
        r"base64|rot13|(?:디코딩|decode)\s*(?:해서|하여|한\s*뒤)[^.\n]{0,10}(?:실행|따라|수행)",
    ], True),
    ("long_encoded_blob", MEDIUM, [r"[a-z0-9+/=]{48,}"], False),
    # ── soft: 하나는 무해, 여러 턴 쌓이면 징후 ──
    ("hypothetical", SOFT, [
        r"(?:가정|가상)(?:해서|하고|의|이라고|으로|해\s*보)", r"만약\s*(?:에)?\s*(?:규칙|제한|제약)", r"hypothetical|what\s+if\s+you",
    ], False),
    ("roleplay", SOFT, [r"역할\s*극|롤\s*플레이|role\s*-?\s*play|시뮬레이션\s*(?:해|하고|모드)|연기\s*해"], False),
    ("purpose_claim", SOFT, [
        r"(?:연구|교육|학습|테스트|시험|보안\s*점검|레드\s*팀|red\s*team)\s*(?:목적|용도|차원)",
    ], False),
    ("exception_plea", SOFT, [
        r"(?:한\s*번만|이번만|딱\s*한\s*번)[^.\n]{0,10}(?:예외|봐\s*줘|풀어|허용|해\s*줘)",
        r"예외(?:로|적으로)\s*(?:해|허용|답|처리|인정)",
    ], False),
    ("rule_probing", SOFT, [
        r"(?:거절|거부)\s*(?:기준|규칙|조건|이유|하는\s*이유|되는\s*경우)",
        r"(?:너|네|당신|시스템|에이전트)(?:의|가)?\s*(?:내부\s*)?(?:규칙|제한|정책|설정|한계|제약|가이드라인)(?:이|은|는|을|를)?\s*"
        r"(?:뭐|무엇|어떻게|알려|말해|설명)",
        r"(?:너|시스템|에이전트|챗봇)[^.\n]{0,8}(?:어떻게|어떤\s*원리로|무슨\s*규칙으로)\s*(?:동작|작동|판단|거절|답)",
        r"어디까지\s*(?:답변|대답|가능|허용)",
    ], False),
    ("workaround", SOFT, [
        r"우회(?!\s*상장)", r"돌려서|돌려\s*말해|에둘러", r"(?:다른\s*방식으로|간접적으로)\s*(?:알려|말해|표현)",
        r"(?:방금|아까|앞에서)\s*(?:거절|못\s*한다고)",
    ], False),
]


def _squashed_source(pattern: str) -> str:
    """패턴에서 공백 요소를 빼 '공백 제거 문장'에 맞춘다(글자 사이 공백으로 쪼갠 난독화 대응)."""

    return pattern.replace(r"\s*", "").replace(r"\s+", "").replace(r"\b", "")


def _compile(signals):
    return [
        (
            name, score,
            [re.compile(p) for p in patterns],
            [re.compile(_squashed_source(p)) for p in patterns] if squash else [],
        )
        for name, score, patterns, squash in signals
    ]


# 규칙(applied_rules) 본문에서만 위험한 '출력 조작' 지침. 질문에는 쓰이지 않아 질문 사전과 분리한다.
# (Spec docs/specs/rule-injection-guard.md)
RULE_SIGNALS: list[tuple[str, int, list[str], bool]] = [
    ("rule_output_hijack", HARD, [
        r"chunk[_\s-]?ids?[^.\n]{0,12}(?:비우|비워|생략|넣지|없이)",
        r"reason(?:에는|에|은|는)?[^.\n]{0,25}(?:만|으로만)\s*(?:적|쓰|기재|출력)",
        r"(?:항상|무조건|반드시|모든\s*(?:항목|질문))[^.\n]{0,20}(?:not_applicable|applicable|needs_check|승인|통과|특이\s*사항\s*없음)",
        r"(?:인용|근거|검증|verification)(?:을|를|은|는)?\s*(?:생략|건너[뛰뛴뜀]|무시|하지\s*않)",
    ], True),
]

_COMPILED = _compile(SIGNALS)
_RULE_COMPILED = _compile(RULE_SIGNALS)


# ── 3. 한 턴 스캔 ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TurnScan:
    score: int
    signals: tuple[str, ...]
    normalized: str
    squashed: str


def _scan(text: str, compiled) -> TurnScan:
    normalized = normalize_text(text)
    squashed = squash_text(normalized)
    found, total = [], 0
    for name, score, plain, squash in compiled:
        if any(p.search(normalized) for p in plain) or any(p.search(squashed) for p in squash):
            found.append(name)
            total += score
    return TurnScan(score=min(total, HARD), signals=tuple(found), normalized=normalized, squashed=squashed)


def scan_turn(question: str) -> TurnScan:
    """질문 한 건에서 신호를 찾는다. 서로 다른 신호의 점수를 더한다(상한 100)."""

    return _scan(question, _COMPILED)


def scan_rule(text: str) -> TurnScan:
    """저장 규칙 본문을 스캔한다: 질문 신호 + 규칙 전용 출력 조작 신호."""

    return _scan(text, _COMPILED + _RULE_COMPILED)


def rule_is_unsafe(text: str) -> tuple[bool, tuple[str, ...]]:
    """규칙 본문이 악성인가(점수가 차단 임계값 이상). 반환: (악성 여부, 걸린 신호 이름)."""

    scan = scan_rule(text)
    return scan.score >= BLOCK_RISK, scan.signals


# ── 4. 대화 누적 평가 ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Assessment:
    blocked: bool
    level: str  # clear | watch | blocked
    turn_score: int
    risk: float
    signals: tuple[str, ...]
    state: dict[str, Any] | None  # None이면 State에 쓰지 않는다(깨끗한 첫 질문)


def _bigrams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _overlap(a: str, b: str) -> float:
    """겹침 비율 = |A∩B| / min(|A|,|B|). 공격 문장에 말을 덧붙여 다시 보내도 잡는다."""

    grams_a, grams_b = _bigrams(a), _bigrams(b)
    if not grams_a or not grams_b:
        return 0.0
    return len(grams_a & grams_b) / min(len(grams_a), len(grams_b))


def assess(question: str, previous: dict[str, Any] | None = None) -> Assessment:
    """이번 질문을 이전 대화 상태와 합쳐 평가한다. previous는 이전 턴의 Assessment.state."""

    previous = previous or {}
    scan = scan_turn(question)
    signals = list(scan.signals)
    turn_score = scan.score

    last_blocked = str(previous.get("last_blocked") or "")
    if last_blocked and _overlap(scan.squashed, last_blocked) >= RETRY_OVERLAP:
        signals.append("retry_after_block")
        turn_score = min(turn_score + RETRY_AFTER_BLOCK, HARD + RETRY_AFTER_BLOCK)

    risk = round(float(previous.get("risk", 0.0)) * DECAY + turn_score, 1)
    blocked = bool(signals) and risk >= BLOCK_RISK
    level = "blocked" if blocked else ("watch" if signals or risk >= WATCH_RISK else "clear")

    if not previous and not signals:
        state = None  # 깨끗한 첫 질문: 기존 State와 똑같이 둔다
    else:
        state = {
            "risk": risk,
            "scores": [*list(previous.get("scores") or [])[-(HISTORY_TURNS - 1):], turn_score],
            "blocked_turns": int(previous.get("blocked_turns", 0)) + (1 if blocked else 0),
            "last_blocked": scan.squashed[:LAST_BLOCKED_CHARS] if blocked else last_blocked,
        }
    return Assessment(blocked=blocked, level=level, turn_score=turn_score, risk=risk, signals=tuple(signals), state=state)
