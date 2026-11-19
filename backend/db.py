"""DATABASE_URL 기반 PostgreSQL 연결 (SPEC §5.2)."""

from __future__ import annotations

import os

import psycopg


class DatabaseConfigError(RuntimeError):
    """DB 연결 설정이 없는 상태."""


def connect_db(database_url: str | None = None) -> psycopg.Connection:
    """Supabase transaction pooler(6543)에서도 prepared statement를 만들지 않는다."""
    dsn = database_url if database_url is not None else os.getenv("DATABASE_URL", "")
    if not dsn:
        raise DatabaseConfigError("DATABASE_URL이 필요합니다")
    return psycopg.connect(dsn, prepare_threshold=None, connect_timeout=5)


def check_database() -> None:
    """연결과 질의를 확인한다. 트랜잭션 범위에서 SELECT 시간도 제한한다."""
    with connect_db() as connection:
        connection.execute("SET LOCAL statement_timeout = 3000")
        connection.execute("SELECT 1")
