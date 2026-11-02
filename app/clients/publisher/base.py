"""Post BE(vans_story_be_post) 클라이언트 계약 (설계 06 문서 2절·3절 clients/publisher).

clients 는 models 를 import 하지 않으므로 카테고리 타입을 여기 둔다. 메인·서브 쌍(CategoryOption)으로 묶는 일은 서비스가 한다.
발행(PostCreatePayload, publish)은 6단계에서 더한다.
"""

from typing import Protocol

from pydantic import BaseModel


class PostCategory(BaseModel):
    """GET /api/v1/categories 응답 한 건. parent_id 가 None 이면 메인 카테고리다."""

    id: str
    parent_id: str | None = None
    value: str
    label: str
    is_active: bool = True


class Publisher(Protocol):
    async def list_categories(self) -> list[PostCategory]:
        """활성 카테고리 전부. 실패는 PublishFailedError (라운드를 만들지 않는다)."""
        ...
