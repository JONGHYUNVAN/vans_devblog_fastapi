"""Post BE(NestJS)가 소유한 posts·categories 컬렉션의 읽기 전용 조회 (비동기). 뜻의 글 목록 링크용 (설계 08 문서 2.6절).

동기화 서비스의 PostRepository 와 같은 컬렉션을 읽지만, 파이프라인 경로는 비동기 클라이언트라 따로 둔다. 쓰지 않는다.
"""

from collections.abc import Iterable

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.models.glossary import PostRefRecord


class PostRefRepository:
    """게시글 id 묶음 → 제목·메인 카테고리. 전역 사전의 글 링크에만 쓰고, Post BE 스키마(camelCase 필드)를 여기서만 안다."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._posts = database["posts"]  # Post BE(NestJS) 컬렉션 이름 그대로. 이 앱은 만들지도 인덱스를 걸지도 않는다
        self._categories = database["categories"]

    async def list_refs(self, post_ids: Iterable[str]) -> dict[str, PostRefRecord]:
        """게시글 id → 제목·메인 카테고리 값. 없거나 지워진 글은 빠진다."""
        # 뜻의 post_ids 는 Publication.external_post_id 문자열이다. 옛 데이터에 ObjectId 가 아닌 값이 섞여 있어도
        # 전역 사전 응답 전체가 깨지면 안 되므로 걸러 내고 진행한다
        object_ids = [ObjectId(post_id) for post_id in dict.fromkeys(post_ids) if ObjectId.is_valid(post_id)]
        if not object_ids:
            return {}
        docs = await self._posts.find({"_id": {"$in": object_ids}}).to_list()
        # 블로그 링크가 /post/view/{main_category}/{post_id} 라 카테고리 문서의 value(슬러그)가 필요하다
        category_ids = [doc["mainCategoryId"] for doc in docs if doc.get("mainCategoryId") is not None]
        values: dict = {}
        if category_ids:
            categories = await self._categories.find({"_id": {"$in": category_ids}}).to_list()
            values = {category["_id"]: category.get("value") or "" for category in categories}
        # 제목·카테고리가 없는 글은 빈 문자열로 준다. None 이면 응답 스키마(PostRef.title: str)가 깨진다. 지워진 글은 아예 빠진다
        return {
            str(doc["_id"]): PostRefRecord(
                post_id=str(doc["_id"]),
                title=doc.get("title") or "",
                main_category=values.get(doc.get("mainCategoryId"), ""),
            )
            for doc in docs
        }
