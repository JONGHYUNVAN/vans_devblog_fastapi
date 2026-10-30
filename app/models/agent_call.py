"""LLM 호출 1회의 영수증 (설계 02 문서 2절, 07 문서 D-18·D-26)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AgentSlot, CallKind
from app.models.model_binding import ModelBinding


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class AgentCall(BaseModel):
    """agent_calls 컬렉션 문서. 시도마다 한 건을 insert 만 한다. 재시도가 이전 호출을 덮어쓰지 않는다.

    토큰 비용은 성공 여부와 무관하게 모든 usage 를 합산한다.
    """

    model_config = ConfigDict(validate_by_name=True)

    call_id: str = Field(alias="_id")
    round_id: str
    draft_id: str | None = None  # 주제 제안 호출은 글이 없다
    kind: CallKind
    slot: AgentSlot
    model_binding: ModelBinding  # 호출 시점 스냅샷
    prompt_template_version: str
    # 어느 단계의 호출인지
    target_version: int | None = None
    topic_attempt: int | None = None
    item_id: str | None = None
    attempt_number: int = Field(ge=1)  # 같은 단계 호출 안의 몇 번째 시도
    messages: list[ChatMessage]  # 이 시도에 실제로 보낸 메시지
    output_raw: str | None = None
    output_parsed_ok: bool = False
    error: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int = 0
    started_at: datetime
    finished_at: datetime
