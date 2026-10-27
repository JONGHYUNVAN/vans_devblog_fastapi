"""Django/DRF 서비스와 같은 형식의 에러 응답."""

import logging
from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("search")

_LOCATIONS = {"query", "body", "path", "header"}


def _details(exc: RequestValidationError) -> dict[str, list[str]]:
    """DRF serializer.errors 와 같은 {필드: [메시지]} 형태로 바꾼다."""
    details: dict[str, list[str]] = defaultdict(list)
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", ()) if p not in _LOCATIONS]
        field = ".".join(loc) or "non_field_errors"
        msg = str(err.get("msg", "invalid")).removeprefix("Value error, ")
        details[field].append(msg)
    return dict(details)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return JSONResponse(
            {"error": "Invalid request parameters", "details": _details(exc)},
            status_code=400,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.error("Unhandled error on %s %s: %s", request.method, request.url.path, exc, exc_info=exc)
        return JSONResponse(
            {"error": "Internal server error", "message": "An unexpected error occurred. Please try again."},
            status_code=500,
        )
