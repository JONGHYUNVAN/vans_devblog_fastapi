"""용어 사전·제작 기록·글 참조 리포지토리 (FakeAsyncCollection 위에서 조회·저장 조건과 unique 다중키 인덱스를 본다)."""

from datetime import UTC, datetime

import pytest
from bson import ObjectId

from app.models.enums import GlossaryStatus
from app.models.glossary import GlossaryHeadword, GlossarySense
from app.repositories.glossary import GlossaryRepository
from app.repositories.post_ref import PostRefRepository
from tests.fakes import FakeAsyncDatabase

pytestmark = pytest.mark.anyio

T = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)


def _headword(key: str, *lookup: str) -> GlossaryHeadword:
    return GlossaryHeadword(headword_key=key, term_ko=key, lookup_keys=list(lookup), created_at=T, updated_at=T)


async def test_a_lookup_key_belongs_to_one_headword_only():
    repo = GlossaryRepository(FakeAsyncDatabase())
    await repo.ensure_indexes()

    assert await repo.add_headword_if_missing(_headword("stateless", "stateless", "무상태"))
    assert not await repo.add_headword_if_missing(_headword("stateless2", "무상태"))  # 별칭 충돌
    assert not await repo.add_headword_if_missing(_headword("stateless", "other"))  # 같은 키
    assert (await repo.find_headword_by_lookup("무상태")).headword_key == "stateless"
    assert await repo.find_headword_by_lookup("nope") is None
    assert [each.headword_key for each in await repo.list_headwords([GlossaryStatus.PROPOSED])] == ["stateless"]


async def test_sense_keys_are_sequential_and_post_ids_accumulate_once():
    repo = GlossaryRepository(FakeAsyncDatabase())

    assert [await repo.next_sense_key() for _ in range(3)] == ["s00001", "s00002", "s00003"]
    sense = GlossarySense(
        sense_key="s00001", headword_key="h", domains=["db"], definition="d", created_at=T, updated_at=T
    )
    await repo.add_sense(sense)
    await repo.add_post_to_senses(["s00001", "s00001"], "p1")
    await repo.add_post_to_senses(["s00001"], "p1")
    await repo.add_post_to_senses([], "p9")

    assert (await repo.get_sense("s00001")).post_ids == ["p1"]
    assert [s.sense_key for s in await repo.list_senses_by_headwords(["h"], [GlossaryStatus.PROPOSED])] == ["s00001"]
    assert await repo.list_senses_by_keys([]) == [] and await repo.list_senses_by_headwords([]) == []


async def test_post_refs_read_titles_and_main_category_values():
    database = FakeAsyncDatabase()
    post_id, category_id = ObjectId(), ObjectId()
    await database["categories"].insert_one({"_id": category_id, "value": "nestjs", "label": "NestJS"})
    await database["posts"].insert_one({"_id": post_id, "title": "무상태 서버", "mainCategoryId": category_id})
    await database["posts"].insert_one({"_id": ObjectId(), "title": "카테고리 없음"})

    refs = await PostRefRepository(database).list_refs([str(post_id), "not-an-object-id", str(ObjectId())])

    assert refs == {str(post_id): refs[str(post_id)]}
    assert (refs[str(post_id)].title, refs[str(post_id)].main_category) == ("무상태 서버", "nestjs")
    assert await PostRefRepository(database).list_refs(["nope"]) == {}
