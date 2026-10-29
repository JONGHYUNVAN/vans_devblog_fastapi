"""PostRepository 테스트. Mongo 문서를 색인할 Post 로 바꾸는 규칙을 확인한다."""

from datetime import datetime
from typing import Any

from bson import ObjectId

from app.repositories.post import PostRepository


class FakeCollection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs
        self.pipelines: list[list[dict[str, Any]]] = []

    def aggregate(self, pipeline: list[dict[str, Any]], **kwargs: Any) -> list[dict[str, Any]]:
        self.pipelines.append(pipeline)
        return self.docs


class FakeDatabase:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.posts = FakeCollection(docs)


def test_iter_all_converts_mongo_document():
    post_id = ObjectId()
    doc = {
        "_id": post_id,
        "title": "제목",
        "content": {"type": "doc", "content": [{"type": "text", "text": "가"}, {"type": "text", "text": "나"}]},
        "tags": ["a"],
        "mainCategory": "backend",
        "subCategory": "",
        "createdAt": datetime(2025, 3, 4, 11, 12, 9, 369000),
        "authorEmail": "someone@example.com",
    }

    [post] = PostRepository(FakeDatabase([doc])).iter_all()

    assert post.post_id == str(post_id)
    assert (post.title, post.content_text, post.tags, post.main_category) == ("제목", "가 나", ["a"], "backend")
    assert (post.description, post.topic, post.author, post.language) == ("", "", "", "ko")
    assert post.created_at == "2025-03-04T11:12:09.369000"
    assert post.updated_at is None


def test_iter_updated_since_matches_created_or_updated():
    database = FakeDatabase([])
    since = datetime(2026, 10, 1)

    list(PostRepository(database).iter_updated_since(since))

    assert database.posts.pipelines[0][0] == {
        "$match": {"$or": [{"updatedAt": {"$gte": since}}, {"createdAt": {"$gte": since}}]}
    }
