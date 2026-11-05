"""라운드 집합 루트 PublishingRound 와 주제 선정 이력, 라운드 상태 전이 (설계 02 문서 2절, 03 문서 3절)."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.exceptions import InvalidTransitionError
from app.models.enums import AgentSlot, RoundAbortReason, RoundStatus
from app.models.model_binding import ModelBinding
from app.models.routing import RoutingSnapshot
from app.models.topic import CategoryOption, TopicProposal


class TopicConflict(BaseModel):
    slot: AgentSlot
    proposal_index: int
    other_slot: AgentSlot | None  # 레지스트리와 겹치면 None
    other_index: int | None
    registry_title: str | None
    kind: Literal["keyword", "title", "registry"]
    score: float


class LockedTopic(BaseModel):
    slot: AgentSlot
    source_attempt: int
    proposal_index: int
    proposal: TopicProposal  # 원본 배열이 바뀌어도 선택 결과가 보존된다


class TopicAttempt(BaseModel):
    attempt_number: int = Field(ge=1)  # 1..MAX_TOPIC_ATTEMPTS
    proposals: dict[AgentSlot, list[TopicProposal]] = Field(default_factory=dict)  # 이번 시도에서 제안한 슬롯만
    locked: dict[AgentSlot, LockedTopic] = Field(default_factory=dict)  # 이전 시도의 선택도 스냅샷으로 유지
    conflicts: list[TopicConflict] = Field(default_factory=list)

    @property
    def is_fully_locked(self) -> bool:
        return set(self.locked) == set(AgentSlot)


class PublishingRound(BaseModel):
    """agent_rounds 컬렉션 문서. 문서는 TOPIC_SELECTION 으로 생긴다(설계 07 문서 D-42).

    잘못된 전이는 InvalidTransitionError. 저장 조건(revision 낙관적 잠금, 상태 조건 갱신)은 리포지토리가 맡는다.
    """

    model_config = ConfigDict(validate_by_name=True)

    round_id: str = Field(alias="_id")
    round_seq: int = Field(ge=1)  # 주제 배정 동률을 round_seq % 3 으로 가른다
    status: RoundStatus = RoundStatus.TOPIC_SELECTION
    theme_keyword: str | None = None
    slot_bindings: dict[
        AgentSlot, ModelBinding
    ]  # 슬롯의 페르소나와 작성 모델. 응답 계약이자 발행 계정(D-45)의 기준이다
    routing: RoutingSnapshot | None = None  # 호출별 모델 후보. 응답에 내보내지 않는다. 라우팅 전 라운드는 None
    evidence: str | None = (
        None  # 실제 프로젝트 근거(git 이력, code-graph context, 측정 기록). 없어도 300 까지는 쓸 수 있다
    )
    categories: list[CategoryOption] = Field(min_length=1)  # 라운드 생성 때 Post BE 에서 읽은 스냅샷
    draft_ids: list[str] = Field(default_factory=list)  # 길이 0 (주제 선정 중) 또는 3 (확정 후)
    topic_attempts: list[TopicAttempt] = Field(default_factory=list)
    abort_reason: RoundAbortReason | None = None
    revision: int = 0  # 낙관적 잠금 (설계 02 문서 6절)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("slot_bindings")
    @classmethod
    def _all_slots_bound(cls, bindings: dict[AgentSlot, ModelBinding]) -> dict[AgentSlot, ModelBinding]:
        if set(bindings) != set(AgentSlot):
            raise ValueError("slot_bindings must bind every slot (A, B, C)")
        return bindings

    @property
    def is_terminal(self) -> bool:
        return self.status in (RoundStatus.COMPLETED, RoundStatus.ABORTED)

    @property
    def latest_attempt(self) -> TopicAttempt | None:
        return self.topic_attempts[-1] if self.topic_attempts else None

    def draft_id_for(self, slot: AgentSlot) -> str:
        """그 슬롯 글의 결정론적 ID. 확정 쓰기를 다시 해도 같은 문서를 가리킨다(설계 07 문서 D-28)."""
        return f"{self.round_id}:{slot}"

    def lock_topics(self) -> None:
        """세 슬롯 주제가 모두 잠긴 라운드를 IN_PROGRESS 로 바꾸고 draft_ids 를 채운다. 확정의 커밋 지점이다."""
        if self.status != RoundStatus.TOPIC_SELECTION:
            raise InvalidTransitionError(f"round {self.round_id} is {self.status}, not TOPIC_SELECTION")
        attempt = self.latest_attempt
        if attempt is None or not attempt.is_fully_locked:
            raise InvalidTransitionError(f"round {self.round_id} has not locked a topic for every slot")
        self.status = RoundStatus.IN_PROGRESS
        self.draft_ids = [self.draft_id_for(slot) for slot in AgentSlot]

    def abort(self, reason: RoundAbortReason) -> None:
        """라운드를 중단한다. 글·레지스트리 정리는 RoundService 가 이어서 한다(설계 04 문서 1절)."""
        if self.is_terminal:
            raise InvalidTransitionError(f"round {self.round_id} is already {self.status}")
        self.status = RoundStatus.ABORTED
        self.abort_reason = reason

    def complete(self) -> None:
        """3편이 모두 종결된 라운드를 닫는다. 종결 여부 확인은 RoundService.close_if_complete 가 한다."""
        if self.status != RoundStatus.IN_PROGRESS:
            raise InvalidTransitionError(f"round {self.round_id} is {self.status}, not IN_PROGRESS")
        self.status = RoundStatus.COMPLETED
