"""KIS 라우터 테스트. 스냅샷 응답 계약(golden)과 SSE 프레임·헤더를 확인한다."""

from collections.abc import AsyncIterator

from app.dependencies import get_kis_service
from app.main import app
from app.services.kis import IsDisconnected, KISService, StreamEvent
from tests.conftest import load_golden
from tests.fakes import FakeKISStream


class FiniteKISService:
    """이벤트 두 개를 보내고 끝나는 스트림. TestClient 는 끝나지 않는 스트림을 읽지 못한다."""

    def open_stream(self, symbol: str, is_disconnected: IsDisconnected) -> AsyncIterator[StreamEvent]:
        async def events() -> AsyncIterator[StreamEvent]:
            yield "connected", {"symbol": symbol}
            yield "trade", {"symbol": symbol, "price": 58300}

        return events()


def _use_service(is_configured: bool = True) -> None:
    app.dependency_overrides[get_kis_service] = lambda: KISService(FakeKISStream(), is_configured, snapshot_wait=0)


def test_snapshot_matches_golden_when_no_data(client):
    _use_service()

    response = client.get("/api/kis/snapshot/005930/")

    assert response.status_code == 200
    assert response.json() == load_golden("kis_snapshot")


def test_snapshot_400_matches_golden_when_symbol_invalid(client):
    _use_service()

    response = client.get("/api/kis/snapshot/12345/")

    assert response.status_code == 400
    assert response.json() == load_golden("kis_snapshot_invalid")


def test_snapshot_503_when_kis_not_configured(client):
    _use_service(is_configured=False)

    response = client.get("/api/kis/snapshot/005930/")

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "error": {"message": "KIS OpenAPI가 설정되지 않았습니다.", "code": "KIS_NOT_CONFIGURED"},
    }


def test_stream_400_sse_error_when_symbol_invalid(client):
    _use_service()

    response = client.get("/api/kis/stream/abc/")

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text == 'event: error\ndata: {"message": "invalid symbol", "code": "INVALID_SYMBOL"}\n\n'


def test_stream_sends_sse_frames_without_caching(client):
    app.dependency_overrides[get_kis_service] = FiniteKISService

    response = client.get("/api/kis/stream/005930/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert (response.headers["cache-control"], response.headers["x-accel-buffering"]) == ("no-cache", "no")
    assert response.text == (
        'event: connected\ndata: {"symbol": "005930"}\n\nevent: trade\ndata: {"symbol": "005930", "price": 58300}\n\n'
    )
