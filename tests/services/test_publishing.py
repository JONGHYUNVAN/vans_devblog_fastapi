"""PublishingService 테스트 (설계 06 문서 6절 publishing 행). 가짜 LLM·FakePostBE 와 FakeAsyncDatabase 위의 실제 리포지토리로 돈다."""

import pytest

from app.core.exceptions import PublishFailedError, PublishUncertainError
from app.models.enums import (
    AgentSlot,
    EscalationDecisionKind,
    EscalationReason,
    PostStatus,
    RoundStatus,
    TopicRegistryStatus,
)
from app.models.post_draft import PostDraft
from app.models.tiptap import markdown_to_tiptap
from app.services.pipeline_views import build_draft_public
from app.services.publishing import build_payload, build_publish_note
from tests.factories import (
    add_reviews,
    arbitrate_output,
    draft_output,
    make_draft,
    make_round,
    resolution,
    review_output,
    revision_output,
    spot_check_output,
    write_next_version,
)
from tests.fakes import FakePostBE, PipelineHarness

pytestmark = pytest.mark.anyio

A, B, C = AgentSlot.A, AgentSlot.B, AgentSlot.C


def _fast_track() -> dict:
    return {"WRITE_DRAFT": [draft_output()], "REVIEW_FULL": review_output()}


async def _approved(harness: PipelineHarness) -> PostDraft:
    """fast-track 으로 READY_TO_PUBLISH 에 닿은 뒤 승인한 글."""
    await harness.seed_round()
    draft = await harness.drive(A)
    assert draft.status == PostStatus.READY_TO_PUBLISH
    draft.record_approval("사람", requires_approval=True)
    await harness.drafts.save(draft)
    return draft


async def test_publish_maps_create_post_dto_and_marks_registry_published():
    harness = PipelineHarness(_fast_track(), publisher=FakePostBE(["post-42"]))
    await _approved(harness)

    draft = await harness.drive(A)

    assert draft.status == PostStatus.PUBLISHED
    assert draft.publication is not None
    assert (draft.publication.external_post_id, draft.publication.final_version, draft.publication.approved_by) == (
        "post-42",
        1,
        "사람",
    )
    [payload] = harness.publisher.payloads
    dto = payload.to_dto()
    assert set(dto) == {
        "title",
        "content",
        "description",
        "tags",
        "topic",
        "mainCategoryId",
        "subCategoryId",
        "language",
    }
    assert (dto["title"], dto["description"], dto["tags"], dto["topic"]) == (
        "제목",
        "요약",
        draft.topic.keywords,
        draft.topic.title,
    )
    assert (dto["mainCategoryId"], dto["subCategoryId"], dto["language"]) == ("main-1", "sub-1", "ko")
    note = draft.publication.peer_review_note
    assert note.startswith("---\n\n## AI Information\n\n- 작성: ")
    assert dto["content"] == markdown_to_tiptap(draft.current_version.content_markdown + "\n\n" + note)
    assert dto["content"]["content"][-1]["type"] == "bulletList"  # 기각 항목이 없어 노트는 목록으로 끝난다
    entry = await harness.topics.get(draft.draft_id)
    assert entry is not None and entry.status == TopicRegistryStatus.PUBLISHED
    assert entry.published_at == draft.publication.published_at


async def test_crash_after_post_be_call_escalates_publish_uncertain_without_calling_again():
    def die_after_call(payload):
        raise RuntimeError("process died after the call reached Post BE")

    harness = PipelineHarness(_fast_track(), publisher=FakePostBE([die_after_call]))
    await _approved(harness)

    with pytest.raises(RuntimeError):
        await harness.drive(A)
    draft = await harness.draft(A)
    assert draft.status == PostStatus.READY_TO_PUBLISH and draft.has_uncertain_publication

    draft = await harness.drive(A)

    assert (draft.status, draft.escalation_reason) == (PostStatus.ESCALATED, EscalationReason.PUBLISH_UNCERTAIN)
    assert len(harness.publisher.payloads) == 1
    assert draft.publication_intent is not None and draft.publication_intent.failed_at is None


async def test_publish_uses_the_account_of_the_provider_that_wrote_the_draft():
    harness = PipelineHarness(_fast_track(), publisher=FakePostBE(["post-1"]))
    draft = await _approved(harness)
    publishing_round = await harness.rounds.get("round-1")
    assert publishing_round is not None
    for slot, provider in ((A, "anthropic"), (B, "google"), (C, "xai")):
        publishing_round.slot_bindings[slot] = publishing_round.slot_bindings[slot].model_copy(
            update={"provider": provider}
        )

    await harness.publishing.publish(draft, publishing_round)

    assert harness.publisher.providers == ["anthropic"]


async def test_missing_provider_account_stops_before_saving_the_publication_intent():
    harness = PipelineHarness(_fast_track(), publisher=FakePostBE(accounts={"other"}))
    draft = await _approved(harness)
    publishing_round = await harness.rounds.get("round-1")
    assert publishing_round is not None

    with pytest.raises(ValueError, match="not configured"):
        await harness.publishing.publish(draft, publishing_round)

    stored = await harness.drafts.get(draft.draft_id)
    assert stored is not None and stored.publication_intent is None
    assert harness.publisher.payloads == []


async def test_failed_attempts_retry_until_the_limit_then_escalate_publish_failed():
    harness = PipelineHarness(_fast_track(), publisher=FakePostBE([PublishFailedError("401")] * 3))
    await _approved(harness)

    draft = await harness.drive(A)

    assert (draft.status, draft.escalation_reason) == (PostStatus.ESCALATED, EscalationReason.PUBLISH_FAILED)
    assert (draft.publish_attempts, len(harness.publisher.payloads)) == (3, 3)
    assert draft.publication_intent is not None
    assert (draft.publication_intent.attempt, draft.publication_intent.failed_at is not None) == (3, True)


async def test_uncertain_result_escalates_immediately():
    harness = PipelineHarness(_fast_track(), publisher=FakePostBE([PublishUncertainError("503")]))
    await _approved(harness)

    draft = await harness.drive(A)

    assert (draft.status, draft.escalation_reason) == (PostStatus.ESCALATED, EscalationReason.PUBLISH_UNCERTAIN)
    assert (draft.publish_attempts, len(harness.publisher.payloads)) == (0, 1)


async def test_confirm_published_closes_without_a_call_and_publish_as_is_resets_counters():
    harness = PipelineHarness(_fast_track(), publisher=FakePostBE([PublishUncertainError("503"), "post-7"]))
    await _approved(harness)
    draft = await harness.drive(A)
    assert draft.escalation_reason == EscalationReason.PUBLISH_UNCERTAIN

    confirmed = await harness.decisions.resolve_escalation(
        draft.draft_id, EscalationDecisionKind.CONFIRM_PUBLISHED, decided_by="사람", external_post_id="post-found"
    )

    assert confirmed.status == PostStatus.PUBLISHED
    assert confirmed.publication is not None
    assert (confirmed.publication.external_post_id, confirmed.publication.final_version) == ("post-found", 1)
    assert "## AI Information" in confirmed.publication.peer_review_note
    assert len(harness.publisher.payloads) == 1
    assert (await harness.topics.get(draft.draft_id)).status == TopicRegistryStatus.PUBLISHED

    # 같은 상황에서 PUBLISH_AS_IS 를 고르면 카운터·의도를 지우고 다시 발행한다
    other = PipelineHarness(_fast_track(), publisher=FakePostBE([PublishUncertainError("503"), "post-7"]))
    await _approved(other)
    draft = await other.drive(A)
    resumed = await other.decisions.resolve_escalation(
        draft.draft_id, EscalationDecisionKind.PUBLISH_AS_IS, decided_by="사람"
    )
    assert (resumed.status, resumed.publish_attempts, resumed.publication_intent, resumed.approved_by) == (
        PostStatus.READY_TO_PUBLISH,
        0,
        None,
        "사람",
    )
    published = await other.drive(A)
    assert published.status == PostStatus.PUBLISHED and published.publication.external_post_id == "post-7"


async def test_rejected_before_the_result_is_saved_keeps_external_post_id_on_the_intent():
    harness = PipelineHarness(_fast_track())
    await _approved(harness)

    def abort_during_call(payload):
        doc = harness.database.agent_drafts.docs[0]
        doc["status"] = PostStatus.REJECTED.value
        doc["abort_reason"] = "MANUAL"
        doc["revision"] += 1
        return "post-9"

    harness.publisher.outcomes = [abort_during_call]

    draft = await harness.drive(A)

    assert (draft.status, draft.publication) == (PostStatus.REJECTED, None)
    assert draft.publication_intent is not None and draft.publication_intent.external_post_id == "post-9"
    assert build_draft_public(draft).external_post_id == "post-9"  # 회수 대상 id 가 요약 응답에 보인다
    assert (
        await harness.topics.get(draft.draft_id)
    ).status == TopicRegistryStatus.RESERVED  # 정리가 RELEASED 로 맞춘다


async def test_note_says_who_did_what_and_nothing_from_the_review_history():
    """글 끝 표기는 "역할: 모델" 줄뿐이다(D-80·D-87). 지적·기각 사유·중재 판정 같은 LLM 텍스트와 건수는 제작 기록 몫이다."""
    harness = PipelineHarness(
        {
            "WRITE_DRAFT": [draft_output()],
            "REVIEW_FULL": [review_output("CRITICAL", "MINOR"), review_output("CRITICAL")],  # 논리, 비판 렌즈 순
            "REVISE_DRAFT": lambda call_input: revision_output(
                [
                    resolution(item["item_id"], "REJECTED_WITH_REASON", reason="기각 사유 문장")
                    if ":CRITICAL_PRACTICAL:" in item["item_id"]  # 비판 렌즈의 CRITICAL 만 기각 → 교착 → 중재
                    else resolution(item["item_id"])
                    for review in call_input["reviews"]
                    for item in review["items"]
                ]
            ),
            "SPOT_CHECK": [spot_check_output(resolved=True), spot_check_output(resolved=False)],
            "ARBITRATE": [arbitrate_output("UPHOLD_AUTHOR")],
        }
    )
    await harness.seed_round()
    draft = await harness.drive(A)
    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert draft.arbitrations
    publishing_round = await harness.round_service.get(draft.round_id)

    note = build_publish_note(draft, publishing_round)

    # 같은 모델이 두 렌즈를 맡으면 한 번만 적는다. 작성 모델이 직접 고친 수정은 줄이 없다
    assert note == (
        "---\n\n## AI Information\n\n"
        "- 작성: fake-a-1\n"
        "- 리뷰: fake-b-1(논리·가독성, 비판·실무), fake-c-1(사실·구조)\n"
        "- 중재: fake-c-1\n"
    )
    assert "기각 사유 문장" not in note
    payload = build_payload(draft, note)
    rule, heading, roles = payload.content["content"][-3:]
    assert (rule["type"], heading["type"], heading["attrs"]["level"]) == ("horizontalRule", "heading", 2)
    assert heading["content"][0]["text"] == "AI Information"
    assert roles["type"] == "bulletList" and len(roles["content"]) == 3


async def test_round_closes_when_the_last_draft_publishes():
    harness = PipelineHarness(_fast_track())
    await _approved(harness)
    for slot in (B, C):
        sibling = await harness.draft(slot)
        await harness.set_status(sibling, PostStatus.REJECTED)

    draft = await harness.drive(A)

    assert draft.status == PostStatus.PUBLISHED
    assert (await harness.rounds.get(draft.round_id)).status == RoundStatus.COMPLETED


# ---------- 수준 라벨과 모델 표기 ----------


async def test_level_is_sent_only_when_the_setting_is_on():
    harness = PipelineHarness(_fast_track(), publisher=FakePostBE(["post-1"]))
    await _approved(harness)
    await harness.drive(A)
    assert "level" not in harness.publisher.payloads[0].to_dto()

    enabled = PipelineHarness(_fast_track(), publisher=FakePostBE(["post-1"]), publish_post_level=True)
    await _approved(enabled)
    await enabled.drive(A)
    assert enabled.publisher.payloads[0].to_dto()["level"] == 200


async def test_human_path_post_has_no_level_to_send():
    harness = PipelineHarness(_fast_track(), publisher=FakePostBE(["post-1"]), publish_post_level=True)
    await _approved(harness)
    draft = await harness.draft(A)
    draft.level_assessment = None
    await harness.drafts.save(draft)

    await harness.drive(A)

    assert "level" not in harness.publisher.payloads[0].to_dto()


async def test_note_lists_only_models_that_changed_the_body():
    harness = PipelineHarness(_fast_track())
    await harness.seed_round()
    draft = await harness.drive(A)
    publishing_round = await harness.round_service.get(draft.round_id)

    note = build_publish_note(draft, publishing_round)

    assert note.endswith("- 작성: fake-a-1\n- 리뷰: fake-b-1(논리·가독성, 비판·실무), fake-c-1(사실·구조)\n")


async def test_note_for_a_reviser_on_another_model_says_so():
    harness = PipelineHarness(
        {
            "WRITE_DRAFT": [draft_output()],
            "REVIEW_FULL": [review_output("CRITICAL"), review_output()],
            "REVISE_DRAFT": lambda call_input: revision_output(
                [resolution(item["item_id"]) for review in call_input["reviews"] for item in review["items"]]
            ),
            "SPOT_CHECK": spot_check_output(resolved=True),
        }
    )
    await harness.seed_round()
    publishing_round = await harness.round_service.get("round-1")
    routes = publishing_round.routing.routes
    routes["REVISE_DRAFT:architect"] = [
        routes["REVISE_DRAFT:architect"][0].model_copy(update={"model_id": "fake-a-cheap"})
    ]
    await harness.rounds.save(publishing_round)

    draft = await harness.drive(A)
    note = build_publish_note(draft, await harness.round_service.get("round-1"))

    assert "- 수정: fake-a-cheap\n" in note


async def test_note_of_an_old_draft_reads_models_from_slot_bindings():
    publishing_round = make_round()
    draft = make_draft(A)
    write_next_version(draft)
    add_reviews(draft)

    note = build_publish_note(draft, publishing_round)

    assert note.endswith("- 작성: fake-1\n- 리뷰: fake-1(사실·구조, 비판·실무)\n")


async def test_out_of_scope_reason_stays_out_of_the_note():
    harness = PipelineHarness(
        {
            "WRITE_DRAFT": [draft_output()],
            "REVIEW_FULL": [review_output("MAJOR"), review_output()],
            "REVISE_DRAFT": lambda call_input: revision_output(
                [
                    resolution(item["item_id"], "OUT_OF_SCOPE", reason="배포 전환은 범위 밖이다")
                    for review in call_input["reviews"]
                    for item in review["items"]
                ]
            ),
            "SPOT_CHECK": spot_check_output(resolved=True),
        }
    )
    await harness.seed_round()
    draft = await harness.drive(A)

    note = build_publish_note(draft, await harness.round_service.get(draft.round_id))

    assert "배포 전환은 범위 밖이다" not in note  # 작성자 입장은 제작 기록이 보인다
    assert note.startswith("---\n\n## AI Information\n\n- 작성: fake-a-1\n- 리뷰: ")
