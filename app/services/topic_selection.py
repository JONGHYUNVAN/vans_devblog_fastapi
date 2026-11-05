"""Phase 1 주제 선정: 제안 수집, 중복 판별, 배정, 확정 (설계 04 문서 2절, 03 문서 3절).

drive(round_id) 는 라운드가 TOPIC_SELECTION 인 동안 한 걸음(제안 → 배정 → 확정)씩 진행하고 매번 저장한다.
재시작하면 저장된 TopicAttempt 로 빠진 슬롯만 다시 부르고, 전체 잠금이 있으면 확정만 다시 한다.
"""

import asyncio
import itertools
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from app.agents.builders import build_propose_topic_input, render_messages
from app.agents.contracts import ProposeTopicOutput
from app.agents.parsers import topic_proposal_check
from app.core.config import Settings
from app.core.exceptions import ConcurrentUpdateError, LLMRetriesExhaustedError
from app.models.enums import AgentSlot, CallKind, RoundAbortReason, RoundStatus
from app.models.publishing_round import LockedTopic, PublishingRound, TopicAttempt, TopicConflict
from app.models.topic import TopicProposal, TopicRegistryEntry
from app.repositories.agent_round import PublishingRoundRepository
from app.repositories.post_draft import PostDraftRepository
from app.repositories.topic_registry import TopicRegistryRepository
from app.services.agent_calls import AgentCallService
from app.services.round import RoundService
from app.services.routing import proposer_of

logger = logging.getLogger(__name__)

_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)


class TopicSelectionService:
    """주제 선정 중인 라운드 하나를 확정(IN_PROGRESS)이나 ABORTED 까지 진행한다. 러너가 라운드마다 코루틴 하나로 돌린다."""

    def __init__(
        self,
        *,
        round_repository: PublishingRoundRepository,
        draft_repository: PostDraftRepository,
        topic_registry_repository: TopicRegistryRepository,
        round_service: RoundService,
        agent_call_service: AgentCallService,
        settings: Settings,
    ) -> None:
        self._rounds = round_repository
        self._drafts = draft_repository
        self._topics = topic_registry_repository
        self._round_service = round_service
        self._calls = agent_call_service
        self._settings = settings

    async def drive(self, round_id: str) -> None:
        while True:
            publishing_round = await self._rounds.get(round_id)
            if publishing_round is None or publishing_round.status != RoundStatus.TOPIC_SELECTION:
                return
            try:
                await self._step(publishing_round)
            except ConcurrentUpdateError:
                # 라운드 중단과 겹쳤다. 결과를 버리고 다시 읽는다. 호출 비용은 AgentCall 에 남는다
                continue

    async def _step(self, publishing_round: PublishingRound) -> None:
        attempt = publishing_round.latest_attempt
        if attempt is None:
            attempt = TopicAttempt(attempt_number=1)
            publishing_round.topic_attempts.append(attempt)
        if attempt.is_fully_locked:
            await self._commit(publishing_round)
            return
        pending = [slot for slot in AgentSlot if slot not in attempt.locked and slot not in attempt.proposals]
        if pending:
            await self._propose(publishing_round, attempt, pending)
        else:
            await self._assign(publishing_round, attempt)
        await self._rounds.save(publishing_round)

    async def _propose(self, publishing_round: PublishingRound, attempt: TopicAttempt, slots: list[AgentSlot]) -> None:
        """잠기지 않은 슬롯에 PROPOSE_TOPIC 을 병렬 호출한다. 하나라도 소진하면 나머지를 기다린 뒤 ABORTED(LLM_FAILED)."""
        since = datetime.now(UTC) - timedelta(days=self._settings.topic_avoid_lookback_days)
        avoid_entries = [
            entry
            for entry in await self._topics.list_active_since(since)
            if entry.round_id != publishing_round.round_id
        ]
        previous = publishing_round.topic_attempts[-2] if len(publishing_round.topic_attempts) > 1 else None
        results = await asyncio.gather(
            *(
                self._calls.call(
                    kind=CallKind.PROPOSE_TOPIC,
                    round_id=publishing_round.round_id,
                    slot=slot,
                    binding=proposer_of(publishing_round, slot),
                    messages=render_messages(
                        build_propose_topic_input(
                            publishing_round,
                            slot,
                            attempt_number=attempt.attempt_number,
                            avoid_entries=avoid_entries,
                            conflict_feedback=_conflict_feedback(previous, slot),
                        ),
                        self._settings.prompt_version,
                    ),
                    schema=ProposeTopicOutput,
                    check=topic_proposal_check(
                        publishing_round.categories,
                        persona_id=publishing_round.slot_bindings[slot].persona_id,
                        has_evidence=bool(publishing_round.evidence),
                    ),
                    topic_attempt=attempt.attempt_number,
                )
                for slot in slots
            ),
            return_exceptions=True,
        )
        failed = False
        for slot, result in zip(slots, results, strict=True):
            if isinstance(result, LLMRetriesExhaustedError):
                failed = True
            elif isinstance(result, BaseException):
                raise result
            else:
                attempt.proposals[slot] = result.output.proposals
        if failed:
            # 글이 없으므로 ESCALATED 로 올릴 대상이 없다. 성공한 제안은 이력으로 남긴다
            publishing_round.abort(RoundAbortReason.LLM_FAILED)

    async def _assign(self, publishing_round: PublishingRound, attempt: TopicAttempt) -> None:
        """충돌 없는 조합 중 잠그는 슬롯 수가 가장 많고 순위 합이 가장 작은 배정 (설계 04 문서 2절 4~6단계)."""
        registry = [entry for entry in await self._topics.list_active() if entry.round_id != publishing_round.round_id]
        attempt.conflicts = self._find_conflicts(attempt, registry)
        # 잠긴 주제·레지스트리와의 충돌은 어느 조합에서든 못 쓴다. 제안끼리의 충돌은 조합마다 본다
        conflicting = {
            (conflict.slot, conflict.proposal_index)
            for conflict in attempt.conflicts
            if conflict.registry_title is not None or conflict.other_slot in attempt.locked
        }
        open_slots = [slot for slot in AgentSlot if slot not in attempt.locked]
        priority = _slot_priority(publishing_round.round_seq)
        best: tuple[tuple[int, int, tuple[int, ...]], dict[AgentSlot, int]] | None = None
        for choice in itertools.product(*([None, 0, 1, 2] for _ in open_slots)):
            chosen = {slot: index for slot, index in zip(open_slots, choice, strict=True) if index is not None}
            if any((slot, index) in conflicting for slot, index in chosen.items()):
                continue
            if _conflicts_among(
                [(slot, attempt.proposals[slot][index]) for slot, index in chosen.items()], self._settings
            ):
                continue
            key = (
                -len(chosen),
                sum(chosen.values()),
                tuple(chosen.get(slot, len(AgentSlot)) for slot in priority),
            )
            if best is None or key < best[0]:
                best = (key, chosen)
        assert best is not None  # 아무것도 잠그지 않는 조합은 항상 유효하다
        for slot, index in best[1].items():
            attempt.locked[slot] = LockedTopic(
                slot=slot,
                source_attempt=attempt.attempt_number,
                proposal_index=index,
                proposal=attempt.proposals[slot][index],
            )
        if attempt.is_fully_locked:
            return
        if attempt.attempt_number >= self._settings.max_topic_attempts:
            publishing_round.abort(RoundAbortReason.TOPIC_ATTEMPTS_EXHAUSTED)
            return
        # 잠긴 주제는 스냅샷으로 다음 시도에 넘기고, 남은 슬롯만 재제안한다
        publishing_round.topic_attempts.append(
            TopicAttempt(attempt_number=attempt.attempt_number + 1, locked=dict(attempt.locked))
        )

    def _find_conflicts(self, attempt: TopicAttempt, registry: list[TopicRegistryEntry]) -> list[TopicConflict]:
        """이번 시도 제안 각각이 잠긴 주제·다른 슬롯의 제안·레지스트리와 겹치는지 (설계 04 문서 2절 3단계)."""
        conflicts: list[TopicConflict] = []
        others: list[tuple[AgentSlot, int, TopicProposal]] = [
            (locked_slot, locked.proposal_index, locked.proposal) for locked_slot, locked in attempt.locked.items()
        ] + [
            (other_slot, other_index, other)
            for other_slot, proposals in attempt.proposals.items()
            if other_slot not in attempt.locked
            for other_index, other in enumerate(proposals)
        ]
        for slot, proposals in attempt.proposals.items():
            if slot in attempt.locked:
                continue
            for index, proposal in enumerate(proposals):
                for other_slot, other_index, other in others:
                    if other_slot == slot:
                        continue
                    kind, score = _overlap(proposal, other.title, other.keywords, self._settings)
                    if kind:
                        conflicts.append(
                            TopicConflict(
                                slot=slot,
                                proposal_index=index,
                                other_slot=other_slot,
                                other_index=other_index,
                                registry_title=None,
                                kind=kind,
                                score=score,
                            )
                        )
                for entry in registry:
                    kind, score = _overlap(proposal, entry.title, entry.keywords, self._settings)
                    if kind:
                        conflicts.append(
                            TopicConflict(
                                slot=slot,
                                proposal_index=index,
                                other_slot=None,
                                other_index=None,
                                registry_title=entry.title,
                                kind="registry",
                                score=score,
                            )
                        )
        return conflicts

    async def _commit(self, publishing_round: PublishingRound) -> None:
        """글 3편·레지스트리 3건을 없을 때만 만들고 라운드를 IN_PROGRESS 로 바꾼다. 라운드 저장이 커밋 지점이다 (설계 07 문서 D-28)."""
        for slot in AgentSlot:
            draft = self._round_service.build_draft(publishing_round, slot, self._settings.draft_token_limit)
            await self._drafts.add_if_missing(draft)
            await self._topics.add_if_missing(
                TopicRegistryEntry(
                    entry_id=draft.draft_id,
                    title=draft.topic.title,
                    keywords=draft.topic.keywords,
                    draft_id=draft.draft_id,
                    round_id=publishing_round.round_id,
                )
            )
        publishing_round.lock_topics()
        await self._rounds.save(publishing_round)
        logger.info("round %s locked topics for every slot", publishing_round.round_id)


def _slot_priority(round_seq: int) -> list[AgentSlot]:
    slots = list(AgentSlot)
    start = round_seq % len(slots)
    return slots[start:] + slots[:start]


def _conflicts_among(chosen: list[tuple[AgentSlot, TopicProposal]], settings: Settings) -> bool:
    for (_, first), (_, second) in itertools.combinations(chosen, 2):
        if _overlap(first, second.title, second.keywords, settings)[0]:
            return True
    return False


def _overlap(
    proposal: TopicProposal, title: str, keywords: Iterable[str], settings: Settings
) -> tuple[str | None, float]:
    """충돌 종류와 점수. 키워드 Jaccard 를 먼저 보고, 아니면 제목 토큰 Jaccard 를 본다. 둘 다 아니면 (None, 0)."""
    keyword_score = _jaccard(_normalize(proposal.keywords), _normalize(keywords))
    if keyword_score >= settings.topic_keyword_overlap:
        return "keyword", keyword_score
    title_score = _jaccard(_tokens(proposal.title), _tokens(title))
    if title_score >= settings.topic_title_overlap:
        return "title", title_score
    return None, 0.0


def _conflict_feedback(previous: TopicAttempt | None, slot: AgentSlot) -> str | None:
    if previous is None:
        return None
    lines = []
    for conflict in previous.conflicts:
        if conflict.slot != slot:
            continue
        title = previous.proposals[slot][conflict.proposal_index].title
        if conflict.registry_title is not None:
            other = f'이미 다룬 주제 "{conflict.registry_title}"'
        elif conflict.other_slot in previous.proposals and conflict.other_index is not None:
            other = f'다른 작성자의 주제 "{previous.proposals[conflict.other_slot][conflict.other_index].title}"'
        elif conflict.other_slot in previous.locked:
            # 이전 시도에서 잠긴 주제와 겹쳤다. 잠긴 슬롯은 그 시도에 제안이 없다
            other = f'다른 작성자의 주제 "{previous.locked[conflict.other_slot].proposal.title}"'
        else:
            other = "다른 작성자의 주제"
        lines.append(f'이전 제안 "{title}" 은(는) {other} 와 {conflict.kind} 기준으로 겹쳤다')
    if not lines:
        return "이전 제안이 다른 작성자의 제안과 겹쳐 잠그지 못했다. 다른 영역의 주제를 내라"
    return "\n".join(lines)


def _normalize(words: Iterable[str]) -> set[str]:
    return {word.strip().lower() for word in words if word.strip()}


def _tokens(title: str) -> set[str]:
    return {token.lower() for token in _TOKEN_PATTERN.findall(title)}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
