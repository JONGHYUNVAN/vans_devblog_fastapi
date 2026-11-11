"""expository_runs 리포지토리: 없는 실행은 None, upsert 는 만들고 덮어쓴다."""

import pytest

from app.models.expository import ExpositoryRun
from app.repositories.expository_run import ExpositoryRunRepository
from tests.fakes import FakeAsyncDatabase

pytestmark = pytest.mark.anyio


async def test_get_returns_none_for_an_unknown_run():
    assert await ExpositoryRunRepository(FakeAsyncDatabase()).get("expository-none") is None


async def test_upsert_creates_then_overwrites_the_run_document():
    repository = ExpositoryRunRepository(FakeAsyncDatabase())
    run = ExpositoryRun(run_id="expository-1")
    await repository.upsert(run)

    run.stage_outputs["2_outline"] = {"core_message": "m"}
    run.stage_prompts["2_outline"] = "prompt"
    run.checked_markdown = "body"
    await repository.upsert(run)

    stored = await repository.get("expository-1")
    assert stored is not None
    assert (stored.stage_outputs, stored.stage_prompts, stored.checked_markdown) == (
        {"2_outline": {"core_message": "m"}},
        {"2_outline": "prompt"},
        "body",
    )
