"""SPEC §4·§5.5·§6의 공통 API 오류 응답과 요청 크기 제한."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.limit_values import MAX_REQUEST_BODY_BYTES, QUERY_TIMEOUT_SECONDS



class ApiError(Exception):
    """입력값이나 내부 예외 메시지를 응답으로 전달하지 않는 API 오류."""

    def __init__(
        self, status_code: int, code: str, message: str,
        details: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def resource_not_found() -> ApiError:
    """다른 사용자의 리소스도 존재하지 않는 리소스와 똑같이 숨긴다."""
    return ApiError(404, "RESOURCE_NOT_FOUND", "리소스를 찾을 수 없습니다")


def not_awaiting_approval() -> ApiError:
    return ApiError(409, "CONFLICT_NOT_AWAITING_APPROVAL", "대기 중인 승인 요청이 없습니다")


def review_closed() -> ApiError:
    """승인이 끝나 닫힌 심사 건에는 더 질의할 수 없다(SPEC §4). 새 심사 건을 만들어야 한다."""
    return ApiError(409, "CONFLICT_REVIEW_CLOSED", "승인이 끝난 심사 건에는 더 질의할 수 없습니다. 새 심사 건을 만들어 주세요")


def query_timeout() -> ApiError:
    return ApiError(
        504, "UPSTREAM_LLM_TIMEOUT", f"질의 처리 시간이 {int(QUERY_TIMEOUT_SECONDS)}초를 넘었습니다. 잠시 뒤 다시 시도하세요"
    )


def database_unavailable() -> ApiError:
    return ApiError(503, "SERVICE_UNAVAILABLE_DB", "데이터베이스를 사용할 수 없습니다")


# 외부 API가 일시적으로 못 쓰는 상태로 보는 HTTP 상태(인증·한도·크레딧·충돌·5xx). 400·404·422는 우리 요청 문제라 500으로 둔다.
UPSTREAM_UNAVAILABLE_STATUS = {401, 402, 403, 408, 409, 429}


def upstream_api_error(exc: BaseException) -> ApiError | None:
    """llm.py 예외를 SPEC §6 응답으로 바꾼다. 해당 없으면 None(그대로 500 INTERNAL_ERROR).

    11/7 팀 OpenRouter 키 한도 초과(403)가 500 '예상하지 못한 오류'로 보였다. 사용자에게는 '잠시 뒤 다시'를,
    운영에는 키·외부 장애임을 구분해 준다. 메시지에 키나 공급자 응답 본문을 넣지 않는다.
    """

    from backend.services import llm

    if isinstance(exc, llm.QueryDeadlineExceeded):
        return query_timeout()
    if isinstance(exc, llm.ExternalApiTimeout):
        return ApiError(504, "UPSTREAM_LLM_TIMEOUT", "외부 AI 서비스 응답이 제시간에 오지 않았습니다. 잠시 뒤 다시 시도하세요")
    if isinstance(exc, llm.ExternalApiResponseError):
        return ApiError(502, "UPSTREAM_LLM_BAD_OUTPUT", "외부 AI 서비스의 응답 형식이 올바르지 않습니다. 다시 시도하세요")
    if isinstance(exc, llm.ExternalApiKeyMissing) or (
        isinstance(exc, llm.ExternalApiStatusError)
        and (exc.status_code in UPSTREAM_UNAVAILABLE_STATUS or exc.status_code >= 500)
    ):
        return ApiError(503, "SERVICE_UNAVAILABLE_UPSTREAM", "외부 AI 서비스를 일시적으로 사용할 수 없습니다. 잠시 뒤 다시 시도하세요")
    return None


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, str]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {
            "code": code,
            "message": message,
            "details": details or [],
            "request_id": str(uuid.uuid4()),
        }},
        headers=headers,
    )


async def _handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
    return _error_response(exc.status_code, exc.code, exc.message, exc.details)


async def _handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI의 기본 오류에는 input이 들어 있으므로 위치와 오류 종류만 사용한다.
    details = [
        {"field": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
        for error in exc.errors()
    ]
    return _error_response(400, "VALIDATION_ERROR", "요청 형식이 올바르지 않습니다", details)


async def _handle_http_error(_: Request, exc: HTTPException) -> JSONResponse:
    if exc.status_code == 404:
        return await _handle_api_error(_, resource_not_found())
    # 상세에는 사용자 입력이 섞일 수 있다. 상태와 프로토콜 헤더(Allow 등)는 유지한다.
    return _error_response(
        exc.status_code, "HTTP_ERROR", "요청을 처리할 수 없습니다", headers=exc.headers
    )


async def _handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    # 예외 원문은 응답에 포함하지 않는다.
    return _error_response(500, "INTERNAL_ERROR", "서버 오류가 발생했습니다")


async def _handle_database_connection_error(_: Request, exc: Exception) -> JSONResponse:
    """DB 연결이 끊긴 요청은 500이 아니라 503으로 알린다(잠시 뒤 다시 시도하면 되는 상태).

    11/17 배포 측정에서 한도 조회 중 `server closed the connection unexpectedly`가 INTERNAL_ERROR 500으로 나갔다.
    """

    error = database_unavailable()
    return _error_response(error.status_code, error.code, "데이터베이스에 연결하지 못했습니다. 잠시 뒤 다시 시도하세요")


class RequestBodyLimitMiddleware:
    """Content-Length가 없는 스트리밍 요청에도 64KB 상한을 적용한다."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def reject() -> None:
            response = _error_response(
                413, "VALIDATION_PAYLOAD_TOO_LARGE", f"요청 본문이 {MAX_REQUEST_BODY_BYTES // 1024}KB를 초과했습니다"
            )
            await response(scope, receive, send)

        headers = dict(scope.get("headers", []))
        try:
            content_length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            content_length = 0
        if content_length > MAX_REQUEST_BODY_BYTES:
            await reject()
            return

        buffered: list[Message] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            buffered.append(message)
            size += len(message.get("body", b""))
            if size > MAX_REQUEST_BODY_BYTES:
                await reject()
                return
            if not message.get("more_body", False):
                break

        async def replay_receive() -> Message:
            if buffered:
                return buffered.pop(0)
            return await receive()

        await self.app(scope, replay_receive, send)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(HTTPException, _handle_http_error)
    import psycopg

    # OperationalError: 연결 끊김·연결 실패·시간 초과(문장 오류인 ProgrammingError 등은 500 그대로)
    app.add_exception_handler(psycopg.OperationalError, _handle_database_connection_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
    app.add_middleware(RequestBodyLimitMiddleware)
