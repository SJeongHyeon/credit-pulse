"""검색 결과가 작성기에 닿기 전의 신뢰 경계(사전 등록 89번, SPEC §5.7). 신뢰 경계 시험 R4 대비.

두 겹을 검색기 바깥에 씌운다(그래프 조립에서 guarded_retriever로 감싸기만 한다).

1. 무결성: 청크 본문의 SHA-256이 적재 때 기록한 content_sha256과 다르면 그 청크를 버린다.
   코퍼스는 DART 원문 ZIP에서만 만들고 해시를 남기므로, 적재 뒤 파일·DB에 끼워 넣은 문장은 여기서 확정적으로 걸린다.
   content_sha256이 없는 청크(시험용 가짜 검색기)는 대조할 값이 없어 그대로 둔다.
2. 지시형 문장 제거: 독자에게 판단을 지시하는 문장('~로 판단하시기 바랍니다', '심사 시', '이전 지시를 무시',
   'AI에게')을 작성기 입력에서 지운다. 실제 100사 청크 31,967개에서 아래 패턴은 0건이다(11/7 확인,
   tests/test_trust_boundary.py가 v2 평가 청크 전체에서 0건임을 다시 확인). '참고하시기 바랍니다' 같은 상호 참조는
   건드리지 않는다.

3. (130번) 난독화 풀기와 새 패턴: 문장을 정규화(전각→반각, 제로폭·제어 문자 제거, 키릴·그리스 유사 문자 치환, 소문자)한
   것과 '공백·기호를 모두 뺀 것' 두 곳에서 찾는다. 글자 사이에 제로폭 문자나 공백을 끼운 지시('판 단 하 시 기')가 2번을
   지나가던 것을 막는다. 영문 지시, 가짜 역할 구분자, 입력 구조 탈출(따옴표·괄호), 그리고 우리 LLM 출력 계약의 칸 이름
   (missing_items·chunk_ids·verdict 등)을 쓴 문장을 더한다. 공시 원문에 이 칸 이름이 나올 이유가 없다.
   새 패턴도 100사 청크 31,967개(문장 451,147개)에서 정규화·공백 제거 양쪽 모두 0건이다(11/11 확인).
4. (130번) 채점기 출력 거르기: 검색 채점 LLM이 내놓는 '찾지 못한 항목'(missing_items)은 Supervisor LLM과 작성기
   입력으로 넘어간다(125·126번). 청크에 심은 문장이 채점기를 거쳐 이 목록으로 실려 가지 않게, 지시형·인젝션 신호가
   있는 항목을 버린다(safe_missing_items).

5. (141번) 표와 어긋나는 무효화 주장 빼기: 표가 아닌 문장이 같은 청크의 금액 표에 있는 항목을 가리키며 '보고기간 뒤에
   다 갚았다·없어졌다', '다 갚아 잔액이 없다', '상기 …은 전액 상환되었다'고 하면 그 문장을 뺀다(remove_contradicting_claims).
   보고기간 말 표의 금액을 뒤집는 주장은 그 표에 대한 답에 쓰지 않고, 청크에 표시를 남겨 사람이 원문에서 확인하게 한다.
   지시가 없는 사실 문체 위장(redteam R7)이 답에 '주석이 있음'으로 실리던 것을 막는다. 세 규칙 모두 100사 청크 31,967개에서
   0건이다(11/12 확인). 진짜 공시에 같은 모양의 문장이 생기면 그 문장도 빠진다(표시는 남는다)는 것이 이 규칙의 대가다.
6. (141번) 검색 단계 정제: 키워드 색인의 토큰과 리랭커에 넘기는 본문도 2·3·5번을 거친 글로 만든다(sanitized_for_index).
   공격 문장이 검색 점수를 바꿔 정답 청크를 밀어내는 것(redteam D3·H3)을 막는다. 벡터는 미리 계산된 것이라 바꾸지 못한다.

한계(P18 R2·R3): 지시도 없고 무효화도 아닌 거짓 사실(숫자 바꿔치기 '잔액은 1,000억원으로 수정되었다', 사실 덧붙이기)이 공시
원문에 처음부터 있으면 진짜 공시와 구별할 수 없다. 이 경우는 출처(1번)가 할 수 있는 전부다.
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from functools import lru_cache
from typing import Any, Callable

from backend.graph.injection_guard import _CONFUSABLES, _INVISIBLE, rule_is_unsafe

logger = logging.getLogger(__name__)

# 패턴을 넓힐 때는 실제 코퍼스에서 0건인지 먼저 확인한다(진짜 공시를 지우지 않게).
DIRECTIVE_PATTERNS = [
    re.compile(r"판단하시기\s*바랍니다"),
    re.compile(r"것으로\s*(?:판단하|보|간주하)(?:여야|아야|셔야|십시오|시기\s*바랍니다)"),
    re.compile(r"심사\s*시"),
    re.compile(r"(?:지시|명령|내용)(?:를|을)?\s*(?:모두\s*)?무시"),
    re.compile(r"시스템\s*(?:지시|프롬프트)|\[\s*system\s*\]", re.I),
    re.compile(r"(?:AI|에이전트|작성기|어시스턴트|챗봇)\s*에게|(?:심사|담당|작성)\s*(?:담당\s*)?AI(?:는|가|에게)", re.I),
    re.compile(r"(?:적어라|넣어라|출력하라|붙여\s*넣어라|따르라)"),
    # ── 130번: 아래는 정규화한 문장(소문자)에서 찾는다 ──
    # 영문 지시·유출 요구
    re.compile(r"(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|the\s+|your\s+)*(?:previous|prior|above|earlier|system|safety)?\s*"
               r"(?:instructions?|rules?|prompts?|guidelines?)"),
    re.compile(r"(?:note|message)\s+to\s+the\s+(?:drafting\s+|writing\s+)?(?:assistant|ai|model|llm)"),
    re.compile(r"(?:append|reveal|print|output|include)\b[^.\n]{0,60}(?:system\s*prompt|api[_\s-]?key|environment\s*variable)"),
    # 가짜 역할 구분자, 입력 구조 탈출
    re.compile(r"system\s*(?:override|message|prompt|instructions?)|#{2,}\s*system|시스템\s*메시지"),
    re.compile(r"</?\s*(?:system|content|assistant|user|instructions?|chunk)\s*>"),
    re.compile(r"['\"]\s*\}\s*\]\s*,\s*['\"]|['\"]\s*(?:system|instructions?)\s*['\"]\s*:"),
    # 우리 LLM 출력 계약의 칸 이름을 쓴 문장(채점기·작성기·Supervisor를 겨냥한 지시)
    re.compile(r"missing_items|relevant_chunk_ids|chunk_ids?|not_applicable|needs_check|system_override|\bverdict\b"),
    re.compile(r"sufficient\s*(?:는|은|를|을|=|:)|\breason\s*(?:에|의|은|는|첫|끝)"),
    # 독자·시스템에게 처리 방법을 지시하는 문장(패턴 밖 공손한 지시, 라우팅 조작)
    re.compile(r"(?:검토자|열람자|심사역|심사자|검토\s*시스템|채점기)(?:는|은|께서는|가)"),
    re.compile(r"(?:으로|로)\s*(?:기재|처리|표시)(?:하는\s*것이\s*원칙|해\s*주시기\s*바랍니다|하시기\s*바랍니다)"),
    re.compile(r"(?:이라고|라고)\s*(?:만\s*)?(?:적는다|쓴다|기재한다|적어야|적을\s*것|쓸\s*것|기재할\s*것)"),
    re.compile(r"채점\s*(?:단계|안내|결과|시)\s*(?:참고|안내|:)|검색\s*결과\s*채점"),
    re.compile(r"승인\s*단계(?:로|를)?\s*(?:즉시\s*)?(?:진행|직행)|초안\s*작성\s*(?:없이|을\s*생략)"),
]
# 공백·기호를 모두 뺀 문장에 맞춘 패턴(글자 사이를 띄운 난독화 대응. 질문 가드 injection_guard와 같은 방식)
_SQUASHED_PATTERNS = [
    re.compile(p.pattern.replace(r"\s*", "").replace(r"\s+", "").replace(r"\b", ""), re.I) for p in DIRECTIVE_PATTERNS
]
_PLAIN_PATTERNS = [re.compile(p.pattern, p.flags | re.I) for p in DIRECTIVE_PATTERNS]


def _normalize(text: str) -> str:
    """난독화를 풀어 비교하기 쉬운 문장으로 만든다. 소문자로 바꾼 뒤 유사 문자를 치환한다(대문자 키릴도 잡히게)."""

    text = unicodedata.normalize("NFKC", str(text)).translate(_INVISIBLE).lower().translate(_CONFUSABLES)
    text = "".join(ch if (ch in "\n\t" or unicodedata.category(ch) != "Cc") else " " for ch in text)
    return " ".join(text.split())


def is_directive(text: str) -> bool:
    """문장 하나가 지시형인가: 정규화한 문장 또는 공백·기호를 뺀 문장에서 패턴이 걸리면 그렇다."""

    normalized = _normalize(text)
    if any(pattern.search(normalized) for pattern in _PLAIN_PATTERNS):
        return True
    squashed = re.sub(r"[\W_]+", "", normalized)
    return any(pattern.search(squashed) for pattern in _SQUASHED_PATTERNS)
# 문장 경계: 줄바꿈, 또는 마침표·물음표·느낌표 뒤 공백. 표 행(|)은 줄 단위라 줄바꿈에서 나뉜다.
_SENTENCE_SPLIT = re.compile(r"(\n|(?<=[.!?])\s+)")


def content_intact(chunk: dict[str, Any]) -> bool:
    expected = chunk.get("content_sha256")
    if not expected:
        return True
    return hashlib.sha256(str(chunk.get("content", "")).encode("utf-8")).hexdigest() == expected


def remove_directive_sentences(text: str) -> tuple[str, int]:
    """지시형 패턴이 든 문장을 지운다. 반환: (정리한 본문, 지운 문장 수). 걸리는 것이 없으면 본문을 그대로 돌려준다."""

    if not is_directive(text):  # 본문 전체에서 먼저 본다(걸리는 것이 없으면 문장으로 나누지 않는다)
        return text, 0
    parts = _SENTENCE_SPLIT.split(text)
    kept, removed = [], 0
    for part in parts:
        if part and not _SENTENCE_SPLIT.fullmatch(part) and is_directive(part):
            removed += 1
            continue
        kept.append(part)
    if not removed:  # 문장 경계를 걸쳐서만 걸린 경우(공백 제거 비교): 지울 문장이 없으므로 그대로 둔다
        return text, 0
    return "".join(kept).strip(), removed


# ── 141번: 표와 어긋나는 무효화 주장 ──────────────────────────────────────────────────────────
_AMOUNT = re.compile(r"\d{1,3}(?:,\d{3})+")
_KOREAN_WORD = re.compile(r"[가-힣]{2,}")
_JOSA = re.compile(r"(?:은|는|이|가|을|를|의|에|도|과|와|로|으로)$")
_TABLE_STOPWORDS = frozenset({
    "합계", "구분", "당기", "전기", "당기말", "전기말", "금액", "기타", "단위", "백만원", "천원", "내역", "현재",
    "이상", "이하", "미만", "초과", "연결", "별도", "소계", "총계", "변동", "기초", "기말",
})
# 다 갚았다·없어졌다·효력이 없다
_NULLIFY = re.compile(r"(?:전액|전부|모두|일체)\s*(?:조기\s*)?(?:상환|변제|해지|취하|소멸|말소|종결)|(?:효력|의무)(?:이|을|가)?\s*(?:없|상실|소멸)")
_ZERO_BALANCE = re.compile(r"잔액(?:이|은|도)?\s*(?:없|0\s*원)")
# 보고기간 뒤의 일이라는 표시
_AFTER_PERIOD = re.compile(r"보고기간\s*(?:종료\s*)?(?:말\s*)?(?:이\s*)?후|보고서\s*제출일\s*현재|보고서\s*작성(?:기준)?일\s*(?:현재|이후)")
# 표를 가리키는 말로 시작하는 문장
_REFERS_TO_TABLE = re.compile(r"^\s*(?:※\s*)?(?:\(\*?\d*\)\s*)?(?:상기|위|동|해당|아래)\s")


def _has_amount_table(parts: list[str]) -> bool:
    return any(" | " in part and _AMOUNT.search(part) for part in parts)


def _topic_words(parts: list[str], skip: str) -> set[str]:
    """그 문장을 뺀 청크 나머지 글의 한글 낱말(표 항목·절 제목·다른 문장)."""

    words: set[str] = set()
    for part in parts:
        if part is not skip:
            words.update(word for word in _KOREAN_WORD.findall(part) if word not in _TABLE_STOPWORDS)
    return words


def _contradicts_table(sentence: str, parts: list[str]) -> bool:
    if " | " in sentence:  # 표 행은 건드리지 않는다
        return False
    nullify = bool(_NULLIFY.search(sentence))
    zero = bool(_ZERO_BALANCE.search(sentence))
    if not (nullify or zero):
        return False
    nouns = {_JOSA.sub("", word) for word in _KOREAN_WORD.findall(sentence)}
    nouns = {word for word in nouns if len(word) >= 2 and word not in _TABLE_STOPWORDS}
    topics = _topic_words(parts, sentence)
    if not any(topic in sentence or any(noun in topic for noun in nouns) for topic in topics):
        return False  # 이 청크가 다루는 것(표 항목·절 제목)을 말하는 문장이 아니다
    if _AFTER_PERIOD.search(sentence):
        return True  # ① 보고기간 뒤의 일이라며 무효화
    if nullify and zero:
        return True  # ② 다 갚아 잔액이 없다
    return nullify and bool(_REFERS_TO_TABLE.search(sentence))  # ③ '상기 …은 전액 상환…'


def remove_contradicting_claims(text: str) -> tuple[str, int]:
    """금액 표가 있는 청크에서 그 청크가 다루는 항목을 뒤집는 무효화 주장 문장을 지운다. 반환: (남은 본문, 지운 문장 수).

    처음에는 '금액 표의 항목 이름과 겹치는 문장'만 봤는데, 공격 문장이 정답 청크 여러 개에 심길 때 표 항목이 다른 청크
    (리스부채를 말하는 문장이 사용권자산 표 청크에 붙은 경우)에서 빠져나갔다(141번 v5 첫 측정 F4·F8). 그래서 겹침의 기준을
    그 청크의 나머지 글 전체로 넓혔다. 넓힌 조건도 100사 청크 31,967개에서 0건이다.
    """

    if not (_NULLIFY.search(text) or _ZERO_BALANCE.search(text)) or " | " not in text:
        return text, 0
    pieces = _SENTENCE_SPLIT.split(text)
    parts = [part for part in pieces if part and not _SENTENCE_SPLIT.fullmatch(part)]
    if not _has_amount_table(parts):
        return text, 0
    kept, removed = [], 0
    for part in pieces:
        if part and not _SENTENCE_SPLIT.fullmatch(part) and _contradicts_table(part, parts):
            removed += 1
            continue
        kept.append(part)
    return ("".join(kept).strip(), removed) if removed else (text, 0)


@lru_cache(maxsize=65536)
def sanitized_for_index(text: str) -> str:
    """141번: 키워드 색인·리랭커에 넘길 본문. 지시형 문장과 표를 뒤집는 주장을 뺀 글이다(본문별로 한 번만 계산).

    검색 뒤에만 지우면 공격 문장이 검색 점수를 바꿔 정답 청크를 밀어낼 수 있다. 실제 코퍼스는 걸리는 문장이 0건이라
    바뀌는 것이 없다.
    """

    cleaned, _ = remove_directive_sentences(text)
    cleaned, _ = remove_contradicting_claims(cleaned)
    return cleaned


def safe_missing_items(items: list[Any]) -> tuple[list[str], int]:
    """채점 LLM이 낸 '찾지 못한 항목'에서 지시형·인젝션 신호가 있는 것을 버린다. 반환: (남긴 항목, 버린 수).

    이 목록은 Supervisor LLM 입력과 작성기 입력으로 넘어간다. 청크에 심은 문장이 채점기를 거쳐 다른 LLM의 지시가
    되는 길을 막는다. 정상 항목은 '담보설정금액', '사채 잔액 합계' 같은 짧은 명사구라 걸리지 않는다.
    """

    kept, dropped = [], 0
    for item in items or []:
        text = " ".join(str(item).split())
        if not text:
            continue
        if is_directive(text) or rule_is_unsafe(text)[0]:
            dropped += 1
            continue
        kept.append(text)
    if dropped:
        logger.warning("채점 결과의 빠진 항목 %d개를 지시형으로 보고 버렸습니다", dropped)
    return kept, dropped


def guard_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """무결성이 깨진 청크는 버리고, 지시형 문장은 지운다. 바뀐 청크에는 trust_boundary 기록을 붙인다."""

    guarded = []
    for chunk in chunks:
        if not content_intact(chunk):
            logger.warning("청크 본문 해시 불일치로 제외: %s", chunk.get("chunk_id"))
            continue
        content, removed = remove_directive_sentences(str(chunk.get("content", "")))
        record: dict[str, int] = {}
        if removed:
            logger.warning("지시형 문장 %d개 제거: %s", removed, chunk.get("chunk_id"))
            record["removed_directive_sentences"] = removed
        content, contradicting = remove_contradicting_claims(content)
        if contradicting:
            # 141번: 표의 금액을 뒤집는 주장은 답에 쓰지 않는다. 표시를 남겨 사람이 원문에서 확인하게 한다.
            logger.warning("표와 어긋나는 무효화 주장 %d개 제외: %s", contradicting, chunk.get("chunk_id"))
            record["removed_contradicting_sentences"] = contradicting
        if record:
            chunk = {**chunk, "content": content, "trust_boundary": record}
        guarded.append(chunk)
    return guarded


def guarded_retriever(retriever: Callable[[Any], Any] | None) -> Callable[[Any], Any] | None:
    """검색기를 감싼다. 결과가 None(검색 안 함)이면 그대로 돌려준다."""

    if retriever is None:
        return None

    def run(state):
        chunks = retriever(state)
        return None if chunks is None else guard_chunks(list(chunks))

    return run
