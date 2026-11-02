"""실제 Post BE 에서 활성 카테고리를 읽어 메인·서브 쌍이 나오는지 본다. 기본 실행에서 빠진다 (로드맵 5단계).

사용: POST_BE_URL=https://<Post BE 주소> pytest -m e2e tests/e2e/test_post_be.py
"""

import os

import pytest

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
