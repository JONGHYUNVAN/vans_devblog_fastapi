"""동기화 API 요청·응답. 키와 값 형식은 Django 서비스 응답(tests/golden/sync_status.json)과 같다."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class SyncOptions(BaseModel):
    """동기화 옵션. 본문을 비우면 기본값으로 전체 동기화한다."""

    batch_size: int = Field(50, ge=1, le=500, description="MongoDB 에서 한 번에 읽을 게시글 수")
    force_all: bool = Field(False, description="받기만 한다. 발행 여부 필드가 없어 항상 모든 게시글을 동기화한다")
    incremental: bool = Field(False, description="최근 days 일 안에 생성·수정된 게시글만 동기화한다")
    days: int = Field(7, ge=1, le=365)
    clear_existing: bool = Field(False, description="posts 인덱스 문서를 모두 지운 뒤 동기화한다")
    dry_run: bool = Field(False, description="색인·삭제 없이 처리할 건수만 센다")

    @model_validator(mode="after")
    def check_exclusive(self) -> Self:
        if self.incremental and self.force_all:
            raise ValueError("incremental과 force_all 옵션은 동시에 사용할 수 없습니다.")
        return self


class SyncResult(BaseModel):
    """동기화 결과. 실패해도 이 형식이다 (상태 코드만 500)."""

    status: Literal["completed", "partial", "failed"]
    type: Literal["full", "incremental"]
    processed: int = 0
    synced: int = 0
    skipped: int = 0
    errors: int = 0
    success_rate: float = Field(0.0, description="synced / processed × 100")
    execution_time: float = Field(0.0, description="초")
    message: str
    ghost_deleted: int | None = Field(
        None, description="전체 동기화에서 지운 고스트 문서(MongoDB 에 없는 색인) 수. 증분 동기화·연결 실패면 키가 없다"
    )


class SyncStatus(BaseModel):
    mongodb_connected: bool
    elasticsearch_connected: bool
    total_posts_in_mongodb: int
    published_posts_in_mongodb: int = Field(description="발행 여부 필드가 없어 total_posts_in_mongodb 와 같다")
    total_docs_in_elasticsearch: int = Field(description="posts 인덱스의 문서 수")
    last_sync_time: datetime | None = Field(description="이 프로세스가 마지막으로 동기화한 시각. 재시작하면 null")
    sync_needed: bool
