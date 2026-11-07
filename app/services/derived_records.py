"""발행된 글의 파생 문서 따라 쓰기: post_glossary, 뜻의 글 목록, post_provenance (설계 08 문서 2.2절 "발행 연결", 3.4절).

커밋 지점은 글 PUBLISHED 이고 여기는 그 뒤의 멱등한 따라 쓰기다. 발행·CONFIRM_PUBLISHED 가 바로 부르고, 중간에 멈추면
정리(reconcile)가 publication.derived_at 이 없는 PUBLISHED 글을 찾아 마저 쓴다. LLM 을 부르지 않는다.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from app.core.exceptions import NotFoundError
from app.models.enums import PostStatus
from app.models.post_draft import PostDraft
from app.repositories.agent_call import AgentCallRepository
from app.repositories.agent_round import PublishingRoundRepository
from app.repositories.glossary import GlossaryRepository
from app.repositories.post_draft import PostDraftRepository
from app.repositories.post_provenance import PostProvenanceRepository
from app.services.glossary import build_post_glossary
from app.services.provenance import build_provenance

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


class DerivedRecordsWriter:
    def __init__(
        self,
        *,
        draft_repository: PostDraftRepository,
        round_repository: PublishingRoundRepository,
        agent_call_repository: AgentCallRepository,
        glossary_repository: GlossaryRepository,
        provenance_repository: PostProvenanceRepository,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self._drafts = draft_repository
        self._rounds = round_repository
        self._calls = agent_call_repository
        self._glossary = glossary_repository
        self._provenance = provenance_repository
        self._now = now

    async def write(self, draft: PostDraft, *, built_by: str) -> None:
        """PUBLISHED 글 하나의 파생 문서를 쓰고 derived_at 을 남긴다. 되풀이해도 결과가 같다. 발행 전 글이면 아무것도 하지 않는다."""
        if draft.status != PostStatus.PUBLISHED or draft.publication is None:
            return
        post_id = draft.publication.external_post_id
        calls = await self._calls.list_by_round(draft.round_id)
        if draft.glossary is not None:
            glossary_call = next((call for call in calls if call.call_id == draft.glossary.call_id), None)
            post_glossary = build_post_glossary(
                draft,
                draft.glossary,
                glossary_call.prompt_template_version if glossary_call else None,
                glossary_call.pipeline_version if glossary_call else None,
            )
            if post_glossary is not None:
                await self._glossary.add_post_glossary_if_missing(post_glossary)
                await self._glossary.add_post_to_senses([item.sense_key for item in post_glossary.items], post_id)
        publishing_round = await self._rounds.get(draft.round_id)
        if publishing_round is None:
            raise NotFoundError(f"round {draft.round_id} does not exist")
        now = self._now()
        await self._provenance.add_if_missing(
            build_provenance(draft, publishing_round, calls, built_by=built_by, now=now)
        )
        await self._drafts.set_derived_at(draft.draft_id, now)

    async def write_missing(self, *, built_by: str = "reconcile") -> list[str]:
        """derived_at 이 없는 PUBLISHED 글 전부. 한 글의 실패가 다른 글을 막지 않는다. 쓴 글 id 를 돌려준다."""
        written: list[str] = []
        for draft in await self._drafts.list_published_without_derived():
            try:
                await self.write(draft, built_by=built_by)
            except Exception:  # 정리 경계. 다음 스캔이 다시 시도한다
                logger.exception("draft %s derived records failed; reconcile will retry", draft.draft_id)
                continue
            written.append(draft.draft_id)
        return written
