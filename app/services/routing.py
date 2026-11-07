"""글 하나의 호출별 모델 선택. models/routing 의 순수 함수를 라운드·글 상태로 묶는다.

라우팅 도입 전에 만든 라운드는 스냅샷이 없다. 그 라운드의 슬롯 바인딩으로 같은 동작의 스냅샷을 만들어 쓴다.
"""

from collections.abc import Mapping

from app.agents.personas import get_persona
from app.models.enums import AgentSlot, ReviewLens
from app.models.model_binding import CallModel, ModelBinding
from app.models.persona import PersonaProfile
from app.models.post_draft import PostDraft
from app.models.publishing_round import PublishingRound
from app.models.routing import (
    ROUTE_ARBITRATE,
    ROUTE_ASSESS_LEVEL,
    ROUTE_FACT_TRANSFORM,
    ROUTE_FINAL_READ,
    ROUTE_GLOSSARY,
    ROUTE_POLISH_TONE,
    RoutingSnapshot,
    propose_route,
    review_route,
    revise_route,
    select_any,
    select_arbiter,
    select_independent,
    select_optional,
    select_proposer,
    select_reviewers,
    select_reviser,
    select_writer,
    write_route,
)

Personas = Mapping[str, PersonaProfile] | None  # 라운드 버전 문서의 personas. None 이면 코드 기본 상수


def _call_model(binding: ModelBinding) -> CallModel:
    return CallModel(provider=binding.provider, model_id=binding.model_id, max_output_tokens=binding.max_output_tokens)


def legacy_snapshot(slot_bindings: dict[AgentSlot, ModelBinding], personas: Personas = None) -> RoutingSnapshot:
    """슬롯 바인딩 → 스냅샷. 리뷰 라우트는 그 렌즈를 주 렌즈로 가진 슬롯의 모델을 먼저 둔다."""
    models = {slot: _call_model(binding) for slot, binding in slot_bindings.items()}
    everyone = list(models.values())
    routes: dict[str, list[CallModel]] = {}
    for slot, binding in slot_bindings.items():
        persona_id = binding.persona_id
        routes[propose_route(persona_id)] = [models[slot]]
        routes[write_route(persona_id)] = [models[slot]]
        routes[revise_route(persona_id)] = [models[slot]]
    # 리뷰 라우트는 그 렌즈를 주 렌즈로 가진 슬롯의 모델을 앞에 두고 전원을 뒤에 둔다. 선택기(select_reviewers)가 작성 벤더와
    # 앞선 리뷰 벤더를 피한 첫 후보를 고르므로 이 순서가 곧 라우팅 전의 "주 렌즈 리뷰어 우선" 동작이다
    for lens in ReviewLens:
        owners = [
            slot
            for slot, binding in slot_bindings.items()
            if get_persona(binding.persona_id, personas).primary_lens == lens
        ]
        routes[review_route(lens)] = [models[slot] for slot in owners] + everyone
    for route_id in (
        ROUTE_ARBITRATE,
        ROUTE_FINAL_READ,
        ROUTE_ASSESS_LEVEL,
        ROUTE_POLISH_TONE,
        ROUTE_FACT_TRANSFORM,
    ):
        routes[route_id] = everyone
    # relaxed: 슬롯 바인딩 셋만으로는 벤더가 셋 미만일 수 있다. 독립 후보가 없으면 첫 후보로 물러서야 옛 라운드가 멈추지 않는다
    return RoutingSnapshot(
        routes=routes, slot_personas={slot: binding.persona_id for slot, binding in slot_bindings.items()}, relaxed=True
    )


def snapshot_of(publishing_round: PublishingRound, personas: Personas = None) -> RoutingSnapshot:
    """라운드의 유효 스냅샷. 라우팅 전 라운드는 슬롯 바인딩으로 같은 동작의 스냅샷을 만든다."""
    return publishing_round.routing or legacy_snapshot(publishing_round.slot_bindings, personas)


def proposer_of(publishing_round: PublishingRound, slot: AgentSlot, personas: Personas = None) -> CallModel:
    """슬롯의 주제 제안 모델."""
    snapshot = snapshot_of(publishing_round, personas)
    return select_proposer(snapshot, snapshot.slot_personas[slot])


class DraftRoutes:
    """글 하나에서 호출 종류별로 고른 모델. 스냅샷과 글의 배정만 읽으므로 재시작해도 같다."""

    def __init__(self, publishing_round: PublishingRound, draft: PostDraft, personas: Personas = None) -> None:
        self._snapshot = snapshot_of(publishing_round, personas)
        self._persona_id = self._snapshot.slot_personas[draft.author_slot]
        self._writer = select_writer(self._snapshot, self._persona_id)
        self._lenses = [assignment.primary_lens for assignment in draft.reviewer_assignments]

    @property
    def writer(self) -> CallModel:
        """작성 모델(v1). 이 벤더가 회피의 기준이다."""
        return self._writer

    @property
    def reviser(self) -> CallModel:
        """수정 모델: 작성 벤더 안에서 고른다. 사람 재수정도 같다(D-52)."""
        return select_reviser(self._snapshot, self._persona_id, self._writer.provider)

    def reviewer(self, lens: ReviewLens) -> CallModel:
        """SPOT_CHECK 라우트는 없다. 자기 지적을 확인하는 호출이라 FULL 을 맡은 모델을 다시 쓴다."""
        return select_reviewers(self._snapshot, self._writer.provider, self._lenses)[lens]

    def arbiter(self, reviewer_lens: ReviewLens) -> CallModel:
        """교착 항목의 중재 모델: 작성 벤더와 그 렌즈의 리뷰 벤더 밖."""
        return select_arbiter(self._snapshot, self._writer.provider, self.reviewer(reviewer_lens).provider)

    @property
    def final_reader(self) -> CallModel:
        """전체 읽기 모델: 작성 벤더 밖."""
        return select_independent(self._snapshot, ROUTE_FINAL_READ, self._writer.provider)

    @property
    def level_assessor(self) -> CallModel:
        """수준 평가 모델: 작성 벤더 밖."""
        return select_independent(self._snapshot, ROUTE_ASSESS_LEVEL, self._writer.provider)

    @property
    def tone_polisher(self) -> CallModel:
        """문체 다듬기 모델: 작성 벤더 밖."""
        return select_independent(self._snapshot, ROUTE_POLISH_TONE, self._writer.provider)

    @property
    def fact_transformer(self) -> CallModel:
        """사실 확인 변환 모델: 벤더 제약이 없다(저가 모델)."""
        return select_any(self._snapshot, ROUTE_FACT_TRANSFORM)

    @property
    def glossary_extractor(self) -> CallModel | None:
        """용어 추출. 벤더 제약이 없다. 라우트가 없는 라운드(이 기능 전 스냅샷)는 None 이고 단계를 건너뛴다."""
        return select_optional(self._snapshot, ROUTE_GLOSSARY)
