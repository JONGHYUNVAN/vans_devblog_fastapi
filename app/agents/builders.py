"""도메인 상태 → 프롬프트 입력 모델과 메시지 (설계 05 문서 2절, 04 문서 3절·7절).

리뷰·중재 입력에는 저자·모델·페르소나 식별 정보를 넣지 않는다(블라인드): 주제는 rationale 을 뺀 TopicForReviewer,
지적은 슬롯 필드를 뺀 ReviewItemForReviewer 다. 작성자 입력에는 리뷰어의 모델명을 넣지 않는다.
메시지 렌더링은 결정론적이다: 시스템 메시지는 템플릿, 사용자 메시지는 입력 모델의 JSON 이다.
"""

from string import Template

from pydantic import BaseModel

from app.agents.contracts import (
    ArbitrateInput,
    ArbitrateSearchInput,
    ArbitrationForAuthor,
    AssessLevelInput,
    AvoidTopic,
    CitationRef,
    DomainOption,
    DraftOutput,
    FactCheckInput,
    FactTransformInput,
    FinalReadInput,
    GlossaryInput,
    KnownHeadword,
    LinkStatus,
    PolishToneInput,
    ProposeTopicInput,
    ReviewForAuthor,
    ReviewInput,
    ReviewItemForReviewer,
    ReviseDraftInput,
    SpotCheckInput,
    StructureRules,
    TonePreservationRule,
    TopicForReviewer,
    WriteDraftInput,
)
from app.agents.personas import PersonaProfile, get_persona
from app.agents.prompts.registry import load_template, prompt_family
from app.models.agent_call import ChatMessage
from app.models.enums import AgentSlot, ArbitrationOutcome, ReviewLens, ReviewScope, RevisionCause
from app.models.glossary import DOMAINS
from app.models.level import DEFAULT_LEVEL_CRITERIA, LevelCriteria
from app.models.model_binding import CallModel
from app.models.post_draft import DraftVersion, PostDraft, ReviewItem
from app.models.publishing_round import PublishingRound
from app.models.sections import CODE_ANCHOR_PATTERN, changed_sections, find_anchors, section_text
from app.models.topic import TopicRegistryEntry

_TEMPLATE_BY_INPUT: dict[type[BaseModel], str] = {
    ProposeTopicInput: "propose_topic",
    WriteDraftInput: "write_draft",
    ReviseDraftInput: "revise_draft",
    ReviewInput: "review_full",
    SpotCheckInput: "review_spot_check",
    ArbitrateInput: "arbitrate",
    ArbitrateSearchInput: "arbitrate_search",
    FactCheckInput: "fact_check",
    FactTransformInput: "fact_transform",
    FinalReadInput: "final_read",
    AssessLevelInput: "assess_level",
    PolishToneInput: "polish_tone",
    GlossaryInput: "glossary",
}


def persona_of(publishing_round: PublishingRound, slot: AgentSlot) -> PersonaProfile:
    return get_persona(publishing_round.slot_bindings[slot].persona_id)


def level_criteria_of(publishing_round: PublishingRound) -> LevelCriteria:
    return publishing_round.routing.level_criteria if publishing_round.routing else DEFAULT_LEVEL_CRITERIA


def reviewer_label(draft: PostDraft, lens: ReviewLens) -> str:
    """배정 순서로 "리뷰어 1"·"리뷰어 2". 모델·페르소나 이름 대신 쓴다."""
    for index, assignment in enumerate(draft.reviewer_assignments, start=1):
        if assignment.primary_lens == lens:
            return f"리뷰어 {index}"
    raise ValueError(f"lens {lens} has no reviewer in draft {draft.draft_id}")


def build_propose_topic_input(
    publishing_round: PublishingRound,
    slot: AgentSlot,
    *,
    attempt_number: int,
    avoid_entries: list[TopicRegistryEntry],
    conflict_feedback: str | None,
) -> ProposeTopicInput:
    """avoid_entries 는 레지스트리의 최근 항목이다. 재제안이면 이번 라운드에서 이미 잠긴 주제를 더한다."""
    avoid = [AvoidTopic(title=entry.title, keywords=entry.keywords) for entry in avoid_entries]
    attempt = publishing_round.latest_attempt
    if attempt is not None:
        avoid += [
            AvoidTopic(title=locked.proposal.title, keywords=locked.proposal.keywords)
            for locked_slot, locked in attempt.locked.items()
            if locked_slot != slot
        ]
    return ProposeTopicInput(
        persona=persona_of(publishing_round, slot),
        theme_keyword=publishing_round.theme_keyword,
        avoid_topics=avoid,
        attempt_number=attempt_number,
        conflict_feedback=conflict_feedback,
        categories=publishing_round.categories,
        level_criteria=level_criteria_of(publishing_round),
        evidence=publishing_round.evidence,
    )


def build_write_draft_input(
    draft: PostDraft, publishing_round: PublishingRound, rules: StructureRules
) -> WriteDraftInput:
    """작성자 입력에는 페르소나가 없다(v4). 수준 기준표를 넣어 target_level 이 요구하는 깊이를 작성자가 본다."""
    return WriteDraftInput(
        topic=draft.topic,
        structure_rules=rules,
        level_criteria=level_criteria_of(publishing_round),
        evidence=publishing_round.evidence,
    )


def build_revise_draft_input(
    draft: PostDraft, publishing_round: PublishingRound, rules: StructureRules, *, max_iterations: int
) -> ReviseDraftInput:
    """previous 는 직전 제출 버전(게이트 실패본 포함), reviews 는 마지막 리뷰 완료 버전의 미결 차단 항목 기준이다 (04 문서 3절).

    DRAFTING 에서 부른다. revision_cause 가 HUMAN 이거나 게이트 재작성 대상 버전이 HUMAN 원인이면 human_instruction 을 넣는다.
    """
    previous = draft.current_version
    reviews: list[ReviewForAuthor] = []
    arbitration_results: list[ArbitrationForAuthor] = []
    if draft.last_reviewed_version is not None:
        reviewed = draft.last_reviewed_version
        blocking = draft.open_blocking_items(reviewed)
        blocking_ids = {item.item_id for item in blocking}
        reviewed_reviews = draft.reviews_of(reviewed)
        is_full = any(review.scope == ReviewScope.FULL for review in reviewed_reviews)
        for review in reviewed_reviews:
            items = [item for item in blocking if draft.reviewer_of(item) == review.lens]
            if is_full:
                items += [item for item in review.items if not item.severity.is_blocking]
            # 아직 열려 있는 항목만. 중재에서 작성자가 이긴 항목(UPHOLD_AUTHOR)은 종결이라 미해소 노트에서 뺀다
            unresolved = {
                check.item_id: check.note
                for check in review.item_checks
                if not check.resolved and check.item_id in blocking_ids
            }
            reviews.append(
                ReviewForAuthor(
                    reviewer_label=reviewer_label(draft, review.lens),
                    lens=review.lens,
                    items=items,
                    unresolved_notes=unresolved,
                    overall_feedback=review.overall_feedback,
                )
            )
        arbitration_results = [
            ArbitrationForAuthor(
                item_id=arbitration.item_id,
                outcome=arbitration.outcome,
                reasoning=arbitration.reasoning,
                must_accept=arbitration.outcome == ArbitrationOutcome.UPHOLD_REVIEWER,
            )
            for arbitration in draft.arbitrations
            # 열린 항목의 판정(UPHOLD_REVIEWER, 수용 필수)과 이번 리뷰 버전의 판정 전부(UPHOLD_AUTHOR 는 종결 안내)
            if arbitration.item_id in blocking_ids or arbitration.target_version == reviewed
        ]
    human_revision = draft.revision_cause == RevisionCause.HUMAN or (
        draft.revision_cause == RevisionCause.GATE and previous.origin_cause == RevisionCause.HUMAN
    )
    return ReviseDraftInput(
        topic=draft.topic,
        structure_rules=rules,
        previous=DraftOutput(
            title=previous.title, content_markdown=previous.content_markdown, summary=previous.summary
        ),
        reviews=reviews,
        arbitration_results=arbitration_results,
        validation_errors=list(previous.validation_errors),
        human_instruction=draft.human_instruction if human_revision else None,
        iteration=draft.iteration,
        max_iterations=max_iterations,
        evidence=publishing_round.evidence,
    )


def _tone_rule() -> TonePreservationRule:
    """v4: 어조 마커가 없다. 리뷰어는 must_not_flag 만 지킨다."""
    return TonePreservationRule()


def build_review_input(draft: PostDraft, publishing_round: PublishingRound, lens: ReviewLens) -> ReviewInput:
    """FULL 리뷰. 저자 슬롯·모델·페르소나 이름과 주제의 rationale 은 넣지 않는다."""
    assignment = draft.assignment_of(lens)
    return ReviewInput(
        lens=assignment.primary_lens,
        secondary_lens=assignment.secondary_lens,
        topic=TopicForReviewer.from_topic(draft.topic),
        content_markdown=draft.current_version.content_markdown,
        level_criteria=level_criteria_of(publishing_round),
        tone_preservation=_tone_rule(),
    )


def build_spot_check_input(draft: PostDraft, publishing_round: PublishingRound, lens: ReviewLens) -> SpotCheckInput:
    """SPOT_CHECK. my_previous_items 는 직전 리뷰 완료 버전의 미결 차단 항목 중 이 리뷰어가 낸 것이다."""
    if draft.last_reviewed_version is None:
        raise ValueError(f"draft {draft.draft_id} has no reviewed version to spot-check against")
    assignment = draft.assignment_of(lens)
    previous = draft.versions[draft.last_reviewed_version - 1]
    current = draft.current_version
    my_items = draft.previous_items_of(lens)
    my_ids = {item.item_id for item in my_items}
    return SpotCheckInput(
        lens=assignment.primary_lens,
        secondary_lens=assignment.secondary_lens,
        topic=TopicForReviewer.from_topic(draft.topic),
        previous_content=previous.content_markdown,
        current_content=current.content_markdown,
        my_previous_items=[ReviewItemForReviewer.from_item(item) for item in my_items],
        resolutions=[resolution for resolution in current.resolutions if resolution.item_id in my_ids],
        tone_preservation=_tone_rule(),
    )


def build_fact_check_input(
    draft: PostDraft, publishing_round: PublishingRound, lens: ReviewLens, link_status: list[LinkStatus]
) -> FactCheckInput:
    """사실 확인 검색 호출. SPOT_CHECK 는 수정으로 바뀐 섹션의 주장만 다시 뽑는다 (D-09 의 범위를 사실 확인에만 넓힌다)."""
    current = draft.current_version
    my_items = draft.previous_items_of(lens)
    my_ids = {item.item_id for item in my_items}
    changed: list[str] = []
    if draft.last_reviewed_version is not None:
        previous = draft.versions[draft.last_reviewed_version - 1]
        changed = changed_sections(previous.content_markdown, current.content_markdown)
    return FactCheckInput(
        scope=draft.current_review_scope.value,
        lens=lens,
        topic=TopicForReviewer.from_topic(draft.topic),
        content_markdown=current.content_markdown,
        changed_section_ids=changed,
        known_claims=[claim.claim for review in draft.reviews for claim in review.claims],
        my_previous_items=[ReviewItemForReviewer.from_item(item) for item in my_items],
        resolutions=[resolution for resolution in current.resolutions if resolution.item_id in my_ids],
        link_status=link_status,
        tone_preservation=_tone_rule(),
    )


def build_fact_transform_input(
    draft: PostDraft, lens: ReviewLens, verification_text: str, citations: list[CitationRef]
) -> FactTransformInput:
    return FactTransformInput(
        scope=draft.current_review_scope.value,
        lens=lens,
        section_ids=find_anchors(draft.current_version.content_markdown),
        expected_item_ids=[item.item_id for item in draft.previous_items_of(lens)],
        verification_text=verification_text,
        citations=citations,
    )


def _scope_declaration(markdown: str) -> str:
    """본문의 범위 선언: tradeoff, 없으면 conclusion, 둘 다 없으면(수준 100) 마지막 섹션. 코드 앵커는 섹션이 아니다."""
    for section_id in ("tradeoff", "conclusion"):
        text = section_text(markdown, section_id)
        if text:
            return text
    sections = [anchor for anchor in find_anchors(markdown) if not CODE_ANCHOR_PATTERN.match(anchor)]
    return (section_text(markdown, sections[-1]) if sections else None) or ""


def build_arbitrate_input(draft: PostDraft, item: ReviewItem, search_evidence: str | None = None) -> ArbitrateInput:
    """교착 항목 하나. 전후 버전의 해당 섹션과 글의 범위를 넘긴다. 섹션이 없으면 빈 문자열이다."""
    raised_version = next(
        review.target_version for review in draft.reviews if review.review_id == item.source_review_id
    )
    current = draft.current_version
    resolution = current.resolution_for(item.item_id)
    if resolution is None or not resolution.reason:
        raise ValueError(f"item {item.item_id} was not rejected with a reason in v{current.version_number}")
    section_after_id = resolution.section_id_after or item.section_id
    note = next(
        (
            check.note
            for review in draft.reviews_of(current.version_number)
            for check in review.item_checks
            if check.item_id == item.item_id and not check.resolved
        ),
        "",
    )
    return ArbitrateInput(
        item=ReviewItemForReviewer.from_item(item),
        section_before=section_text(draft.versions[raised_version - 1].content_markdown, item.section_id) or "",
        section_after=section_text(current.content_markdown, section_after_id) or "",
        dispute_kind=resolution.status,
        author_rejection_reason=resolution.reason,
        reviewer_unresolved_note=note,
        core_mechanism=draft.topic.core_mechanism,
        out_of_scope=draft.topic.out_of_scope,
        scope_declaration=_scope_declaration(current.content_markdown),
        search_evidence=search_evidence,
    )


def build_arbitrate_search_input(draft: PostDraft, item: ReviewItem) -> ArbitrateSearchInput:
    current = draft.current_version
    resolution = current.resolution_for(item.item_id)
    if resolution is None or not resolution.reason:
        raise ValueError(f"item {item.item_id} was not rejected with a reason in v{current.version_number}")
    return ArbitrateSearchInput(
        item=ReviewItemForReviewer.from_item(item),
        section_after=section_text(current.content_markdown, resolution.section_id_after or item.section_id) or "",
        author_rejection_reason=resolution.reason,
    )


def build_final_read_input(draft: PostDraft) -> FinalReadInput:
    version = draft.current_version
    return FinalReadInput(
        title=version.title, topic=TopicForReviewer.from_topic(draft.topic), content_markdown=version.content_markdown
    )


def build_assess_level_input(draft: PostDraft, publishing_round: PublishingRound) -> AssessLevelInput:
    """목표 수준은 넣지 않는다. 평가자는 본문과 기준표만 본다."""
    version = draft.current_version
    return AssessLevelInput(
        title=version.title,
        content_markdown=version.content_markdown,
        level_criteria=level_criteria_of(publishing_round),
    )


def build_glossary_input(
    draft: PostDraft,
    publishing_round: PublishingRound,
    version: DraftVersion,
    known_headwords: list[KnownHeadword],
    *,
    min_terms: int,
    max_terms: int,
) -> GlossaryInput:
    """용어 추출 입력 (설계 08 문서 2.3절). version 은 발행본이다(추출 단계에서는 현재 버전, 보충에서는 발행된 버전)."""
    topic = draft.topic
    main_label = next(
        (
            category.main_label
            for category in publishing_round.categories
            if category.main_category_id == topic.main_category_id
        ),
        topic.main_category_id,
    )
    return GlossaryInput(
        title=version.title,
        audience=topic.audience,
        prerequisites=topic.prerequisites,
        main_category=main_label,
        keywords=list(topic.keywords),
        content_markdown=version.content_markdown,
        section_ids=find_anchors(version.content_markdown),
        known_headwords=known_headwords,
        allowed_domains=[DomainOption(slug=slug, label=label) for slug, label in DOMAINS.items()],
        min_terms=min_terms,
        max_terms=max_terms,
    )


def render_messages(model: BaseModel, prompt_version: str, call_model: CallModel) -> list[ChatMessage]:
    """시스템 메시지는 입력 종류와 프롬프트 가족(호출 모델로 정한다)의 템플릿, 사용자 메시지는 입력 JSON.

    치환은 propose_topic 의 $writer_bias 뿐이다. 입력에 persona 가 없는 작성·수정 호출에서 v3 이전 템플릿의 $display_name 은 빈 글이 된다.
    """
    template = Template(load_template(_TEMPLATE_BY_INPUT[type(model)], prompt_version, prompt_family(call_model)))
    persona = getattr(model, "persona", None)
    system = template.safe_substitute(
        display_name=persona.display_name if persona else "",
        writer_bias=persona.writer_bias if persona else "",
    )
    return [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content="입력(JSON):\n" + model.model_dump_json(indent=2)),
    ]
