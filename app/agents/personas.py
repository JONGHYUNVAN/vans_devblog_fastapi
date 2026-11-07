"""기본 페르소나 프로필 3종 (설계 05 문서 1절). 모델은 models/persona.py 다.

실행 중에는 파이프라인 버전 문서의 personas 를 읽고(09 문서 2.2절), 이 상수는 기준 버전을 만드는 입력(bootstrap)과 테스트의 기본값이다.
어떤 모델이 어떤 페르소나를 맡는지는 ModelBinding 이 정한다.
"""

from collections.abc import Mapping

from app.models.enums import ReviewLens
from app.models.persona import PersonaProfile

__all__ = ["PERSONAS", "PersonaProfile", "get_persona"]

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
        max_level_without_evidence=200,  # 근거가 없으면 공개 사례 분석만 쓴다. 지어낸 경험담은 입력 단계에서 막는다
    ),
}


def get_persona(persona_id: str, personas: Mapping[str, PersonaProfile] | None = None) -> PersonaProfile:
    """personas(버전 문서의 것)에서 찾는다. 없으면 기본 상수다. 없는 persona_id 는 바인딩 설정 오류라 ValueError."""
    table = personas if personas is not None else PERSONAS
    try:
        return table[persona_id]
    except KeyError as exc:
        raise ValueError(f"unknown persona {persona_id}") from exc
