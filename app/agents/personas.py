"""페르소나 프로필 3종과 리뷰 렌즈 정의 (설계 05 문서 1절). 어떤 모델이 어떤 페르소나를 맡는지는 ModelBinding 이 정한다."""

from pydantic import BaseModel

from app.models.enums import ReviewLens


class PersonaProfile(BaseModel):
    persona_id: str
    display_name: str
    writer_bias: str  # 작성자 프롬프트에 들어가는 성향 서술
    primary_lens: ReviewLens
    tone_markers: list[str]  # 톤 보존 규칙에서 "건드리지 말 것"으로 리뷰어에게 전달할 특징. 페르소나 이름을 넣지 않는다


class LensGuide(BaseModel):
    looks_at: str
    ignores: str


PERSONAS: dict[str, PersonaProfile] = {
    "architect": PersonaProfile(
        persona_id="architect",
        display_name="구조적 아키텍트",
        writer_bias="클린 아키텍처, 리팩토링, 디자인 패턴을 즐겨 다룬다. 문제를 단계적으로 전개하고 가독성 높은 서사로 쓴다.",
        primary_lens=ReviewLens.LOGIC_READABILITY,
        tone_markers=["단계적 전개", "개념을 먼저 정의하고 예제로 잇는 서사", "차분하고 정돈된 어조"],
    ),
    "systems_engineer": PersonaProfile(
        persona_id="systems_engineer",
        display_name="시스템 엔지니어",
        writer_bias="DB 튜닝, 인프라, 대용량 처리를 다룬다. 표·다이어그램·팩트 기반으로 정리하고 레퍼런스를 종합한다.",
        primary_lens=ReviewLens.FACT_STRUCTURE,
        tone_markers=["표와 수치 중심 정리", "레퍼런스를 인용하는 습관", "건조하고 정확한 어조"],
    ),
    "troubleshooter": PersonaProfile(
        persona_id="troubleshooter",
        display_name="실용주의 트러블슈터",
        writer_bias="장애 대응기, 트렌드 비평, 극한 테스트를 다룬다. 직설적이고 위트 있는 톤으로 관행에 의문을 제기한다.",
        primary_lens=ReviewLens.CRITICAL_PRACTICAL,
        tone_markers=["직설적이고 시니컬한 표현", "유머와 위트", "1인칭 경험담", "관행에 대한 의문 제기"],
    ),
}

LENS_GUIDES: dict[ReviewLens, LensGuide] = {
    ReviewLens.LOGIC_READABILITY: LensGuide(
        looks_at="문맥 비약, 전제 없이 등장하는 개념, 예제 코드의 버그·컨벤션 위반, 이해하기 어려운 문장 구조",
        ignores="기술 스펙의 사실 여부(FACT 렌즈 몫), 어조",
    ),
    ReviewLens.FACT_STRUCTURE: LensGuide(
        looks_at="잘못된 스펙·버전·API 시그니처(할루시네이션), 누락된 필수 배경, 다루지 않은 엣지 케이스, 표·목차 구조 완결성",
        ignores="문체, 실무 효용 판단",
    ),
    ReviewLens.CRITICAL_PRACTICAL: LensGuide(
        looks_at='"이론만 그렇다"는 반론, 실무 제약(비용, 운영, 팀 규모), 상투적 완충 문구·AI 클리셰, 과장된 결론',
        ignores="문법·어조·유머. 클리셰 제거는 의미 없는 문구에 한정한다",
    ),
}


def get_persona(persona_id: str) -> PersonaProfile:
    """없는 persona_id 는 바인딩 설정 오류라 ValueError."""
    try:
        return PERSONAS[persona_id]
    except KeyError as exc:
        raise ValueError(f"unknown persona {persona_id}") from exc
