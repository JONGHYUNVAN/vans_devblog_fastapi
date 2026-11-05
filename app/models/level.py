"""글의 전문성 수준 기준표와 참고 글. AWS 세션 레벨 100~400 틀을 빌렸고 항목 개수가 아니라 인과의 깊이를 본다.

표는 agent_routes 의 LEVEL_CRITERIA 문서로 고칠 수 있고 라운드 생성 때 스냅샷을 뜬다. 문서가 없으면 아래 기본값을 쓴다.
"""

from pydantic import BaseModel, Field

from app.models.enums import PostLevel


class LevelCriterion(BaseModel):
    level: PostLevel
    name: str
    core_question: str
    prerequisites: str
    assessment: str
    evidence: str


class ReferencePost(BaseModel):
    """사용자가 수준을 직접 매긴 글. 평가자가 기준을 맞추는 데 쓴다."""

    title: str
    level: PostLevel
    excerpt: str = ""
    note: str = ""


class LevelCriteria(BaseModel):
    version: str
    criteria: list[LevelCriterion] = Field(min_length=1)
    reference_posts: list[ReferencePost] = Field(default_factory=list)


DEFAULT_LEVEL_CRITERIA = LevelCriteria(
    version="v1",
    criteria=[
        LevelCriterion(
            level=100,
            name="입문",
            core_question="어떻게 쓰는가",
            prerequisites="없음",
            assessment="절차를 따라 하면 동작하는 결과가 나온다",
            evidence="공식 문서",
        ),
        LevelCriterion(
            level=200,
            name="응용 기초",
            core_question="어떻게 제대로 쓰는가",
            prerequisites="기본 사용 경험",
            assessment="방법과 그 이유를 함께 설명한다",
            evidence="실행 가능한 코드",
        ),
        LevelCriterion(
            level=300,
            name="심화",
            core_question="이 결정이 왜 따라오고 코드가 어떻게 바뀌는가",
            prerequisites="여러 접근을 직접 써 본 경험",
            assessment="선택한 전환이나 설계 결정 하나의 인과를 변경 전후 코드로 깊게 설명한다",
            evidence="변경 전후 코드와 테스트. 표준 시나리오도 된다",
        ),
        LevelCriterion(
            level=400,
            name="전문",
            core_question="왜 그렇게 동작하고 어디서 실패하는가",
            prerequisites="내부 동작과 운영 경험",
            assessment="내부 동작이나 실패 원인을 측정값으로 설명한다",
            evidence="측정값과 운영 기록. 외부 근거가 있어야 한다",
        ),
    ],
)
