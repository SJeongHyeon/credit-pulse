"""처리 상한 값(SPEC §4). 한도 숫자는 이 파일에만 둔다.

2026-11-11 리뷰 피드백 반영: '평생 누적' 한도는 사실상 걸리지 않아, 분·일 단위로 초기화되는 한도로 바꿨다.
모두 임시값이다. Langfuse 측정(질의당 평균 비용, p95 처리 시간)을 보고 조정한다.
하루 단위는 한국 시간(KST) 자정에 바뀐다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# 사용자당 질의
USER_QUERIES_PER_MINUTE = 5
RATE_WINDOW_SECONDS = 60.0
USER_QUERIES_PER_DAY = 50

# 심사 건당 질의(하루). 승인된 건은 닫혀 더 질의할 수 없다
REVIEW_QUERIES_PER_DAY = 20

# 요청 본문·질문 길이
MAX_REQUEST_BODY_BYTES = 16 * 1024
MAX_QUESTION_CHARS = 1000

# LLM 비용(하루, 달러)
SERVICE_DAILY_BUDGET_USD = 2.0
USER_DAILY_BUDGET_USD = 0.5

# 질의 한 건의 처리 시간(초). 넘으면 새 외부 호출을 하지 않고 504로 끝낸다
QUERY_TIMEOUT_SECONDS = 60.0

KST = timezone(timedelta(hours=9))  # 한국은 서머타임이 없어 고정 오프셋으로 충분하다


def kst_today() -> date:
    """한국 시간 기준 오늘 날짜. 하루 한도의 키로 쓴다."""

    return datetime.now(KST).date()
