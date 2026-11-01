"""페르소나·렌즈 정의 테스트."""

import pytest

from app.agents.personas import LENS_GUIDES, PERSONAS, get_persona
from app.models.enums import ReviewLens


def test_three_personas_cover_the_three_lenses():
    assert {persona.primary_lens for persona in PERSONAS.values()} == set(ReviewLens)
    assert set(LENS_GUIDES) == set(ReviewLens)


def test_get_persona_returns_profile_and_rejects_unknown_id():
    assert get_persona("architect").display_name == "구조적 아키텍트"
    with pytest.raises(ValueError, match="unknown persona"):
        get_persona("nobody")
