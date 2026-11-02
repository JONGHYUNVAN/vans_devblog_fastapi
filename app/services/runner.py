"""PipelineRunner: 기동 시와 주기 스캔으로 코루틴이 없는 진행 대상만 띄운다 (설계 04 문서 1절, 06 문서 3절).

uvicorn 워커 1개 전제다. id → asyncio.Task 표가 프로세스 안에만 있으므로 같은 글을 두 프로세스가 돌리면 호출이 중복된다
(설계 07 문서 Q-04). `agents run` 을 서버와 동시에 띄우지 않는다.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from app.core.config import Settings
from app.core.exceptions import ConcurrentUpdateError
from app.models.enums import FailureCode, RoundAbortReason, RoundStatus
from app.repositories.agent_round import PublishingRoundRepository
from app.repositories.post_draft import PostDraftRepository
from app.services.draft_pipeline import DraftPipelineService
from app.services.round import RoundService
from app.services.topic_selection import TopicSelectionService

logger = logging.getLogger(__name__)


class PipelineRunner:
    """lifespan 이 start() 로 시작하고 종료 때 stop() 으로 코루틴을 전부 cancel 한다. 끊긴 호출은 다음 기동 때 다시 한다."""

    def __init__(
        self,
        *,
        round_repository: PublishingRoundRepository,
        draft_repository: PostDraftRepository,
        round_service: RoundService,
        topic_selection_service: TopicSelectionService,
        draft_pipeline_service: DraftPipelineService,
        settings: Settings,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._rounds = round_repository
        self._drafts = draft_repository
        self._round_service = round_service
        self._topic_selection = topic_selection_service
        self._draft_pipeline = draft_pipeline_service
        self._settings = settings
        self._sleep = sleep
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._loop_task: asyncio.Task[None] | None = None

    @property
    def running(self) -> list[str]:
        """코루틴이 도는 대상 ID. 테스트와 상태 조회용."""
        return list(self._tasks)

    def start(self) -> None:
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._loop(), name="pipeline-runner")

    async def stop(self) -> None:
        tasks = [task for task in [self._loop_task, *self._tasks.values()] if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._loop_task = None
        self._tasks.clear()

    async def _loop(self) -> None:
        while True:
            try:
                await self.scan()
            except Exception:  # 러너 경계. 스캔 실패(예: Mongo 다운)가 루프를 죽이면 안 된다
                logger.exception("pipeline scan failed")
            await self._sleep(self._settings.scan_interval_s)

    async def scan(self) -> None:
        """정리를 먼저 하고, 코루틴이 없는 TOPIC_SELECTION 라운드와 IN_PROGRESS 라운드의 진행 대상 글을 띄운다."""
        await self._round_service.reconcile()
        for publishing_round in await self._rounds.list_by_status(RoundStatus.TOPIC_SELECTION):
            self._spawn(publishing_round.round_id, self._drive_round(publishing_round.round_id))
        for publishing_round in await self._rounds.list_by_status(RoundStatus.IN_PROGRESS):
            for draft in await self._drafts.list_by_round(publishing_round.round_id):
                if draft.is_active and not draft.awaits_approval(self._settings.publish_requires_approval):
                    self._spawn(draft.draft_id, self._drive_draft(draft.draft_id))

    def _spawn(self, key: str, coroutine: Coroutine[Any, Any, None]) -> None:
        if key in self._tasks:
            coroutine.close()
            return
        task = asyncio.create_task(coroutine, name=f"pipeline:{key}")
        self._tasks[key] = task
        task.add_done_callback(lambda _: self._tasks.pop(key, None))

    async def _drive_round(self, round_id: str) -> None:
        try:
            await self._topic_selection.drive(round_id)
        except Exception as exc:  # 코루틴 경계 (설계 03 문서 3절)
            logger.exception("topic selection for round %s crashed", round_id)
            await self._abort_round(round_id, exc)

    async def _drive_draft(self, draft_id: str) -> None:
        try:
            await self._draft_pipeline.drive(draft_id)
        except Exception as exc:  # 코루틴 경계 (설계 03 문서 29행)
            logger.exception("draft %s crashed", draft_id)
            await self._fail_draft(draft_id, exc)

    async def _abort_round(self, round_id: str, exc: Exception) -> None:
        publishing_round = await self._rounds.get(round_id)
        if publishing_round is None or publishing_round.status != RoundStatus.TOPIC_SELECTION:
            return
        publishing_round.abort(RoundAbortReason.INTERNAL_ERROR)
        try:
            await self._rounds.save(publishing_round)
        except ConcurrentUpdateError:
            logger.info("round %s changed while recording internal error", round_id)

    async def _fail_draft(self, draft_id: str, exc: Exception) -> None:
        """같은 오류로 호출을 되풀이하지 않게 FAILED(INTERNAL_ERROR) 로 멈춘다. 사람이 재개하거나 폐기한다."""
        draft = await self._drafts.get(draft_id)
        if draft is None or not draft.is_active:
            return
        draft.mark_failed(FailureCode.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
        try:
            await self._drafts.save(draft)
        except ConcurrentUpdateError:
            logger.info("draft %s changed while recording internal error", draft_id)
