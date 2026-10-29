"""게시글 단건 색인·삭제. Post BE(NestJS)가 게시글을 저장·삭제할 때 내부 API 로 호출한다."""

import logging

from app.core.exceptions import NotFoundError
from app.models.post import Post, extract_content_text
from app.repositories.post_index import PostIndexRepository
from app.schemas.indexing import IndexingResult, SearchIndexPayload

logger = logging.getLogger(__name__)


class IndexingService:
    """posts 인덱스에 게시글 하나를 넣거나 뺀다."""

    def __init__(self, post_index_repository: PostIndexRepository) -> None:
        self._post_index_repository = post_index_repository

    def index_post(self, payload: SearchIndexPayload) -> IndexingResult:
        """게시글을 색인한다. 이미 있으면 통째로 바꾼다. ES 오류는 그대로 던진다."""
        post = Post(
            post_id=payload.post_id,
            title=payload.title,
            description=payload.description,
            content_text=extract_content_text(payload.content),
            topic=payload.topic,
            main_category=payload.main_category,
            sub_category=payload.sub_category,
            tags=payload.tags,
            author=payload.author,
            language=payload.language,
            created_at=payload.created_at,
            updated_at=payload.updated_at,
        )
        self._post_index_repository.upsert(post)
        logger.info("색인 완료: post_id=%s", payload.post_id)
        return IndexingResult(status="indexed", post_id=payload.post_id)

    def remove_post(self, post_id: str) -> IndexingResult:
        """게시글을 색인에서 지운다. 색인에 없으면 NotFoundError. 그 밖의 ES 오류는 그대로 던진다."""
        if not self._post_index_repository.delete(post_id):
            raise NotFoundError(f"Post not found in search index: {post_id}")
        logger.info("색인 삭제 완료: post_id=%s", post_id)
        return IndexingResult(status="deleted", post_id=post_id)
