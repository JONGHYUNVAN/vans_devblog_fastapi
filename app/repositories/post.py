"""MongoDB posts 컬렉션 조회. Post BE(NestJS)가 소유한 컬렉션이라 읽기만 한다."""

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from pymongo.database import Database

from app.models.post import Post, extract_content_text


class PostRepository:
    """posts 컬렉션 조회. 게시글은 posts 인덱스에 넣을 Post 로 바꿔 돌려준다.

    mainCategory/subCategory 는 categories 컬렉션에서 value 를 찾아 붙인다.
    """

    def __init__(self, database: Database) -> None:
        self._posts = database.posts

    def count(self) -> int:
        return self._posts.count_documents({})

    def iter_all(self, batch_size: int = 100) -> Iterator[Post]:
        """모든 게시글."""
        for doc in self._posts.aggregate(_category_lookup_pipeline(), batchSize=batch_size):
            yield _to_post(doc)

    def iter_updated_since(self, since: datetime, batch_size: int = 100) -> Iterator[Post]:
        """since 이후 생성·수정된 게시글. 전체 동기화와 같은 카테고리 조회를 거친다.

        Django 버전은 여기서 find() 를 써서 카테고리가 빠졌고, 증분 동기화가 ES 의 카테고리를 빈 값으로 덮어썼다.
        """
        match = {"$or": [{"updatedAt": {"$gte": since}}, {"createdAt": {"$gte": since}}]}
        for doc in self._posts.aggregate(_category_lookup_pipeline(match), batchSize=batch_size):
            yield _to_post(doc)

    def list_ids(self) -> list[str]:
        return [str(doc["_id"]) for doc in self._posts.find({}, {"_id": 1})]


def _to_post(doc: dict[str, Any]) -> Post:
    """Mongo 문서 → 색인할 Post. 빈 값 기본값은 Django 의 PostDocument.create_from_mongo_post 와 같다.

    Mongo 게시글에는 author 필드가 없어(authorEmail 만 있다) 동기화로 색인한 문서의 author 는 빈 문자열이다.
    """
    return Post(
        post_id=str(doc["_id"]),
        title=doc.get("title") or "",
        description=doc.get("description") or "",
        content_text=extract_content_text(doc.get("content")),
        topic=doc.get("topic") or "",
        main_category=doc.get("mainCategory") or "",
        sub_category=doc.get("subCategory") or "",
        tags=doc.get("tags") or [],
        author=doc.get("author") or "",
        language=doc.get("language") or "ko",
        created_at=_isoformat(doc.get("createdAt")),
        updated_at=_isoformat(doc.get("updatedAt")),
    )


def _isoformat(value: object) -> str | None:
    # pymongo 는 UTC naive datetime 을 준다. Django 와 같은 형식(2025-03-04T11:12:09.369000)으로 저장한다.
    if isinstance(value, datetime):
        return value.isoformat()
    return value if isinstance(value, str) else None


def _category_lookup_pipeline(match: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """categories 컬렉션에서 mainCategoryId/subCategoryId 의 value 를 mainCategory/subCategory 로 붙인다."""
    pipeline: list[dict[str, Any]] = []
    if match:
        pipeline.append({"$match": match})
    pipeline += [
        {"$lookup": {"from": "categories", "localField": "mainCategoryId", "foreignField": "_id", "as": "_mainCat"}},
        {"$lookup": {"from": "categories", "localField": "subCategoryId", "foreignField": "_id", "as": "_subCat"}},
        {
            "$addFields": {
                "mainCategory": {"$ifNull": [{"$arrayElemAt": ["$_mainCat.value", 0]}, ""]},
                "subCategory": {"$ifNull": [{"$arrayElemAt": ["$_subCat.value", 0]}, ""]},
            }
        },
        {"$project": {"_mainCat": 0, "_subCat": 0}},
    ]
    return pipeline
