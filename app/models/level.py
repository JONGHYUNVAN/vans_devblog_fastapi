"""글의 전문성 수준 기준표와 참고 글. AWS 세션 레벨 100~400 틀을 빌렸고 항목 개수가 아니라 인과의 깊이를 본다.

표는 agent_routes 의 LEVEL_CRITERIA 문서로 고칠 수 있고 라운드 생성 때 스냅샷을 뜬다. 문서가 없으면 아래 기본값을 쓴다.
"""

from pydantic import BaseModel, Field

from app.models.enums import PostLevel


class LevelCriterion(BaseModel):
    """수준 하나의 기준. 핵심 질문·전제·평가 기준·근거 종류로 깊이를 가른다. 작성·리뷰·수준 평가 입력에 그대로 들어간다."""

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
    """기준표 전체와 참고 글. 라운드 생성 때 버전 문서에서 스냅샷되어 그 라운드의 모든 호출이 같은 기준을 본다."""

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
            assessment=(
                "용어를 처음 듣는 독자가 끝까지 읽고 따라 할 수 있다. 개념을 한 문장으로 정의하고 바로 일상 예시"
                "(블로그 서비스의 사용자·게시글·댓글처럼)로 보여 준다. 문장은 짧고(20~30자) 작은 제목 아래 2~4문장씩 끊으며, "
                "코드는 5~10줄 조각마다 주석 한 줄. 증명 스크립트·변경 전후 비교는 쓰지 않는다"
            ),
            evidence="공식 문서. 작은 코드 조각과 그 실행 결과",
        ),
        LevelCriterion(
            level=200,
            name="응용 기초",
            core_question="어떻게 제대로 쓰는가",
            prerequisites="기본 사용 경험",
            assessment=(
                "방법과 그 이유를 함께 설명한다. 입문과 같은 호흡(짧은 문장, 작은 제목, 일상 예시, 주석 달린 작은 코드 조각)을 "
                "유지하되 단계마다 '왜 이렇게 하는가'를 붙인다. 심화(300)의 변경 전후 코드·테스트 증명은 요구하지 않는다"
            ),
            evidence="실행 가능한 코드와 그 결과",
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
    # 사용자가 2026-11-08 에 "레벨 100~200 은 이런 느낌" 으로 지정한 글. 합쇼체, 문장 평균 23자, 작은 제목 93개 아래 2~4문장,
    # 일상 예시, 5~10줄 코드 조각 42개에 주석 한 줄씩. 작성·리뷰·수준 평가 입력의 level_criteria 로 모델에 그대로 전달된다
    reference_posts=[
        ReferencePost(
            title="데이터 모델의 이해",
            level=100,
            excerpt=(
                "데이터 모델링은 소프트웨어 개발에서 핵심적인 역할을 담당하는 활동 중 하나입니다. 쉽게 말해, 데이터 모델링은 "
                "데이터의 구조와 관계를 설계하는 과정으로, 시스템이 어떻게 데이터를 저장하고 관리할지를 미리 정의하는 작업입니다. "
                "예를 들어, 블로그 서비스를 개발한다면 사용자(User), 게시글(Post), 댓글(Comment) 같은 데이터가 필요할 것입니다. "
                "각각의 데이터는 어떤 속성(예: 사용자 이름, 게시글 제목, 댓글 내용)을 가져야 하고, 서로 어떤 관계"
                "(예: 사용자가 게시글을 작성, 게시글에 댓글이 달림)를 맺어야 하는지 정해야 합니다."
            ),
            note=(
                "100~200 수준의 기준 글(블로그 database-theory/681c7f6d926e79afe6f3b2ad). 합쇼체(~습니다), 문장 평균 23자, "
                "작은 제목마다 2~4문장, 개념 정의 → 일상 예시 → 정리 순서, 5~10줄 코드 조각마다 주석 한 줄. 증명 스크립트가 없다"
            ),
        )
    ],
)
