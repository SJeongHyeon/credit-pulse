"""질의 한도(SPEC §4): 사용자 분당·하루 횟수, 심사 건 하루 횟수, 서비스·사용자 하루 LLM 비용.

값은 backend/limit_values.py에만 있다. 분당 한도는 서버 프로세스 메모리에 센다(그래서 Cloud Run 인스턴스를 1개로 둔다,
README 배포 절). 하루 단위 횟수와 비용은 DB 표 `daily_usage`(db/05_daily_limits.sql)에 (대상, 한국 날짜)별로 쌓아
재시작·재배포에도 이어지고, 한국 시간 자정에 날짜가 바뀌면 0부터 다시 센다.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from backend import limit_values
from backend.errors import ApiError

SERVICE_SCOPE_ID = "all"


def rate_limited(message: str = "질의가 너무 잦습니다. 잠시 뒤 다시 시도해 주세요") -> ApiError:
    return ApiError(429, "RATE_LIMITED", message)


def daily_rate_limited() -> ApiError:
    return rate_limited("오늘 질의 한도를 모두 썼습니다. 내일 다시 시도해 주세요")


def review_query_limit() -> ApiError:
    return ApiError(409, "CONFLICT_REVIEW_QUERY_LIMIT", "이 심사 건의 오늘 질의 한도를 모두 썼습니다")


def budget_exhausted() -> ApiError:
    return ApiError(503, "SERVICE_BUDGET_EXHAUSTED", "오늘 LLM 사용 한도를 모두 썼습니다")


class PerUserRateLimiter:
    """사용자별 최근 60초 요청 시각을 들고 있다가 분당 한도를 넘으면 429."""

    def __init__(
        self,
        limit: int = limit_values.USER_QUERIES_PER_MINUTE,
        window_seconds: float = limit_values.RATE_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, user_id: str) -> None:
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(user_id, deque())
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                raise rate_limited()
            hits.append(now)


def usage_cost(usage: Iterable[dict[str, Any]]) -> float:
    """llm 사용량 기록의 비용 합. 비용을 모르는 호출(None)은 0으로 센다."""

    return float(sum(float(entry.get("cost_usd") or 0.0) for entry in usage))


DAILY_LIMITS_SQL = Path(__file__).resolve().parents[2] / "db" / "05_daily_limits.sql"


def sql_statements(path: Path) -> list[str]:
    """SQL 파일을 문장 단위로 나눈다(주석 줄 제외). 준비된 문장(prepare)을 쓰는 연결은 한 번에 한 문장만 받는다.
    db/ 파일에는 $$ 본문이 없어 ';'로 나눠도 된다."""

    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("--")]
    return [statement.strip() for statement in "\n".join(lines).split(";") if statement.strip()]


def limit_setup_statements() -> list[str]:
    return sql_statements(DAILY_LIMITS_SQL)


class DailyUsage(Protocol):
    """(대상, 날짜)별 질의 횟수와 비용 저장소."""

    def admit(self, scope: str, scope_id: str, day: date, limit: int) -> bool: ...

    def release(self, scope: str, scope_id: str, day: date) -> None: ...

    def cost(self, scope: str, scope_id: str, day: date) -> float: ...

    def add_cost(self, scope: str, scope_id: str, day: date, cost_usd: float) -> None: ...


class InMemoryDailyUsage:
    """DB 없이 도는 시험·기본 가드용. 운영은 PostgresDailyUsage를 쓴다."""

    def __init__(self) -> None:
        self._queries: dict[tuple[str, str, date], int] = {}
        self._cost: dict[tuple[str, str, date], float] = {}
        self._lock = threading.Lock()

    def admit(self, scope: str, scope_id: str, day: date, limit: int) -> bool:
        key = (scope, scope_id, day)
        with self._lock:
            if self._queries.get(key, 0) >= limit:
                return False
            self._queries[key] = self._queries.get(key, 0) + 1
            return True

    def release(self, scope: str, scope_id: str, day: date) -> None:
        key = (scope, scope_id, day)
        with self._lock:
            self._queries[key] = max(0, self._queries.get(key, 0) - 1)

    def queries(self, scope: str, scope_id: str, day: date) -> int:
        return self._queries.get((scope, scope_id, day), 0)

    def cost(self, scope: str, scope_id: str, day: date) -> float:
        return self._cost.get((scope, scope_id, day), 0.0)

    def add_cost(self, scope: str, scope_id: str, day: date, cost_usd: float) -> None:
        key = (scope, scope_id, day)
        with self._lock:
            self._cost[key] = self._cost.get(key, 0.0) + cost_usd


class PostgresDailyUsage:
    """`daily_usage` 표. 횟수는 '세면서 검사'를 한 문장으로 해서 동시 요청이 한도를 넘지 못한다."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def admit(self, scope: str, scope_id: str, day: date, limit: int) -> bool:
        if limit <= 0:
            return False
        with self._pool.connection() as connection:
            row = connection.execute(
                "INSERT INTO daily_usage (scope, scope_id, day, queries) VALUES (%s, %s, %s, 1) "
                "ON CONFLICT (scope, scope_id, day) DO UPDATE "
                "SET queries = daily_usage.queries + 1, updated_at = now() "
                "WHERE daily_usage.queries < %s RETURNING queries",
                (scope, scope_id, day, limit),
            ).fetchone()
        return row is not None  # 한도에 닿아 있으면 UPDATE가 일어나지 않아 돌아오는 행이 없다

    def release(self, scope: str, scope_id: str, day: date) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                "UPDATE daily_usage SET queries = GREATEST(queries - 1, 0), updated_at = now() "
                "WHERE scope = %s AND scope_id = %s AND day = %s",
                (scope, scope_id, day),
            )

    def cost(self, scope: str, scope_id: str, day: date) -> float:
        import psycopg

        # 읽기만 하는 조회라 연결이 끊겼으면 새 연결로 한 번 더 한다(요청의 첫 DB 접근이라 죽은 연결을 여기서 만난다).
        for attempt in range(2):
            try:
                with self._pool.connection() as connection:
                    row = connection.execute(
                        "SELECT cost_usd FROM daily_usage WHERE scope = %s AND scope_id = %s AND day = %s",
                        (scope, scope_id, day),
                    ).fetchone()
                break
            except psycopg.OperationalError:
                if attempt == 1:
                    raise
        if row is None:
            return 0.0
        return float(row["cost_usd"] if isinstance(row, dict) else row[0])

    def add_cost(self, scope: str, scope_id: str, day: date, cost_usd: float) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                "INSERT INTO daily_usage (scope, scope_id, day, cost_usd) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (scope, scope_id, day) DO UPDATE "
                "SET cost_usd = daily_usage.cost_usd + EXCLUDED.cost_usd, updated_at = now()",
                (scope, scope_id, day, cost_usd),
            )


class QueryGuard:
    """라우터가 그래프 실행 전후에 부른다: 전에는 한도 확인(통과하면 횟수를 센다), 후에는 쓴 비용 기록.

    횟수는 그래프를 돌리기 직전에 센다. 실패한 질의도 비용을 쓰므로 1회로 친다.
    """

    def __init__(
        self,
        rate_limiter: PerUserRateLimiter | None = None,
        usage: DailyUsage | None = None,
        *,
        today: Callable[[], date] = limit_values.kst_today,
        user_queries_per_day: int = limit_values.USER_QUERIES_PER_DAY,
        review_queries_per_day: int = limit_values.REVIEW_QUERIES_PER_DAY,
        service_daily_budget_usd: float = limit_values.SERVICE_DAILY_BUDGET_USD,
        user_daily_budget_usd: float = limit_values.USER_DAILY_BUDGET_USD,
    ) -> None:
        self._rate_limiter = rate_limiter or PerUserRateLimiter()
        self._usage = usage if usage is not None else InMemoryDailyUsage()
        self._today = today
        self._user_queries = user_queries_per_day
        self._review_queries = review_queries_per_day
        self._service_budget = service_daily_budget_usd
        self._user_budget = user_daily_budget_usd

    def before_query(self, user_id: str, review_id: str) -> None:
        """분당 → 하루 비용(서비스, 사용자) → 사용자 하루 횟수 → 심사 건 하루 횟수 순으로 본다. 막히면 ApiError."""

        day = self._today()
        self._rate_limiter.check(user_id)
        if (
            self._usage.cost("service", SERVICE_SCOPE_ID, day) >= self._service_budget
            or self._usage.cost("user", user_id, day) >= self._user_budget
        ):
            raise budget_exhausted()
        if not self._usage.admit("user", user_id, day, self._user_queries):
            raise daily_rate_limited()
        if not self._usage.admit("review", review_id, day, self._review_queries):
            self._usage.release("user", user_id, day)  # 이 질의는 돌지 않으니 사용자 횟수를 되돌린다
            raise review_query_limit()

    def cancel_query(self, user_id: str, review_id: str) -> None:
        """before_query를 통과했지만 그래프를 돌리지 못한 질의(동시 요청에 잠금을 뺏김)의 횟수를 되돌린다."""

        day = self._today()
        self._usage.release("user", user_id, day)
        self._usage.release("review", review_id, day)

    def record(self, user_id: str, usage: Iterable[dict[str, Any]], *, late_usage: Iterable[dict[str, Any]] = ()) -> None:
        """이번 요청이 쓴 비용을 서비스·사용자의 오늘 비용에 더한다.

        late_usage: 요청이 끝난 뒤에 끝난 호출(이중 요청의 늦은 쪽 등)의 비용. 누구 것인지 알 수 없어 서비스 합에만 더한다.
        """

        day = self._today()
        cost = usage_cost(usage)
        late = usage_cost(late_usage)
        if cost + late > 0:
            self._usage.add_cost("service", SERVICE_SCOPE_ID, day, cost + late)
        if cost > 0:
            self._usage.add_cost("user", user_id, day, cost)
