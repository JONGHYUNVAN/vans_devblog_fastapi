"""용어 정의의 사전체 검사 (설계 09 문서 D-88). 추출 계약·승인·무결성 검사가 같은 함수를 쓴다."""

import pytest
from pydantic import ValidationError

from app.agents.contracts import SenseProposal
from app.models.glossary import dictionary_style_problem, normalize_definition


@pytest.mark.parametrize(
    "definition",
    [
        "하나의 일관성 경계를 이루고 외부에서 루트를 통해서만 접근하는 객체들의 묶음",
        "요청 사이에 서버가 클라이언트 상태를 저장하지 않는 설계.",  # 끝 마침표는 형식이라 떼고 받는다
        "HTTP/1.1 의 지속 연결",  # 문장 중간의 마침표는 문장 끝이 아니다
    ],
)
def test_noun_phrases_pass(definition: str) -> None:
    assert dictionary_style_problem(definition) is None


@pytest.mark.parametrize(
    "definition",
    [
        "하나의 일관성 경계를 이루는 객체들의 묶음이다.",
        "요청을 나눠 처리한다",
        "객체들의 묶음이다. 루트로만 접근하는 단위",  # 두 문장. 앞 문장이 "~다" 로 끝난다
        "   .  ",
    ],
)
def test_sentences_and_empty_definitions_are_flagged(definition: str) -> None:
    assert dictionary_style_problem(definition) is not None


def test_contract_strips_the_final_period_and_rejects_sentence_endings() -> None:
    proposal = SenseProposal(domains=["db"], definition="  객체들의 묶음. ")

    assert proposal.definition == "객체들의 묶음" == normalize_definition("  객체들의 묶음. ")
    with pytest.raises(ValidationError, match="dictionary-style noun phrase"):
        SenseProposal(domains=["db"], definition="객체들의 묶음이다.")
