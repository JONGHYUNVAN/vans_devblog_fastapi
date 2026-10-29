"""KISAuthClient 테스트. HTTP 는 httpx2.MockTransport 로 대신한다."""

import json

import httpx2
import pytest

from app.clients.kis.auth import KISAuthClient

pytestmark = pytest.mark.anyio

REST_URL = "https://kis.test"


def _client(handler, app_key: str = "key", app_secret: str = "secret") -> KISAuthClient:
    return KISAuthClient(app_key, app_secret, REST_URL, transport=httpx2.MockTransport(handler))


async def test_get_approval_key_requests_once_then_caches():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"approval_key": "approval"})

    client = _client(handler)

    assert await client.get_approval_key() == "approval"
    assert await client.get_approval_key() == "approval"
    assert len(requests) == 1
    assert str(requests[0].url) == f"{REST_URL}/oauth2/Approval"
    assert json.loads(requests[0].content) == {
        "grant_type": "client_credentials",
        "appkey": "key",
        "secretkey": "secret",
    }


async def test_get_approval_key_none_without_credentials():
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("요청하면 안 된다")

    assert await _client(handler, app_secret="").get_approval_key() is None


async def test_get_approval_key_none_when_api_fails():
    client = _client(lambda request: httpx2.Response(500, text="error"))

    assert await client.get_approval_key() is None


async def test_get_approval_key_none_when_response_has_no_key():
    client = _client(lambda request: httpx2.Response(200, json={"msg": "no key"}))

    assert await client.get_approval_key() is None
