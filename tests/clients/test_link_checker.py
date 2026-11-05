"""LinkChecker 테스트. HTTP 는 httpx2.MockTransport 로 대신한다."""

import httpx2
import pytest

from app.clients.link_checker import LinkChecker

pytestmark = pytest.mark.anyio


async def test_check_returns_status_and_none_on_connection_failure_without_following_redirects():
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "down.test":
            raise httpx2.ConnectError("refused")
        if request.url.host == "moved.test":
            return httpx2.Response(301, headers={"location": "http://169.254.169.254/"})
        return httpx2.Response(200 if request.url.path == "/ok" else 404)

    checker = LinkChecker(transport=httpx2.MockTransport(handler), require_public=False)

    result = await checker.check(
        [
            "https://a.test/ok",
            "https://a.test/gone",
            "https://down.test/",
            "https://moved.test/",
            "ftp://a.test/x",
            "#anchor",
        ]
    )

    assert result == {
        "https://a.test/ok": 200,
        "https://a.test/gone": 404,
        "https://down.test/": None,
        "https://moved.test/": 301,
    }


async def test_head_not_allowed_falls_back_to_get():
    methods: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        methods.append(request.method)
        return httpx2.Response(405 if request.method == "HEAD" else 200)

    checker = LinkChecker(transport=httpx2.MockTransport(handler), require_public=False)

    assert await checker.check(["https://a.test/x"]) == {"https://a.test/x": 200}
    assert methods == ["HEAD", "GET"]


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/admin", "http://[::1]/", "http://10.0.0.1/", "http://169.254.169.254/latest"]
)
async def test_private_addresses_are_not_requested(url):
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("must not be requested")

    assert await LinkChecker(transport=httpx2.MockTransport(handler)).check([url]) == {url: None}
