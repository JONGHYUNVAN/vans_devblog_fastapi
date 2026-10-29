"""MongoDB posts → ES posts 인덱스 동기화와 동기화 상태."""

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.cache import LAST_SYNC_TTL, cache
from app.models.post import Post
from app.repositories.post import PostRepository
from app.repositories.post_index import PostIndexRepository
from app.schemas.sync import SyncOptions, SyncResult, SyncStatus
from app.services.health import ReachabilityCheck

logger = logging.getLogger(__name__)

CHECK_TIMEOUT = 5
_LAST_SYNC_CACHE_KEY = "last_sync_time"


@dataclass
class _SyncCounts:
    processed: int = 0
    synced: int = 0
    skipped: int = 0
    errors: int = 0


class SyncService:
    """MongoDB 게시글을 posts 인덱스로 옮기고, ES 에만 남은 문서(고스트)를 지운다.

    상태 조회는 연결 자체를 보고하므로 헬스체크처럼 연결 확인 함수를 생성자로 받는다.
    결과는 실패해도 SyncResult 로 돌려준다 (Django 응답 계약). 그래서 이 서비스는 경계마다 예외를 잡는다.
    """

    def __init__(
        self,
        post_repository: PostRepository,
        post_index_repository: PostIndexRepository,
        is_es_reachable: ReachabilityCheck,
        is_mongo_reachable: ReachabilityCheck,
    ) -> None:
        self._post_repository = post_repository
        self._post_index_repository = post_index_repository
        self._is_es_reachable = is_es_reachable
        self._is_mongo_reachable = is_mongo_reachable

    def get_sync_status(self) -> SyncStatus:
        """두 저장소의 연결과 건수, 마지막 동기화 시각. 실패한 항목은 False·0 으로 보고한다."""
        is_mongo_up = self._is_mongo_reachable(CHECK_TIMEOUT)
        is_es_up = self._is_es_reachable(CHECK_TIMEOUT)
        total_posts = _count_or_zero(self._post_repository.count, "MongoDB") if is_mongo_up else 0
        # Django 버전은 인덱스를 지정하지 않아 인기 검색어 등 모든 인덱스의 문서를 셌고, 그래서 sync_needed 가 늘 true 였다.
        total_docs = _count_or_zero(self._post_index_repository.count, "Elasticsearch") if is_es_up else 0
        last_sync_at: datetime | None = cache.get(_LAST_SYNC_CACHE_KEY)
        sync_needed = total_posts != total_docs or last_sync_at is None or (datetime.now(UTC) - last_sync_at).days > 1
        return SyncStatus(
            mongodb_connected=is_mongo_up,
            elasticsearch_connected=is_es_up,
            total_posts_in_mongodb=total_posts,
            published_posts_in_mongodb=total_posts,
            total_docs_in_elasticsearch=total_docs,
            last_sync_time=last_sync_at,
            sync_needed=sync_needed,
        )

    def sync_posts(self, options: SyncOptions) -> SyncResult:
        """동기화를 실행한다. 게시글 한 건의 색인 실패는 errors 로 세고 계속한다.

        전체 동기화는 끝난 뒤 고스트 문서를 지운다. dry_run 이면 색인·삭제를 하지 않는다.
        """
        started = time.perf_counter()
        sync_type = "incremental" if options.incremental else "full"
        if not (self._is_mongo_reachable(CHECK_TIMEOUT) and self._is_es_reachable(CHECK_TIMEOUT)):
            return SyncResult(
                status="failed",
                type=sync_type,
                message="MongoDB 또는 Elasticsearch 연결에 실패했습니다.",
                execution_time=time.perf_counter() - started,
            )

        ghost_deleted: int | None = None
        try:
            if options.clear_existing and not options.dry_run:
                self._clear_index()
            if options.incremental:
                since = datetime.now(UTC) - timedelta(days=options.days)
                counts = self._sync(
                    self._post_repository.iter_updated_since(since, options.batch_size), options.dry_run
                )
            else:
                counts = self._sync(self._post_repository.iter_all(options.batch_size), options.dry_run)
                ghost_deleted = 0 if options.dry_run else self._delete_ghosts()
        except Exception as exc:  # 계약: 중간에 실패해도 500 + SyncResult(status=failed)
            # Django 버전은 message 에 예외 문자열을 넣었다. 접속 정보가 섞일 수 있어 로그에만 남긴다.
            logger.exception("동기화 실패: %s", exc)
            return SyncResult(
                status="failed",
                type=sync_type,
                message="동기화 중 오류가 발생했습니다.",
                execution_time=time.perf_counter() - started,
            )

        if not options.dry_run and counts.synced > 0:
            cache.set(_LAST_SYNC_CACHE_KEY, datetime.now(UTC), LAST_SYNC_TTL)
        if counts.errors == 0:
            status, message = "completed", "동기화가 성공적으로 완료되었습니다."
        elif counts.synced > 0:
            status, message = "partial", f"동기화가 부분적으로 완료되었습니다. ({counts.errors}개 오류)"
        else:
            status, message = "failed", "동기화에 실패했습니다."
        return SyncResult(
            status=status,
            type=sync_type,
            processed=counts.processed,
            synced=counts.synced,
            skipped=counts.skipped,
            errors=counts.errors,
            success_rate=counts.synced / counts.processed * 100 if counts.processed else 0.0,
            execution_time=time.perf_counter() - started,
            message=message,
            ghost_deleted=ghost_deleted,
        )

    def sync_all_posts(self) -> SyncResult:
        """기본 옵션(batch_size 50)으로 전체 동기화한다."""
        return self.sync_posts(SyncOptions(force_all=True))

    def _sync(self, posts: Iterator[Post], dry_run: bool) -> _SyncCounts:
        counts = _SyncCounts()
        for post in posts:
            counts.processed += 1
            if not post.title:
                logger.warning("제목이 없어 건너뜀: post_id=%s", post.post_id)
                counts.skipped += 1
                continue
            if dry_run:
                counts.synced += 1
                continue
            try:
                self._post_index_repository.upsert(post)
            except Exception as exc:  # 계약: 한 건 실패는 errors 로 센다 (일부 성공이면 status=partial)
                logger.error("색인 실패: post_id=%s: %s", post.post_id, exc)
                counts.errors += 1
            else:
                counts.synced += 1
        return counts

    def _clear_index(self) -> None:
        # Django 버전은 인덱스를 지정하지 않은 delete_by_query 로 인기 검색어까지 모든 인덱스의 문서를 지웠다.
        try:
            deleted = self._post_index_repository.delete_all()
        except Exception as exc:  # Django 동작 그대로: 비우기 실패는 경고만 하고 동기화를 계속한다
            logger.warning("기존 문서 삭제 실패: %s", exc)
        else:
            logger.info("기존 문서 %s건 삭제", deleted)

    def _delete_ghosts(self) -> int:
        """MongoDB 에 없는데 posts 인덱스에 남은 문서를 지우고 지운 수를 돌려준다."""
        try:
            ghost_ids = set(self._post_index_repository.list_ids()) - set(self._post_repository.list_ids())
        except Exception as exc:  # Django 동작 그대로: 정리 실패는 동기화 결과를 바꾸지 않는다
            logger.error("고스트 문서 조회 실패: %s", exc)
            return 0

        deleted = 0
        for post_id in ghost_ids:
            try:
                if self._post_index_repository.delete(post_id):
                    deleted += 1
            except Exception as exc:  # 한 건 실패는 건너뛰고 나머지를 지운다
                logger.warning("고스트 문서 삭제 실패: post_id=%s: %s", post_id, exc)
        if ghost_ids:
            logger.info("고스트 문서 %s/%s건 삭제", deleted, len(ghost_ids))
        return deleted


def _count_or_zero(count: Callable[[], int], store: str) -> int:
    try:
        return count()
    except Exception as exc:  # 계약: 상태 조회는 실패하지 않는다. 건수를 못 세면 0 으로 보고한다
        logger.warning("%s 건수 조회 실패: %s", store, exc)
        return 0
