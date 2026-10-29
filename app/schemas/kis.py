"""KIS 스냅샷 응답. 형식은 Django 서비스 응답(tests/golden/kis_snapshot.json)과 같다."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SnapshotData(BaseModel):
    model_config = ConfigDict(validate_by_name=True, serialize_by_alias=True)

    symbol: str
    name: str = Field(description="종목 이름. 모르는 종목이면 종목코드")
    trade: dict[str, Any] | None = Field(description="마지막 체결가. 받은 적이 없으면 null")
    orderbook: dict[str, Any] | None = Field(description="마지막 호가. 받은 적이 없으면 null")
    updated_at: str | None = Field(alias="updatedAt", description="조회 시각(UTC ISO 8601). 데이터가 없으면 null")


class Snapshot(BaseModel):
    success: Literal[True] = True
    data: SnapshotData
