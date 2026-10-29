"""도메인 예외와, 예외를 Django/DRF 서비스와 같은 형식의 에러 응답으로 바꾸는 핸들러."""

import logging
from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

_LOCATIONS = {"query", "body", "path", "header"}


class AppError(Exception):
    """서비스가 던지는 예외의 기반. 핸들러가 status_code 와 {"error", "message"} 응답으로 바꾼다."""

    status_code: int = 500
    error: str = "Internal server error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(AppError):
    status_code = 404
    error = "Not found"


class AuthenticationError(AppError):
    status_code = 403  # 계약: Django 서비스가 인증 실패에 403 을 돌려줬다
    error = "Authentication failed"


def _details(exc: RequestValidationError) -> dict[str, list[str]]:
    """DRF serializer.errors 와 같은 {필드: [메시지]} 형태로 바꾼다."""
    details: dict[str, list[str]] = defaultdict(list)
    for error in exc.errors():
        loc = tuple(error.get("loc", ()))
        # 첫 요소만 파라미터 위치(query·body…)다. 필드 이름이 위치 이름과 같을 수 있다(검색어 필드 query).
        if loc and loc[0] in _LOCATIONS:
            loc = loc[1:]
        field = ".".join(str(part) for part in loc) or "non_field_errors"
        msg = str(error.get("msg", "invalid")).removeprefix("Value error, ")
        details[field].append(msg)
    return dict(details)


def register_exception_handlers(app: FastAPI) -> None:
    """AppError, 요청 검증 오류(400), 처리하지 못한 예외(500)의 응답 형식을 등록한다."""

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse({"error": exc.error, "message": exc.message}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {"error": "Invalid request parameters", "details": _details(exc)},
            status_code=400,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled error on %s %s: %s", request.method, request.url.path, exc, exc_info=exc)
        return JSONResponse(
            {"error": "Internal server error", "message": "An unexpected error occurred. Please try again."},
            status_code=500,
        )
