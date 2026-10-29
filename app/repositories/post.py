"""MongoDB posts 컬렉션 조회. Post BE(NestJS)가 소유한 컬렉션이라 읽기만 한다."""

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from pymongo.database import Database


class PostRepository:
    """posts 컬렉션 조회. mainCategory/subCategory 는 categories 컬렉션에서 value 를 찾아 붙인다."""

    def __init__(self, database: Database) -> None:
        self._posts = database.posts

    def count(self) -> int:
        return self._posts.count_documents({})

    def iter_all(self) -> Iterator[dict[str, Any]]:
        """모든 게시물을 mainCategory/subCategory value 를 붙여 순회한다."""
        yield from self._posts.aggregate(_category_lookup_pipeline())

    def iter_updated_since(self, since: datetime, batch_size: int = 100) -> Iterator[dict[str, Any]]:
        """since 이후 생성·수정된 게시물. 전체 동기화와 같은 카테고리 조회를 거친다.

        Django 버전은 여기서 find() 를 써서 카테고리가 빠졌고, 증분 동기화가 ES 의 카테고리를 빈 값으로 덮어썼다.
        """
        match = {"$or": [{"updatedAt": {"$gte": since}}, {"createdAt": {"$gte": since}}]}
        yield from self._posts.aggregate(_category_lookup_pipeline(match), batchSize=batch_size)

    def list_ids(self) -> list[str]:
        return [str(doc["_id"]) for doc in self._posts.find({}, {"_id": 1})]


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
