"""Post BE(NestJS)가 소유한 posts·categories 컬렉션의 읽기 전용 조회 (비동기). 뜻의 글 목록 링크용 (설계 08 문서 2.6절).

동기화 서비스의 PostRepository 와 같은 컬렉션을 읽지만, 파이프라인 경로는 비동기 클라이언트라 따로 둔다. 쓰지 않는다.
"""

from collections.abc import Iterable

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.models.glossary import PostRefRecord


class PostRefRepository:
    def __init__(self, database: AsyncDatabase) -> None:
        self._posts = database["posts"]
        self._categories = database["categories"]

    async def list_refs(self, post_ids: Iterable[str]) -> dict[str, PostRefRecord]:
        """게시글 id → 제목·메인 카테고리 값. 없거나 지워진 글은 빠진다."""
        object_ids = [ObjectId(post_id) for post_id in dict.fromkeys(post_ids) if ObjectId.is_valid(post_id)]
        if not object_ids:
            return {}
        docs = await self._posts.find({"_id": {"$in": object_ids}}).to_list()
        category_ids = [doc["mainCategoryId"] for doc in docs if doc.get("mainCategoryId") is not None]
        values: dict = {}
        if category_ids:
            categories = await self._categories.find({"_id": {"$in": category_ids}}).to_list()
            values = {category["_id"]: category.get("value") or "" for category in categories}
        return {
            str(doc["_id"]): PostRefRecord(
                post_id=str(doc["_id"]),
                title=doc.get("title") or "",
                main_category=values.get(doc.get("mainCategoryId"), ""),
            )
            for doc in docs
        }
