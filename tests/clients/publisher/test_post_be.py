"""PostBEPublisher 테스트 (카테고리 조회, 로그인·발행 분류). HTTP 는 httpx2.MockTransport 로 대신한다."""

import json

import httpx2
import pytest

from app.clients.publisher.base import PostCategory, PostCreatePayload
from app.clients.publisher.post_be import PostBEPublisher
from app.core.exceptions import PublishFailedError, PublishUncertainError

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


# ---------- 발행 (로드맵 6단계) ----------

PAYLOAD = PostCreatePayload(
    title="제목",
    content={"type": "doc", "content": []},
    description="요약",
    tags=["a", "b", "c"],
    topic="주제",
    main_category_id="m1",
    sub_category_id="s1",
    language="ko",
)
LOGIN_URL = "https://user.test/api/v1/auth/login"
POSTS_URL = "https://post.test/api/v1/posts"


def _account(handler) -> PostBEPublisher:
    return PostBEPublisher(
        "https://post.test/",
        timeout_s=5,
        user_service_url="https://user.test/",
        email="bot@example.com",
        password="Password1!",
        transport=httpx2.MockTransport(handler),
    )


def _login_ok() -> httpx2.Response:
    return httpx2.Response(
        200, json={"success": True, "data": None, "message": None}, headers={"Authorization": "Bearer tok-1"}
    )


async def test_publish_logs_in_then_creates_the_post_with_the_bearer_token():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if str(request.url) == LOGIN_URL:
            return _login_ok()
        return httpx2.Response(201, json={"_id": "507f1f77bcf86cd799439011", "title": "제목"})

    result = await _account(handler).publish(PAYLOAD)

    assert result.external_post_id == "507f1f77bcf86cd799439011"
    login, create = requests
    assert json.loads(login.content) == {"email": "bot@example.com", "password": "Password1!"}
    assert str(create.url) == POSTS_URL and create.headers["Authorization"] == "Bearer tok-1"
    assert json.loads(create.content) == {
        "title": "제목",
        "content": {"type": "doc", "content": []},
        "description": "요약",
        "tags": ["a", "b", "c"],
        "topic": "주제",
        "mainCategoryId": "m1",
        "subCategoryId": "s1",
        "language": "ko",
    }


@pytest.mark.parametrize(
    "login_response",
    [
        httpx2.Response(401, json={"success": False, "message": "이메일 또는 비밀번호가 올바르지 않습니다."}),
        httpx2.Response(500, text="down"),
        httpx2.Response(200, json={"success": True}),  # 토큰 헤더 없음
        httpx2.ReadTimeout("slow"),
    ],
)
async def test_any_login_failure_is_a_certain_failure(login_response):
    posts_called = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == LOGIN_URL:
            if isinstance(login_response, Exception):
                raise login_response
            return login_response
        posts_called.append(request)
        return httpx2.Response(201, json={"_id": "x"})

    with pytest.raises(PublishFailedError):
        await _account(handler).publish(PAYLOAD)
    assert posts_called == []


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (httpx2.Response(400, json={"message": "Invalid category"}), PublishFailedError),
        (httpx2.Response(403, text="forbidden"), PublishFailedError),
        (httpx2.ConnectError("refused"), PublishFailedError),
        (httpx2.ConnectTimeout("no route"), PublishFailedError),
        (httpx2.Response(500, text="boom"), PublishUncertainError),
        (httpx2.Response(502, text="bad gateway"), PublishUncertainError),
        (httpx2.ReadTimeout("slow"), PublishUncertainError),
        (httpx2.RemoteProtocolError("closed"), PublishUncertainError),
        (httpx2.Response(201, json={"title": "no id"}), PublishUncertainError),
    ],
)
async def test_post_creation_failures_are_classified_by_whether_the_post_may_exist(outcome, expected):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == LOGIN_URL:
            return _login_ok()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    with pytest.raises(expected):
        await _account(handler).publish(PAYLOAD)


async def test_publish_without_account_settings_is_a_configuration_error():
    with pytest.raises(ValueError, match="PUBLISHER_EMAIL"):
        await _publisher(lambda request: httpx2.Response(201, json={"_id": "x"})).publish(PAYLOAD)
