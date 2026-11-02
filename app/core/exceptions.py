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


class InvalidRequestError(AppError):
    """요청 값이 현재 데이터와 맞지 않는다(재개 상한이 사용량 이하, 결정에 필요한 값 누락 등). 형식 오류(RequestValidationError)와 구분한다."""

    status_code = 400
    error = "Invalid request"


class InvalidTransitionError(AppError):
    """현재 상태에서 허용하지 않는 전이. 에이전트 파이프라인 도메인 모델(PostDraft, PublishingRound) 메서드가 던진다."""

    status_code = 409
    error = "Invalid state transition"


class ConcurrentUpdateError(AppError):
    """revision 낙관적 잠금 충돌. 저장하려던 문서가 읽은 뒤 다른 쓰기로 바뀌었다 (설계 02 문서 6절)."""

    status_code = 409
    error = "Concurrent update"


class LLMTransportError(AppError):
    """LLM 벤더 호출이 응답을 받지 못했다: 네트워크, 5xx, 429, 타임아웃. 같은 메시지로 재시도할 수 있다."""

    status_code = 503
    error = "LLM call failed"


class LLMOutputError(AppError):
    """LLM 응답이 계약(스키마·의미 규칙)에 맞지 않는다. 직전 출력과 오류 안내를 붙여 재시도한다."""

    status_code = 502
    error = "LLM output invalid"


class LLMRetriesExhaustedError(AppError):
    """한 단계의 호출이 MAX_LLM_RETRIES 만큼 실패했다. 글은 FAILED(LLM_RETRIES_EXHAUSTED), 주제 선정은 ABORTED(LLM_FAILED)."""

    status_code = 502
    error = "LLM retries exhausted"


class BudgetExceededError(AppError):
    """글 소프트 예산 초과. 사용량 합계 + 추정치가 token_limit 을 넘어 호출하지 않았다 (설계 07 문서 D-33)."""

    status_code = 409
    error = "Token budget exceeded"


class PublishFailedError(AppError):
    """Post BE 호출이 결과가 확정된 채 실패했다: 로그인 실패, 4xx, 연결 거부. 카테고리 조회 실패도 여기다 (설계 04 문서 8절)."""

    status_code = 502
    error = "Post BE call failed"


class PublishUncertainError(AppError):
    """글 생성 호출의 결과를 모른다: 응답 전 타임아웃·연결 끊김·5xx. 다시 호출하면 중복 게시가 될 수 있다."""

    status_code = 502
    error = "Post BE result unknown"


class KISError(AppError):
    """KIS 엔드포인트 오류. 응답 형식이 다르다: {"success": false, "error": {"message", "code"}} (Django kis 앱 계약)."""

    code: str = "KIS_ERROR"


class InvalidSymbolError(KISError):
    status_code = 400
    code = "INVALID_SYMBOL"


class KISNotConfiguredError(KISError):
    status_code = 503
    code = "KIS_NOT_CONFIGURED"


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
    """AppError·KISError, 요청 검증 오류(400), 처리하지 못한 예외(500)의 응답 형식을 등록한다."""

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse({"error": exc.error, "message": exc.message}, status_code=exc.status_code)

    @app.exception_handler(KISError)
    async def _kis_error(_: Request, exc: KISError) -> JSONResponse:
        return JSONResponse(
            {"success": False, "error": {"message": exc.message, "code": exc.code}}, status_code=exc.status_code
        )

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
