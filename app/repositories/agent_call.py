"""agent_calls 컬렉션. 시도마다 한 건을 insert 만 하는 영수증 (설계 02 문서 6절, 07 문서 D-26)."""

from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from app.models.agent_call import AgentCall, TokenUsage

AGENT_CALLS_COLLECTION = "agent_calls"
# 목록 조회가 읽지 않는 본문 필드. 메시지 전문과 모델 출력 원문이 영수증 크기의 대부분이라, 라운드 하나의 영수증을 본문째
# 읽으면 소켓 제한(20초)을 넘겼다: 2026-11-12 8번 라운드 글의 발행 따라 쓰기가 스캔마다 NetworkTimeout 으로 실패했고
# 영수증 조회 API 도 같은 라운드에서 500 이었다. 목록을 쓰는 곳(제작 기록·지표·영수증 요약)은 본문을 읽지 않는다
_WITHOUT_BODIES = {"messages": 0, "output_raw": 0}


class AgentCallRepository:
    """agent_calls 추가·조회와 글별 usage 합계. 목록 조회는 본문(messages·output_raw) 없는 영수증을 돌려준다."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._calls = database[AGENT_CALLS_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self._calls.create_index("draft_id")
        await self._calls.create_index([("round_id", 1), ("kind", 1)])
        # 라운드 영수증을 시작 순으로 읽는 조회(list_by_round)가 정렬을 인덱스로 한다. 없으면 본문째 메모리에 올려 정렬한다
        await self._calls.create_index([("round_id", 1), ("started_at", 1)])

    async def add(self, call: AgentCall) -> None:
        await self._calls.insert_one(call.model_dump(by_alias=True))

    async def get(self, call_id: str) -> AgentCall | None:
        doc = await self._calls.find_one({"_id": call_id})
        return AgentCall.model_validate(doc) if doc else None

    async def list_by_draft(self, draft_id: str) -> list[AgentCall]:
        """시작 순. 본문 없는 영수증이다(messages 는 빈 목록, output_raw 는 None). 본문은 get 으로 한 건씩 읽는다."""
        docs = await self._calls.find({"draft_id": draft_id}, _WITHOUT_BODIES).sort("started_at", 1).to_list()
        return [_without_bodies(doc) for doc in docs]

    async def list_by_round(self, round_id: str) -> list[AgentCall]:
        """시작 순. 주제 제안 호출(draft_id 없음)도 포함한다. 본문 없는 영수증이다(list_by_draft 와 같다)."""
        docs = await self._calls.find({"round_id": round_id}, _WITHOUT_BODIES).sort("started_at", 1).to_list()
        return [_without_bodies(doc) for doc in docs]

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


def _without_bodies(doc: dict[str, Any]) -> AgentCall:
    """본문 필드를 뺀 문서 → 영수증. messages 는 필수 필드라 빈 목록으로 채운다."""
    return AgentCall.model_validate({**doc, "messages": []})
