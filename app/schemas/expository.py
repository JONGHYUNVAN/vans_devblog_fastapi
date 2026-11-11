"""설명문 파이프라인 실행 결과의 출력 형태. CLI 가 찍는다."""

from pydantic import BaseModel


class ExpositoryResult(BaseModel):
    """실행 하나의 지금 상태. 9단계까지 끝나지 않았으면 글 필드가 None 이다."""

    run_id: str
    finished_stages: list[str]  # 결과가 있는 단계 key, 단계 순서
    pipeline_version: str | None = None
    title: str | None = None
    summary: str | None = None
    markdown: str | None = None  # "# 제목", "> 요약", 본문 순서의 한 덩어리
