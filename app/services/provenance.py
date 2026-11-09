"""제작 기록: 영수증 → 글 단위 문서(순수 함수), 공개 응답 변환, 단건 보충 (설계 08 문서 3절, D-67).

블로그 글 끝의 "제작 기록" 블록을 위해 어느 단계에서 어느 모델이 무엇을 했는지를 글마다 한 문서로 굳힌다. 영수증(agent_calls)은
정리 정책이 생기면 지워질 수 있어(Q-24) 필요한 값을 복사해 자족적으로 만든다. 영수증을 (종류, 렌즈, 버전, 항목) 으로 묶으면
한 묶음이 한 단계의 시도들이고, 단계의 결과는 영수증이 아니라 글 문서에서 call_id 로 찾는다(3.3절). LLM 호출이 없고 입력이
같으면 결과가 같아 다시 만들 수 있다. 만드는 시점은 발행 성공의 따라 쓰기(DerivedRecordsWriter)고 정리가 빠진 글을 마저 쓴다.
"""

from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from app.core.exceptions import InvalidRequestError, NotFoundError
from app.models.agent_call import AgentCall, TokenUsage
from app.models.enums import CallKind, PostStatus, ProvenanceStageKind, ResolutionStatus, ReviewLens
from app.models.post_draft import PostDraft
from app.models.provenance import (
    STAGE_LABELS,
    PostProvenance,
    ProvenanceCall,
    ProvenanceModel,
    ProvenanceStage,
    ProvenanceVersion,
)
from app.models.publishing_round import PublishingRound
from app.repositories.agent_call import AgentCallRepository
from app.repositories.agent_round import PublishingRoundRepository
from app.repositories.post_draft import PostDraftRepository
from app.repositories.post_provenance import PostProvenanceRepository
from app.schemas.provenance import (
    ProvenanceCallPublic,
    ProvenanceModelPublic,
    ProvenancePublic,
    ProvenanceStagePublic,
    ProvenanceVersionPublic,
)

# (단계 종류, 렌즈, 대상 버전, 중재 항목). 영수증을 단계로 묶는 키다 (08 문서 3.3절 표)
_StageKey = tuple[ProvenanceStageKind, ReviewLens | None, int | None, str | None]


def _now() -> datetime:
    return datetime.now(UTC)


def _stage_key(call: AgentCall) -> _StageKey:
    """영수증이 속하는 단계. 모르는 종류는 ValueError 로 바로 드러낸다(CallKind 를 더하면 여기도 더한다)."""
    match call.kind:
        case CallKind.PROPOSE_TOPIC:
            return (ProvenanceStageKind.TOPIC, None, None, None)
        case CallKind.WRITE_DRAFT:
            return (ProvenanceStageKind.WRITE, None, call.target_version, None)
        case CallKind.REVISE_DRAFT:
            return (ProvenanceStageKind.REVISION, None, call.target_version, None)
        case CallKind.REVIEW:
            return (ProvenanceStageKind.REVIEW, call.lens, call.target_version, None)
        case CallKind.FACT_CHECK if call.item_id:
            # 중재가 근거를 찾으려 돌린 검색이다. 사실 확인 단계가 아니라 그 중재 항목의 단계에 묶는다
            return (ProvenanceStageKind.ARBITRATION, None, call.target_version, call.item_id)
        case CallKind.FACT_CHECK | CallKind.FACT_TRANSFORM:
            # 변환(FACT_TRANSFORM)은 같은 렌즈·버전의 사실 확인 호출의 뒷단계라 한 단계로 보인다
            return (ProvenanceStageKind.FACT_CHECK, call.lens, call.target_version, None)
        case CallKind.ARBITRATE:
            return (ProvenanceStageKind.ARBITRATION, None, call.target_version, call.item_id)
        case CallKind.POLISH_FORMAT | CallKind.POLISH_TONE:
            # polish_format 호출은 v4 에서 없어졌지만(D-62) 옛 글 영수증에 남아 있어 계속 받는다
            return (ProvenanceStageKind.POLISH, None, call.target_version, None)
        case CallKind.FINAL_READ:
            return (ProvenanceStageKind.FINAL_READ, None, call.target_version, None)
        case CallKind.ASSESS_LEVEL:
            return (ProvenanceStageKind.ASSESS_LEVEL, None, call.target_version, None)
        case CallKind.GLOSSARY:
            return (ProvenanceStageKind.GLOSSARY, None, call.target_version, None)
    raise ValueError(f"call {call.call_id} has an unknown kind {call.kind}")


def _own_calls(draft: PostDraft, calls: Iterable[AgentCall]) -> list[AgentCall]:
    """이 글의 영수증과, 라운드 영수증 가운데 이 글 슬롯의 주제 제안."""
    # 주제 제안은 글이 생기기 전 호출이라 영수증에 draft_id 가 없다. 라운드 영수증에서 이 글 슬롯의 것을 가져온다
    own = [
        call
        for call in calls
        if call.draft_id == draft.draft_id
        or (call.kind == CallKind.PROPOSE_TOPIC and call.draft_id is None and call.slot == draft.author_slot)
    ]
    return sorted(own, key=lambda call: (call.started_at, call.call_id))  # call_id 는 같은 시각의 안정 정렬용


def _succeeded(call: AgentCall) -> bool:
    return (
        call.output_parsed_ok and call.error is None
    )  # 파싱 실패도 실패다. 재시도 영수증은 attempt_number 로 구분되고 전부 남긴다


def _template_versions(calls: Iterable[AgentCall]) -> list[str]:
    return sorted({call.prompt_template_version for call in calls if call.prompt_template_version})


def _pipeline_versions(calls: Iterable[AgentCall]) -> list[str]:
    return sorted({call.pipeline_version for call in calls if call.pipeline_version})


def _summarize(call: AgentCall) -> ProvenanceCall:
    return ProvenanceCall(
        call_id=call.call_id,
        kind=call.kind,
        provider=call.model_binding.provider,
        model_id=call.model_binding.model_id,
        effort=call.model_binding.effort,
        prompt_template_version=call.prompt_template_version,
        pipeline_version=call.pipeline_version,
        attempt_number=call.attempt_number,
        succeeded=_succeeded(call),
        error=call.error,
        usage=call.usage,
        latency_ms=call.latency_ms,
        started_at=call.started_at,
    )


def _usage_sum(calls: Iterable[AgentCall]) -> TokenUsage:
    total = TokenUsage()
    for call in calls:
        total.prompt_tokens += call.usage.prompt_tokens
        total.completion_tokens += call.usage.completion_tokens
    return total


def _outcome(kind: ProvenanceStageKind, call_ids: set[str], draft: PostDraft, publishing_round: PublishingRound) -> str:
    """단계의 한 줄 결과. 영수증이 아니라 글 문서에서 call_id 로 찾는다(3.3절). 못 찾으면 "기록 없음" 류의 문구다."""
    # 영수증은 시도의 기록이고 결과(수용·기각 수, 판정, 수준)는 글 문서에만 있다. 영수증이 지워진 글도 글 문서는 남아 문장을 만들 수 있다
    version = next((each for each in draft.versions if each.call_id in call_ids), None)
    match kind:
        case ProvenanceStageKind.TOPIC:
            attempt = publishing_round.latest_attempt
            locked = attempt.locked.get(draft.author_slot) if attempt is not None else None
            source = f", 확정 시도 {locked.source_attempt}" if locked is not None else ""
            return f"시도 {len(publishing_round.topic_attempts)}회{source}"
        case ProvenanceStageKind.WRITE | ProvenanceStageKind.REVISION:
            if version is None:
                return "버전 없음"
            gate = "게이트 통과" if version.passed_validation else "게이트 실패"
            if kind == ProvenanceStageKind.WRITE:
                return f"v{version.version_number} {gate}"
            counts = Counter(resolution.status for resolution in version.resolutions)
            return (
                f"v{version.version_number} {gate}, 수용 {counts[ResolutionStatus.ACCEPTED]}"
                f"·기각 {counts[ResolutionStatus.REJECTED_WITH_REASON]}·범위 밖 {counts[ResolutionStatus.OUT_OF_SCOPE]}"
            )
        case ProvenanceStageKind.REVIEW | ProvenanceStageKind.FACT_CHECK:
            review = next((each for each in draft.reviews if each.call_id in call_ids), None)
            if review is None:
                return "리뷰 없음"
            severities = Counter(item.severity for item in review.items)
            text = f"{review.scope} {review.derived_verdict}, " + "·".join(
                f"{severity} {severities[severity]}" for severity in severities or []
            ).rstrip("·")
            if not severities:
                text = f"{review.scope} {review.derived_verdict}, 지적 없음"
            if review.claims:
                statuses = Counter(claim.status for claim in review.claims)
                text += ", 주장 " + "·".join(f"{status} {count}" for status, count in statuses.items())
            return text
        case ProvenanceStageKind.ARBITRATION:
            arbitration = next((each for each in draft.arbitrations if each.call_id in call_ids), None)
            return arbitration.outcome.value if arbitration is not None else "판정 없음"
        case ProvenanceStageKind.POLISH:
            polish = draft.polish
            if polish is None:
                return "기록 없음"
            text = f"적용 {polish.applied}·버림 {len(polish.discarded)}"
            if polish.tells_before:  # AI 문체 패턴 횟수(D-90). 기록이 없는 옛 글은 붙이지 않는다
                text += f", 문체 패턴 {sum(polish.tells_before.values())}→{sum(polish.tells_after.values())}"
            if version is not None:
                text += f", 새 버전 v{version.version_number}"
            if polish.skipped:
                text += ", 건너뜀: " + "; ".join(polish.skipped)
            return text
        case ProvenanceStageKind.FINAL_READ:
            final_read = draft.final_read
            if final_read is None or final_read.call_id not in call_ids:
                return "기록 없음"
            blocking = sum(1 for finding in final_read.findings if finding.kind.is_blocking)
            return f"지적 {len(final_read.findings)}건(차단 {blocking})"
        case ProvenanceStageKind.ASSESS_LEVEL:
            assessment = draft.level_assessment
            return (
                f"수준 {assessment.level}" if assessment is not None and assessment.call_id in call_ids else "기록 없음"
            )
        case ProvenanceStageKind.GLOSSARY:
            glossary = draft.glossary
            if glossary is None:
                return "기록 없음"
            if glossary.skipped:
                return f"건너뜀: {glossary.skipped}"
            return f"용어 {len(glossary.items)}건(보류 {len(glossary.pending)}, 버림 {len(glossary.discarded)})"
    return ""


def _build_stage(
    key: _StageKey, calls: list[AgentCall], draft: PostDraft, publishing_round: PublishingRound
) -> ProvenanceStage:
    kind, lens, target_version, item_id = key
    call_ids = {call.call_id for call in calls}
    models: list[ProvenanceModel] = []
    for call in calls:  # 시도마다 모델이 다를 수 있다(재시도 폴백). 집합이되 첫 등장 순을 지킨다
        model = ProvenanceModel(
            provider=call.model_binding.provider, model_id=call.model_binding.model_id, effort=call.model_binding.effort
        )
        if model not in models:
            models.append(model)
    review = next((each for each in draft.reviews if each.call_id in call_ids), None)
    return ProvenanceStage(
        stage=kind,
        label=STAGE_LABELS[kind],
        # 옛 글(슬롯 리뷰어, 라우팅 전)은 영수증에 렌즈가 없다. 글 문서의 리뷰에서 찾고, 그래도 없으면 비워 둔다(3.3절)
        lens=lens if lens is not None else (review.lens if review is not None else None),
        scope=review.scope if review is not None else None,
        target_version=target_version,
        item_id=item_id,
        models=models,
        prompt_template_versions=_template_versions(calls),
        pipeline_versions=_pipeline_versions(calls),
        calls=[_summarize(call) for call in calls],
        usage=_usage_sum(calls),
        outcome=_outcome(kind, call_ids, draft, publishing_round),
    )


def build_provenance(
    draft: PostDraft,
    publishing_round: PublishingRound,
    calls: Iterable[AgentCall],
    *,
    built_by: str,
    now: datetime | None = None,
) -> PostProvenance:
    """PUBLISHED 글의 제작 기록. 영수증이 하나도 없어도(이미 정리된 글) 글 문서만으로 만든다.

    순수 함수다: 같은 글·라운드·영수증이면 built_at 만 다른 같은 문서가 나온다. 발행 전 글이면 ValueError.
    """
    if draft.publication is None:
        raise ValueError(f"draft {draft.draft_id} is not published")
    # 1) 이 글의 영수증만 시작 순으로 고른다
    own = _own_calls(draft, calls)
    # 2) 단계로 묶는다. dict 는 삽입 순이라 단계 순서 = 그 단계 첫 시도의 시작 순이다(3.3절 "같은 종류는 버전·시작 시각 순")
    groups: dict[_StageKey, list[AgentCall]] = {}
    for call in own:
        groups.setdefault(_stage_key(call), []).append(call)
    stages = [_build_stage(key, group, draft, publishing_round) for key, group in groups.items()]
    # 3) 작성 모델. v1 영수증이 있으면 거기서, 영수증이 지워진 글은 라운드의 슬롯 바인딩에서 (3.4절). 블로그 작성자 표기의 근거다
    by_call_id = {call.call_id: call for call in own}
    first = draft.versions[0] if draft.versions else None
    writer_call = by_call_id.get(first.call_id) if first is not None else None
    if writer_call is not None:
        writer = ProvenanceModel(
            provider=writer_call.model_binding.provider,
            model_id=writer_call.model_binding.model_id,
            effort=writer_call.model_binding.effort,
        )
    else:
        binding = publishing_round.slot_bindings[draft.author_slot]
        writer = ProvenanceModel(
            provider=binding.provider,
            model_id=first.model_id if first is not None and first.model_id else binding.model_id,
        )
    # 4) 발행 버전과 다듬기 전 버전. 머리말 노트의 "v1 → vN" 과 같은 근거라 두 숫자가 어긋나면 안 된다
    final_version = draft.publication.final_version
    authored = draft.authored_publishable_version
    published = next((each for each in draft.versions if each.version_number == final_version), None)
    return PostProvenance(
        post_id=draft.publication.external_post_id,
        draft_id=draft.draft_id,
        round_id=draft.round_id,
        round_seq=publishing_round.round_seq,
        author_slot=draft.author_slot,
        author_provider=publishing_round.slot_bindings[draft.author_slot].provider,
        writer=writer,
        title=published.title if published is not None else draft.topic.title,
        published_at=draft.publication.published_at,
        final_version=final_version,
        authored_version=authored.version_number if authored is not None else final_version,
        versions=[
            ProvenanceVersion(
                version_number=each.version_number,
                origin_cause=each.origin_cause,
                model_id=each.model_id,
                passed_validation=each.passed_validation,
                call_id=each.call_id,
            )
            for each in draft.versions
        ],
        iteration=draft.iteration,
        level=draft.level_assessment.level if draft.level_assessment is not None else None,
        user_level=draft.user_level,
        prompt_template_versions=_template_versions(own),
        pipeline_versions=_pipeline_versions(own),
        stages=stages,
        usage_total=_usage_sum(own),
        call_count=len(own),
        failed_attempts=sum(1 for call in own if not _succeeded(call)),
        built_at=now or _now(),
        built_by=built_by,  # type: ignore[arg-type]  # Literal 필드지만 호출자(따라 쓰기·CLI)는 str 로 넘긴다. pydantic 이 세 값만 받는다
    )


def build_provenance_public(record: PostProvenance) -> ProvenancePublic:
    """공개 응답. 호출 오류 문자열과 built_by 를 뺀다."""
    # 필드를 하나씩 옮기는 것은 일부러다. model_dump 로 통째 넘기면 저장 문서에 필드가 늘 때 공개 범위가 저절로 넓어진다
    return ProvenancePublic(
        post_id=record.post_id,
        draft_id=record.draft_id,
        round_id=record.round_id,
        round_seq=record.round_seq,
        author_slot=record.author_slot,
        author_provider=record.author_provider,
        writer=ProvenanceModelPublic(**record.writer.model_dump()),
        title=record.title,
        published_at=record.published_at,
        final_version=record.final_version,
        authored_version=record.authored_version,
        versions=[
            ProvenanceVersionPublic(
                version_number=each.version_number,
                origin_cause=each.origin_cause,
                model_id=each.model_id,
                passed_validation=each.passed_validation,
            )
            for each in record.versions
        ],
        iteration=record.iteration,
        level=record.level,
        user_level=record.user_level,
        prompt_template_versions=record.prompt_template_versions,
        pipeline_versions=record.pipeline_versions,
        stages=[
            ProvenanceStagePublic(
                stage=stage.stage,
                label=stage.label,
                lens=stage.lens,
                scope=stage.scope,
                target_version=stage.target_version,
                item_id=stage.item_id,
                models=[ProvenanceModelPublic(**model.model_dump()) for model in stage.models],
                prompt_template_versions=stage.prompt_template_versions,
                pipeline_versions=stage.pipeline_versions,
                calls=[
                    ProvenanceCallPublic(
                        call_id=call.call_id,
                        kind=call.kind,
                        provider=call.provider,
                        model_id=call.model_id,
                        effort=call.effort,
                        prompt_template_version=call.prompt_template_version,
                        pipeline_version=call.pipeline_version,
                        attempt_number=call.attempt_number,
                        succeeded=call.succeeded,
                        prompt_tokens=call.usage.prompt_tokens,
                        completion_tokens=call.usage.completion_tokens,
                        latency_ms=call.latency_ms,
                        started_at=call.started_at,
                    )
                    for call in stage.calls
                ],
                prompt_tokens=stage.usage.prompt_tokens,
                completion_tokens=stage.usage.completion_tokens,
                outcome=stage.outcome,
            )
            for stage in record.stages
        ],
        prompt_tokens=record.usage_total.prompt_tokens,
        completion_tokens=record.usage_total.completion_tokens,
        total_tokens=record.usage_total.total_tokens,
        call_count=record.call_count,
        failed_attempts=record.failed_attempts,
        schema_version=record.schema_version,
        built_at=record.built_at,
    )


class ProvenanceService:
    """공개 조회와 단건 보충. 발행 때의 따라 쓰기와 정리는 DerivedRecordsWriter 가 한다."""

    def __init__(
        self,
        *,
        repository: PostProvenanceRepository,
        draft_repository: PostDraftRepository,
        round_repository: PublishingRoundRepository,
        agent_call_repository: AgentCallRepository,
        now: Callable[[], datetime] = _now,
    ) -> None:
        """공개 조회는 repository 만 쓴다. 나머지 셋은 CLI 의 단건 보충(build_for_draft)이 글·라운드·영수증을 읽는 데 필요하다."""
        self._records = repository
        self._drafts = draft_repository
        self._rounds = round_repository
        self._calls = agent_call_repository
        self._now = now

    async def get_public(self, post_id: str) -> ProvenancePublic:
        """게시글 id 로 공개 응답. 기록이 없으면 NotFoundError(라우터가 404).

        여기서 만들어 채우지 않는다: 키 없는 공개 경로가 쓰기를 하면 안 되고, 빠진 글은 정리(reconcile)가 채운다.
        """
        record = await self._records.get(post_id)
        if record is None:
            raise NotFoundError(f"post {post_id} has no provenance")
        return build_provenance_public(record)

    async def build_for_draft(self, draft_id: str, *, rebuild: bool = False, built_by: str = "cli") -> PostProvenance:
        """PUBLISHED 글의 기록을 만든다. 있으면 그대로 두고, --rebuild 면 덮어쓴다.

        글이 없거나 라운드가 없으면 NotFoundError, 발행 전 글이면 InvalidRequestError.
        """
        draft = await self._drafts.get(draft_id)
        if draft is None:
            raise NotFoundError(f"draft {draft_id} does not exist")
        if draft.status != PostStatus.PUBLISHED or draft.publication is None:
            raise InvalidRequestError(f"draft {draft_id} is {draft.status}, not PUBLISHED")
        publishing_round = await self._rounds.get(draft.round_id)
        if publishing_round is None:
            raise NotFoundError(f"round {draft.round_id} does not exist")
        record = build_provenance(
            draft, publishing_round, await self._calls.list_by_round(draft.round_id), built_by=built_by, now=self._now()
        )
        if rebuild:
            await self._records.save(record)  # 스키마가 바뀌었을 때. 옛 문서를 새 모양으로 다시 쓴다
        elif not await self._records.add_if_missing(record):
            # 발행 때 따라 쓰기와 같은 멱등 규칙이다: 이미 있으면 그것이 진실이고 방금 만든 것은 버린다
            existing = await self._records.get(record.post_id)
            if existing is not None:
                return existing
        return record
