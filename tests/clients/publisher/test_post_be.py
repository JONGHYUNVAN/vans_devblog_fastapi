"""PostBEPublisher 테스트 (카테고리 조회). HTTP 는 httpx2.MockTransport 로 대신한다."""

import httpx2
import pytest

from app.clients.publisher.base import PostCategory
from app.clients.publisher.post_be import PostBEPublisher
from app.core.exceptions import PublishFailedError

pytestmark = pytest.mark.anyio

ROWS = [
    {"id": "m1", "parentId": None, "value": "backend", "label": "Backend", "isActive": True, "path": "/backend"},
    {"id": "s1", "parentId": "m1", "value": "db", "label": "Database", "isActive": True, "path": "/backend/db"},
]


def _publisher(handler) -> PostBEPublisher:
    return PostBEPublisher("https://post.test/", timeout_s=5, transport=httpx2.MockTransport(handler))


async def test_list_categories_requests_active_only_and_maps_rows():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=ROWS)

    categories = await _publisher(handler).list_categories()

    assert categories == [
        PostCategory(id="m1", parent_id=None, value="backend", label="Backend", is_active=True),
        PostCategory(id="s1", parent_id="m1", value="db", label="Database", is_active=True),
    ]
    [request] = requests
    assert str(request.url) == "https://post.test/api/v1/categories?activeOnly=true"


@pytest.mark.parametrize("response", [httpx2.Response(500, text="down"), httpx2.Response(200, json={"not": "list"})])
async def test_list_categories_failures_raise_publish_failed_error(response):
    with pytest.raises(PublishFailedError):
        await _publisher(lambda request: response).list_categories()


async def test_list_categories_connection_error_raises_publish_failed_error():
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused")

    with pytest.raises(PublishFailedError):
        await _publisher(handler).list_categories()
