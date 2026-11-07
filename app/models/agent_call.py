"""LLM 호출 1회의 영수증 (설계 02 문서 2절, 07 문서 D-18·D-26).

AgentCallService 가 시도마다 한 건씩 insert 한다. 메시지·원문 출력을 그대로 남기는 이유는 재생(agents replay)·프롬프트 비교·
제작 기록(08 문서)이 이 문서만으로 가능해야 해서다. prompt_template_version 은 1a 이전 영수증의 프롬프트 디렉터리 이름(v3·v4)이고,
1a 부터는 pipeline_version(버전 문서 id)을 적는다. 두 이름 공간이 겹치므로(v4 디렉터리 ≠ 버전 v4) 필드를 합치지 않는다(09 문서 D-73).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AgentSlot, CallKind, ReviewLens
from app.models.model_binding import CallModel


class TokenUsage(BaseModel):
    """호출 한 번의 토큰 사용량. 글 예산은 모든 시도(실패 포함)의 합이다(D-20)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """입력·출력 합. 소프트 예산과 비교하는 값이다."""
        return self.prompt_tokens + self.completion_tokens


class ChatMessage(BaseModel):
    """벤더에 보낸 메시지 하나. 영수증에는 실제로 보낸 그대로 남는다."""

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
    slot: AgentSlot | None = None  # 작성자 호출과 옛 리뷰만. 리뷰는 lens, 나머지는 슬롯과 무관하다
    lens: ReviewLens | None = None  # 어느 렌즈의 호출인지. 모델·렌즈별 지표에 쓴다
    model_binding: CallModel  # 호출 시점 스냅샷. 옛 영수증의 persona_id 는 읽을 때 버린다
    # 메시지를 렌더링한 프롬프트의 출처. 1a 부터는 파이프라인 버전 id(pipeline_version, "v1" 꼴)이고,
    # 옛 영수증의 prompt_template_version 은 저장소 프롬프트 디렉터리 이름(v3·v4)이라 둘을 나눈다 (설계 09 문서 D-73)
    prompt_template_version: str | None = None
    pipeline_version: str | None = None
    # 어느 단계의 호출인지
    target_version: int | None = None
    topic_attempt: int | None = None
    item_id: str | None = None
    attempt_number: int = Field(ge=1)  # 같은 단계 호출 안의 몇 번째 시도
    messages: list[ChatMessage]  # 이 시도에 실제로 보낸 메시지
    output_raw: str | None = None
    output_parsed_ok: bool = False
    stop_reason: str = ""  # 벤더가 준 종료 사유
    citations: list[str] = Field(default_factory=list)  # 검색 호출이 실제로 쓴 출처 URL
    error: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int = 0
    started_at: datetime
    finished_at: datetime
