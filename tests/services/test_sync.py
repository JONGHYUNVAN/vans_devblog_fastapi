"""SyncService 테스트."""

from datetime import UTC, datetime, timedelta

from app.models.post import Post
from app.schemas.sync import SyncOptions
from app.services.sync import SyncService
from tests.fakes import FakePostIndexRepository, FakePostRepository


def _service(
    posts: FakePostRepository,
    index: FakePostIndexRepository,
    is_es_up: bool = True,
    is_mongo_up: bool = True,
) -> SyncService:
    return SyncService(
        post_repository=posts,
        post_index_repository=index,
        is_es_reachable=lambda _: is_es_up,
        is_mongo_reachable=lambda _: is_mongo_up,
    )


def test_sync_posts_counts_skips_errors_and_deletes_ghosts():
    posts = FakePostRepository(
        [Post(post_id="p1", title="글1"), Post(post_id="p2", title=""), Post(post_id="p3", title="글3")]
    )
    index = FakePostIndexRepository(docs={"ghost": Post(post_id="ghost")}, fail_ids={"p3"})

    result = _service(posts, index).sync_posts(SyncOptions())

    assert (result.processed, result.synced, result.skipped, result.errors) == (3, 1, 1, 1)
    assert result.status == "partial"
    assert result.message == "동기화가 부분적으로 완료되었습니다. (1개 오류)"
    assert round(result.success_rate, 2) == 33.33
    assert result.ghost_deleted == 1
    assert set(index.docs) == {"p1"}


def test_sync_posts_failed_when_every_post_fails():
    posts = FakePostRepository([Post(post_id="p1", title="글1")])

    result = _service(posts, FakePostIndexRepository(fail_ids={"p1"})).sync_posts(SyncOptions())

    assert (result.status, result.message) == ("failed", "동기화에 실패했습니다.")


def test_sync_posts_dry_run_writes_nothing():
    posts = FakePostRepository([Post(post_id="p1", title="글1")])
    index = FakePostIndexRepository(docs={"ghost": Post(post_id="ghost")})
    service = _service(posts, index)

    result = service.sync_posts(SyncOptions(dry_run=True, clear_existing=True))

    assert (result.status, result.synced, result.ghost_deleted) == ("completed", 1, 0)
    assert set(index.docs) == {"ghost"}
    assert service.get_sync_status().last_sync_time is None


def test_sync_posts_incremental_reads_recent_posts_without_ghost_cleanup():
    posts = FakePostRepository([Post(post_id="p1", title="글1")])
    index = FakePostIndexRepository(docs={"ghost": Post(post_id="ghost")})

    result = _service(posts, index).sync_posts(SyncOptions(incremental=True, days=3))

    assert (result.type, result.synced, result.ghost_deleted) == ("incremental", 1, None)
    assert "ghost" in index.docs
    assert abs(posts.since_calls[0] - (datetime.now(UTC) - timedelta(days=3))) < timedelta(seconds=5)


def test_sync_posts_clears_index_first_when_clear_existing():
    posts = FakePostRepository([Post(post_id="p1", title="글1")])
    index = FakePostIndexRepository(docs={"old": Post(post_id="old")})

    result = _service(posts, index).sync_posts(SyncOptions(clear_existing=True))

    assert result.ghost_deleted == 0
    assert set(index.docs) == {"p1"}


def test_sync_posts_failed_when_store_unreachable():
    result = _service(FakePostRepository(), FakePostIndexRepository(), is_mongo_up=False).sync_posts(SyncOptions())

    assert (result.status, result.processed, result.ghost_deleted) == ("failed", 0, None)
    assert result.message == "MongoDB 또는 Elasticsearch 연결에 실패했습니다."


def test_sync_posts_failed_when_mongo_read_raises():
    posts = FakePostRepository(error=RuntimeError("mongodb+srv://user:pw@host"))

    result = _service(posts, FakePostIndexRepository()).sync_posts(SyncOptions())

    assert (result.status, result.message) == ("failed", "동기화 중 오류가 발생했습니다.")


def test_get_sync_status_needed_until_counts_match_after_sync():
    posts = FakePostRepository([Post(post_id="p1", title="글1")])
    service = _service(posts, FakePostIndexRepository())

    before = service.get_sync_status()
    service.sync_posts(SyncOptions())
    after = service.get_sync_status()

    assert (before.total_posts_in_mongodb, before.total_docs_in_elasticsearch, before.sync_needed) == (1, 0, True)
    assert before.last_sync_time is None
    assert (after.total_docs_in_elasticsearch, after.sync_needed) == (1, False)
    assert after.last_sync_time is not None


def test_get_sync_status_reports_zero_when_count_fails():
    index = FakePostIndexRepository(error=RuntimeError("es down"))

    status = _service(FakePostRepository(), index, is_mongo_up=False).get_sync_status()

    assert (status.mongodb_connected, status.elasticsearch_connected) == (False, True)
    assert status.total_docs_in_elasticsearch == 0
