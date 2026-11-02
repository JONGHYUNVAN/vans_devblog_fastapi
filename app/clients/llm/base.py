"""LLM 벤더 클라이언트 계약. 벤더 구현(anthropic·google·xai)은 이 Protocol 을 따른다 (설계 06 문서 2절).

clients 는 models 를 import 하지 않으므로 메시지·사용량 타입을 여기 둔다. 서비스가 도메인 타입과 맞춘다.
"""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMParams(BaseModel):
    model_id: str  # ModelBinding.model_id. 클라이언트는 provider 하나를 맡고 모델은 호출마다 받는다
    temperature: (
        float  # Anthropic 현행 API(2026-10-21)는 sampling 파라미터를 거부해 anthropic 클라이언트는 보내지 않는다
    )
    max_output_tokens: int = Field(gt=0)


class LLMUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMResponse(BaseModel):
    text: str  # 모델이 돌려준 본문. 계약 모델의 JSON 이어야 하며 검사는 서비스가 한다
    usage: LLMUsage


class LLMClient(Protocol):
    """벤더 하나의 호출. 구현은 벤더 SDK 의 async 인터페이스나 httpx2.AsyncClient 를 쓰고 timeout 을 반드시 건다.

    실패 계약: 네트워크 오류·5xx·429·타임아웃은 LLMTransportError (같은 메시지로 재시도 가능).
    그 밖의 4xx(키·모델·스키마 거부)는 설정 오류라 ValueError 로 두고 재시도하지 않는다(러너가 INTERNAL_ERROR 로 멈춘다).
    응답은 받았지만 본문이 비었거나 JSON 이 아닌 것은 그대로 돌려준다. 계약 검사는 서비스가 한다.
    schema 는 Pydantic model_json_schema() 그대로다. 벤더별 변환은 클라이언트가 한다.
    """

    async def complete(
        self, messages: list[LLMMessage], schema: dict[str, Any], params: LLMParams, timeout: float
    ) -> LLMResponse: ...
