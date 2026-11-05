"""라우트 스냅샷·후보 선택·라운드 생성 전 검사 테스트. 순수 함수라 DB 없이 돈다."""

import json
from pathlib import Path

import pytest

from app.models.enums import AgentSlot, ReviewLens
from app.models.model_binding import CallModel
from app.models.routing import (
    RoutingSnapshot,
    build_snapshot,
    candidate_errors,
    raised_output_limit,
    select_arbiter,
    select_independent,
    select_reviewers,
    select_reviser,
    validate_snapshot,
)
from tests.factories import FAKE_VENDORS, fake_model, route_documents

A = AgentSlot.A
VENDORS = list(FAKE_VENDORS.values())


def _model(provider: str, model_id: str = "m", **overrides) -> CallModel:
    return CallModel(provider=provider, model_id=model_id, max_output_tokens=1000, **overrides)


def _snapshot(providers=VENDORS, documents=None):
    return build_snapshot(documents if documents is not None else route_documents(), providers)


def _without(route_id: str) -> list[dict]:
    return [document for document in route_documents() if document["_id"] != route_id]


def _replacing(route_id: str, *candidates: dict) -> list[dict]:
    return [*_without(route_id), {"_id": route_id, "candidates": list(candidates)}]


# ---------- 선택 ----------


def test_reviewers_avoid_the_writing_vendor_and_prefer_distinct_vendors():
    chosen = select_reviewers(_snapshot(), "fake-a", ReviewLens)

    assert {lens: model.provider for lens, model in chosen.items()} == {
        ReviewLens.LOGIC_READABILITY: "fake-b",
        ReviewLens.FACT_STRUCTURE: "fake-c",
        ReviewLens.CRITICAL_PRACTICAL: "fake-b",  # 벤더 셋이면 작성 벤더를 뺀 둘이 렌즈 셋을 맡아 겹친다
    }


def test_reviewer_selection_is_deterministic_for_the_same_snapshot():
    snapshot = _snapshot()

    assert select_reviewers(snapshot, "fake-b", ReviewLens) == select_reviewers(snapshot, "fake-b", ReviewLens)


def test_arbiter_is_neither_the_writing_vendor_nor_the_reviewer_vendor():
    assert select_arbiter(_snapshot(), "fake-a", "fake-b").provider == "fake-c"
    assert select_arbiter(_snapshot(), "fake-c", "fake-a").provider == "fake-b"


def test_final_read_uses_a_vendor_other_than_the_writer():
    assert select_independent(_snapshot(), "FINAL_READ", "fake-a").provider == "fake-b"


def test_reviser_stays_with_the_writing_vendor_even_on_another_model():
    documents = _replacing(
        "REVISE_DRAFT:architect",
        {"provider": "fake-b", "model_id": "b", "max_output_tokens": 10},
        {"provider": "fake-a", "model_id": "a-cheap", "max_output_tokens": 10},
    )

    assert select_reviser(_snapshot(documents=documents), "architect", "fake-a").model_id == "a-cheap"


# ---------- 스냅샷·검사 ----------


def test_snapshot_drops_candidates_of_vendors_without_a_key():
    snapshot = _snapshot(providers=["fake-a", "fake-c"])

    assert {model.provider for model in snapshot.candidates("REVIEW:LOGIC_READABILITY")} == {"fake-a", "fake-c"}


def test_missing_route_fails_validation():
    with pytest.raises(ValueError, match="FINAL_READ"):
        validate_snapshot(_snapshot(documents=_without("FINAL_READ")))


def test_missing_slots_document_fails_the_snapshot():
    with pytest.raises(ValueError, match="SLOTS"):
        _snapshot(documents=_without("SLOTS"))


def test_validation_fails_when_a_lens_has_no_vendor_outside_the_writer():
    documents = _replacing("REVIEW:FACT_STRUCTURE", fake_model("fake-a"))

    with pytest.raises(ValueError, match="REVIEW:FACT_STRUCTURE"):
        validate_snapshot(_snapshot(documents=documents))


def test_validation_fails_when_no_arbiter_is_left():
    documents = _replacing("ARBITRATE", fake_model("fake-a"), fake_model("fake-b"))

    with pytest.raises(ValueError, match="ARBITRATE"):
        validate_snapshot(_snapshot(documents=documents))


def test_validation_fails_when_writing_vendor_is_missing_from_revise_candidates():
    documents = _replacing("REVISE_DRAFT:architect", fake_model("fake-b"))

    with pytest.raises(ValueError, match="REVISE_DRAFT:architect"):
        validate_snapshot(_snapshot(documents=documents))


def test_later_write_candidates_do_not_make_validation_stricter():
    # WRITE_DRAFT 는 첫 후보만 실제로 쓴다. fake-b 로 쓰는 조합은 일어나지 않으니 fake-b 의 수정 후보가 없어도 된다
    documents = _replacing("WRITE_DRAFT:architect", fake_model("fake-a"), fake_model("fake-b"))

    validate_snapshot(_snapshot(documents=documents))


def test_relaxed_snapshot_of_an_old_round_keeps_going_with_one_vendor():
    model = _model("only")
    routes = {"REVIEW:LOGIC_READABILITY": [model], "ARBITRATE": [model], "FINAL_READ": [model]}
    snapshot = RoutingSnapshot(routes=routes, slot_personas={}, relaxed=True)

    assert select_reviewers(snapshot, "only", [ReviewLens.LOGIC_READABILITY])[ReviewLens.LOGIC_READABILITY] == model
    assert select_arbiter(snapshot, "only", "only") == model
    assert select_independent(snapshot, "FINAL_READ", "only") == model
    with pytest.raises(ValueError, match="FINAL_READ"):
        select_independent(snapshot.model_copy(update={"relaxed": False}), "FINAL_READ", "only")


def test_validation_passes_on_the_default_documents():
    validate_snapshot(_snapshot())


def test_writer_override_goes_first_and_changes_the_persona_of_the_slot():
    snapshot = build_snapshot(route_documents(), VENDORS, writer_overrides={A: ("architect", _model("fake-b", "ab"))})

    assert snapshot.candidates("WRITE_DRAFT:architect")[0].model_id == "ab"
    assert snapshot.slot_personas[A] == "architect"


# ---------- 지원표 ----------


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (_model("anthropic", "claude-opus-5-5", temperature=0.5), "temperature"),
        (_model("anthropic", "claude-opus-5-5", effort="max"), None),
        (_model("google", "gemini-4-argon", temperature=0.5), "temperature"),
        (_model("google", "gemini-4-argon", effort="xhigh"), "effort"),
        (_model("google", "gemini-4-argon", effort="high"), None),
        (_model("google", "gemini-3.8-flash", temperature=1.0), None),
        (_model("xai", "grok-4.7", effort="max"), "effort"),
        (_model("xai", "grok-4.7", effort="xhigh", temperature=0.7), None),
        (_model("unknown", "x", effort="max", temperature=2.0), None),
    ],
)
def test_candidate_errors_follow_the_support_matrix(candidate, expected):
    errors = candidate_errors("R", candidate)

    assert (expected in errors[0]) if expected else errors == []


def test_argon_output_limit_over_the_non_streaming_cap_is_rejected():
    candidate = CallModel(provider="google", model_id="gemini-4-argon", max_output_tokens=40_000)

    assert "max_output_tokens" in candidate_errors("R", candidate)[0]


def test_unsupported_candidate_fails_the_snapshot():
    documents = _replacing(
        "FINAL_READ",
        {"provider": "anthropic", "model_id": "claude-opus-5-5", "temperature": 0.2, "max_output_tokens": 10},
    )

    with pytest.raises(ValueError, match="does not take temperature"):
        _snapshot(documents=documents)


def test_raised_output_limit_doubles_up_to_the_vendor_cap():
    assert raised_output_limit(CallModel(provider="fake", model_id="m", max_output_tokens=1000)) == 2000
    argon = CallModel(provider="google", model_id="gemini-4-argon", max_output_tokens=20_000)
    assert raised_output_limit(argon) == 32_768
    assert raised_output_limit(argon.model_copy(update={"max_output_tokens": 32_768})) is None


def test_routes_example_file_passes_the_same_checks_as_round_creation():
    example = Path(__file__).parents[2] / "agents_design" / "routes.example.json"

    snapshot = build_snapshot(json.loads(example.read_text(encoding="utf-8")), ["anthropic", "google", "xai"])

    validate_snapshot(snapshot)
    # 작성 벤더마다 리뷰·중재를 모두 고를 수 있어야 한다
    for persona_id, writer_vendor in (
        ("architect", "anthropic"),
        ("systems_engineer", "google"),
        ("troubleshooter", "xai"),
    ):
        assert snapshot.candidates(f"WRITE_DRAFT:{persona_id}")[0].provider == writer_vendor
        reviewers = select_reviewers(snapshot, writer_vendor, ReviewLens)
        assert writer_vendor not in {model.provider for model in reviewers.values()}
