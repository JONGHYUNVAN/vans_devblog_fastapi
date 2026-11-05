"""슬롯과 호출에 어떤 모델을 붙일지 (설계 02 문서 3절)."""

from pydantic import BaseModel, Field


class ModelBinding(BaseModel):
    """슬롯 하나의 페르소나와 작성 모델. 라운드 응답의 slot_bindings 계약이다. 호출은 CallModel 을 쓴다.

    도메인은 벤더를 문자열로만 안다(설계 07 문서 D-02). temperature 는 응답 계약 때문에 남겨 두고 읽지 않는다.
    """

    provider: str  # "anthropic" | "google" | "xai" | ...
    model_id: str
    persona_id: str  # PersonaProfile 식별자. 슬롯당 하나
    temperature: float = 0.7
    max_output_tokens: int = Field(gt=0)


class CallModel(BaseModel):
    """호출 하나에 쓰는 모델. 라우트 후보이자 영수증의 스냅샷이다. 옛 영수증의 persona_id 는 읽을 때 버린다.

    temperature·effort 가 None 이면 보내지 않아 벤더 기본값이 된다. 지원 여부는 routing.candidate_errors 가 검사한다.
    """

    provider: str
    model_id: str
    effort: str | None = None
    temperature: float | None = None
    max_output_tokens: int = Field(gt=0)
