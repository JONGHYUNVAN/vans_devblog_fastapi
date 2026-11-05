"""LLM 벤더 클라이언트 계약. 벤더 구현(anthropic·google·xai)은 이 Protocol 을 따른다 (설계 06 문서 2절).

clients 는 models 를 import 하지 않으므로 메시지·사용량 타입을 여기 둔다. 서비스가 도메인 타입과 맞춘다.
"""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMParams(BaseModel):
    model_id: str  # 클라이언트는 provider 하나를 맡고 모델은 호출마다 받는다
    max_output_tokens: int = Field(gt=0)
    # 둘 다 None 이면 보내지 않아 벤더 기본값이 된다. 지원 조합은 라운드 생성이 미리 거른다(models/routing.candidate_errors)
    temperature: float | None = None
    effort: str | None = None  # low | medium | high | xhigh | max. 벤더 필드로 옮기는 일은 클라이언트가 한다
    web_search: bool = False  # 벤더의 서버 측 웹 검색을 켠다


class LLMUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMCitation(BaseModel):
    """검색 호출이 실제로 쓴 출처."""

    url: str
    title: str = ""


class LLMResponse(BaseModel):
    text: str  # 모델이 돌려준 본문. 스키마를 줬으면 계약 모델의 JSON 이어야 하며 검사는 서비스가 한다
    usage: LLMUsage
    stop_reason: str = ""  # 벤더가 준 종료 사유 그대로. 영수증에 남긴다
    truncated: bool = False  # 출력 한도에서 잘렸다. 계약 위반과 구분해 같은 한도로 재시도하지 않는다
    citations: list[LLMCitation] = Field(default_factory=list)


class LLMClient(Protocol):
    """벤더 하나의 호출. 구현은 벤더 SDK 의 async 인터페이스나 httpx2.AsyncClient 를 쓰고 timeout 을 반드시 건다.

    실패 계약: 네트워크 오류·5xx·429·타임아웃과 검색 도구 자체의 실패는 LLMTransportError (같은 메시지로 재시도 가능).
    그 밖의 4xx(키·모델·스키마 거부)는 설정 오류라 ValueError 로 두고 재시도하지 않는다(러너가 INTERNAL_ERROR 로 멈춘다).
    응답은 받았지만 본문이 비었거나 JSON 이 아닌 것은 그대로 돌려준다. 계약 검사는 서비스가 한다.
    schema 는 Pydantic model_json_schema() 그대로다. 벤더별 변환은 클라이언트가 한다. None 이면 글로 받는다(검색 호출).
    """

    async def complete(
        self, messages: list[LLMMessage], schema: dict[str, Any] | None, params: LLMParams, timeout: float
    ) -> LLMResponse: ...
