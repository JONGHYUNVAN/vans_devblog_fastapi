"""설명문 파이프라인 실행 문서 (09 문서 D-100). 실행 하나의 단계 결과와 완성된 글을 담는다."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.model_binding import CallModel


def _now() -> datetime:
    return datetime.now(UTC)


class ExpositoryArticle(BaseModel):
    """9단계까지 끝난 글. 본문은 한국어 마크다운이고 발행에 필요한 키워드·카테고리를 같이 둔다."""

    title: str
    summary: str
    content_markdown: str
    keywords: list[str] = Field(default_factory=list)
    main_category_id: str | None = None  # 1단계를 사람이 준 실행은 카테고리가 없을 수 있다
    sub_category_id: str | None = None
    topic: str
    core_argument: str
    english_title: str
    pipeline_version: str  # 글을 끝낸 실행이 읽은 버전. 단계별 버전은 영수증에 있다


class ExpositoryPublication(BaseModel):
    """블로그(Post BE)에 올린 기록 (D-101). 한 실행은 한 번만 올린다."""

    external_post_id: str
    published_at: datetime
    provider: str  # 이 벤더의 발행 계정으로 로그인해 올렸다. 글의 작성자가 그 계정이다
    note: str  # 본문 뒤에 붙인 AI Information 표기


class ExpositoryRun(BaseModel):
    """expository_runs 컬렉션 문서. 단계가 끝날 때마다 통째로 덮어쓴다(쓰는 쪽은 그 실행의 러너 하나다).

    단계 출력을 dict 로 두는 이유: 모양을 정하는 것이 버전 문서 프롬프트의 expected_output_schema 라서, 코드에 같은 모양의
    모델을 두면 프롬프트를 재배포 없이 고칠 수 없게 된다. 러너가 쓰는 필드는 services/expository_checks.py 가 검사한다.
    """

    model_config = ConfigDict(validate_by_name=True)

    run_id: str = Field(alias="_id")  # 영수증의 round_id 와 같다
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    stage_outputs: dict[str, dict[str, Any]] = Field(default_factory=dict)  # 단계 key → 검사를 통과한 출력
    # 단계 key → 그 출력을 만든 프롬프트(입력을 채운 것). 다시 돌릴 때 같으면 그 단계를 부르지 않는다
    stage_prompts: dict[str, str] = Field(default_factory=dict)
    # 단계 key → 그 결과를 낸 모델. 글 끝 표기와 발행 계정을 여기서 정한다. D-101 전 실행에는 없다
    stage_models: dict[str, CallModel] = Field(default_factory=dict)
    checked_markdown: str | None = None  # 6단계 패치를 적용한 영어 본문
    rejected_patches: list[dict[str, Any]] = Field(default_factory=list)  # 6단계에서 버린 패치와 이유
    assembled_markdown: str | None = None  # 7단계 표·용어 정리를 넣은 영어 본문(번역 입력)
    article: ExpositoryArticle | None = None  # 9단계까지 끝났을 때만
    # 발행 호출을 시작한 시각. publication 없이 이 값만 있으면 결과를 모른다(응답 전 끊김): 다시 올리면 중복 게시가 될 수 있다
    publish_attempted_at: datetime | None = None
    publication: ExpositoryPublication | None = None

    def drop_from(self, stage_keys: list[str]) -> None:
        """주어진 단계들의 결과를 지운다. 그 단계부터 다시 부르게 할 때 쓴다. 완성된 글도 같이 지운다."""
        for key in stage_keys:
            self.stage_outputs.pop(key, None)
            self.stage_prompts.pop(key, None)
            self.stage_models.pop(key, None)
        if stage_keys:
            self.article = None
