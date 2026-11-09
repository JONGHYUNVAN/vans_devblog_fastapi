"""READY_TO_PUBLISH 글의 발행: 글 끝 AI Information 표기, Tiptap 변환·CreatePostDto 매핑, 발행 의도, Post BE 호출, 결과 반영 (설계 04 문서 8절).

DraftPipelineService 의 DraftPublisher 계약을 구현한다. 글·라운드 재확인(8절 3단계: READY_TO_PUBLISH·IN_PROGRESS)과
승인 대기(1단계)는 글 코루틴이 매 걸음 다시 읽어 검사하므로 여기서는 되풀이하지 않는다.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from string import Template

from app.agents.contracts import StructureRules
from app.clients.publisher.base import PostCreatePayload, Publisher
from app.core.config import Settings
from app.core.exceptions import ConcurrentUpdateError, InvalidTransitionError, PublishFailedError, PublishUncertainError
from app.models.enums import EscalationReason, ReviewLens, RevisionCause, TopicRegistryStatus
from app.models.pipeline import PublishNoteParameters
from app.models.post_draft import PostDraft, Publication, PublicationIntent
from app.models.publishing_round import PublishingRound
from app.models.tiptap import markdown_to_tiptap
from app.repositories.post_draft import PostDraftRepository
from app.repositories.topic_registry import TopicRegistryRepository
from app.services.derived_records import DerivedRecordsWriter
from app.services.pipeline_versions import PipelineVersionService

logger = logging.getLogger(__name__)

_BACKOFF_BASE_S = 2.0  # 결과 확정 실패 뒤 다음 호출까지 (03 문서 28a행)
_LANGUAGE = StructureRules.model_fields["language"].default  # "ko"


def _fill(template: str, **values: object) -> str:
    return Template(template).safe_substitute(**{key: str(value) for key, value in values.items()})


def build_publish_note(
    draft: PostDraft, publishing_round: PublishingRound, note: PublishNoteParameters | None = None
) -> str:
    """글 끝 "AI Information" 표기 마크다운 (09 문서 D-80·D-87). LLM 호출 없음. 발행 본문 뒤에 붙여 함께 Tiptap 으로 변환한다.

    독자에게는 누가 무엇을 했는지만 보인다: 작성·리뷰 줄은 항상, 수정·중재·다듬기 줄은 본문에 영향을 줬을 때만.
    문구는 라운드 버전 문서의 조절 값(publish.note)이고 어느 줄을 넣을지는 여기 코드다. 이력 전체는 제작 기록이 보인다.
    줄에 들어가는 값은 라우트 설정의 모델 id 와 렌즈 라벨뿐이라 LLM 텍스트 이스케이프가 필요 없다.
    """
    note = note if note is not None else PublishNoteParameters()
    lines = ["---", "", note.heading, ""]
    lines += [_fill(template, models=models) for template, models in _role_lines(draft, publishing_round, note)]
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
        self._versions = version_service  # 승인 토글·발행 시도 상한·글 끝 표기 문구는 라운드 버전의 조절 값이다
        self._sleep = sleep
        self._derived = (
            derived_writer  # 발행 뒤 post_glossary·post_provenance 따라 쓰기 (설계 08 문서). 없으면 정리가 쓴다
        )
        self._in_flight: set[asyncio.Task[None]] = set()  # 호출부터 결과 반영까지. cancel 되지 않게 shield 한다

    async def publish(self, draft: PostDraft, publishing_round: PublishingRound) -> None:
        """READY_TO_PUBLISH 글 하나를 발행한다(04 문서 8절). 결과 불명이면 호출 없이 ESCALATED(PUBLISH_UNCERTAIN), 작성 벤더의 계정이 없으면 의도 저장 전에 ValueError.

        호출과 결과 반영은 shield 된 태스크라 글 코루틴이 cancel 돼도 끝까지 간다. 승인 토글·시도 상한·글 끝 표기 문구는 라운드 버전에서 읽는다.
        """
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
        note = build_publish_note(draft, publishing_round, parameters.publish.note)
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
        """항상 ValueError. 발행 설정이 없는 배포에서 발행 단계에 닿은 글을 FAILED(INTERNAL_ERROR) 로 멈춘다."""
        raise ValueError("Post BE publishing is not configured (POST_BE_URL)")

    async def drain(self) -> None:
        """기다릴 호출이 없다."""
        return None


def _writer_model(draft: PostDraft, publishing_round: PublishingRound) -> str:
    """첫 버전을 쓴 모델. 옛 글은 슬롯 바인딩의 모델이다. v4 부터 페르소나가 글을 규정하지 않아 글 끝 표기에도 모델을 적는다."""
    writer = next((version.model_id for version in draft.versions if version.model_id), None)
    return writer or publishing_round.slot_bindings[draft.author_slot].model_id


def _role_lines(
    draft: PostDraft, publishing_round: PublishingRound, note: PublishNoteParameters
) -> list[tuple[str, str]]:
    """(줄 문구, 모델 표기) 목록. 본문에 영향을 준 모델만 적는다: 작성·리뷰는 항상, 수정은 작성과 다를 때,
    중재는 있었을 때, 다듬기는 작성과 다른 모델의 패치가 적용됐을 때, 다듬기 검토는 작성 모델이 그 제안을 보고 고쳤을 때(D-91).

    전체 읽기·수준 평가·용어 추출은 본문을 바꾸지 않아 적지 않는다(제작 기록에 남는다). 옛 글은 슬롯 바인딩에서 찾는다.
    """
    bindings = publishing_round.slot_bindings
    writer = _writer_model(draft, publishing_round)
    lines = [(note.writer, writer)]
    # 리뷰는 렌즈마다 첫 리뷰의 모델이다(반복 리뷰가 같은 렌즈를 다시 봐도 한 번). 같은 모델이 여러 렌즈를 맡으면
    # "모델(렌즈, 렌즈)" 로 묶는다. 독자에게는 모델별로 무엇을 봤는지가 렌즈 순서보다 읽기 쉽다
    lenses_by_model: dict[str, list[str]] = {}
    seen_lenses: set[ReviewLens] = set()
    for review in draft.reviews:
        if review.lens in seen_lenses:
            continue
        seen_lenses.add(review.lens)
        model = review.model_id or bindings[review.reviewer_slot].model_id
        lenses_by_model.setdefault(model, []).append(_lens_label(note, review.lens))
    if lenses_by_model:
        lines.append(
            (note.reviewers, ", ".join(f"{model}({', '.join(lenses)})" for model, lenses in lenses_by_model.items()))
        )
    revisers = {
        version.model_id
        for version in draft.versions
        if version.origin_cause in (RevisionCause.REVIEW, RevisionCause.HUMAN)
        and version.model_id not in (None, writer)
    }
    if revisers:
        lines.append((note.revisers, ", ".join(sorted(revisers))))
    arbiters = {
        arbitration.model_id or bindings[arbitration.arbiter_slot].model_id for arbitration in draft.arbitrations
    }
    if arbiters:
        lines.append((note.arbiters, ", ".join(sorted(arbiters))))
    if draft.polish:
        # D-91 부터 다듬기 패치는 작성 벤더의 수정 모델이 쓴다. 작성 모델이면 "작성" 줄로 충분하고, 제안한 모델은 검토 줄에 적는다
        polishers = set(draft.polish.model_ids) - {writer}
        if polishers:
            lines.append((note.polishers, ", ".join(sorted(polishers))))
        if draft.polish.proposer_model_ids:
            lines.append((note.polish_reviewers, ", ".join(sorted(set(draft.polish.proposer_model_ids)))))
    return lines
