from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import conninfo_to_dict

from backend.main import app


def test_health(monkeypatch):
    # 배포 환경과 평가자가 서비스 상태를 확인하는 엔드포인트. 이게 깨지면 G1, G2가 모두 위험하다.
    monkeypatch.setattr("backend.main.db.check_database", lambda: None)
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_startup_validates_database_embedding_dimension(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:app@db:5432/app")
    calls = []
    monkeypatch.setattr(
        "backend.main.validate_database_embedding_dimension",
        lambda **kwargs: calls.append(kwargs),
    )
    monkeypatch.setattr("backend.main.db.check_database", lambda: None)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert len(calls) == 1
    connection_settings = conninfo_to_dict(calls[0]["database_url"])
    assert connection_settings["host"] == "db"
    assert connection_settings["connect_timeout"] == "5"


def test_startup_skips_dimension_check_without_database_url(monkeypatch):
    # 기동은 가능하지만 DB 없이 /health가 성공을 보고해서는 안 된다.
    monkeypatch.delenv("DATABASE_URL", raising=False)

    def fail_if_called():
        raise AssertionError("DATABASE_URL이 없으면 차원 검사를 호출하지 않아야 한다")

    monkeypatch.setattr("backend.main.validate_database_embedding_dimension", fail_if_called)

    with TestClient(app) as client:
        r = client.get("/health")

    assert r.status_code == 503
    assert r.json()["error"]["code"] == "SERVICE_UNAVAILABLE_DB"


def test_health_returns_503_when_database_fails(monkeypatch):
    def fail():
        raise psycopg.OperationalError("private database hostname")

    monkeypatch.setattr("backend.main.db.check_database", fail)
    response = TestClient(app).get("/health")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE_DB"
    assert "private database hostname" not in response.text


def test_startup_survives_database_outage_to_report_health(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unavailable")

    def fail(**kwargs):
        raise psycopg.OperationalError("down")

    monkeypatch.setattr("backend.main.validate_database_embedding_dimension", fail)
    monkeypatch.setattr("backend.main.db.check_database", fail)
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 503
