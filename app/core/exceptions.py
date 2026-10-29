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


def _details(exc: RequestValidationError) -> dict[str, list[str]]:
    """DRF serializer.errors 와 같은 {필드: [메시지]} 형태로 바꾼다."""
    details: dict[str, list[str]] = defaultdict(list)
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", ()) if p not in _LOCATIONS]
        field = ".".join(loc) or "non_field_errors"
        msg = str(err.get("msg", "invalid")).removeprefix("Value error, ")
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
