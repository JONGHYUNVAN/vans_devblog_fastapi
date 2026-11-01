"""agent_calls 컬렉션. 시도마다 한 건을 insert 만 하는 영수증 (설계 02 문서 6절, 07 문서 D-26)."""

from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from app.models.agent_call import AgentCall, TokenUsage

AGENT_CALLS_COLLECTION = "agent_calls"


class AgentCallRepository:
    """agent_calls 추가·조회와 글별 usage 합계."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._calls = database[AGENT_CALLS_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self._calls.create_index("draft_id")
        await self._calls.create_index([("round_id", 1), ("kind", 1)])

    async def add(self, call: AgentCall) -> None:
        await self._calls.insert_one(call.model_dump(by_alias=True))

    async def get(self, call_id: str) -> AgentCall | None:
        doc = await self._calls.find_one({"_id": call_id})
        return AgentCall.model_validate(doc) if doc else None

    async def list_by_draft(self, draft_id: str) -> list[AgentCall]:
        """시작 순."""
        docs = await self._calls.find({"draft_id": draft_id}).sort("started_at", 1).to_list()
        return [AgentCall.model_validate(doc) for doc in docs]

    async def list_by_round(self, round_id: str) -> list[AgentCall]:
        """시작 순. 주제 제안 호출(draft_id 없음)도 포함한다."""
        docs = await self._calls.find({"round_id": round_id}).sort("started_at", 1).to_list()
        return [AgentCall.model_validate(doc) for doc in docs]

    async def sum_usage(self, draft_id: str) -> TokenUsage:
        """그 글의 모든 시도(실패 포함)의 usage 합계. 소프트 예산 검사의 기준이다 (설계 07 문서 D-20)."""
        pipeline: list[dict[str, Any]] = [
            {"$match": {"draft_id": draft_id}},
            {
                "$group": {
                    "_id": None,
                    "prompt_tokens": {"$sum": "$usage.prompt_tokens"},
                    "completion_tokens": {"$sum": "$usage.completion_tokens"},
                }
            },
        ]
        cursor = await self._calls.aggregate(pipeline)
        rows = await cursor.to_list()
        if not rows:
            return TokenUsage()
        return TokenUsage(prompt_tokens=rows[0]["prompt_tokens"], completion_tokens=rows[0]["completion_tokens"])
