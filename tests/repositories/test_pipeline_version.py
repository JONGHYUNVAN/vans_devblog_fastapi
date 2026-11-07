"""agent_pipeline_versions: insert 만, 순번 발급, 최신 조회 (설계 09 문서 2.1절)."""

import pytest
from pymongo.errors import DuplicateKeyError

from app.repositories.pipeline_version import PipelineVersionRepository
from tests.factories import make_pipeline_version
from tests.fakes import FakeAsyncDatabase

pytestmark = pytest.mark.anyio


async def test_latest_is_the_highest_sequence_and_none_when_empty():
    repository = PipelineVersionRepository(FakeAsyncDatabase())
    await repository.ensure_indexes()
    assert await repository.latest() is None

    await repository.add(make_pipeline_version("v2", 2))
    await repository.add(make_pipeline_version("v1", 1))

    latest = await repository.latest()
    assert latest is not None and latest.version_id == "v2"
    assert [each.version_id for each in await repository.list_all()] == ["v1", "v2"]
    assert (await repository.get("v1")).seq == 1 and await repository.get("v9") is None


async def test_sequence_counter_and_same_id_refusal():
    repository = PipelineVersionRepository(FakeAsyncDatabase())

    assert [await repository.next_seq() for _ in range(3)] == [1, 2, 3]
    await repository.add(make_pipeline_version("v1", 1))
    with pytest.raises(DuplicateKeyError):
        await repository.add(make_pipeline_version("v1", 1))


async def test_find_by_hash_lists_versions_with_the_same_content():
    repository = PipelineVersionRepository(FakeAsyncDatabase())
    original = make_pipeline_version("v1", 1)
    await repository.add(original)
    await repository.add(make_pipeline_version("v2", 2, max_iterations=3))
    await repository.add(make_pipeline_version("v3", 3, parent_version_id="v2"))  # v1 과 같은 본문(되돌리기)

    assert [each.version_id for each in await repository.find_by_hash(original.content_hash)] == ["v1", "v3"]


def test_repository_has_no_update_or_delete():
    """append-only (D-71). 고치려면 자식 버전을 만든다."""
    assert not any(
        name.startswith(("update", "replace", "delete", "remove")) for name in dir(PipelineVersionRepository)
    )
