"""슬롯에 어떤 모델·페르소나를 붙일지 (설계 02 문서 3절)."""

from pydantic import BaseModel, Field


class ModelBinding(BaseModel):
    """슬롯 하나의 벤더·모델·페르소나. 도메인은 벤더를 문자열로만 안다(설계 07 문서 D-02)."""

    provider: str  # "anthropic" | "google" | "xai" | ...
    model_id: str
    persona_id: str  # PersonaProfile 식별자. 슬롯당 하나
    temperature: float = 0.7  # PROPOSE_TOPIC, WRITE_DRAFT, REVISE_DRAFT. 리뷰·중재는 Settings.review_temperature
    max_output_tokens: int = Field(gt=0)
