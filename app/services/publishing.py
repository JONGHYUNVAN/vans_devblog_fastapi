"""READY_TO_PUBLISH 글의 발행: 요약 노트, Tiptap 변환·CreatePostDto 매핑, 발행 의도, Post BE 호출, 결과 반영 (설계 04 문서 8절).

DraftPipelineService 의 DraftPublisher 계약을 구현한다. 글·라운드 재확인(8절 3단계: READY_TO_PUBLISH·IN_PROGRESS)과
승인 대기(1단계)는 글 코루틴이 매 걸음 다시 읽어 검사하므로 여기서는 되풀이하지 않는다.
"""

import asyncio
import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from string import Template

from app.agents.contracts import StructureRules
from app.clients.publisher.base import PostCreatePayload, Publisher
from app.core.config import Settings
from app.core.exceptions import ConcurrentUpdateError, InvalidTransitionError, PublishFailedError, PublishUncertainError
from app.models.enums import (
    EscalationReason,
    IssueSeverity,
    ResolutionStatus,
    ReviewLens,
    RevisionCause,
    TopicRegistryStatus,
)
from app.models.pipeline import PublishNoteParameters
from app.models.post_draft import ItemResolution, PostDraft, Publication, PublicationIntent
from app.models.publishing_round import PublishingRound
from app.models.tiptap import markdown_to_tiptap
from app.repositories.post_draft import PostDraftRepository
from app.repositories.topic_registry import TopicRegistryRepository
from app.services.derived_records import DerivedRecordsWriter
from app.services.pipeline_versions import PipelineVersionService

logger = logging.getLogger(__name__)

_BACKOFF_BASE_S = 2.0  # 결과 확정 실패 뒤 다음 호출까지 (03 문서 28a행)
_LANGUAGE = StructureRules.model_fields["language"].default  # "ko"
_SEVERITY_ORDER = {severity: index for index, severity in enumerate(IssueSeverity)}
# 노트에 넣는 LLM 텍스트는 문단 텍스트로만 들어가야 한다. ASCII 구두점을 백슬래시로 이스케이프하면 CommonMark 가 글자로 읽는다
# ^ 도 넣는다: 정규식 얘기([^a-z])가 각주 패턴([^n])에 걸려 변환이 거부되지 않게
_MARKDOWN_SPECIALS = re.compile(r"([\\`*_{}\[\]()#+\-.!<>&|~^])")


def _fill(template: str, **values: object) -> str:
    return Template(template).safe_substitute(**{key: str(value) for key, value in values.items()})


def build_peer_review_note(
    draft: PostDraft, publishing_round: PublishingRound, note: PublishNoteParameters | None = None
) -> str:
    """피어 리뷰 요약 노트 마크다운 (05 문서 5절 템플릿). LLM 호출 없음. 발행 본문 뒤에 붙여 함께 Tiptap 으로 변환한다.

    문구는 라운드 버전 문서의 조절 값(publish.note)이고 줄의 구조는 여기 코드다. 다듬은 버전은 버전 수에 넣지 않는다.
    모델은 본문에 영향을 준 것만 적는다.
    """
    note = note if note is not None else PublishNoteParameters()
    version = draft.authored_publishable_version or draft.publishable_version or draft.current_version
    lens_labels = ", ".join(_lens_label(note, assignment.primary_lens) for assignment in draft.reviewer_assignments)
    items = [item for review in draft.reviews for item in review.items]
    severity_counts = Counter(item.severity for item in items)
    final_resolutions = _final_resolutions(draft)
    accepted = sum(1 for resolution in final_resolutions.values() if resolution.status == ResolutionStatus.ACCEPTED)
    rejected = sorted(
        (item for item in items if _is_rejected(final_resolutions.get(item.item_id))),
        key=lambda item: _SEVERITY_ORDER[item.severity],
    )
    arbitration_by_item = {arbitration.item_id: arbitration for arbitration in draft.arbitrations}

    lines = [
        "---",
        "",
        note.heading,
        "",
        _fill(note.intro, writer_model=_writer_model(draft, publishing_round), lens_labels=lens_labels),
        "",
        _fill(note.versions_line, final_version=version.version_number),
        _fill(
            note.blocking_line,
            critical=severity_counts[IssueSeverity.CRITICAL],
            major=severity_counts[IssueSeverity.MAJOR],
        ),
        _fill(
            note.non_blocking_line,
            minor=severity_counts[IssueSeverity.MINOR],
            nitpick=severity_counts[IssueSeverity.NITPICK],
        ),
        _fill(note.resolutions_line, accepted=accepted, rejected=len(rejected)),
        _fill(note.models_line, models=_model_line(draft, publishing_round, note)),
    ]
    if draft.arbitrations:
        lines.append(_fill(note.arbitrations_line, count=len(arbitration_by_item)))
    for item in rejected[: note.max_rejected_shown]:
        resolution = final_resolutions[item.item_id]
        quote = [
            _fill(note.reviewer_quote, lens=_lens_label(note, draft.reviewer_of(item)), comment=_plain(item.comment)),
            _fill(note.author_quote, reason=_plain(resolution.reason or "")),
        ]
        arbitration = arbitration_by_item.get(item.item_id)
        if arbitration is not None:
            outcome = note.outcome_text.get(arbitration.outcome.value, arbitration.outcome.value)
            quote.append(_fill(note.verdict_quote, outcome=outcome, reasoning=_plain(arbitration.reasoning)))
        lines += ["", "\\\n".join(quote)]  # 줄 끝 백슬래시는 hardBreak (04 문서 8절 변환표)
    return "\n".join(lines) + "\n"


def _lens_label(note: PublishNoteParameters, lens: ReviewLens) -> str:
    return note.lens_labels.get(lens.value, lens.value)


def build_payload(draft: PostDraft, note: str, *, include_level: bool = False) -> PostCreatePayload:
    """발행본(마지막 passed_validation 버전) + 노트 → CreatePostDto (04 문서 8절 매핑표).

    사람 경로로 발행하는 글은 평가가 없어 level 이 비고 키도 빠진다.
    """
    version = draft.publishable_version
    if version is None:
        raise InvalidTransitionError(f"draft {draft.draft_id} has no version that passed validation")
    return PostCreatePayload(
        title=version.title,
        content=markdown_to_tiptap(f"{version.content_markdown.rstrip()}\n\n{note}"),
        description=version.summary,
        tags=list(draft.topic.keywords),
        topic=draft.topic.title,
        main_category_id=draft.topic.main_category_id,
        sub_category_id=draft.topic.sub_category_id,
        language=_LANGUAGE,
        level=draft.level_assessment.level if include_level and draft.level_assessment else None,
    )


class PublishingService:
    """글 코루틴이 READY_TO_PUBLISH 에서 부른다. 돌아오면 글은 PUBLISHED, READY_TO_PUBLISH(재호출 대상) 또는 ESCALATED 다.

    sleep 은 결과 확정 실패 뒤 백오프용이고 테스트가 바꾼다.
    """

    def __init__(
        self,
        *,
        draft_repository: PostDraftRepository,
        topic_registry_repository: TopicRegistryRepository,
        publisher: Publisher,
        settings: Settings,
        version_service: PipelineVersionService,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        derived_writer: DerivedRecordsWriter | None = None,
    ) -> None:
        self._drafts = draft_repository
        self._topics = topic_registry_repository
        self._publisher = publisher
        self._settings = settings
        self._versions = version_service  # 승인 토글·발행 시도 상한·머리말 문구는 라운드 버전의 조절 값이다
        self._sleep = sleep
        self._derived = (
            derived_writer  # 발행 뒤 post_glossary·post_provenance 따라 쓰기 (설계 08 문서). 없으면 정리가 쓴다
        )
        self._in_flight: set[asyncio.Task[None]] = set()  # 호출부터 결과 반영까지. cancel 되지 않게 shield 한다

    async def publish(self, draft: PostDraft, publishing_round: PublishingRound) -> None:
        parameters = await self._versions.parameters_for(publishing_round)
        requires_approval = parameters.publish.requires_approval
        if draft.has_uncertain_publication:
            # 직전 호출이 Post BE 에 닿았는지 모른다(호출 중 프로세스 종료). 다시 호출하지 않는다 (8절 4단계)
            draft.escalate(EscalationReason.PUBLISH_UNCERTAIN)
            await self._drafts.save(draft)
            return
        provider = publishing_round.slot_bindings[draft.author_slot].provider
        if not self._publisher.has_account(provider):
            # 의도를 저장하기 전에 멈춘다. 설정을 고치고 재개하면 결과 불명으로 격리되지 않고 바로 발행한다
            raise ValueError(f"publishing account for provider {provider!r} is not configured (USER_SERVICE_URL too)")
        previous = draft.publication_intent
        if previous is not None:
            await self._sleep(_BACKOFF_BASE_S * 2 ** (previous.attempt - 1))  # 결과 확정 실패 뒤 재호출
        note = build_peer_review_note(draft, publishing_round, parameters.publish.note)
        payload = build_payload(draft, note, include_level=self._settings.publish_post_level)
        intent = draft.begin_publish(requires_approval)
        await self._drafts.save(draft)  # 이 쓰기가 실패(충돌)하면 호출하지 않는다 (8절 6단계)
        # 호출과 결과 반영은 글 코루틴이 cancel 돼도(runner.stop) 끝까지 간다. 끊기면 결과를 모르는 의도가 남아
        # 평범한 재배포가 PUBLISH_UNCERTAIN 을 만들기 때문이다. 종료는 drain 으로 기다린다
        task = asyncio.create_task(
            self._call_and_record(
                draft, intent, payload, note, provider, requires_approval, parameters.max_publish_attempts
            )
        )
        self._in_flight.add(task)
        task.add_done_callback(self._in_flight.discard)
        await asyncio.shield(task)

    async def drain(self) -> None:
        """진행 중인 발행 호출·결과 반영이 끝나기를 기다린다. 종료 때 러너가 부른다."""
        if self._in_flight:
            await asyncio.gather(*list(self._in_flight), return_exceptions=True)

    async def _call_and_record(
        self,
        draft: PostDraft,
        intent: PublicationIntent,
        payload: PostCreatePayload,
        note: str,
        provider: str,
        requires_approval: bool,
        max_publish_attempts: int,
    ) -> None:
        try:
            result = await self._publisher.publish(payload, provider)
        except PublishFailedError as exc:
            logger.warning("draft %s publish attempt %s failed: %s", draft.draft_id, intent.attempt, exc)
            draft.fail_publish(max_publish_attempts)
            await self._drafts.save(draft)
            return
        except PublishUncertainError as exc:
            logger.warning("draft %s publish attempt %s result unknown: %s", draft.draft_id, intent.attempt, exc)
            draft.escalate(EscalationReason.PUBLISH_UNCERTAIN)
            await self._drafts.save(draft)
            return
        publication = Publication(
            published_at=datetime.now(UTC),
            final_version=intent.final_version,
            external_post_id=result.external_post_id,
            peer_review_note=note,
            approved_by=draft.approved_by,
        )
        draft.publish(publication, requires_approval)
        try:
            await self._drafts.save(draft)
        except ConcurrentUpdateError:
            # 라운드 중단이 먼저 썼다. PUBLISHED 로 쓰지 않고 게시글 id 만 의도에 남긴다. 운영자가 회수한다 (8절 9단계)
            await self._record_external_post_id(draft.draft_id, result.external_post_id)
            raise
        await self._topics.set_status(draft.draft_id, TopicRegistryStatus.PUBLISHED, publication.published_at)
        if self._derived is not None:
            try:
                await self._derived.write(draft, built_by="publish")
            except Exception:  # 발행은 끝났다. 파생 문서는 정리(reconcile)가 마저 쓴다 (설계 08 문서 2.2절)
                logger.exception("draft %s derived records deferred to reconcile", draft.draft_id)

    async def _record_external_post_id(self, draft_id: str, external_post_id: str) -> None:
        while True:
            draft = await self._drafts.get(draft_id)
            if draft is None or draft.publication_intent is None:
                logger.error("draft %s: published post %s could not be recorded", draft_id, external_post_id)
                return
            draft.publication_intent.external_post_id = external_post_id
            try:
                await self._drafts.save(draft)
            except ConcurrentUpdateError:
                continue
            if draft.is_terminal:
                logger.error(
                    "draft %s is %s but post %s was created on the blog; take it down manually",
                    draft_id,
                    draft.status,
                    external_post_id,
                )
            else:
                logger.error(
                    "draft %s is %s; post %s was created on the blog and is kept on the intent (CONFIRM_PUBLISHED)",
                    draft_id,
                    draft.status,
                    external_post_id,
                )
            return


class UnconfiguredDraftPublisher:
    """POST_BE_URL 이 비어 있을 때 꽂는 DraftPublisher. 발행 단계에 닿은 글을 FAILED(INTERNAL_ERROR) 로 멈춘다.

    발행 의도를 저장하기 전에 멈춰야 설정을 고치고 재개했을 때 결과 불명(PUBLISH_UNCERTAIN)으로 격리되지 않는다.
    """

    async def publish(self, draft: PostDraft, publishing_round: PublishingRound) -> None:
        raise ValueError("Post BE publishing is not configured (POST_BE_URL)")

    async def drain(self) -> None:
        return None


def _final_resolutions(draft: PostDraft) -> dict[str, ItemResolution]:
    """항목마다 마지막 버전의 해소. 중재에서 리뷰어가 맞다고 판정돼 다음 버전에서 수용하면 그 수용이 남는다."""
    final: dict[str, ItemResolution] = {}
    for version in draft.versions:
        for resolution in version.resolutions:
            final[resolution.item_id] = resolution
    return final


def _is_rejected(resolution: ItemResolution | None) -> bool:
    """작성자가 지적을 받아들이지 않고 유지한 항목. 범위 밖 판단도 유지다."""
    return resolution is not None and resolution.status in (
        ResolutionStatus.REJECTED_WITH_REASON,
        ResolutionStatus.OUT_OF_SCOPE,
    )


def _writer_model(draft: PostDraft, publishing_round: PublishingRound) -> str:
    """첫 버전을 쓴 모델. 옛 글은 슬롯 바인딩의 모델이다. v4 부터 페르소나가 글을 규정하지 않아 머리말에도 모델을 적는다."""
    writer = next((version.model_id for version in draft.versions if version.model_id), None)
    return writer or publishing_round.slot_bindings[draft.author_slot].model_id


def _model_line(draft: PostDraft, publishing_round: PublishingRound, note: PublishNoteParameters) -> str:
    """본문에 영향을 준 모델만 적는다: 작성·리뷰는 항상, 수정은 작성과 다를 때, 중재는 있었을 때, 다듬기는 패치가 적용됐을 때.

    전체 읽기·수준 평가는 본문을 바꾸지 않아 적지 않는다(agent_calls 에 남는다). 옛 글은 슬롯 바인딩에서 찾는다.
    """
    bindings = publishing_round.slot_bindings
    roles = note.model_roles
    writer = _writer_model(draft, publishing_round)
    parts = [f"{writer} {roles['writer']}"]
    reviewers: dict[ReviewLens, str] = {}
    for review in draft.reviews:
        reviewers.setdefault(review.lens, review.model_id or bindings[review.reviewer_slot].model_id)
    if reviewers:
        parts.append(
            "·".join(f"{model}({_lens_label(note, lens)})" for lens, model in reviewers.items()) + f" {roles['review']}"
        )
    revisers = {
        version.model_id
        for version in draft.versions
        if version.origin_cause in (RevisionCause.REVIEW, RevisionCause.HUMAN)
        and version.model_id not in (None, writer)
    }
    if revisers:
        parts.append("·".join(sorted(revisers)) + f" {roles['revise']}")
    arbiters = {
        arbitration.model_id or bindings[arbitration.arbiter_slot].model_id for arbitration in draft.arbitrations
    }
    if arbiters:
        parts.append("·".join(sorted(arbiters)) + f" {roles['arbitrate']}")
    if draft.polish and draft.polish.model_ids:
        parts.append("·".join(sorted(set(draft.polish.model_ids))) + f" {roles['polish']}")
    return ", ".join(parts)


def _plain(text: str) -> str:
    """줄바꿈을 공백으로 바꾸고 마크다운·HTML 특수문자를 이스케이프해 문단 텍스트로만 들어가게 한다 (05 문서 5절)."""
    return _MARKDOWN_SPECIALS.sub(r"\\\1", " ".join(text.split()))
