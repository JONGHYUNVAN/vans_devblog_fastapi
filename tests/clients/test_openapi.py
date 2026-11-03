"""OpenApiClient 테스트 (세션 로그인 뒤 문서 받기, 실패 분류). HTTP 는 httpx2.MockTransport 로 대신한다."""

import httpx2
import pytest

from app.clients.openapi import OpenApiClient
from app.core.exceptions import ServiceUnavailableError

DOCUMENT = {"openapi": "3.0.0", "paths": {"/api/v1/posts": {}}}


def _client(handler) -> OpenApiClient:
    return OpenApiClient(timeout_s=5, transport=httpx2.MockTransport(handler))


def test_fetch_returns_document_without_login():
    assert _client(lambda request: httpx2.Response(200, json=DOCUMENT)).fetch("https://spring/v3/api-docs") == DOCUMENT


def test_fetch_logs_in_and_sends_session_cookie():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/api/posts/swagger/auth":
            return httpx2.Response(200, json={"authorized": True}, headers={"Set-Cookie": "sid=abc; Path=/"})
        if request.headers.get("Cookie") != "sid=abc":
            return httpx2.Response(401)
        return httpx2.Response(200, json=DOCUMENT)

    document = _client(handler).fetch(
        "https://post/api/posts/swagger-json", login_url="https://post/api/posts/swagger/auth", password="pw"
    )

    assert document == DOCUMENT
    assert [(each.method, each.url.path) for each in requests] == [
        ("POST", "/api/posts/swagger/auth"),
        ("GET", "/api/posts/swagger-json"),
    ]
    assert requests[0].content == b'{"password":"pw"}'


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(401, json={"message": "인증이 필요합니다"}),
        httpx2.Response(200, content=b"<html>"),
        httpx2.Response(200, json={"openapi": "3.0.0"}),
    ],
)
def test_fetch_raises_service_unavailable_on_unusable_response(response):
    with pytest.raises(ServiceUnavailableError):
        _client(lambda request: response).fetch("https://spring/v3/api-docs")


def test_fetch_raises_service_unavailable_when_login_fails():
    with pytest.raises(ServiceUnavailableError):
        _client(lambda request: httpx2.Response(401)).fetch(
            "https://post/api/posts/swagger-json", login_url="https://post/api/posts/swagger/auth", password="x"
        )
