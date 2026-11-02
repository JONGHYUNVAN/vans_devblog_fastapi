"""vans_story_be_post 호출. 5단계는 카테고리 조회만, 로그인·발행은 6단계 (설계 06 문서 3절 clients/publisher)."""

import httpx2

from app.clients.publisher.base import PostCategory
from app.core.exceptions import PublishFailedError


class PostBEPublisher:
    def __init__(self, post_be_url: str, timeout_s: float, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._post_be_url = post_be_url.rstrip("/")
        self._timeout_s = timeout_s
        self._transport = transport

    async def list_categories(self) -> list[PostCategory]:
        """GET /api/v1/categories?activeOnly=true. 어떤 실패든 PublishFailedError 다(조회에는 결과 불명이 없다)."""
        try:
            async with httpx2.AsyncClient(transport=self._transport, timeout=self._timeout_s) as client:
                response = await client.get(f"{self._post_be_url}/api/v1/categories", params={"activeOnly": "true"})
                response.raise_for_status()
                rows = response.json()
        except (httpx2.HTTPError, ValueError) as exc:
            raise PublishFailedError(f"category lookup failed: {type(exc).__name__}: {exc}") from exc
        if not isinstance(rows, list):
            raise PublishFailedError("category lookup returned a non-list body")
        return [
            PostCategory(
                id=row["id"],
                parent_id=row.get("parentId"),
                value=row["value"],
                label=row["label"],
                is_active=row.get("isActive", True),
            )
            for row in rows
        ]
