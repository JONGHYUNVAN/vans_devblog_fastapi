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
from app.services.publishing import build_payload, build_peer_review_note
from tests.factories import (
    arbitrate_output,
    draft_output,
    resolution,
    review_output,
    revision_output,
    spot_check_output,
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
    draft.record_approval("사람")
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
    assert note.startswith("---\n\n## 이 글은 어떻게 만들어졌나")
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
    assert "이 글은 어떻게 만들어졌나" in confirmed.publication.peer_review_note
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
    assert (
        await harness.topics.get(draft.draft_id)
    ).status == TopicRegistryStatus.RESERVED  # 정리가 RELEASED 로 맞춘다


async def test_note_counts_items_and_escapes_rejected_comments():
    harness = PipelineHarness(
        {
            "WRITE_DRAFT": [draft_output()],
            "REVIEW_FULL": [review_output("CRITICAL", "MINOR"), review_output("CRITICAL")],  # B, C 순
            "REVISE_DRAFT": lambda call_input: revision_output(
                [
                    resolution(item["item_id"], "REJECTED_WITH_REASON", reason="<b>근거</b>\n여러 줄 *강조* [링크](x)")
                    if ":C:" in item["item_id"]  # C 의 CRITICAL 만 기각 → 교착 → 중재
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
    publishing_round = await harness.round_service.get(draft.round_id)
    for review in draft.reviews:
        for item in review.items:
            object.__setattr__(item, "comment", "<script>alert(1)</script>\n두 번째 줄 # 제목처럼")

    note = build_peer_review_note(draft, publishing_round)

    assert "- 버전: v1 → v2" in note
    assert "- 발행을 막은 사항: CRITICAL 2, MAJOR 0" in note
    assert "- 기록만 남긴 사항: MINOR 1, NITPICK 0" in note
    assert "- 작성자가 수용한 사항: 2건 / 근거를 들어 유지한 사항: 1건" in note
    assert "- 의견이 갈려 제3자 판정을 받은 사항: 1건" in note
    assert "- 모델: fake-1 작성, fake-1·fake-1 리뷰" in note
    assert "> **실용주의 트러블슈터의 지적**: \\<script\\>alert\\(1\\)\\</script\\> 두 번째 줄 \\# 제목처럼\\" in note
    assert "> **작성자의 입장**: \\<b\\>근거\\</b\\> 여러 줄 \\*강조\\* \\[링크\\]\\(x\\)\\" in note
    assert "> **판정 (시스템 엔지니어)**: 작성자 입장을 인정 — 판단 근거" in note
    payload = build_payload(draft, note)
    quote = payload.content["content"][-1]
    assert quote["type"] == "blockquote"
    texts = [node.get("text", "") for node in quote["content"][0]["content"]]
    assert ": <script>alert(1)</script> 두 번째 줄 # 제목처럼" in texts  # 태그가 텍스트로 남고 줄바꿈은 공백이다
    assert sum(node["type"] == "hardBreak" for node in quote["content"][0]["content"]) == 2


async def test_round_closes_when_the_last_draft_publishes():
    harness = PipelineHarness(_fast_track())
    await _approved(harness)
    for slot in (B, C):
        sibling = await harness.draft(slot)
        await harness.set_status(sibling, PostStatus.REJECTED)

    draft = await harness.drive(A)

    assert draft.status == PostStatus.PUBLISHED
    assert (await harness.rounds.get(draft.round_id)).status == RoundStatus.COMPLETED


def test_note_escapes_caret_so_regex_text_is_not_taken_for_a_footnote():
    from app.services.publishing import _plain

    text = _plain("정규식 [^a-z]+ 를 쓰라")

    document = markdown_to_tiptap(f"> **지적**: {text}")
    [paragraph] = document["content"][0]["content"]
    assert paragraph["content"][-1]["text"] == ": 정규식 [^a-z]+ 를 쓰라"
