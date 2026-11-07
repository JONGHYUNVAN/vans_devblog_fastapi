"""글 한 편을 사람 대기나 종결까지 진행하는 코루틴 (설계 04 문서 1절·3~5절, 06 문서 3절).

_step 은 현재 상태에서 한 걸음만 진행하고 한 번 저장한다. 저장 단위가 작아 재시작 때 잃는 것이 그 단계의 호출뿐이다.
발행(READY_TO_PUBLISH)은 DraftPublisher 가 맡는다. 6단계 PublishingService 가 그 계약을 구현한다.
프롬프트·조절 값·페르소나는 라운드가 고정한 파이프라인 버전 문서에서 읽는다(09 문서 D-73, 이행 1a). 흐름은 아직 코드다.
"""

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any, Protocol

from app.agents.builders import (
    build_arbitrate_input,
    build_arbitrate_search_input,
    build_assess_level_input,
    build_fact_check_input,
    build_fact_transform_input,
    build_final_read_input,
    build_review_input,
    build_revise_draft_input,
    build_spot_check_input,
    build_write_draft_input,
    level_criteria_of,
    render_messages,
)
from app.agents.contracts import (
    ArbitrateOutput,
    AssessLevelOutput,
    DraftOutput,
    FactCheckOutput,
    FactSpotCheckOutput,
    FinalReadOutput,
    LinkStatus,
    ReviewOutput,
    RevisionOutput,
    SpotCheckOutput,
    StructureRules,
)
from app.agents.parsers import (
    claim_items,
    fact_output_check,
    fact_spot_check_output_check,
    final_read_check,
    level_assessment_check,
    normalize_claims,
    review_items,
    review_output_check,
    spot_check_items,
    spot_check_output_check,
)
from app.clients.link_checker import LinkChecker
from app.core.exceptions import (
    BudgetExceededError,
    ConcurrentUpdateError,
    LLMOutputTruncatedError,
    LLMRetriesExhaustedError,
)
from app.models.enums import CallKind, FailureCode, PostReviewStage, PostStatus, ReviewLens, ReviewScope, RoundStatus
from app.models.glossary import GlossaryRecord
from app.models.pipeline import Parameters, PipelineVersion
from app.models.post_draft import (
    Arbitration,
    FinalRead,
    FinalReadFinding,
    LevelAssessment,
    LevelEvidence,
    PeerReview,
    PostDraft,
    ReviewItem,
    VerificationChecklist,
)
from app.models.publishing_round import PublishingRound
from app.models.sections import find_anchors, markdown_links
from app.repositories.post_draft import PostDraftRepository
from app.services.agent_calls import AgentCallService
from app.services.evaluation import derive_verdict, evaluate
from app.services.glossary import GlossaryService
from app.services.pipeline_versions import NoPipelineVersionError, PipelineVersionService
from app.services.polishing import PolishingService
from app.services.round import RoundService
from app.services.routing import DraftRoutes
from app.services.validation import ValidationGate

logger = logging.getLogger(__name__)

_STEP_FAILURES = (LLMRetriesExhaustedError, BudgetExceededError)


class DraftPublisher(Protocol):
    """READY_TO_PUBLISH 글의 발행 (설계 04 문서 8절). 의도 저장·외부 호출·결과 반영과 그 저장을 모두 맡는다.

    돌아오면 글은 PUBLISHED, READY_TO_PUBLISH(결과 확정 실패, 재호출 대상) 또는 ESCALATED 다. 6단계 PublishingService 가 구현한다.
    """

    async def publish(self, draft: PostDraft, publishing_round: PublishingRound) -> None: ...

    async def drain(self) -> None:
        """진행 중인 발행 호출·결과 반영이 끝나기를 기다린다. 종료 때 러너가 부른다(04 문서 8절 4단계)."""
        ...


def structure_rules_of(parameters: Parameters, level: int | None) -> StructureRules:
    """조절 값 → 작성·수정 입력의 구조 규칙. 필수 섹션은 글 수준으로 정한다."""
    structure = parameters.structure
    return StructureRules(
        required_sections=structure.required_sections_for(level),
        anchor_format=structure.anchor_format,
        min_chars=parameters.draft_min_chars,
        max_chars=parameters.draft_max_chars,
        min_code_blocks=parameters.min_code_blocks,
        language=structure.language,
        forbidden_syntax=list(structure.forbidden_syntax),
    )


def call_options(version: PipelineVersion) -> dict[str, Any]:
    """AgentCallService.call·search 에 넘기는 버전 의존 값: 재시도 수와 영수증의 버전 id."""
    return {"max_attempts": version.parameters.max_llm_retries, "pipeline_version": version.version_id}


class DraftPipelineService:
    """러너가 글마다 asyncio 태스크 하나로 drive 를 돌린다."""

    def __init__(
        self,
        *,
        draft_repository: PostDraftRepository,
        round_service: RoundService,
        agent_call_service: AgentCallService,
        publisher: DraftPublisher,
        version_service: PipelineVersionService,
        link_checker: LinkChecker | None = None,
        glossary_service: GlossaryService | None = None,
    ) -> None:
        self._drafts = draft_repository
        self._rounds = round_service
        self._calls = agent_call_service
        self._publisher = publisher
        self._versions = version_service
        self._links = link_checker
        self._glossary = glossary_service
        self._polisher = PolishingService(agent_call_service=agent_call_service)

    async def drive(self, draft_id: str) -> None:
        """사람 대기(승인·ESCALATED·FAILED)나 종결, 라운드가 IN_PROGRESS 가 아닐 때 끝난다."""
        while True:
            draft = await self._drafts.get(draft_id)
            if draft is None:
                return
            publishing_round = await self._rounds.get(draft.round_id)
            if publishing_round is None:
                return
            try:
                version = await self._versions.for_round(publishing_round)
            except NoPipelineVersionError as exc:
                # 버전이 없으면 진행할 수 없다. 러너가 다음 스캔에 다시 띄운다 (09 문서 2.7절)
                logger.error("draft %s cannot run: %s", draft_id, exc)
                return
            if not self._should_run(draft, publishing_round, version.parameters):
                return
            try:
                draft = await self._step(draft, publishing_round, version)
            except ConcurrentUpdateError:
                continue  # 라운드 중단과 겹쳤다. 결과를 버리고 다시 읽는다. 호출 비용은 AgentCall 에 남는다
            if draft.is_terminal:
                await self._rounds.close_if_complete(draft.round_id)

    async def drain(self) -> None:
        """발행 호출은 cancel 되지 않는다. runner.stop 이 결과 반영까지 기다린다."""
        await self._publisher.drain()

    def _should_run(self, draft: PostDraft, publishing_round: PublishingRound, parameters: Parameters) -> bool:
        return (
            publishing_round.status == RoundStatus.IN_PROGRESS
            and draft.is_active
            and not draft.awaits_approval(parameters.publish.requires_approval)
        )

    async def _step(self, draft: PostDraft, publishing_round: PublishingRound, version: PipelineVersion) -> PostDraft:
        """현재 상태에서 한 걸음. LLM 호출을 직접 await 하고 결과를 반영해 한 번 저장한다."""
        parameters = version.parameters
        match draft.status:
            case PostStatus.TOPIC_LOCKED | PostStatus.REVISING:
                draft.start_drafting(parameters.max_iterations)
            case PostStatus.DRAFTING:
                await self._write(draft, publishing_round, version)
            case PostStatus.VALIDATING:
                self._validate(draft, parameters)
            case PostStatus.IN_REVIEW | PostStatus.IN_SPOT_CHECK:
                await self._review(draft, publishing_round, version)
            case PostStatus.IN_ARBITRATION:
                await self._arbitrate(draft, publishing_round, version)
            case PostStatus.POST_REVIEW:
                await self._post_review(draft, publishing_round, version)
            case PostStatus.READY_TO_PUBLISH:
                await self._publisher.publish(draft, publishing_round)
                return draft  # 발행은 의도·결과를 스스로 저장한다
            case _:
                raise ValueError(f"draft {draft.draft_id} is {draft.status}, nothing to drive")
        await self._drafts.save(draft)
        return draft

    async def _write(self, draft: PostDraft, publishing_round: PublishingRound, version: PipelineVersion) -> None:
        """revision_cause 가 없으면 WRITE_DRAFT, 있으면 REVISE_DRAFT. DRAFTING 에서 재시작해도 같은 호출이다 (설계 07 문서 D-11)."""
        target_version = len(draft.versions) + 1
        routes = DraftRoutes(publishing_round, draft, version.personas)
        rules = structure_rules_of(version.parameters, draft.topic.target_level)
        try:
            if draft.revision_cause is None:
                model = routes.writer
                result = await self._calls.call(
                    kind=CallKind.WRITE_DRAFT,
                    round_id=draft.round_id,
                    slot=draft.author_slot,
                    binding=model,
                    messages=render_messages(
                        build_write_draft_input(draft, publishing_round, rules), version.prompts, model
                    ),
                    schema=DraftOutput,
                    draft=draft,
                    target_version=target_version,
                    **call_options(version),
                )
                resolutions, author_note = [], ""
            else:
                model = routes.reviser
                result = await self._calls.call(
                    kind=CallKind.REVISE_DRAFT,
                    round_id=draft.round_id,
                    slot=draft.author_slot,
                    binding=model,
                    messages=render_messages(
                        build_revise_draft_input(
                            draft, publishing_round, rules, max_iterations=version.parameters.max_iterations
                        ),
                        version.prompts,
                        model,
                    ),
                    schema=RevisionOutput,
                    draft=draft,
                    target_version=target_version,
                    **call_options(version),
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
            model_id=model.model_id,
        )

    def _validate(self, draft: PostDraft, parameters: Parameters) -> None:
        errors = ValidationGate.from_parameters(parameters).validate(draft)
        if errors:
            draft.fail_validation(errors, parameters.max_validation_retries)
        else:
            draft.pass_validation()

    async def _review(self, draft: PostDraft, publishing_round: PublishingRound, version: PipelineVersion) -> None:
        """응답이 없는 리뷰어만 병렬 호출한다. 하나가 실패해도 나머지를 기다려 성공분을 넣고 FAILED 다 (설계 04 문서 6절).

        예상하지 못한 예외도 같다: 성공한 형제의 결과를 저장한 뒤 FAILED(INTERNAL_ERROR) 로 멈춘다.
        """
        scope = draft.current_review_scope
        reviews, failures = await _gather(
            [self._call_review(draft, publishing_round, version, lens, scope) for lens in draft.pending_reviewers()]
        )
        for review in reviews:
            draft.add_review(review)
        if failures:
            self._fail(draft, failures)
        elif draft.is_review_complete():
            draft.apply_evaluation(evaluate(draft, version.parameters.max_iterations))

    async def _arbitrate(self, draft: PostDraft, publishing_round: PublishingRound, version: PipelineVersion) -> None:
        arbitrations, failures = await _gather(
            [self._call_arbitrate(draft, publishing_round, version, item) for item in draft.pending_arbitration_items()]
        )
        for arbitration in arbitrations:
            draft.add_arbitration(arbitration)
        if failures:
            self._fail(draft, failures)
        elif draft.arbitration_complete():
            draft.apply_evaluation(evaluate(draft, version.parameters.max_iterations))

    async def _post_review(self, draft: PostDraft, publishing_round: PublishingRound, version: PipelineVersion) -> None:
        """다듬기 → 전체 읽기 → 수준 평가 → 용어 추출. 한 걸음에 한 단계만 하고 포인터와 결과를 함께 저장해 재개 때 되풀이하지 않는다.

        읽기와 평가가 최종 본문을 보도록 다듬기가 먼저다. 다듬기·용어 추출 실패는 건너뛰고, 읽기·평가의 재시도 소진·예산 초과는 FAILED 다.
        """
        try:
            match draft.post_review_stage:
                case PostReviewStage.POLISH:
                    await self._polisher.polish(draft, publishing_round, version)
                case PostReviewStage.FINAL_READ:
                    await self._final_read(draft, publishing_round, version)
                case PostReviewStage.ASSESS_LEVEL:
                    await self._assess_level(draft, publishing_round, version)
                case PostReviewStage.GLOSSARY:
                    if self._glossary is None:
                        draft.finish_glossary(
                            GlossaryRecord(
                                target_version=draft.current_version.version_number, skipped="no glossary service"
                            )
                        )
                    else:
                        await self._glossary.extract(draft, publishing_round, version)
                case stage:
                    raise ValueError(f"draft {draft.draft_id} is POST_REVIEW at stage {stage}")
        except _STEP_FAILURES as exc:
            self._fail(draft, [exc])

    async def _final_read(self, draft: PostDraft, publishing_round: PublishingRound, version: PipelineVersion) -> None:
        current = draft.current_version
        model = DraftRoutes(publishing_round, draft, version.personas).final_reader
        result = await self._calls.call(
            kind=CallKind.FINAL_READ,
            round_id=draft.round_id,
            binding=model,
            messages=render_messages(build_final_read_input(draft), version.prompts, model),
            schema=FinalReadOutput,
            check=final_read_check(current.content_markdown, find_anchors(current.content_markdown)),
            draft=draft,
            target_version=current.version_number,
            **call_options(version),
        )
        draft.record_final_read(
            FinalRead(
                target_version=current.version_number,
                findings=[FinalReadFinding(**finding.model_dump()) for finding in result.output.findings],
                overall=result.output.overall,
                call_id=result.call_id,
            ),
            blocking_kinds=version.parameters.final_read.blocking_kinds,
        )

    async def _assess_level(
        self, draft: PostDraft, publishing_round: PublishingRound, version: PipelineVersion
    ) -> None:
        current = draft.current_version
        model = DraftRoutes(publishing_round, draft, version.personas).level_assessor
        result = await self._calls.call(
            kind=CallKind.ASSESS_LEVEL,
            round_id=draft.round_id,
            binding=model,
            messages=render_messages(build_assess_level_input(draft, publishing_round), version.prompts, model),
            schema=AssessLevelOutput,
            check=level_assessment_check(current.content_markdown),
            draft=draft,
            target_version=current.version_number,
            **call_options(version),
        )
        draft.record_level_assessment(
            LevelAssessment(
                target_version=current.version_number,
                level=result.output.level,
                evidence=[LevelEvidence(**evidence.model_dump()) for evidence in result.output.evidence],
                reasoning=result.output.reasoning,
                criteria_version=level_criteria_of(publishing_round).version,
                call_id=result.call_id,
            )
        )

    def _fail(self, draft: PostDraft, failures: list[Exception]) -> None:
        """사유가 여럿이면 BUDGET_EXCEEDED 를 남긴다 (설계 03 문서 5행). 재개 때 상한을 올릴 수 있기 때문이다.

        병렬 단계의 예상하지 못한 예외(설정 오류 등)는 INTERNAL_ERROR 다. 로그가 신호다 (04 문서 9절).
        """
        unexpected = [failure for failure in failures if not isinstance(failure, _STEP_FAILURES)]
        for failure in unexpected:
            logger.error("draft %s step crashed", draft.draft_id, exc_info=failure)
        if any(isinstance(failure, BudgetExceededError) for failure in failures):
            code = FailureCode.BUDGET_EXCEEDED
        elif unexpected:
            code = FailureCode.INTERNAL_ERROR
        elif any(isinstance(failure, LLMOutputTruncatedError) for failure in failures):
            code = FailureCode.OUTPUT_TRUNCATED
        else:
            code = FailureCode.LLM_RETRIES_EXHAUSTED
        draft.mark_failed(
            code,
            "; ".join(
                str(failure) if isinstance(failure, _STEP_FAILURES) else f"{type(failure).__name__}: {failure}"
                for failure in failures
            ),
        )

    async def _call_review(
        self,
        draft: PostDraft,
        publishing_round: PublishingRound,
        version: PipelineVersion,
        lens: ReviewLens,
        scope: ReviewScope,
    ) -> PeerReview:
        if draft.checks_facts(lens):
            return await self._call_fact_review(draft, publishing_round, version, lens, scope)
        current = draft.current_version
        assignment = draft.assignment_of(lens)
        review_id = f"{draft.draft_id}:v{current.version_number}:{assignment.slot or lens}:{scope}"
        section_ids = find_anchors(current.content_markdown)
        model = DraftRoutes(publishing_round, draft, version.personas).reviewer(lens)
        parameters = version.parameters
        if scope == ReviewScope.FULL:
            result = await self._calls.call(
                kind=CallKind.REVIEW,
                round_id=draft.round_id,
                lens=lens,
                binding=model,
                messages=render_messages(
                    build_review_input(draft, publishing_round, lens, parameters), version.prompts, model
                ),
                schema=ReviewOutput,
                check=review_output_check(section_ids, assignment.lenses),
                draft=draft,
                target_version=current.version_number,
                **call_options(version),
            )
            output = result.output
            items = review_items(output.items, review_id=review_id)
            item_checks: list = []
            reported = output.verdict
            feedback = output.overall_feedback
        else:
            expected_ids = [item.item_id for item in draft.previous_items_of(lens)]
            result = await self._calls.call(
                kind=CallKind.REVIEW,
                round_id=draft.round_id,
                lens=lens,
                binding=model,
                messages=render_messages(
                    build_spot_check_input(draft, publishing_round, lens, parameters), version.prompts, model
                ),
                schema=SpotCheckOutput,
                check=spot_check_output_check(section_ids, expected_ids, assignment.lenses),
                draft=draft,
                target_version=current.version_number,
                **call_options(version),
            )
            output = result.output
            items = spot_check_items(output, review_id=review_id)
            item_checks = list(output.item_checks)
            reported = None
            feedback = ""
        derived = derive_verdict(scope, items, item_checks)
        if reported is not None and reported != derived:
            # 튜닝 지표 (설계 07 문서 4절). 판정에는 derived 만 쓴다(D-03)
            logger.info("review %s reported %s but derived %s", review_id, reported, derived)
        return PeerReview(
            review_id=review_id,
            target_version=current.version_number,
            reviewer_slot=assignment.slot,
            model_id=model.model_id,
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

    async def _link_status(self, markdown: str) -> list[LinkStatus]:
        if self._links is None:
            return []
        statuses = await self._links.check(markdown_links(markdown))
        return [LinkStatus(url=url, status=status) for url, status in statuses.items()]

    async def _call_fact_review(
        self,
        draft: PostDraft,
        publishing_round: PublishingRound,
        version: PipelineVersion,
        lens: ReviewLens,
        scope: ReviewScope,
    ) -> PeerReview:
        """사실 확인 렌즈: 웹 검색 호출 → 변환 호출. 심각도는 코드가 정한다(기본 REFUTED CRITICAL, UNVERIFIABLE MINOR. 표는 조절 값).

        구조화 출력과 검색을 한 호출에 묶을 수 있는지가 벤더마다 달라(Claude 는 400) 모든 벤더가 두 호출을 쓴다.
        변환 모델이 URL 을 지어내도 검색 호출의 출처에 없으면 지운다. 검색 도구 자체의 실패는 호출 실패다.
        """
        current = draft.current_version
        review_id = f"{draft.draft_id}:v{current.version_number}:{lens}:{scope}"
        routes = DraftRoutes(publishing_round, draft, version.personas)
        model = routes.reviewer(lens)
        parameters = version.parameters
        fact_input = build_fact_check_input(
            draft, publishing_round, lens, await self._link_status(current.content_markdown), parameters
        )
        if scope == ReviewScope.SPOT_CHECK and not (fact_input.changed_section_ids or fact_input.my_previous_items):
            # 바뀐 섹션도 확인할 지적도 없다. 호출하지 않고 리뷰만 남긴다
            return PeerReview(
                review_id=review_id,
                target_version=current.version_number,
                model_id=model.model_id,
                lens=lens,
                scope=scope,
                derived_verdict=derive_verdict(scope, [], []),
                checklist=VerificationChecklist(checked_claims=["수정으로 바뀐 섹션이 없다"]),
                overall_feedback="",
                call_id="",
            )
        searched = await self._calls.search(
            round_id=draft.round_id,
            binding=model,
            messages=render_messages(fact_input, version.prompts, model),
            draft=draft,
            lens=lens,
            target_version=current.version_number,
            **call_options(version),
        )
        transform_messages = render_messages(
            build_fact_transform_input(draft, lens, searched.text, searched.citations),
            version.prompts,
            routes.fact_transformer,
        )
        section_ids = find_anchors(current.content_markdown)
        if scope == ReviewScope.FULL:
            result = await self._calls.call(
                kind=CallKind.FACT_TRANSFORM,
                round_id=draft.round_id,
                lens=lens,
                binding=routes.fact_transformer,
                messages=transform_messages,
                schema=FactCheckOutput,
                check=fact_output_check(section_ids, {lens}),
                draft=draft,
                target_version=current.version_number,
                **call_options(version),
            )
            output = result.output
            items = review_items(output.items, review_id=review_id)
            item_checks: list = []
            reported = output.verdict
            feedback = output.overall_feedback
        else:
            expected_ids = [item.item_id for item in draft.previous_items_of(lens)]
            result = await self._calls.call(
                kind=CallKind.FACT_TRANSFORM,
                round_id=draft.round_id,
                lens=lens,
                binding=routes.fact_transformer,
                messages=transform_messages,
                schema=FactSpotCheckOutput,
                check=fact_spot_check_output_check(section_ids, expected_ids, {lens}),
                draft=draft,
                target_version=current.version_number,
                **call_options(version),
            )
            output = result.output
            items = spot_check_items(output, review_id=review_id)
            item_checks = list(output.item_checks)
            reported = None
            feedback = ""
        if output.claims and not searched.citations:
            # 모델이 검색 없이 기억으로 답했다. 전부 UNVERIFIABLE 로 내려 틀린 사실이 조용히 통과하지 않게 호출 실패로 둔다
            raise LLMRetriesExhaustedError(f"fact check {review_id} made claims but the search call cited nothing")
        known = set(fact_input.known_claims)  # 모델이 지시를 어겨도 이미 끝난 주장이 새 지적으로 되살아나지 않는다
        claims = normalize_claims([claim for claim in output.claims if claim.claim not in known], searched.citations)
        items += claim_items(
            claims,
            review_id=review_id,
            lens=lens,
            scope=scope,
            start=len(items) + 1,
            severity=parameters.fact_check.severity,
        )
        return PeerReview(
            review_id=review_id,
            target_version=current.version_number,
            model_id=model.model_id,
            lens=lens,
            scope=scope,
            reported_verdict=reported,
            derived_verdict=derive_verdict(scope, items, item_checks),
            items=items,
            item_checks=item_checks,
            checklist=output.checklist,
            overall_feedback=feedback,
            call_id=result.call_id,
            claims=claims,
        )

    async def _call_arbitrate(
        self, draft: PostDraft, publishing_round: PublishingRound, version: PipelineVersion, item: ReviewItem
    ) -> Arbitration:
        """중재자는 작성·리뷰 벤더가 아닌 ARBITRATE 후보다. 사실 확인 지적의 분쟁이면 기억이 아니라 검색한 근거로 판정한다."""
        model = DraftRoutes(publishing_round, draft, version.personas).arbiter(draft.reviewer_of(item))
        version_number = draft.current_version.version_number
        evidence = None
        if item.from_fact_check:
            searched = await self._calls.search(
                round_id=draft.round_id,
                binding=model,
                messages=render_messages(build_arbitrate_search_input(draft, item), version.prompts, model),
                draft=draft,
                target_version=version_number,
                item_id=item.item_id,
                **call_options(version),
            )
            sources = "\n".join(citation.url for citation in searched.citations)
            evidence = f"{searched.text}\n\n출처:\n{sources}" if sources else searched.text
        result = await self._calls.call(
            kind=CallKind.ARBITRATE,
            round_id=draft.round_id,
            binding=model,
            messages=render_messages(build_arbitrate_input(draft, item, evidence), version.prompts, model),
            schema=ArbitrateOutput,
            draft=draft,
            target_version=version_number,
            item_id=item.item_id,
            **call_options(version),
        )
        return Arbitration(
            arbitration_id=f"{item.item_id}:arb:v{version_number}",
            target_version=version_number,
            item_id=item.item_id,
            model_id=model.model_id,
            outcome=result.output.outcome,
            reasoning=result.output.reasoning,
            call_id=result.call_id,
        )


async def _gather[T](calls: list[Awaitable[T]]) -> tuple[list[T], list[Exception]]:
    """병렬 호출을 모두 기다려 (성공 결과, 예외) 로 나눈다. Exception 이 아닌 것(취소 등)은 그대로 던진다."""
    results = await asyncio.gather(*calls, return_exceptions=True)
    successes: list[T] = []
    failures: list[Exception] = []
    for result in results:
        if isinstance(result, Exception):
            failures.append(result)
        elif isinstance(result, BaseException):
            raise result
        else:
            successes.append(result)
    return successes, failures
