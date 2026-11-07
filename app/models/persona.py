"""슬롯의 주제 탐색 도메인과 기본 리뷰 렌즈 (설계 05 문서 1절, 09 문서 2.2절).

v4 부터 작성·수정 프롬프트는 페르소나를 보지 않는다. writer_bias 는 propose_topic 의 탐색 도메인($writer_bias)으로만 쓰이고,
display_name 은 라운드 응답·CLI 표시용이다. 프로필은 파이프라인 버전 문서의 personas 에 있고(1a), 코드 상수는 기준 버전의 입력이다.
"""

from pydantic import BaseModel

from app.models.enums import ReviewLens


class PersonaProfile(BaseModel):
    """슬롯 하나의 페르소나: 주제 제안의 탐색 도메인과 기본 리뷰 렌즈. 어떤 모델이 맡는지는 ModelBinding 이 정한다(D-02)."""

    persona_id: str
    display_name: str  # 한국어 영역 라벨. 프롬프트·발행 본문에는 넣지 않는다
    writer_bias: str  # propose_topic 의 탐색 도메인(영어). $writer_bias 로 치환된다
    primary_lens: ReviewLens
    # 근거(evidence)가 없는 라운드에서 이 페르소나가 고를 수 있는 수준 상한. None 이면 400 미만까지(공통 규칙)다
    max_level_without_evidence: int | None = None
