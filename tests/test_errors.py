"""D6 공통 오류 규약과 요청 본문 제한."""

import asyncio
import json

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.errors import (
    RequestBodyLimitMiddleware,
    database_unavailable,
    not_awaiting_approval,
    register_error_handlers,
    resource_not_found,
)


def _client() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/hidden")
    def hidden():
        raise resource_not_found()

    @app.get("/missing")
    def missing():
        raise HTTPException(404, detail="private user id")

    @app.get("/approval")
    def approval():
        raise not_awaiting_approval()

    @app.get("/db")
    def db():
        raise database_unavailable()

    @app.get("/limited")
    def limited():
        raise HTTPException(429, detail="private limit state", headers={"Retry-After": "5"})

    @app.post("/echo")
    def echo(body: dict):
        return body

    return TestClient(app, raise_server_exceptions=False)


def test_hidden_resource_and_missing_route_have_the_same_public_404():
    client = _client()
    errors = [client.get(path).json()["error"] for path in ("/hidden", "/missing", "/absent")]
    assert all(error["code"] == "RESOURCE_NOT_FOUND" for error in errors)
    assert all(error["message"] == "리소스를 찾을 수 없습니다" for error in errors)
    assert all(set(error) == {"code", "message", "details", "request_id"} for error in errors)
    assert len({error["request_id"] for error in errors}) == 3
    assert "private user id" not in str(errors)


def test_approval_conflict_and_db_unavailable_codes():
    client = _client()
    for path, status, code in (
        ("/approval", 409, "CONFLICT_NOT_AWAITING_APPROVAL"),
        ("/db", 503, "SERVICE_UNAVAILABLE_DB"),
    ):
        response = client.get(path)
        assert response.status_code == status
        assert response.json()["error"]["code"] == code


def test_http_exception_keeps_status_and_protocol_headers_without_detail():
    client = _client()
    method_error = client.post("/hidden")
    assert method_error.status_code == 405
    assert method_error.headers["allow"] == "GET"
    assert method_error.json()["error"]["code"] == "HTTP_ERROR"

    limited = client.get("/limited")
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "5"
    assert limited.json()["error"]["code"] == "HTTP_ERROR"
    assert "private limit state" not in limited.text


def test_request_body_limit_is_inclusive_and_does_not_echo_payload():
    client = _client()
    prefix, suffix = b'{"data":"sensitive-value', b'"}'
    payload = prefix + b"x" * (16384 - len(prefix) - len(suffix)) + suffix
    assert len(payload) == 16384  # 리터럴: SPEC §4 요청 본문 16KB
    accepted = client.post("/echo", content=payload, headers={"content-type": "application/json"})
    assert accepted.status_code == 200
    oversized = payload[:-len(suffix)] + b"x" + suffix
    assert len(oversized) == 16385
    response = client.post("/echo", content=oversized, headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "VALIDATION_PAYLOAD_TOO_LARGE"
    assert "sensitive-value" not in response.text


def test_two_asgi_body_messages_without_content_length_hit_the_limit():
    received: list[dict] = []
    sent: list[dict] = []
    downstream_called = False

    async def downstream(_scope, _receive, _send):
        nonlocal downstream_called
        downstream_called = True

    async def receive():
        return received.pop(0)

    async def send(message):
        sent.append(message)

    received.extend([
        {"type": "http.request", "body": b"x" * 10000, "more_body": True},
        {"type": "http.request", "body": b"y" * 6385, "more_body": False},  # 합 16,385바이트: 16KB를 1바이트 넘김
    ])
    scope = {"type": "http", "method": "POST", "path": "/", "headers": []}
    asyncio.run(RequestBodyLimitMiddleware(downstream)(scope, receive, send))

    assert not downstream_called
    assert not received
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 413
    assert json.loads(sent[1]["body"])["error"]["code"] == "VALIDATION_PAYLOAD_TOO_LARGE"


def test_dropped_database_connection_is_503_with_a_retry_message_not_500():
    """11/17 배포 측정: 한도 조회 중 DB 연결이 끊기자 INTERNAL_ERROR 500이 나갔다. 다시 시도하면 되는 상태는 503으로 알린다."""

    import psycopg

    app = FastAPI()
    register_error_handlers(app)

    @app.get("/dropped")
    def dropped():
        raise psycopg.OperationalError("consuming input failed: server closed the connection unexpectedly")

    @app.get("/bad-sql")
    def bad_sql():
        raise psycopg.ProgrammingError("syntax error at or near secret_table")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/dropped")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "SERVICE_UNAVAILABLE_DB"
    assert body["error"]["message"] == "데이터베이스에 연결하지 못했습니다. 잠시 뒤 다시 시도하세요"
    assert "server closed" not in response.text  # 예외 원문은 내보내지 않는다

    bad = client.get("/bad-sql")  # 연결 문제가 아닌 DB 오류는 500 그대로이고 원문도 내보내지 않는다
    assert bad.status_code == 500 and bad.json()["error"]["code"] == "INTERNAL_ERROR" and "secret_table" not in bad.text
