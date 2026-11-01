"""revision 낙관적 잠금으로 문서 전체를 바꾸는 저장. agent_rounds·agent_drafts 가 같이 쓴다 (설계 02 문서 6절)."""

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel
from pymongo.asynchronous.collection import AsyncCollection

from app.core.exceptions import ConcurrentUpdateError


class Versioned(Protocol):
    revision: int
    updated_at: datetime


async def replace_with_revision(collection: AsyncCollection, doc_id: str, model: BaseModel) -> None:
    """읽었을 때의 revision 이 그대로일 때만 문서 전체를 바꾸고 revision 을 1 올린다.

    바뀌어 있으면 ConcurrentUpdateError 를 던지고 모델의 revision·updated_at 을 되돌린다.
    설계는 $set + $inc 로 적었지만 모델이 문서 전체를 들고 있어 replace 가 같은 효과다.
    """
    versioned: Versioned = model  # type: ignore[assignment] — revision·updated_at 을 가진 모델만 넘긴다
    expected_revision = versioned.revision
    previous_updated_at = versioned.updated_at
    versioned.revision = expected_revision + 1
    versioned.updated_at = datetime.now(UTC)
    result = await collection.replace_one(
        {"_id": doc_id, "revision": expected_revision}, model.model_dump(by_alias=True)
    )
    if result.matched_count == 0:
        versioned.revision = expected_revision
        versioned.updated_at = previous_updated_at
        raise ConcurrentUpdateError(f"{collection.name} {doc_id} changed since revision {expected_revision}")
