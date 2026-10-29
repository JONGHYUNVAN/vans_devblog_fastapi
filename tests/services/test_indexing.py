"""IndexingService 테스트."""

import pytest

from app.core.exceptions import NotFoundError
from app.models.post import Post
from app.schemas.indexing import SearchIndexPayload
from app.services.indexing import IndexingService
from tests.fakes import FakePostIndexRepository


def test_index_post_upserts_with_content_text():
    index = FakePostIndexRepository()
    payload = SearchIndexPayload.model_validate(
        {
            "post_id": "p1",
            "title": "제목",
            "content": {
                "type": "doc",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "본문"}]}],
            },
            "mainCategory": "backend",
            "createdAt": "2026-10-23T02:07:21.896Z",
        }
    )

    result = IndexingService(index).index_post(payload)

    assert (result.status, result.post_id) == ("indexed", "p1")
    post = index.docs["p1"]
    assert (post.content_text, post.main_category, post.language) == ("본문", "backend", "ko")
    assert post.created_at == "2026-10-23T02:07:21.896Z"


def test_remove_post_deletes_indexed_post():
    index = FakePostIndexRepository(docs={"p1": Post(post_id="p1")})

    result = IndexingService(index).remove_post("p1")

    assert (result.status, result.post_id) == ("deleted", "p1")
    assert index.docs == {}


def test_remove_post_raises_not_found_when_missing():
    with pytest.raises(NotFoundError):
        IndexingService(FakePostIndexRepository()).remove_post("missing")
