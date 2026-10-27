"""MongoDB 클라이언트. Post BE 의 posts/categories 컬렉션을 읽기 전용으로 조회한다."""

import logging
from datetime import datetime
from typing import Any, Iterator, Optional

import certifi
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

from app.config import Settings, get_settings

logger = logging.getLogger("search")


def _connection_url(s: Settings) -> str:
    if s.mongodb_uri:
        return s.mongodb_uri
    if s.mongodb_username and s.mongodb_password:
        return (
            f"mongodb://{s.mongodb_username}:{s.mongodb_password}@{s.mongodb_host}:{s.mongodb_port}/"
            f"{s.mongodb_db_name}?authSource={s.mongodb_auth_source}"
            f"&directConnection={str(s.mongodb_direct_connection).lower()}"
        )
    return f"mongodb://{s.mongodb_host}:{s.mongodb_port}/{s.mongodb_db_name}"


def _uses_tls(url: str) -> bool:
    lowered = url.lower()
    return lowered.startswith("mongodb+srv://") or "tls=true" in lowered or "ssl=true" in lowered


class MongoDBClient:
    """posts 컬렉션 조회 클라이언트. 생성 시 ping 으로 연결을 확인하고, 실패하면 ConnectionFailure."""

    def __init__(self, timeout: Optional[int] = None, settings: Optional[Settings] = None):
        s = settings or get_settings()
        url = _connection_url(s)

        if timeout:
            ms = timeout * 1000
            options: dict[str, Any] = {
                "serverSelectionTimeoutMS": ms,
                "connectTimeoutMS": ms,
                "socketTimeoutMS": ms,
            }
        else:
            options = {
                "serverSelectionTimeoutMS": 10000,
                "connectTimeoutMS": 10000,
                "socketTimeoutMS": 20000,
            }

        # Windows 로컬의 OpenSSL CA 문제 대응. tlsCAFile 은 TLS 를 강제하므로 TLS 연결일 때만 지정한다.
        if _uses_tls(url):
            options["tlsCAFile"] = certifi.where()

        try:
            self.client = MongoClient(url, **options)
            self.database = self.client[s.mongodb_db_name]
            self.posts_collection = self.database.posts
            self.client.admin.command("ping")
        except Exception as e:
            logger.error("Failed to initialize MongoDB client: %s", e)
            raise ConnectionFailure(f"Cannot connect to MongoDB: {e}") from e

    def check_connection(self) -> bool:
        try:
            self.client.admin.command("ping")
            return True
        except Exception as e:
            logger.warning("MongoDB connection check failed: %s", e)
            return False

    def get_posts_count(self) -> int:
        try:
            return self.posts_collection.count_documents({})
        except Exception as e:
            logger.error("Failed to get posts count: %s", e)
            return 0

    def get_all_posts(self) -> Iterator[dict[str, Any]]:
        """모든 게시물을 mainCategory/subCategory value 를 붙여 반환한다."""
        yield from self.posts_collection.aggregate(self._category_lookup_pipeline())

    def get_posts_updated_since(self, since: datetime, batch_size: int = 100) -> Iterator[dict[str, Any]]:
        """since 이후 생성·수정된 게시물. 전체 동기화와 같은 카테고리 조회를 거친다.

        Django 버전은 여기서 find() 를 써서 카테고리가 빠졌고, 증분 동기화가 ES 의 카테고리를 빈 값으로 덮어썼다.
        """
        match = {"$or": [{"updatedAt": {"$gte": since}}, {"createdAt": {"$gte": since}}]}
        yield from self.posts_collection.aggregate(
            self._category_lookup_pipeline(match), batchSize=batch_size
        )

    def get_all_post_ids(self) -> set[str]:
        return {str(doc["_id"]) for doc in self.posts_collection.find({}, {"_id": 1})}

    @staticmethod
    def _category_lookup_pipeline(match: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
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

    def close(self) -> None:
        try:
            self.client.close()
        except Exception as e:
            logger.error("Failed to close MongoDB connection: %s", e)

    def __enter__(self) -> "MongoDBClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
