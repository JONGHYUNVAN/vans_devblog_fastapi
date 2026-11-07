"""수준 기준표 기본값. 100·200 은 참조 글(데이터 모델의 이해)의 호흡을 적고, 참조 글이 모델 입력에 그대로 들어간다 (D-86)."""

from app.models.level import DEFAULT_LEVEL_CRITERIA


def test_entry_levels_describe_the_reference_feel_and_carry_the_reference_post():
    by_level = {criterion.level: criterion for criterion in DEFAULT_LEVEL_CRITERIA.criteria}
    assert "일상 예시" in by_level[100].assessment and "주석 한 줄" in by_level[100].assessment
    assert "왜 이렇게 하는가" in by_level[200].assessment
    assert [post.title for post in DEFAULT_LEVEL_CRITERIA.reference_posts] == ["데이터 모델의 이해"]
    assert DEFAULT_LEVEL_CRITERIA.reference_posts[0].level == 100
