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

from app.agents.contracts import StructureRules
from app.agents.personas import get_persona
from app.clients.publisher.base import PostCreatePayload, Publisher
from app.core.config import Settings
from app.core.exceptions import ConcurrentUpdateError, InvalidTransitionError, PublishFailedError, PublishUncertainError
from app.models.enums import (
    AgentSlot,
    ArbitrationOutcome,
    EscalationReason,
    IssueSeverity,
    ResolutionStatus,
    ReviewLens,
    TopicRegistryStatus,
)
from app.models.post_draft import ItemResolution, PostDraft, Publication, PublicationIntent
from app.models.publishing_round import PublishingRound
from app.models.tiptap import markdown_to_tiptap
from app.repositories.post_draft import PostDraftRepository
from app.repositories.topic_registry import TopicRegistryRepository

logger = logging.getLogger(__name__)

_BACKOFF_BASE_S = 2.0  # 결과 확정 실패 뒤 다음 호출까지 (03 문서 28a행)
_MAX_REJECTED_SHOWN = 3  # 노트에 노출하는 기각 항목 수. 나머지는 건수만 (05 문서 5절)
_LANGUAGE = StructureRules.model_fields["language"].default  # "ko"
_LENS_LABELS = {
    ReviewLens.LOGIC_READABILITY: "논리·가독성",
    ReviewLens.FACT_STRUCTURE: "사실·구조",
    ReviewLens.CRITICAL_PRACTICAL: "비판·실무",
}
_OUTCOME_TEXT = {
    ArbitrationOutcome.UPHOLD_REVIEWER: "리뷰어 의견을 인용",
    ArbitrationOutcome.UPHOLD_AUTHOR: "작성자 입장을 인정",
}
_SEVERITY_ORDER = {severity: index for index, severity in enumerate(IssueSeverity)}
# 노트에 넣는 LLM 텍스트는 문단 텍스트로만 들어가야 한다. ASCII 구두점을 백슬래시로 이스케이프하면 CommonMark 가 글자로 읽는다
# ^ 도 넣는다: 정규식 얘기([^a-z])가 각주 패턴([^n])에 걸려 변환이 거부되지 않게
_MARKDOWN_SPECIALS = re.compile(r"([\\`*_{}\[\]()#+\-.!<>&|~^])")


def build_peer_review_note(draft: PostDraft, publishing_round: PublishingRound) -> str:
    """피어 리뷰 요약 노트 마크다운 (05 문서 5절 템플릿). LLM 호출 없음. 발행 본문 뒤에 붙여 함께 Tiptap 으로 변환한다."""
    version = draft.publishable_version or draft.current_version
    reviewer_1, reviewer_2 = draft.reviewer_assignments
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
        "## 이 글은 어떻게 만들어졌나",
        "",
        f"이 글은 **{_display(publishing_round, draft.author_slot)}**가 작성하고 "
        f"**{_display(publishing_round, reviewer_1.slot)}**({_LENS_LABELS[reviewer_1.primary_lens]})와 "
        f"**{_display(publishing_round, reviewer_2.slot)}**({_LENS_LABELS[reviewer_2.primary_lens]})의 "
        "교차 리뷰를 거쳐 완성되었습니다.",
        "",
        f"- 버전: v1 → v{version.version_number}",
        f"- 발행을 막은 사항: CRITICAL {severity_counts[IssueSeverity.CRITICAL]}, MAJOR {severity_counts[IssueSeverity.MAJOR]}",
        f"- 기록만 남긴 사항: MINOR {severity_counts[IssueSeverity.MINOR]}, NITPICK {severity_counts[IssueSeverity.NITPICK]} "
        "(발행을 막지 않아 수정하지 않음)",
        f"- 작성자가 수용한 사항: {accepted}건 / 근거를 들어 유지한 사항: {len(rejected)}건",
        f"- 모델: {_model(publishing_round, draft.author_slot)} 작성, "
        f"{_model(publishing_round, reviewer_1.slot)}·{_model(publishing_round, reviewer_2.slot)} 리뷰",
    ]
    if draft.arbitrations:
        lines.append(f"- 의견이 갈려 제3자 판정을 받은 사항: {len(arbitration_by_item)}건")
    for item in rejected[:_MAX_REJECTED_SHOWN]:
        resolution = final_resolutions[item.item_id]
        quote = [
            f"> **{_display(publishing_round, item.reviewer_slot)}의 지적**: {_plain(item.comment)}",
            f"> **작성자의 입장**: {_plain(resolution.reason or '')}",
        ]
        arbitration = arbitration_by_item.get(item.item_id)
        if arbitration is not None:
            quote.append(
                f"> **판정 ({_display(publishing_round, arbitration.arbiter_slot)})**: "
                f"{_OUTCOME_TEXT[arbitration.outcome]} — {_plain(arbitration.reasoning)}"
            )
        lines += ["", "\\\n".join(quote)]  # 줄 끝 백슬래시는 hardBreak (04 문서 8절 변환표)
    return "\n".join(lines) + "\n"


def build_payload(draft: PostDraft, note: str) -> PostCreatePayload:
    """발행본(마지막 passed_validation 버전) + 노트 → CreatePostDto (04 문서 8절 매핑표)."""
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
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._drafts = draft_repository
        self._topics = topic_registry_repository
        self._publisher = publisher
        self._settings = settings
        self._sleep = sleep
        self._in_flight: set[asyncio.Task[None]] = set()  # 호출부터 결과 반영까지. cancel 되지 않게 shield 한다

    async def publish(self, draft: PostDraft, publishing_round: PublishingRound) -> None:
        requires_approval = self._settings.publish_requires_approval
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
        note = build_peer_review_note(draft, publishing_round)
        payload = build_payload(draft, note)
        intent = draft.begin_publish(requires_approval)
        await self._drafts.save(draft)  # 이 쓰기가 실패(충돌)하면 호출하지 않는다 (8절 6단계)
        # 호출과 결과 반영은 글 코루틴이 cancel 돼도(runner.stop) 끝까지 간다. 끊기면 결과를 모르는 의도가 남아
        # 평범한 재배포가 PUBLISH_UNCERTAIN 을 만들기 때문이다. 종료는 drain 으로 기다린다
        task = asyncio.create_task(self._call_and_record(draft, intent, payload, note, provider, requires_approval))
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
    ) -> None:
        try:
            result = await self._publisher.publish(payload, provider)
        except PublishFailedError as exc:
            logger.warning("draft %s publish attempt %s failed: %s", draft.draft_id, intent.attempt, exc)
            draft.fail_publish(self._settings.max_publish_attempts)
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
    return resolution is not None and resolution.status == ResolutionStatus.REJECTED_WITH_REASON


def _display(publishing_round: PublishingRound, slot: AgentSlot) -> str:
    return get_persona(publishing_round.slot_bindings[slot].persona_id).display_name


def _model(publishing_round: PublishingRound, slot: AgentSlot) -> str:
    return publishing_round.slot_bindings[slot].model_id


def _plain(text: str) -> str:
    """줄바꿈을 공백으로 바꾸고 마크다운·HTML 특수문자를 이스케이프해 문단 텍스트로만 들어가게 한다 (05 문서 5절)."""
    return _MARKDOWN_SPECIALS.sub(r"\\\1", " ".join(text.split()))
