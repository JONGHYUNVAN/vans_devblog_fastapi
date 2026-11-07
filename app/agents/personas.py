"""슬롯별 주제 탐색 도메인과 기본 리뷰 렌즈 (설계 05 문서 1절, v4: 페르소나·말투 강제 제거).

v3 까지는 작성·수정 프롬프트가 "너는 {display_name}이다. 성향: {writer_bias}" 로 인격을 부여하고 리뷰어에게 tone_markers 를
보존하라고 했다. v4 는 작성자가 확정된 topic 만 보게 하고, writer_bias 는 propose_topic 에서 세 슬롯의 주제가 겹치지 않게 하는
탐색 도메인으로만 쓴다. 어떤 모델이 어떤 페르소나를 맡는지는 ModelBinding 이 정한다.
"""

from pydantic import BaseModel

from app.models.enums import ReviewLens


class PersonaProfile(BaseModel):
    persona_id: str
    display_name: str  # 한국어 영역 라벨. 라운드 응답·CLI 표시용. 프롬프트·발행 본문에는 넣지 않는다
    writer_bias: str  # propose_topic 의 탐색 도메인(영어). $writer_bias 로 치환된다
    primary_lens: ReviewLens


PERSONAS: dict[str, PersonaProfile] = {
    "architect": PersonaProfile(
        persona_id="architect",
        display_name="아키텍처와 경계",
        writer_bias=(
            "Module boundaries, dependency direction, state lifecycle management, and domain modeling. "
            "Focus on how structural code changes resolve coupling or invariant violations."
        ),
        primary_lens=ReviewLens.LOGIC_READABILITY,
    ),
    "systems_engineer": PersonaProfile(
        persona_id="systems_engineer",
        display_name="시스템 내부 동작",
        writer_bias=(
            "Database execution plans, concurrency control, lock contention, I/O and memory overhead, "
            "and runtime specifications. Focus on internal mechanics backed by official specs and metrics."
        ),
        primary_lens=ReviewLens.FACT_STRUCTURE,
    ),
    "troubleshooter": PersonaProfile(
        persona_id="troubleshooter",
        display_name="엣지 케이스와 실패 모드",
        writer_bias=(
            "Public failure postmortems, edge-case breakdowns, pitfalls of default configurations, "
            "and critical analysis of conventional practices through concrete counterexamples."
        ),
        primary_lens=ReviewLens.CRITICAL_PRACTICAL,
    ),
}


def get_persona(persona_id: str) -> PersonaProfile:
    """없는 persona_id 는 바인딩 설정 오류라 ValueError."""
    try:
        return PERSONAS[persona_id]
    except KeyError as exc:
        raise ValueError(f"unknown persona {persona_id}") from exc
