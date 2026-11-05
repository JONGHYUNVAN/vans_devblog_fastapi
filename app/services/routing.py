"""글 하나의 호출별 모델 선택. models/routing 의 순수 함수를 라운드·글 상태로 묶는다.

라우팅 도입 전에 만든 라운드는 스냅샷이 없다. 그 라운드의 슬롯 바인딩으로 같은 동작의 스냅샷을 만들어 쓴다.
"""

from app.agents.personas import get_persona
from app.models.enums import AgentSlot, ReviewLens
from app.models.model_binding import CallModel, ModelBinding
from app.models.post_draft import PostDraft
from app.models.publishing_round import PublishingRound
from app.models.routing import (
    ROUTE_ARBITRATE,
    ROUTE_ASSESS_LEVEL,
    ROUTE_FACT_TRANSFORM,
    ROUTE_FINAL_READ,
    ROUTE_POLISH_FORMAT,
    ROUTE_POLISH_TONE,
    RoutingSnapshot,
    propose_route,
    review_route,
    revise_route,
    select_any,
    select_arbiter,
    select_independent,
    select_proposer,
    select_reviewers,
    select_reviser,
    select_writer,
    write_route,
)


def _call_model(binding: ModelBinding) -> CallModel:
    return CallModel(provider=binding.provider, model_id=binding.model_id, max_output_tokens=binding.max_output_tokens)


def legacy_snapshot(slot_bindings: dict[AgentSlot, ModelBinding]) -> RoutingSnapshot:
    """슬롯 바인딩 → 스냅샷. 리뷰 라우트는 그 렌즈를 주 렌즈로 가진 슬롯의 모델을 먼저 둔다."""
    models = {slot: _call_model(binding) for slot, binding in slot_bindings.items()}
    everyone = list(models.values())
    routes: dict[str, list[CallModel]] = {}
    for slot, binding in slot_bindings.items():
        persona_id = binding.persona_id
        routes[propose_route(persona_id)] = [models[slot]]
        routes[write_route(persona_id)] = [models[slot]]
        routes[revise_route(persona_id)] = [models[slot]]
    for lens in ReviewLens:
        owners = [
            slot for slot, binding in slot_bindings.items() if get_persona(binding.persona_id).primary_lens == lens
        ]
        routes[review_route(lens)] = [models[slot] for slot in owners] + everyone
    for route_id in (
        ROUTE_ARBITRATE,
        ROUTE_FINAL_READ,
        ROUTE_ASSESS_LEVEL,
        ROUTE_POLISH_FORMAT,
        ROUTE_POLISH_TONE,
        ROUTE_FACT_TRANSFORM,
    ):
        routes[route_id] = everyone
    return RoutingSnapshot(
        routes=routes, slot_personas={slot: binding.persona_id for slot, binding in slot_bindings.items()}, relaxed=True
    )


def snapshot_of(publishing_round: PublishingRound) -> RoutingSnapshot:
    return publishing_round.routing or legacy_snapshot(publishing_round.slot_bindings)


def proposer_of(publishing_round: PublishingRound, slot: AgentSlot) -> CallModel:
    snapshot = snapshot_of(publishing_round)
    return select_proposer(snapshot, snapshot.slot_personas[slot])


class DraftRoutes:
    """글 하나에서 호출 종류별로 고른 모델. 스냅샷과 글의 배정만 읽으므로 재시작해도 같다."""

    def __init__(self, publishing_round: PublishingRound, draft: PostDraft) -> None:
        self._snapshot = snapshot_of(publishing_round)
        self._persona_id = self._snapshot.slot_personas[draft.author_slot]
        self._writer = select_writer(self._snapshot, self._persona_id)
        self._lenses = [assignment.primary_lens for assignment in draft.reviewer_assignments]

    @property
    def writer(self) -> CallModel:
        return self._writer

    @property
    def reviser(self) -> CallModel:
        return select_reviser(self._snapshot, self._persona_id, self._writer.provider)

    def reviewer(self, lens: ReviewLens) -> CallModel:
        """SPOT_CHECK 라우트는 없다. 자기 지적을 확인하는 호출이라 FULL 을 맡은 모델을 다시 쓴다."""
        return select_reviewers(self._snapshot, self._writer.provider, self._lenses)[lens]

    def arbiter(self, reviewer_lens: ReviewLens) -> CallModel:
        return select_arbiter(self._snapshot, self._writer.provider, self.reviewer(reviewer_lens).provider)

    @property
    def final_reader(self) -> CallModel:
        return select_independent(self._snapshot, ROUTE_FINAL_READ, self._writer.provider)

    @property
    def level_assessor(self) -> CallModel:
        return select_independent(self._snapshot, ROUTE_ASSESS_LEVEL, self._writer.provider)

    @property
    def tone_polisher(self) -> CallModel:
        return select_independent(self._snapshot, ROUTE_POLISH_TONE, self._writer.provider)

    @property
    def format_polisher(self) -> CallModel:
        return select_any(self._snapshot, ROUTE_POLISH_FORMAT)

    @property
    def fact_transformer(self) -> CallModel:
        return select_any(self._snapshot, ROUTE_FACT_TRANSFORM)
