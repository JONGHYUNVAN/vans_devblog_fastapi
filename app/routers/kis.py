"""KIS 실시간 시세 엔드포인트. vans_story(Next.js BFF)가 대시보드 종목에 대해 호출한다."""

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.exceptions import InvalidSymbolError
from app.dependencies import get_kis_service
from app.schemas.kis import Snapshot
from app.services.kis import KISService, StreamEvent

router = APIRouter(prefix="/api/kis", tags=["KIS"])

_SSE_MEDIA_TYPE = "text/event-stream"
# X-Accel-Buffering: 프록시(nginx)가 이벤트를 모아 보내지 않게 한다
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.get(
    "/snapshot/{symbol}/",
    summary="종목 스냅샷",
    responses={400: {"description": "종목코드가 6자리 숫자가 아님"}, 503: {"description": "KIS 미설정"}},
)
async def get_snapshot(symbol: str, kis_service: Annotated[KISService, Depends(get_kis_service)]) -> Snapshot:
    """최신 체결가·호가. 데이터가 없으면 최대 5초 기다리고, 그래도 없으면 `trade`·`orderbook` 이 null 이다.

    오류 형식은 `{"success": false, "error": {"message", "code"}}` 이다.
    """
    return await kis_service.get_snapshot(symbol)


@router.get(
    "/stream/{symbol}/",
    summary="실시간 시세 스트림 (SSE)",
    response_class=StreamingResponse,
    responses={200: {"content": {_SSE_MEDIA_TYPE: {}}, "description": "connected·trade·orderbook·ping·error 이벤트"}},
)
async def open_stream(
    symbol: str,
    request: Request,
    kis_service: Annotated[KISService, Depends(get_kis_service)],
) -> StreamingResponse:
    """체결가·호가를 Server-Sent Events 로 보낸다. 15초마다 `ping`. KIS 가 설정되지 않았으면 30초마다 `ping` 만 온다."""
    try:
        events = kis_service.open_stream(symbol, request.is_disconnected)
    except InvalidSymbolError:
        # 계약: 스트림의 오류도 EventSource 가 읽을 수 있게 SSE error 이벤트로 보낸다 (Django 와 같은 본문·400).
        error = _sse_frame("error", {"message": "invalid symbol", "code": InvalidSymbolError.code})
        return StreamingResponse(iter([error]), status_code=400, media_type=_SSE_MEDIA_TYPE)
    return StreamingResponse(_sse_frames(events), media_type=_SSE_MEDIA_TYPE, headers=_SSE_HEADERS)


async def _sse_frames(events: AsyncIterator[StreamEvent]) -> AsyncIterator[str]:
    async for event, data in events:
        yield _sse_frame(event, data)


def _sse_frame(event: str, data: dict[str, Any]) -> str:
    # json.dumps 기본 형식(", " 구분, ASCII 이스케이프)은 Django 가 보내던 바이트와 같다
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
