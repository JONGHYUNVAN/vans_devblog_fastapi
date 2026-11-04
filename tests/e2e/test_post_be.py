"""실제 Post BE 에서 활성 카테고리를 읽고(5단계), 발행 계정으로 로그인해 본다(6단계). 기본 실행에서 빠진다.

사용: POST_BE_URL=... USER_SERVICE_URL=... ANTHROPIC_PUBLISHER_EMAIL=... ANTHROPIC_PUBLISHER_PASSWORD=... (GOOGLE_·XAI_ 도 같다) pytest -m e2e tests/e2e/test_post_be.py
실제 발행은 라운드를 돌려 확인한다(agents start-round --wait + agents run + agents approve).
"""

import os

import httpx2
import pytest

from app.clients.publisher.base import PublisherAccount
from app.clients.publisher.post_be import PostBEPublisher
from app.services.round import category_options

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

POST_BE_URL = os.environ.get("POST_BE_URL", "").rstrip("/")


async def test_active_categories_form_main_sub_pairs():
    if not POST_BE_URL:
        pytest.skip("POST_BE_URL 이 없다")

    categories = await PostBEPublisher(POST_BE_URL, timeout_s=30).list_categories()
    options = category_options(categories)

    assert categories and all(category.is_active for category in categories)
    assert options, "활성 메인 아래 활성 서브 카테고리가 하나는 있어야 라운드를 만들 수 있다"
    assert all(option.main_category_id != option.sub_category_id for option in options)


@pytest.mark.parametrize("provider", ["ANTHROPIC", "GOOGLE", "XAI"])
async def test_publisher_account_can_log_in(provider):
    """벤더별 발행 계정으로 로그인만 해 본다(글은 만들지 않는다). 6단계."""
    user_service_url = os.environ.get("USER_SERVICE_URL", "")
    email = os.environ.get(f"{provider}_PUBLISHER_EMAIL", "")
    password = os.environ.get(f"{provider}_PUBLISHER_PASSWORD", "")
    if not (POST_BE_URL and user_service_url and email and password):
        pytest.skip(f"USER_SERVICE_URL, {provider}_PUBLISHER_EMAIL, {provider}_PUBLISHER_PASSWORD 가 없다")

    publisher = PostBEPublisher(POST_BE_URL, timeout_s=30, user_service_url=user_service_url)
    async with httpx2.AsyncClient(timeout=30) as client:
        token = await publisher._login(client, PublisherAccount(email=email, password=password))  # noqa: SLF001

    assert token.count(".") == 2  # JWT
