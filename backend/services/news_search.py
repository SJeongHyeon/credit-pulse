"""거절 질문에 붙이는 네이버 뉴스 검색 클라이언트 (Spec docs/specs/refusal-news-search.md).

뉴스는 참고용이다. 공시 근거(citation)가 아니며 판정·승인·메모 저장에 쓰지 않는다.
이 파일의 규칙:
  - HTTP 호출은 이 파일에서만 한다. 질문당 1회, 타임아웃 5초, 재시도 없음.
  - 키는 환경변수에서만 읽는다. NAVER API HUB 키(NAVER_HUB_CLIENT_ID, NAVER_HUB_CLIENT_SECRET)가 있으면 HUB로,
    없으면 기존 개발자센터 키(NAVER_CLIENT_ID, NAVER_CLIENT_SECRET)로 부른다(개발자센터는 신규 발급이 막혀 HUB로 이관 중). 값을 로그·오류 메시지에 적지 않는다.
  - 가져온 텍스트는 LLM 프롬프트에 넣지 않는다(요약 없음). 태그 제거와 html.unescape만 하고 그대로 데이터로 돌려준다.
    지시형 문장도 지우지 않는다(AC-N8): 어디에서도 지시로 쓰이지 않기 때문이다.
  - 질문 원문은 보내지 않는다. 개인정보 패턴을 지우고 명사 몇 개만 남긴 짧은 검색어를 만든다.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import html
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

NEWS_API_URL = "https://openapi.naver.com/v1/search/news.json"  # 기존 개발자센터(키가 이미 있는 경우)
HUB_NEWS_API_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"  # NAVER API HUB
NEWS_DISPLAY = 5  # 화면에 담는 항목 수 상한(고정)
NEWS_FETCH = 20  # 한 번에 받는 후보 수(관련도순). 최근 기사만 골라 NEWS_DISPLAY건으로 줄인다
NEWS_SORT = "sim"
NEWS_WINDOW_DAYS = 90  # 이보다 오래된 기사는 '최근 이슈' 참고로 보이지 않는다(측정: docs/evidence/news_sort_measure_2026-11-12.md)
NEWS_TIMEOUT_SECONDS = 5.0

STATUS_OK = "ok"
STATUS_SKIPPED_NO_KEY = "skipped_no_key"
STATUS_ERROR = "error"

MAX_QUERY_TOKENS = 4
MAX_QUERY_CHARS = 30
MAX_COMPANY_NAMES = 2  # 검색어 맨 앞에 한 덩어리로 두는 코퍼스 회사명의 최대 개수

# 뉴스 검색어에 도움이 안 되는 업무 용어. 회사명·주제어만 남기려는 최소 목록(코퍼스 의존 값이 아니다).
_STOPWORDS = frozenset({
    "최근", "관련", "내용", "정보", "질문", "뉴스", "이슈", "회사", "기업", "사업", "보고서", "사업보고서",
    "공시", "기간", "연도", "사업연도", "알려", "확인", "요청", "문의", "대해", "경우", "것", "수", "등",
    "주요", "현황",  # 범용 낱말: 붙으면 관련 없는 기사가 올라온다("오뚜기 주요 소송 현황" → 인물 소개 기사, 화면 검증 11/12)
    "바탕", "fy",  # "~를 바탕으로", "FY2025"의 FY: 붙으면 뉴스 검색이 0건이 된다(실호출 확인)
})
_TAG_RE = re.compile(r"<[^>]+>")
_PII_PATTERNS = (
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),  # 이메일
    re.compile(r"\d[\d\s-]{6,}\d"),  # 전화번호·계좌 같은 긴 숫자열
)
_LOGGER = logging.getLogger(__name__)
_FALLBACK_WARNED = False  # 대체 동작 경고는 프로세스당 한 번만 남긴다
_KIWI = None  # 프로세스당 한 번만 만든다(로딩 약 1초, 첫 호출 워밍업 약 1.5초, 이후 호출 0.3ms)


def _get_kiwi():
    global _KIWI
    if _KIWI is None:
        from kiwipiepy import Kiwi

        _KIWI = Kiwi()
    return _KIWI


def extract_nouns(text: str) -> list[str]:
    """kiwipiepy로 일반·고유명사와 외국어 낱말을 뽑는다. kiwipiepy가 없으면 공백 분리로 대신한다."""

    try:
        kiwi = _get_kiwi()
    except ImportError:
        global _FALLBACK_WARNED
        if not _FALLBACK_WARNED:
            _FALLBACK_WARNED = True
            _LOGGER.warning(
                "kiwipiepy가 설치되어 있지 않아 뉴스 검색어를 공백 분리로 만듭니다. 조사가 붙어 검색 결과가 0건일 수 있으니 "
                "pip install -r backend/requirements.txt 로 설치하세요."
            )
        return re.findall(r"[0-9A-Za-z가-힣]{2,}", text)
    return [token.form for token in kiwi.tokenize(text) if token.tag in ("NNG", "NNP", "SL")]


def _split_out_company_names(text: str, company_names) -> tuple[list[str], str]:
    """text에서 코퍼스 회사명을 긴 이름부터 찾아 (등장 순서의 이름 목록, 이름을 지운 문장)으로 돌려준다.

    형태소 분석기가 '한화시스템'을 '한화'+'시스템'으로 쪼개면 다른 계열사 기사가 검색되므로, 이름은 분석 전에 떼어 둔다.
    찾은 이름은 문장에서 지워 짧은 이름('한화')이 따로 또 잡히지 않게 한다.
    """

    found: list[str] = []
    for name in sorted({str(n) for n in company_names if str(n)}, key=len, reverse=True):
        if len(found) == MAX_COMPANY_NAMES:
            break
        if name in text:
            found.append(name)
            text = text.replace(name, " ")
    return found, text


def find_company_names(text: str, company_names) -> tuple[str, ...]:
    """text에 들어 있는 코퍼스 회사명을 긴 이름 우선·최대 MAX_COMPANY_NAMES개로 찾아 질문에 나온 순서로 돌려준다(없으면 빈 튜플)."""

    found, _ = _split_out_company_names(str(text), company_names)
    return tuple(sorted(found, key=str(text).index))


def build_news_query(
    question: str,
    noun_extractor: Callable[[str], list[str]] | None = None,
    company_names=(),
    quote_company_names: bool = False,
) -> str:
    """질문에서 개인정보를 지우고 회사명·핵심 명사만 남긴 짧은 검색어를 만든다(LLM 없음).

    명사가 하나도 남지 않으면 빈 문자열을 돌려준다(호출하지 않는다).
    """

    text = str(question)
    for pattern in _PII_PATTERNS:
        text = pattern.sub(" ", text)
    original = text
    names, text = _split_out_company_names(text, company_names)
    ordered = sorted(names, key=original.index)  # 회사명은 한 덩어리로 맨 앞(질문에 나온 순서)
    # 큰따옴표는 네이버의 '정확히 일치' 검색이라 그 회사가 들어간 기사만 남긴다(측정 전 탐색, 2차 사전 등록 대상).
    tokens: list[str] = [f'"{name}"' if quote_company_names else name for name in ordered]
    for form in (noun_extractor or extract_nouns)(text):
        form = form.strip().replace('"', "")  # 질문 속 따옴표가 검색 연산자로 새지 않게
        if len(form) < 2 or form.lower() in _STOPWORDS or form.isdigit() or form in tokens:
            continue
        candidate = " ".join([*tokens, form])
        if len(candidate) > MAX_QUERY_CHARS:
            break
        tokens.append(form)
        if len(tokens) == MAX_QUERY_TOKENS:
            break
    return " ".join(tokens)


def clean_text(value: Any) -> str:
    """HTML 태그를 지우고 엔티티를 풀어 한 줄 텍스트로 만든다."""

    return " ".join(html.unescape(_TAG_RE.sub("", str(value or ""))).split())


def _source_domain(item: dict[str, Any]) -> str:
    for key in ("originallink", "link"):
        host = urllib.parse.urlparse(str(item.get(key) or "")).hostname
        if host:
            return host.lower()
    return ""


def parse_news_items(payload: Any, limit: int = 5) -> list[dict[str, str]]:
    """응답 JSON에서 항목을 최대 limit개(기본 5) 꺼낸다. 형식이 다르면 ValueError."""

    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("뉴스 응답 형식 오류")
    items = []
    for raw in payload["items"][:limit]:
        if not isinstance(raw, dict):
            raise ValueError("뉴스 항목 형식 오류")
        items.append({
            "title": clean_text(raw.get("title")),
            "description": clean_text(raw.get("description")),
            "link": str(raw.get("link") or ""),
            "originallink": str(raw.get("originallink") or ""),
            "pub_date": str(raw.get("pubDate") or ""),
            "source_domain": _source_domain(raw),
        })
    return items


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))


def select_recent_items(
    items: list[dict[str, str]], now: dt.datetime, require_any: tuple[str, ...] = (),
) -> list[dict[str, str]]:
    """발행일이 NEWS_WINDOW_DAYS일 이내인 기사만 남겨 날짜 내림차순으로 NEWS_DISPLAY건. 날짜를 못 읽는 기사는 뺀다.

    require_any(기본 비어 있음): 주면 제목 또는 요약에 그 이름 중 하나가 들어간 기사만 남긴다. 5건으로 자르기 전에 걸러서
    무관한 최신 기사에 밀리지 않는다(docs/evidence/news_company_filter_2026-11-12.md).
    """

    names = tuple(name for name in require_any if name)
    dated = []
    for item in items:
        if names and not any(name in item.get("title", "") + " " + item.get("description", "") for name in names):
            continue
        try:
            when = email.utils.parsedate_to_datetime(item.get("pub_date", ""))
        except (TypeError, ValueError):
            continue
        if when is not None and now - when <= dt.timedelta(days=NEWS_WINDOW_DAYS):
            dated.append((when, item))
    dated.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in dated[:NEWS_DISPLAY]]


def _default_http_get(url: str, headers: dict[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 고정 https 주소
        return response.read()


def _endpoint_and_headers() -> tuple[str, dict[str, str] | None]:
    """쓸 주소와 인증 헤더를 돌려준다. 키가 없으면 헤더가 None. HUB 키가 있으면 HUB를 먼저 쓴다."""

    hub_id = os.getenv("NAVER_HUB_CLIENT_ID", "").strip()
    hub_secret = os.getenv("NAVER_HUB_CLIENT_SECRET", "").strip()
    if hub_id and hub_secret:
        return HUB_NEWS_API_URL, {"X-NCP-APIGW-API-KEY-ID": hub_id, "X-NCP-APIGW-API-KEY": hub_secret}
    client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        return NEWS_API_URL, {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
    return NEWS_API_URL, None


def search_news(
    query: str,
    *,
    http_get: Callable[[str, dict[str, str], float], bytes] | None = None,
    require_company_names: tuple[str, ...] = (),
) -> dict[str, Any]:
    """네이버 뉴스를 한 번만 검색한다. 예외를 밖으로 내지 않고 상태를 돌려준다.

    반환: {"status": "ok" | "skipped_no_key" | "error", "items": [...], "calls": 0 | 1}.
    키가 없거나 검색어가 비면 HTTP를 부르지 않는다(calls=0, 검색어가 비면 error).
    429·타임아웃·형식 오류 등은 재시도 없이 error(calls=1). 오류 내용에 키 값을 넣지 않는다.
    """

    url, headers = _endpoint_and_headers()
    if headers is None:
        return {"status": STATUS_SKIPPED_NO_KEY, "items": [], "calls": 0}
    if not query.strip():
        return {"status": STATUS_ERROR, "items": [], "calls": 0}

    params = urllib.parse.urlencode({"query": query, "display": NEWS_FETCH, "start": 1, "sort": NEWS_SORT})
    try:
        body = (http_get or _default_http_get)(f"{url}?{params}", headers, NEWS_TIMEOUT_SECONDS)
        items = select_recent_items(
            parse_news_items(json.loads(body), limit=NEWS_FETCH), _now(), tuple(require_company_names))
    except Exception:  # noqa: BLE001 429·타임아웃·형식 오류 모두 같은 폴백. 재시도하지 않는다
        return {"status": STATUS_ERROR, "items": [], "calls": 1}
    return {"status": STATUS_OK, "items": items, "calls": 1}
