from unittest.mock import MagicMock, call

import pytest

from backend import db


def test_connect_db_disables_prepared_statements(monkeypatch):
    connection = object()
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(db.psycopg, "connect", connect)
    assert db.connect_db("postgresql://local-test") is connection
    connect.assert_called_once_with(
        "postgresql://local-test", prepare_threshold=None, connect_timeout=5
    )


def test_connect_db_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(db.DatabaseConfigError, match="DATABASE_URL"):
        db.connect_db()


def test_connect_db_uses_environment_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://local-env-test")
    connection = object()
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(db.psycopg, "connect", connect)

    assert db.connect_db() is connection
    connect.assert_called_once_with(
        "postgresql://local-env-test", prepare_threshold=None, connect_timeout=5
    )


def test_check_database_executes_select_one(monkeypatch):
    connection = MagicMock()
    connection.__enter__.return_value = connection
    monkeypatch.setattr(db, "connect_db", lambda: connection)
    db.check_database()
    assert connection.execute.call_args_list == [
        call("SET LOCAL statement_timeout = 3000"),
        call("SELECT 1"),
    ]
