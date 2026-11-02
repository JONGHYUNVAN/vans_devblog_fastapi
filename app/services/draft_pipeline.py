"""글 한 편을 사람 대기나 종결까지 진행하는 코루틴 (설계 04 문서 1절·3~5절, 06 문서 3절).

_step 은 현재 상태에서 한 걸음만 진행하고 한 번 저장한다. 저장 단위가 작아 재시작 때 잃는 것이 그 단계의 호출뿐이다.
발행(READY_TO_PUBLISH)은 DraftPublisher 가 맡는다. 6단계 PublishingService 가 그 계약을 구현한다.
"""

import asyncio
import logging
from collections.abc import Awaitable
from typing import Protocol

from app.agents.builders import (
    build_arbitrate_input,
    build_review_input,
    build_revise_draft_input,
    build_spot_check_input,
    build_write_draft_input,
    render_messages,
)
from app.agents.contracts import (
    ArbitrateOutput,
    DraftOutput,
    ReviewOutput,
    RevisionOutput,
    SpotCheckOutput,
    StructureRules,
)
from app.agents.parsers import review_items, review_output_check, spot_check_items, spot_check_output_check
from app.core.config import Settings
from app.core.exceptions import BudgetExceededError, ConcurrentUpdateError, LLMRetriesExhaustedError
from app.models.enums import AgentSlot, CallKind, FailureCode, PostStatus, ReviewScope, RoundStatus
from app.models.post_draft import Arbitration, PeerReview, PostDraft, ReviewItem
from app.models.publishing_round import PublishingRound
from app.models.sections import find_anchors
from app.repositories.post_draft import PostDraftRepository
from app.services.agent_calls import AgentCallService
from app.services.evaluation import derive_verdict, evaluate
from app.services.round import RoundService
from app.services.validation import ValidationGate

logger = logging.getLogger(__name__)

_STEP_FAILURES = (LLMRetriesExhaustedError, BudgetExceededError)


class DraftPublisher(Protocol):
    """READY_TO_PUBLISH 글의 발행 (설계 04 문서 8절). 의도 저장·외부 호출·결과 반영과 그 저장을 모두 맡는다.

    돌아오면 글은 PUBLISHED, READY_TO_PUBLISH(결과 확정 실패, 재호출 대상) 또는 ESCALATED 다. 6단계 PublishingService 가 구현한다.
    """

    async def publish(self, draft: PostDraft, publishing_round: PublishingRound) -> None: ...


class DraftPipelineService:
    """러너가 글마다 asyncio 태스크 하나로 drive 를 돌린다."""

    def __init__(
        self,
        *,
        draft_repository: PostDraftRepository,
        round_service: RoundService,
        agent_call_service: AgentCallService,
        gate: ValidationGate,
        publisher: DraftPublisher,
        settings: Settings,
    ) -> None:
        self._drafts = draft_repository
        self._rounds = round_service
        self._calls = agent_call_service
        self._gate = gate
        self._publisher = publisher
        self._settings = settings
        self._rules = StructureRules(min_chars=settings.draft_min_chars, max_chars=settings.draft_max_chars)

    async def drive(self, draft_id: str) -> None:
        """사람 대기(승인·ESCALATED·FAILED)나 종결, 라운드가 IN_PROGRESS 가 아닐 때 끝난다."""
        while True:
            draft = await self._drafts.get(draft_id)
            if draft is None:
                return
            publishing_round = await self._rounds.get(draft.round_id)
            if publishing_round is None or not self._should_run(draft, publishing_round):
                return
            try:
                draft = await self._step(draft, publishing_round)
            except ConcurrentUpdateError:
                continue  # 라운드 중단과 겹쳤다. 결과를 버리고 다시 읽는다. 호출 비용은 AgentCall 에 남는다
            if draft.is_terminal:
                await self._rounds.close_if_complete(draft.round_id)

    def _should_run(self, draft: PostDraft, publishing_round: PublishingRound) -> bool:
        return (
            publishing_round.status == RoundStatus.IN_PROGRESS
            and draft.is_active
            and not draft.awaits_approval(self._settings.publish_requires_approval)
        )

    async def _step(self, draft: PostDraft, publishing_round: PublishingRound) -> PostDraft:
        """현재 상태에서 한 걸음. LLM 호출을 직접 await 하고 결과를 반영해 한 번 저장한다."""
        match draft.status:
            case PostStatus.TOPIC_LOCKED | PostStatus.REVISING:
                draft.start_drafting(self._settings.max_iterations)
            case PostStatus.DRAFTING:
                await self._write(draft, publishing_round)
            case PostStatus.VALIDATING:
                self._validate(draft)
            case PostStatus.IN_REVIEW | PostStatus.IN_SPOT_CHECK:
                await self._review(draft, publishing_round)
            case PostStatus.IN_ARBITRATION:
                await self._arbitrate(draft, publishing_round)
            case PostStatus.READY_TO_PUBLISH:
                await self._publisher.publish(draft, publishing_round)
                return draft  # 발행은 의도·결과를 스스로 저장한다
            case _:
                raise ValueError(f"draft {draft.draft_id} is {draft.status}, nothing to drive")
        await self._drafts.save(draft)
        return draft

    async def _write(self, draft: PostDraft, publishing_round: PublishingRound) -> None:
        """revision_cause 가 없으면 WRITE_DRAFT, 있으면 REVISE_DRAFT. DRAFTING 에서 재시작해도 같은 호출이다 (설계 07 문서 D-11)."""
        target_version = len(draft.versions) + 1
        try:
            if draft.revision_cause is None:
                result = await self._calls.call(
                    kind=CallKind.WRITE_DRAFT,
                    round_id=draft.round_id,
                    slot=draft.author_slot,
                    binding=publishing_round.slot_bindings[draft.author_slot],
                    messages=render_messages(
                        build_write_draft_input(draft, publishing_round, self._rules), self._settings.prompt_version
                    ),
                    schema=DraftOutput,
                    draft=draft,
                    target_version=target_version,
                )
                resolutions, author_note = [], ""
            else:
                result = await self._calls.call(
                    kind=CallKind.REVISE_DRAFT,
                    round_id=draft.round_id,
                    slot=draft.author_slot,
                    binding=publishing_round.slot_bindings[draft.author_slot],
                    messages=render_messages(
                        build_revise_draft_input(
                            draft, publishing_round, self._rules, max_iterations=self._settings.max_iterations
                        ),
                        self._settings.prompt_version,
                    ),
                    schema=RevisionOutput,
                    draft=draft,
                    target_version=target_version,
                )
                resolutions, author_note = result.output.resolutions, result.output.author_note
        except _STEP_FAILURES as exc:
            self._fail(draft, [exc])
            return
        output = result.output
        draft.submit_version(
            title=output.title,
            summary=output.summary,
            content_markdown=output.content_markdown,
            section_ids=find_anchors(output.content_markdown),
            resolutions=resolutions,
            author_note=author_note,
            call_id=result.call_id,
        )

    def _validate(self, draft: PostDraft) -> None:
        errors = self._gate.validate(draft)
        if errors:
            draft.fail_validation(errors, self._settings.max_validation_retries)
        else:
            draft.pass_validation()

    async def _review(self, draft: PostDraft, publishing_round: PublishingRound) -> None:
        """응답이 없는 리뷰어만 병렬 호출한다. 하나가 실패해도 나머지를 기다려 성공분을 넣고 FAILED 다 (설계 04 문서 6절)."""
        scope = draft.current_review_scope
        reviews, failures = await _gather(
            [self._call_review(draft, publishing_round, slot, scope) for slot in draft.pending_reviewers()]
        )
        for review in reviews:
            draft.add_review(review)
        if failures:
            self._fail(draft, failures)
        elif draft.is_review_complete():
            draft.apply_evaluation(evaluate(draft, self._settings.max_iterations))

    async def _arbitrate(self, draft: PostDraft, publishing_round: PublishingRound) -> None:
        arbitrations, failures = await _gather(
            [self._call_arbitrate(draft, publishing_round, item) for item in draft.pending_arbitration_items()]
        )
        for arbitration in arbitrations:
            draft.add_arbitration(arbitration)
        if failures:
            self._fail(draft, failures)
        elif draft.arbitration_complete():
            draft.apply_evaluation(evaluate(draft, self._settings.max_iterations))

    def _fail(self, draft: PostDraft, failures: list[Exception]) -> None:
        """사유가 여럿이면 BUDGET_EXCEEDED 를 남긴다 (설계 03 문서 5행). 재개 때 상한을 올릴 수 있기 때문이다."""
        has_budget = any(isinstance(failure, BudgetExceededError) for failure in failures)
        code = FailureCode.BUDGET_EXCEEDED if has_budget else FailureCode.LLM_RETRIES_EXHAUSTED
        draft.mark_failed(code, "; ".join(str(failure) for failure in failures))

    async def _call_review(
        self, draft: PostDraft, publishing_round: PublishingRound, slot: AgentSlot, scope: ReviewScope
    ) -> PeerReview:
        version = draft.current_version
        assignment = draft.assignment_of(slot)
        review_id = f"{draft.draft_id}:v{version.version_number}:{slot}:{scope}"
        section_ids = find_anchors(version.content_markdown)
        if scope == ReviewScope.FULL:
            result = await self._calls.call(
                kind=CallKind.REVIEW,
                round_id=draft.round_id,
                slot=slot,
                binding=publishing_round.slot_bindings[slot],
                messages=render_messages(
                    build_review_input(draft, publishing_round, slot), self._settings.prompt_version
                ),
                schema=ReviewOutput,
                check=review_output_check(section_ids, assignment.lenses),
                draft=draft,
                target_version=version.version_number,
            )
            output = result.output
            items = review_items(output.items, review_id=review_id, reviewer_slot=slot)
            item_checks: list = []
            reported = output.verdict
            feedback = output.overall_feedback
        else:
            expected_ids = [item.item_id for item in draft.previous_items_of(slot)]
            result = await self._calls.call(
                kind=CallKind.REVIEW,
                round_id=draft.round_id,
                slot=slot,
                binding=publishing_round.slot_bindings[slot],
                messages=render_messages(
                    build_spot_check_input(draft, publishing_round, slot), self._settings.prompt_version
                ),
                schema=SpotCheckOutput,
                check=spot_check_output_check(section_ids, expected_ids, assignment.lenses),
                draft=draft,
                target_version=version.version_number,
            )
            output = result.output
            items = spot_check_items(output, review_id=review_id, reviewer_slot=slot)
            item_checks = list(output.item_checks)
            reported = None
            feedback = ""
        derived = derive_verdict(scope, items, item_checks)
        if reported is not None and reported != derived:
            # 튜닝 지표 (설계 07 문서 4절). 판정에는 derived 만 쓴다(D-03)
            logger.info("review %s reported %s but derived %s", review_id, reported, derived)
        return PeerReview(
            review_id=review_id,
            target_version=version.version_number,
            reviewer_slot=slot,
            lens=assignment.primary_lens,
            secondary_lens=assignment.secondary_lens,
            scope=scope,
            reported_verdict=reported,
            derived_verdict=derived,
            items=items,
            item_checks=item_checks,
            checklist=output.checklist,
            overall_feedback=feedback,
            call_id=result.call_id,
        )

    async def _call_arbitrate(
        self, draft: PostDraft, publishing_round: PublishingRound, item: ReviewItem
    ) -> Arbitration:
        arbiter = next(slot for slot in AgentSlot if slot not in (draft.author_slot, item.reviewer_slot))
        version_number = draft.current_version.version_number
        result = await self._calls.call(
            kind=CallKind.ARBITRATE,
            round_id=draft.round_id,
            slot=arbiter,
            binding=publishing_round.slot_bindings[arbiter],
            messages=render_messages(build_arbitrate_input(draft, item), self._settings.prompt_version),
            schema=ArbitrateOutput,
            draft=draft,
            target_version=version_number,
            item_id=item.item_id,
        )
        return Arbitration(
            arbitration_id=f"{item.item_id}:arb:v{version_number}",
            target_version=version_number,
            item_id=item.item_id,
            arbiter_slot=arbiter,
            outcome=result.output.outcome,
            reasoning=result.output.reasoning,
            call_id=result.call_id,
        )


async def _gather[T](calls: list[Awaitable[T]]) -> tuple[list[T], list[Exception]]:
    """병렬 호출을 모두 기다려 (성공 결과, 재시도 소진·예산 초과) 로 나눈다. 그 밖의 예외는 첫 것을 그대로 던진다."""
    results = await asyncio.gather(*calls, return_exceptions=True)
    successes: list[T] = []
    failures: list[Exception] = []
    for result in results:
        if isinstance(result, _STEP_FAILURES):
            failures.append(result)
        elif isinstance(result, BaseException):
            raise result
        else:
            successes.append(result)
    return successes, failures
