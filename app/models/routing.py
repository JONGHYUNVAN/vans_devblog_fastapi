"""호출별 모델 라우트: agent_routes 문서, 라운드 스냅샷, 후보 선택과 라운드 생성 전 검사.

라우트는 라운드를 만들 때 한 번 읽어 스냅샷을 뜬다. 호출마다 읽으면 진행 중인 글의 모델이 중간에 바뀐다.
후보 배열은 장애 대비(failover)가 아니라 벤더 충돌을 피하려고 있다. 호출이 실패하면 재시도를 소진하고 FAILED 다.
선택은 스냅샷과 글 상태만 읽는 순수 함수라 재시작해도 결과가 같다.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import AgentSlot, ReviewLens
from app.models.level import DEFAULT_LEVEL_CRITERIA, LevelCriteria
from app.models.model_binding import CallModel

SLOTS_DOCUMENT_ID = "SLOTS"  # {_id: "SLOTS", A: "architect", B: ..., C: ...}
LEVEL_CRITERIA_DOCUMENT_ID = "LEVEL_CRITERIA"

ROUTE_ARBITRATE = "ARBITRATE"
ROUTE_FINAL_READ = "FINAL_READ"
ROUTE_ASSESS_LEVEL = "ASSESS_LEVEL"
ROUTE_POLISH_FORMAT = "POLISH_FORMAT"  # v4 에서 뺐다. 옛 스냅샷·영수증에만 있다
ROUTE_POLISH_TONE = "POLISH_TONE"
ROUTE_FACT_TRANSFORM = "FACT_TRANSFORM"
ROUTE_GLOSSARY = "GLOSSARY"  # 용어 추출. 없으면 단계를 건너뛴다 (설계 08 문서 D-64). 배포된 라우트에 뒤늦게 더해진 유일한 선택 라우트
# 웹 검색을 하는 렌즈. 이 렌즈의 모델이 사실 확인 검색과 그 지적의 중재 검색을 모두 한다 (09 문서 D-92)
WEB_SEARCH_LENS = ReviewLens.FACT_STRUCTURE

_DEFAULT_MAX_OUTPUT = 64_000  # 비스트리밍 호출로 받는 상한. 그 이상은 스트리밍이 필요하다


def propose_route(persona_id: str) -> str:
    """주제 제안 라우트 id `PROPOSE_TOPIC:{persona}`."""
    return f"PROPOSE_TOPIC:{persona_id}"


def write_route(persona_id: str) -> str:
    """작성 라우트 id `WRITE_DRAFT:{persona}`."""
    return f"WRITE_DRAFT:{persona_id}"


def revise_route(persona_id: str) -> str:
    """수정 라우트 id `REVISE_DRAFT:{persona}`."""
    return f"REVISE_DRAFT:{persona_id}"


def review_route(lens: ReviewLens) -> str:
    """리뷰 라우트 id `REVIEW:{lens}`. 렌즈마다 모델 하나다(D-55)."""
    return f"REVIEW:{lens}"


@dataclass(frozen=True)
class _Capability:
    efforts: frozenset[str]
    temperature: bool
    max_output_tokens: int = _DEFAULT_MAX_OUTPUT


_EFFORTS_TO_MAX = frozenset({"low", "medium", "high", "xhigh", "max"})
_EFFORTS_TO_XHIGH = frozenset({"low", "medium", "high", "xhigh"})
_EFFORTS_TO_HIGH = frozenset({"low", "medium", "high"})
# 벤더·모델 지원표. 클라이언트가 값을 조용히 버리면 영수증과 실제 요청이 달라지므로 라운드 생성에서 거절한다.
# 표에 없는 벤더(테스트의 가짜 등)는 제한하지 않는다. 모델 키는 접두사다. 비스트리밍 직접 호출의 Gemini 상한은 32,768 이다.
_CAPABILITIES: dict[str, dict[str, _Capability]] = {
    "anthropic": {"": _Capability(_EFFORTS_TO_MAX, temperature=False)},
    "google": {
        "gemini-4-argon": _Capability(_EFFORTS_TO_HIGH, temperature=False, max_output_tokens=32_768),
        "": _Capability(_EFFORTS_TO_HIGH, temperature=True, max_output_tokens=32_768),
    },
    "xai": {"": _Capability(_EFFORTS_TO_XHIGH, temperature=True)},
}


def _capability_of(provider: str, model_id: str) -> _Capability | None:
    by_model = _CAPABILITIES.get(provider)
    if by_model is None:
        return None
    return next(capability for prefix, capability in by_model.items() if model_id.startswith(prefix))


def candidate_errors(route_id: str, candidate: CallModel) -> list[str]:
    """지원표에 없는 조합의 설명. 비어 있으면 통과다."""
    capability = _capability_of(candidate.provider, candidate.model_id)
    if capability is None:
        return []
    label = f"route {route_id}: {candidate.provider}/{candidate.model_id}"
    errors = []
    if candidate.temperature is not None and not capability.temperature:
        errors.append(f"{label} does not take temperature")
    if candidate.effort is not None and candidate.effort not in capability.efforts:
        errors.append(f"{label} does not take effort {candidate.effort}")
    if candidate.max_output_tokens > capability.max_output_tokens:
        errors.append(f"{label} max_output_tokens exceeds {capability.max_output_tokens}")
    return errors


def raised_output_limit(model: CallModel) -> int | None:
    """응답이 잘렸을 때 다음 시도의 출력 한도. 벤더 상한에 이미 닿았으면 None."""
    capability = _capability_of(model.provider, model.model_id)
    cap = capability.max_output_tokens if capability else _DEFAULT_MAX_OUTPUT
    if model.max_output_tokens >= cap:
        return None
    return min(model.max_output_tokens * 2, cap)


class RoutingSnapshot(BaseModel):
    """라운드 생성 때 뜬 라우트 전체. 키 없는 벤더의 후보는 이때 빠진다."""

    routes: dict[str, list[CallModel]]
    slot_personas: dict[AgentSlot, str]
    level_criteria: LevelCriteria = Field(default_factory=lambda: DEFAULT_LEVEL_CRITERIA.model_copy(deep=True))
    # 라우팅 전 라운드는 벤더가 셋이 아닐 수 있다. 생성 전 검사를 받지 않았으므로 피할 벤더뿐이어도 글을 멈추지 않는다
    relaxed: bool = False

    def candidates(self, route_id: str) -> list[CallModel]:
        """라우트의 후보 목록. 비어 있으면(라우트가 없거나 키 없는 벤더뿐) ValueError. 선택 라우트는 has_route 로 먼저 본다."""
        if not self.routes.get(route_id):
            raise ValueError(f"route {route_id} has no candidate")
        return self.routes[route_id]

    def has_route(self, route_id: str) -> bool:
        """키 없는 벤더 후보를 뺀 뒤에도 후보가 남아 있는지. 선택 라우트(GLOSSARY)는 없으면 그 단계를 건너뛴다."""
        return bool(self.routes.get(route_id))


def _first_avoiding(snapshot: RoutingSnapshot, route_id: str, avoid: Iterable[str]) -> CallModel | None:
    excluded = set(avoid)
    return next((each for each in snapshot.candidates(route_id) if each.provider not in excluded), None)


def _required(snapshot: RoutingSnapshot, model: CallModel | None, route_id: str, reason: str) -> CallModel:
    if model is not None:
        return model
    if snapshot.relaxed:
        return snapshot.candidates(route_id)[0]
    raise ValueError(f"route {route_id} has no candidate {reason}")


def select_proposer(snapshot: RoutingSnapshot, persona_id: str) -> CallModel:
    """주제 제안 모델: 페르소나 라우트의 첫 후보. 벤더 제약이 없다."""
    return snapshot.candidates(propose_route(persona_id))[0]


def select_writer(snapshot: RoutingSnapshot, persona_id: str) -> CallModel:
    """작성 모델: 페르소나 라우트의 첫 후보. 이 벤더가 리뷰·중재·독립 호출의 회피 기준이 된다(D-52)."""
    return snapshot.candidates(write_route(persona_id))[0]


def select_reviser(snapshot: RoutingSnapshot, persona_id: str, writer_vendor: str) -> CallModel:
    """수정은 작성 벤더 안에서 고른다. 모델은 달라도 된다. 사람 재수정도 같은 규칙이다."""
    route_id = revise_route(persona_id)
    found = next((each for each in snapshot.candidates(route_id) if each.provider == writer_vendor), None)
    return _required(snapshot, found, route_id, f"from writing vendor {writer_vendor}")


def select_reviewers(
    snapshot: RoutingSnapshot, writer_vendor: str, lenses: Iterable[ReviewLens]
) -> dict[ReviewLens, CallModel]:
    """렌즈 순서대로 고른다. 작성 벤더는 항상 피하고, 같은 글의 앞선 리뷰 벤더는 다른 벤더가 있을 때만 피한다.

    벤더가 셋이면 작성 벤더를 뺀 둘로 렌즈 셋을 맡아야 하므로 겹칠 수 있다. 다른 회사의 모델만 독립 검증으로 친다.
    예외는 웹 검색 렌즈(사실 확인)다. 라우트 첫 후보로 고정하고 작성 벤더도 피하지 않는다 (09 문서 D-92).
    """
    chosen: dict[ReviewLens, CallModel] = {}
    used: set[str] = set()
    for lens in lenses:
        route_id = review_route(lens)
        if lens == WEB_SEARCH_LENS:
            # 검색은 한 벤더로 모은다. 작성 벤더를 피하면 그 벤더의 글만 다른 검색 도구로 확인하게 된다
            model = snapshot.candidates(route_id)[0]
        else:
            model = _first_avoiding(snapshot, route_id, {writer_vendor} | used) or _first_avoiding(
                snapshot, route_id, {writer_vendor}
            )
        chosen[lens] = _required(snapshot, model, route_id, f"outside writing vendor {writer_vendor}")
        used.add(chosen[lens].provider)
    return chosen


def select_arbiter(snapshot: RoutingSnapshot, writer_vendor: str, reviewer_vendor: str) -> CallModel:
    """중재 모델: 작성 벤더와 그 지적을 낸 리뷰 벤더 밖의 첫 후보(D-52). 없으면 ValueError(relaxed 스냅샷은 첫 후보)."""
    model = _first_avoiding(snapshot, ROUTE_ARBITRATE, {writer_vendor, reviewer_vendor})
    return _required(snapshot, model, ROUTE_ARBITRATE, f"outside {writer_vendor} and {reviewer_vendor}")


def select_independent(snapshot: RoutingSnapshot, route_id: str, writer_vendor: str) -> CallModel:
    """전체 읽기·수준 평가·문체 다듬기. 작성 벤더가 아닌 첫 후보."""
    model = _first_avoiding(snapshot, route_id, {writer_vendor})
    return _required(snapshot, model, route_id, f"outside {writer_vendor}")


def select_any(snapshot: RoutingSnapshot, route_id: str) -> CallModel:
    """줄바꿈 다듬기·사실 확인 변환. 벤더 제약이 없다."""
    return snapshot.candidates(route_id)[0]


def select_optional(snapshot: RoutingSnapshot, route_id: str) -> CallModel | None:
    """선택 라우트. 없으면 None 이고 호출자가 그 단계를 건너뛴다. 지금은 GLOSSARY 뿐이다."""
    return snapshot.candidates(route_id)[0] if snapshot.has_route(route_id) else None


def build_snapshot(
    documents: Iterable[Mapping[str, Any]],
    providers: Iterable[str],
    *,
    writer_overrides: Mapping[AgentSlot, tuple[str, CallModel]] | None = None,
) -> RoutingSnapshot:
    """agent_routes 문서 → 스냅샷. 키 없는 벤더 후보를 빼고 검사한다. 빠진 설정은 ValueError 다. 대체값을 두면 설정 누락이 숨는다.

    writer_overrides 는 슬롯별 (페르소나, 작성·수정·제안 모델) 이다. 요청으로 작성 모델을 바꾸는 A/B 에 쓴다.
    """
    available = set(providers)
    routes: dict[str, list[CallModel]] = {}
    personas: dict[AgentSlot, str] | None = None
    criteria = DEFAULT_LEVEL_CRITERIA.model_copy(deep=True)
    errors: list[str] = []
    for document in documents:
        route_id = document["_id"]
        if route_id == SLOTS_DOCUMENT_ID:
            personas = {slot: document[slot.value] for slot in AgentSlot if slot.value in document}
        elif route_id == LEVEL_CRITERIA_DOCUMENT_ID:
            criteria = LevelCriteria.model_validate({key: value for key, value in document.items() if key != "_id"})
        else:
            candidates = [CallModel.model_validate(each) for each in document.get("candidates", [])]
            errors += [error for each in candidates for error in candidate_errors(route_id, each)]
            routes[route_id] = [each for each in candidates if each.provider in available]
    for slot, (persona_id, model) in (writer_overrides or {}).items():
        errors += candidate_errors(write_route(persona_id), model)
        for route_id in (propose_route(persona_id), write_route(persona_id), revise_route(persona_id)):
            routes[route_id] = [model, *routes.get(route_id, [])]
        personas = {**(personas or {}), slot: persona_id}
    if personas is None or set(personas) != set(AgentSlot):
        raise ValueError(f"route document {SLOTS_DOCUMENT_ID} must name a persona for every slot (A, B, C)")
    if errors:
        raise ValueError("; ".join(errors))
    return RoutingSnapshot(routes=routes, slot_personas=personas, level_criteria=criteria)


def validate_snapshot(snapshot: RoutingSnapshot) -> None:
    """라운드를 만들기 전에 실제로 고를 조합을 끝까지 계산한다. 글 중간에 설정 오류를 만나지 않으려고 한다. 틀리면 ValueError.

    candidates 는 failover 가 아니고 WRITE_DRAFT 에는 벤더 제외가 없으므로, 뒤쪽 작성 후보는 일어나지 않는 조합이라 보지 않는다.
    """
    for persona_id in snapshot.slot_personas.values():
        select_proposer(snapshot, persona_id)
        writer_vendor = select_writer(snapshot, persona_id).provider
        select_reviser(snapshot, persona_id, writer_vendor)
        reviewers = select_reviewers(snapshot, writer_vendor, ReviewLens)
        for reviewer in reviewers.values():
            select_arbiter(snapshot, writer_vendor, reviewer.provider)
        for route_id in (ROUTE_FINAL_READ, ROUTE_ASSESS_LEVEL, ROUTE_POLISH_TONE):
            select_independent(snapshot, route_id, writer_vendor)
    select_any(snapshot, ROUTE_FACT_TRANSFORM)
    select_optional(snapshot, ROUTE_GLOSSARY)  # 배포된 agent_routes 에 아직 없을 수 있다. 없어도 라운드를 만든다
