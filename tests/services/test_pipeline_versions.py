"""PipelineVersionService: 최신=활성, 라운드 고정, 생성·되돌리기·bootstrap·내보내기/들여오기·edit-* (설계 09 문서 2·5절)."""

import json
from pathlib import Path

import pytest

from app.core.exceptions import NotFoundError
from app.models.pipeline import EXPOSITORY_FAMILY, content_hash_of
from app.repositories.pipeline_version import PipelineVersionRepository
from app.services.pipeline_versions import (
    NoPipelineVersionError,
    PipelineVersionService,
    build_baseline_version,
    export_version,
    meta_of,
    prompts_from_reference,
    read_directory,
)
from tests.factories import (
    EXPOSITORY_DIR,
    make_pipeline_body,
    make_pipeline_version,
    make_round,
    route_documents,
)
from tests.fakes import FakeAsyncDatabase, PipelineHarness

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).parents[2]
ROUTES_EXAMPLE = ROOT / "agents_design" / "routes.example.json"


def _service() -> PipelineVersionService:
    return PipelineVersionService(PipelineVersionRepository(FakeAsyncDatabase()), created_by="test")


async def test_latest_is_active_and_rounds_pin_their_own_version():
    service = _service()
    with pytest.raises(NoPipelineVersionError):
        await service.require_latest()
    with pytest.raises(NoPipelineVersionError):
        await service.for_round(make_round())

    first = await service.create(make_pipeline_body(), source="import", note="first")
    second = await service.create(make_pipeline_body(max_iterations=3), source="import", note="second")

    assert (first.version_id, second.version_id, second.parent_version_id) == ("v1", "v2", "v1")
    assert (await service.require_latest()).version_id == "v2"
    assert (await service.for_round(make_round())).version_id == "v2"  # 옛 라운드는 최신으로 (D-73)
    assert (await service.for_round(make_round(pipeline_version_id="v1"))).version_id == "v1"
    with pytest.raises(NoPipelineVersionError):
        await service.for_round(make_round(pipeline_version_id="v9"))
    assert (await service.parameters_or_default(make_round(pipeline_version_id="v9"))).max_iterations == 2


async def test_create_refuses_invalid_bodies_and_the_same_content_as_the_latest():
    service = _service()
    body = make_pipeline_body()
    await service.create(body, source="import")

    with pytest.raises(ValueError, match="same content as the latest version v1"):
        await service.create(make_pipeline_body(), source="import")
    broken = make_pipeline_body(max_iterations=0)
    with pytest.raises(ValueError, match="R-09"):
        await service.create(broken, source="import")
    assert [each.version_id for each in await service.list_versions()] == ["v1"]


async def test_reimporting_an_older_body_is_a_rollback_as_a_new_version():
    service = _service()
    original = await service.create(make_pipeline_body(), source="import")
    await service.create(make_pipeline_body(max_iterations=3), source="import")

    rolled_back = await service.create(make_pipeline_body(), source="import", note="rollback")

    assert (rolled_back.version_id, rolled_back.parent_version_id) == ("v3", "v2")
    assert rolled_back.content_hash == original.content_hash
    assert (await service.require_latest()).version_id == "v3"


async def test_bootstrap_reproduces_the_code_constants_once():
    service = _service()
    documents = json.loads(ROUTES_EXAMPLE.read_text(encoding="utf-8"))

    version = await service.bootstrap(route_documents=documents, prompts_dir="v4", note="첫 버전")

    assert (version.version_id, version.source, version.parent_version_id) == ("v1", "bootstrap", None)
    assert version.content_hash == content_hash_of(build_baseline_version("v4", documents))
    assert version.slots == {"A": "architect", "B": "systems_engineer", "C": "troubleshooter"}
    assert version.personas["troubleshooter"].max_level_without_evidence == 200
    assert set(version.prompts) == {"claude", "gemini", "grok", "haiku", "common"}
    assert "GLOSSARY" in version.routes and version.parameters.max_iterations == 2
    with pytest.raises(ValueError, match="already exist"):
        await service.bootstrap(route_documents=documents)


async def test_export_import_round_trip_keeps_the_hash_and_is_refused_as_a_duplicate(tmp_path):
    service = _service()
    version = await service.create(make_pipeline_body(), source="import", label="fixture")

    written = export_version(version, tmp_path / "out", meta=meta_of(version))

    assert (tmp_path / "out" / "pipeline.json") in written
    assert (
        (tmp_path / "out" / "prompts" / "common" / "write_draft.md")
        .read_text(encoding="utf-8")
        .startswith("[template:write_draft]")
    )
    body = read_directory(tmp_path / "out")
    assert content_hash_of(body) == version.content_hash
    with pytest.raises(ValueError, match="same content as the latest"):
        await service.import_directory(tmp_path / "out")
    (tmp_path / "out" / "prompts" / "common" / "write_draft.md").write_text(
        "[template:write_draft] 글 한 편을 한국어로 쓴다. 더 짧게. JSON", encoding="utf-8"
    )
    imported = await service.import_directory(tmp_path / "out", note="문구 조정")
    assert (imported.version_id, imported.source, imported.note) == ("v2", "import", "문구 조정")
    assert imported.prompts["common"]["write_draft"].endswith("더 짧게. JSON")


async def test_edit_prompt_param_and_routes_make_child_versions_of_the_latest():
    service = _service()
    await service.create(make_pipeline_body(), source="import")

    prompt = await service.edit_prompt(
        "common", "write_draft", "[template:write_draft] 글 한 편을 한국어로 쓴다. JSON!"
    )
    param = await service.edit_parameters({"publish.requires_approval": False, "max_iterations": 3})
    documents = route_documents(with_glossary=True)
    routes = await service.edit_routes(documents, note="용어 추출 추가")

    assert (prompt.version_id, prompt.parent_version_id, prompt.source) == ("v2", "v1", "edit-prompt")
    assert prompt.prompts["common"]["write_draft"].endswith("JSON!")
    assert (param.version_id, param.parameters.publish.requires_approval, param.parameters.max_iterations) == (
        "v3",
        False,
        3,
    )
    assert param.prompts == prompt.prompts
    assert (routes.version_id, routes.note) == ("v4", "용어 추출 추가")
    assert "GLOSSARY" in routes.routes and routes.parameters.max_iterations == 3
    with pytest.raises(ValueError, match="unknown parameter path"):
        await service.edit_parameters({"publish.nope": 1})
    with pytest.raises(ValueError, match="R-08"):  # 검증을 통과하지 못한 문구는 버전이 되지 않는다
        await service.edit_prompt("common", "glossary", "", note="빈 템플릿")


async def test_get_caches_immutable_versions():
    service = _service()
    created = await service.create(make_pipeline_body(), source="import")

    first = await service.get(created.version_id)
    assert await service.get(created.version_id) is first
    with pytest.raises(NotFoundError):
        await service.get("v9")


def test_prompts_from_reference_reads_export_and_repository_directories(tmp_path):
    version = make_pipeline_version()
    export_version(version, tmp_path / "exported")

    assert prompts_from_reference(str(tmp_path / "exported")) == version.prompts
    assert "claude" in prompts_from_reference("v4")
    assert prompts_from_reference("v1", {"v1": version.prompts}) == version.prompts


def test_committed_baseline_matches_the_code_constants_and_validates():
    """pipeline/baseline/ 은 기준 생성기의 결과와 해시가 같아야 한다(D-78). 코드 상수를 고치고 내보내기를 잊으면 실패한다."""
    documents = json.loads(ROUTES_EXAMPLE.read_text(encoding="utf-8"))
    generated = build_baseline_version("v4", documents)
    committed = read_directory(ROOT / "pipeline" / "baseline")
    meta = json.loads((ROOT / "pipeline" / "baseline" / "pipeline.json").read_text(encoding="utf-8"))["meta"]

    assert content_hash_of(committed) == content_hash_of(generated) == meta["content_hash"]


async def test_harness_round_keeps_its_version_after_a_new_one_lands():  # ㉑·㉒
    harness = PipelineHarness({"WRITE_DRAFT": [lambda _: '{"nope": 1}'] * 0})
    publishing_round = await harness.seed_round()
    newer = await harness.versions.edit_parameters({"max_iterations": 3})

    assert publishing_round.pipeline_version_id == "v1"
    assert (await harness.versions.for_round(publishing_round)).version_id == "v1"
    assert (await harness.versions.for_round(make_round("old"))).version_id == newer.version_id == "v2"


async def test_import_expository_adds_prompts_and_routes_and_survives_export_import(tmp_path):
    service = _service()
    first = await service.create(make_pipeline_body(), source="import")

    seeded = await service.import_expository(EXPOSITORY_DIR)

    assert seeded.parent_version_id == first.version_id
    assert len(seeded.prompts[EXPOSITORY_FAMILY]) == 9
    assert seeded.routes["EXPOSITORY:5_write"][0].max_output_tokens == 32000
    assert {key: value for key, value in seeded.routes.items() if not key.startswith("EXPOSITORY:")} == first.routes
    with pytest.raises(ValueError, match="same content"):
        await service.import_expository(EXPOSITORY_DIR)

    # 내보낸 디렉터리를 다시 읽어도 설명문 프롬프트가 남는다(가족 이름으로 걸러져 사라지지 않는다)
    export_version(seeded, tmp_path)
    assert content_hash_of(read_directory(tmp_path)) == seeded.content_hash

    changed = seeded.prompts[EXPOSITORY_FAMILY]["5_write"] + "\n"
    edited = await service.edit_prompt(EXPOSITORY_FAMILY, "5_write", changed)
    assert edited.parent_version_id == seeded.version_id


async def test_import_expository_raises_when_a_prompt_file_is_missing(tmp_path):
    service = _service()
    await service.create(make_pipeline_body(), source="import")
    with pytest.raises(FileNotFoundError):
        await service.import_expository(tmp_path)


async def test_edit_route_replaces_one_route_and_keeps_the_rest():
    service = _service()
    first = await service.create(make_pipeline_body(), source="import")
    candidates = [{"provider": "fake-b", "model_id": "fake-b-2", "effort": None, "max_output_tokens": 2000}]

    edited = await service.edit_route("FACT_TRANSFORM", candidates)

    assert (edited.version_id, edited.parent_version_id, edited.source) == ("v2", "v1", "edit-route")
    assert edited.note == "edit-route FACT_TRANSFORM"
    assert [(each.provider, each.model_id) for each in edited.routes["FACT_TRANSFORM"]] == [("fake-b", "fake-b-2")]
    rest = {key: value for key, value in edited.routes.items() if key != "FACT_TRANSFORM"}
    assert rest == {key: value for key, value in first.routes.items() if key != "FACT_TRANSFORM"}
    assert edited.prompts == first.prompts and edited.parameters == first.parameters


async def test_edit_route_also_edits_an_expository_stage_route():
    service = _service()
    await service.create(make_pipeline_body(), source="import")
    await service.import_expository(EXPOSITORY_DIR)
    candidates = [{"provider": "anthropic", "model_id": "claude-test", "effort": "high", "max_output_tokens": 32000}]

    edited = await service.edit_route("EXPOSITORY:5_write", candidates)

    assert edited.routes["EXPOSITORY:5_write"][0].model_id == "claude-test"


async def test_edit_route_rejects_unknown_routes_empty_and_unsupported_candidates():
    service = _service()
    await service.create(make_pipeline_body(), source="import")
    model = {"provider": "fake-a", "model_id": "fake-a-1", "max_output_tokens": 1000}

    with pytest.raises(ValueError, match="unknown route NOPE"):
        await service.edit_route("NOPE", [model])
    with pytest.raises(ValueError, match="at least one candidate"):
        await service.edit_route("FACT_TRANSFORM", [])
    with pytest.raises(ValueError, match="does not take temperature"):  # 지원표에 없는 조합
        await service.edit_route(
            "FACT_TRANSFORM",
            [{"provider": "anthropic", "model_id": "claude-test", "temperature": 0.5, "max_output_tokens": 1000}],
        )
    assert (await service.require_latest()).version_id == "v1"  # 거절된 시도는 버전을 만들지 않는다
