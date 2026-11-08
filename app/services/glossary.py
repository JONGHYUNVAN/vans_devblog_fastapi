"""용어 사전: 추출 단계, 등록 확인·뜻 선택, 승인·별칭·병합·무결성 (설계 08 문서 2.2~2.5절, D-64·D-65).

추출은 POST_REVIEW 의 넷째 단계 GLOSSARY 에서 글 코루틴(DraftPipelineService)이 부른다. 다듬기·전체 읽기·수준 판정 뒤라
surface 가 발행본과 같고, 발행 전이라 _step 한 걸음으로 끝난다. 실패해도 글을 멈추지 않는다(다듬기와 같은 정책, D-64).
등록 확인은 코드가 먼저 한다: 정규화 키 정확 일치 → AI 의 same_as(목록 안만) → 비슷한 표제어(사람 참고) → 새 표제어(PROPOSED).
코드 판정이 AI 판정보다 우선한다(D-65). 새 표제어·별칭·뜻은 사람 승인 뒤에만 표시된다. 승인은 전역이다.
불변식(08 문서 2.1절)을 지키는 곳이 여기다: lookup_keys 는 저장 전에 다시 계산하고, 둘째 뜻 승인은 모든 뜻의 구분 문구를 요구하며,
글 하나 안에서 surface 와 sense_key 는 중복이 없다. 운영 Mongo 를 배포된 러너와 나눠 쓰므로 CLI 경로도 같은 규칙을 탄다.
"""

import logging
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from app.agents.builders import build_glossary_input, render_messages
from app.agents.contracts import GlossaryCandidate, GlossaryOutput, KnownHeadword, KnownSense
from app.core.config import Settings
from app.core.exceptions import InvalidRequestError, NotFoundError
from app.models.enums import CallKind, GlossaryStatus, PostStatus
from app.models.glossary import (
    DOMAINS,
    OTHER_DOMAIN,
    AliasProposal,
    DiscardedTerm,
    GlossaryHeadword,
    GlossaryRecord,
    GlossarySense,
    ItemResolutionKind,
    PendingTerm,
    PostGlossary,
    PostGlossaryItem,
    ProposalSource,
)
from app.models.model_binding import CallModel
from app.models.pipeline import GlossaryParameters, PipelineVersion
from app.models.post_draft import DraftVersion, PostDraft
from app.models.publishing_round import PublishingRound
from app.models.routing import ROUTE_GLOSSARY
from app.models.sections import find_anchors, mask_code_fences
from app.repositories.agent_round import PublishingRoundRepository
from app.repositories.glossary import GlossaryRepository
from app.repositories.post_draft import PostDraftRepository
from app.services.agent_calls import AgentCallService
from app.services.glossary_matching import headword_key_of, normalize_key, similar_headwords, similarity
from app.services.pipeline_versions import PipelineVersionService
from app.services.routing import DraftRoutes

logger = logging.getLogger(__name__)

_INLINE_CODE = re.compile(r"`[^`\n]+`")
_KNOWN_STATUSES = (GlossaryStatus.APPROVED, GlossaryStatus.PROPOSED)
_DEDUPE_THRESHOLD = 0.55  # dedupe 가 사람에게 보일 표제어 쌍의 유사도 하한


def _now() -> datetime:
    return datetime.now(UTC)


def _mask_code(markdown: str) -> str:
    """코드 펜스와 인라인 코드를 가린 본문. surface 는 여기서 찾아야 코드 안의 글자를 용어로 잡지 않는다."""
    return _INLINE_CODE.sub(lambda match: " " * len(match.group(0)), mask_code_fences(markdown))


class GlossaryService:
    """추출(글 코루틴)과 승인·정리(CLI)를 한 서비스가 맡는다. 리포지토리는 여기서만 쓴다."""

    def __init__(
        self,
        *,
        repository: GlossaryRepository,
        settings: Settings,
        agent_call_service: AgentCallService | None = None,
        draft_repository: PostDraftRepository | None = None,
        round_repository: PublishingRoundRepository | None = None,
        version_service: PipelineVersionService | None = None,
        now: Callable[[], datetime] = _now,
    ) -> None:
        """리포지토리와 설정은 필수, 나머지는 쓰는 경로에 따라 선택이다.

        agent_call_service 가 없으면 추출은 "no LLM client" 로 건너뛴다. draft·round 리포지토리와 version_service 는
        보충(backfill)·list_missing 에 필수라 없으면 ValueError 고, pending·show 는 있을 때만 발행 전 글 문서까지 본다.
        """
        self._repo = repository
        self._settings = settings
        self._calls = agent_call_service
        self._drafts = draft_repository
        self._rounds = round_repository
        self._versions = version_service  # 보충(backfill)이 라운드의 버전(프롬프트·용어 값)을 찾을 때 쓴다
        self._now = now

    # ── 추출 단계 ────────────────────────────────────────────────────

    async def extract(self, draft: PostDraft, publishing_round: PublishingRound, version: PipelineVersion) -> None:
        """POST_REVIEW(GLOSSARY) 한 걸음. 돌아오면 글은 READY_TO_PUBLISH 다. 호출·라우트가 없으면 건너뛴 기록만 남는다."""
        # 대상은 현재 버전(다듬기까지 끝난 발행본)이다. 여기서 찾은 surface 가 프런트가 감쌀 글자와 같아야 한다
        record = await self._run(draft, draft.current_version, publishing_round, version)
        # 기록과 단계 전진을 한 객체에 두어 글 코루틴이 한 번에 저장한다. 재개 때 추출을 되풀이하지 않는다(D-49)
        draft.finish_glossary(record)

    async def backfill(self, draft_id: str) -> GlossaryRecord:
        """보충: 발행된 버전으로 추출을 다시 돌려 글 기록과 post_glossary 를 덮어쓴다. 사람의 resolve·drop 은 사라진다."""
        if self._drafts is None or self._rounds is None or self._versions is None:
            raise ValueError("backfill needs draft and round repositories and the pipeline version service")
        draft = await self._drafts.get(draft_id)
        if draft is None:
            raise NotFoundError(f"draft {draft_id} does not exist")
        publishing_round = await self._rounds.get(draft.round_id)
        if publishing_round is None:
            raise NotFoundError(f"round {draft.round_id} does not exist")
        # 발행된 버전으로 돌린다. current_version 은 사람 재수정으로 발행본보다 뒤일 수 있어 surface 가 블로그 본문과 어긋난다
        version = _published_version(draft)
        if version is None:
            raise InvalidRequestError(f"draft {draft_id} has no version that passed validation")
        # 라운드가 고정한 파이프라인 버전(프롬프트·용어 조절 값)을 쓴다. 지금 활성 버전이 아니다(09 문서, 라운드 단위 고정)
        pipeline_version = await self._versions.for_round(publishing_round)
        # 보충 대상은 대개 GLOSSARY 라우트가 생기기 전의 라운드다(2026-11-08 b48cf759). 라운드 스냅샷에 라우트가 없으면
        # 최신 개정의 라우트로 뽑는다. 프롬프트·조절 값은 그대로 라운드 개정이다(D-73)
        extractor = DraftRoutes(publishing_round, draft, pipeline_version.personas).glossary_extractor
        if extractor is None:
            latest = await self._versions.latest()
            candidates = latest.routes.get(ROUTE_GLOSSARY, []) if latest is not None else []
            extractor = candidates[0] if candidates else None
        record = await self._run(draft, version, publishing_round, pipeline_version, extractor=extractor)
        draft.glossary = record
        await self._drafts.save(draft)
        # 발행된 글이면 post_glossary 와 뜻의 글 목록까지 바로 덮어쓴다(따라 쓰기는 이미 지나갔다). 사람의 resolve·drop 은 사라진다
        if draft.status == PostStatus.PUBLISHED and draft.publication is not None:
            template_version, pipeline_version_id = await self._template_version(record)
            post_glossary = build_post_glossary(draft, record, template_version, pipeline_version_id)
            if post_glossary is not None:
                await self._repo.save_post_glossary(post_glossary)
                await self._repo.add_post_to_senses(
                    [item.sense_key for item in record.items], draft.publication.external_post_id
                )
        return record

    async def list_missing_draft_ids(self) -> list[str]:
        """PUBLISHED 인데 추출 기록이 없는 글. `agents glossary extract --missing` 의 대상이다."""
        if self._drafts is None:
            raise ValueError("listing needs the draft repository")
        return [
            draft.draft_id
            for draft in await self._drafts.list_by_status(PostStatus.PUBLISHED)
            if draft.glossary is None
        ]

    async def _template_version(self, record: GlossaryRecord) -> tuple[str | None, str | None]:
        """추출 호출 영수증의 (옛 프롬프트 디렉터리 이름, 파이프라인 버전 id)."""
        if record.call_id is None or self._calls is None:
            return None, None
        try:
            call = await self._calls.get(record.call_id)
        except NotFoundError:
            return None, None
        return call.prompt_template_version, call.pipeline_version

    async def _run(
        self,
        draft: PostDraft,
        version: DraftVersion,
        publishing_round: PublishingRound,
        pipeline_version: PipelineVersion,
        extractor: CallModel | None = None,
    ) -> GlossaryRecord:
        """추출 호출 한 번과 그 뒤 등록 확인. 어떤 경우에도 GlossaryRecord 를 돌려주고 예외를 내지 않는다(건너뛴 사유는 skipped).

        extractor 를 주면 라운드 스냅샷 대신 그 모델로 부른다(보충 경로). 없으면 라운드의 GLOSSARY 라우트다.
        """
        parameters = pipeline_version.parameters.glossary
        record = GlossaryRecord(target_version=version.version_number, extracted_at=self._now())
        # GLOSSARY 는 선택 라우트다(D-64). 배포된 라우트 문서에 아직 없거나 이 기능 전에 만든 라운드면 호출 없이 지나간다
        model = extractor or DraftRoutes(publishing_round, draft, pipeline_version.personas).glossary_extractor
        if model is None:
            record.skipped = "no GLOSSARY route"
            return record
        if self._calls is None:  # CLI 조회 경로처럼 LLM 호출 서비스 없이 만든 구성
            record.skipped = "no LLM client"
            return record
        # 기존 표제어·뜻 목록을 입력에 넣어 AI 가 same_as·sense_key 로 맞추게 한다. 상한을 넘으면 많이 쓰인 것부터 남긴다
        known, by_key = await self._known_headwords(parameters.full_list_max)
        record.known_headwords_sent = len(known)  # 목록이 잘렸는지 지표로 본다 (Q-20)
        messages = render_messages(
            build_glossary_input(
                draft,
                publishing_round,
                version,
                known,
                min_terms=parameters.min_terms,
                max_terms=parameters.max_terms,
            ),
            pipeline_version.prompts,
            model,
        )
        # 영수증(agent_calls)은 AgentCallService 가 남긴다. 실패해도 영수증은 남아 제작 기록에 실패 시도로 보인다
        try:
            result = await self._calls.call(
                kind=CallKind.GLOSSARY,
                round_id=draft.round_id,
                binding=model,
                messages=messages,
                schema=GlossaryOutput,
                draft=draft,
                target_version=version.version_number,
                max_attempts=pipeline_version.parameters.max_llm_retries,
                pipeline_version=pipeline_version.version_id,
            )
        except Exception as exc:  # 용어 추출 실패는 글을 멈추지 않는다(재시도 소진·예산 초과·벤더 거부 전부, D-64)
            logger.warning("draft %s skipped %s: %s", draft.draft_id, CallKind.GLOSSARY, exc)
            record.skipped = f"{type(exc).__name__}: {exc}"
            return record
        record.call_id = result.call_id
        source = ProposalSource(draft_id=draft.draft_id, call_id=result.call_id)
        # 후보 검사·등록 확인·뜻 선택은 DB 가 필요해 파서가 아니라 여기서 한다(D-65). 파서는 모양만 본다
        await self._resolve(result.output, draft, version, record, source, by_key, parameters)
        return record

    async def _known_headwords(self, full_list_max: int) -> tuple[list[KnownHeadword], dict[str, GlossaryHeadword]]:
        """추출 입력의 표제어 목록. 상한을 넘으면 글이 많이 가리키는 표제어부터 남긴다 (08 문서 Q-20, 2단계 호출 전까지의 임시 규칙)."""
        # PROPOSED 도 보낸다: 승인 전이라도 같은 표제어를 다른 글이 또 만들지 않게. 표시 여부는 읽기 API 가 가른다
        headwords = await self._repo.list_headwords(_KNOWN_STATUSES)
        senses = await self._repo.list_senses(statuses=_KNOWN_STATUSES)
        by_headword: dict[str, list[GlossarySense]] = {}
        for sense in senses:
            by_headword.setdefault(sense.headword_key, []).append(sense)
        if len(headwords) > full_list_max:
            headwords.sort(
                key=lambda each: (
                    -sum(len(sense.post_ids) for sense in by_headword.get(each.headword_key, [])),
                    each.headword_key,
                )
            )
            headwords = headwords[:full_list_max]
        known = [
            KnownHeadword(
                key=each.headword_key,
                term_ko=each.term_ko,
                term_en=each.term_en,
                aliases=list(each.aliases),
                senses=[
                    KnownSense(
                        sense_key=sense.sense_key,
                        domains=list(sense.domains),
                        definition=sense.definition,
                        distinguisher=sense.distinguisher,
                        status=sense.status,
                    )
                    for sense in by_headword.get(each.headword_key, [])
                ],
            )
            for each in headwords
        ]
        return known, {each.headword_key: each for each in headwords}

    async def _resolve(
        self,
        output: GlossaryOutput,
        draft: PostDraft,
        version: DraftVersion,
        record: GlossaryRecord,
        source: ProposalSource,
        known_by_key: dict[str, GlossaryHeadword],
        parameters: GlossaryParameters,
    ) -> None:
        """후보마다 등록 확인(①~④)과 뜻 선택을 한다. 버리는 사유는 record.discarded 에 남긴다."""
        # 코드 펜스·인라인 코드를 가린 본문에서만 surface 를 찾는다. 코드 안 식별자를 용어로 잡지 않기 위해서다(2.1절 불변식)
        prose = _mask_code(version.content_markdown)
        # 앵커는 프런트가 섹션으로 잇는 값이라 없는 앵커를 가리키는 후보는 버린다. 전제 지식에 든 용어는 독자가 안다고 본 것이라 설명하지 않는다
        section_ids = set(find_anchors(version.content_markdown))
        prerequisites = draft.topic.prerequisites.casefold()
        # ③ 비슷한 표제어 후보의 모집단. 새 표제어를 만들 때마다 더해 같은 글 안의 후보끼리도 서로 보인다
        all_keys = [each.headword_key for each in await self._repo.list_headwords()]
        seen_surfaces: set[str] = set()
        seen_senses: set[str] = set()
        # 파서는 24개까지 받고 조절 값(max_terms)으로 여기서 자른다. 앞쪽이 더 중요한 후보라는 프롬프트 규칙에 기댄다
        for candidate in output.candidates[: parameters.max_terms]:
            # 1) 후보 자체의 검사: 표면형 모양·본문 존재·앵커·전제 지식·표면형 중복. DB 없이 판단할 수 있는 것
            reason = self._discard_reason(
                candidate, prose, section_ids, prerequisites, seen_surfaces, parameters.surface_max_chars
            )
            if reason is not None:
                record.discarded.append(DiscardedTerm(surface=candidate.surface, reason=reason))
                continue
            seen_surfaces.add(candidate.surface)
            # 2) 등록 확인 ①②④: 어느 표제어인지. None 은 동시 추출 충돌 뒤에도 못 찾은 경우라 버린다
            resolved = await self._resolve_headword(candidate, known_by_key, source, all_keys)
            if resolved is None:
                record.discarded.append(DiscardedTerm(surface=candidate.surface, reason="표제어 충돌"))
                continue
            headword, resolution = resolved
            # 3) 뜻 선택: 그 표제어의 어느 뜻인지. REJECTED 뜻은 후보에서 뺀다(AI 가 그 키를 줘도 무시)
            senses = [
                sense
                for sense in await self._repo.list_senses(headword.headword_key)
                if sense.status != GlossaryStatus.REJECTED
            ]
            sense_key, resolution, pending_reason, sense_candidates = await self._resolve_sense(
                candidate, headword, senses, resolution, source
            )
            # 뜻을 못 고르면 보류로 남겨 사람이 resolve 한다. 표제어 제안은 그대로 둔다(2.2절 "뜻 선택")
            if sense_key is None:
                record.pending.append(
                    PendingTerm(
                        term_ko=candidate.term_ko or candidate.term_en or "",
                        term_en=candidate.term_en,
                        surface=candidate.surface,
                        section_id=candidate.section_id,
                        headword_key=headword.headword_key,
                        sense_candidates=sense_candidates,
                        similar_headwords=headword.similar_headwords if resolution == "NEW" else [],
                        reason=pending_reason or "뜻 미정",
                    )
                )
                continue
            # 같은 뜻을 두 표면형이 가리키면 첫 등장만 남긴다(글 안에서 sense_key 유일). 프런트는 첫 등장만 감싼다
            if sense_key in seen_senses:
                record.discarded.append(DiscardedTerm(surface=candidate.surface, reason="뜻 중복"))
                continue
            seen_senses.add(sense_key)
            record.items.append(
                PostGlossaryItem(
                    sense_key=sense_key,
                    surface=candidate.surface,
                    section_id=candidate.section_id,
                    resolution=resolution,
                )
            )

    def _discard_reason(
        self,
        candidate: GlossaryCandidate,
        prose: str,
        section_ids: set[str],
        prerequisites: str,
        seen_surfaces: set[str],
        surface_max_chars: int,
    ) -> str | None:
        """후보를 버릴 사유. 없으면 None. 사유 문자열은 record.discarded 에 그대로 남아 프롬프트 튜닝 지표가 된다."""
        term_ko, term_en = candidate.term_ko.strip(), (candidate.term_en or "").strip()
        if not term_ko and not term_en:
            return "표기 없음"
        surface = candidate.surface
        if "\n" in surface or len(surface) > surface_max_chars:
            return "표면형 형식"  # 줄바꿈이 있거나 너무 길면 프런트의 정확 일치 감싸기가 실패한다
        if surface in seen_surfaces:
            return "표면형 중복"
        if surface not in prose:
            return "본문에 없음"  # 코드 밖 본문에 글자 그대로 없으면 AI 가 표면형을 다듬은 것이다. 프런트도 못 찾으므로 버린다
        if candidate.section_id not in section_ids:
            return "앵커 없음"
        # 전제 지식 대조는 조사가 붙지 않은 term_ko·term_en 으로 한다. surface 는 "트랜잭션을" 처럼 조사가 붙는다
        if prerequisites and any(term and term.casefold() in prerequisites for term in (term_ko, term_en)):
            return "전제 지식"
        return None

    async def _resolve_headword(
        self,
        candidate: GlossaryCandidate,
        known_by_key: dict[str, GlossaryHeadword],
        source: ProposalSource,
        all_keys: list[str],
    ) -> tuple[GlossaryHeadword, ItemResolutionKind] | None:
        """등록 확인 ①~④ (08 문서 2.2절). 정확 일치가 AI 의 same_as 를 이긴다.

        None 은 새로 넣지도 다시 찾지도 못한 경우(동시 추출 충돌 뒤 조회 실패)다. 호출자가 "표제어 충돌" 로 버린다.
        """
        # surface 는 조사가 붙어 있어 조회에 쓰지 않는다. 키는 term_en 우선으로, 표제어 키 규칙(headword_key_of)과 같다
        term_ko, term_en = candidate.term_ko.strip(), (candidate.term_en or "").strip() or None
        en_key = normalize_key(term_en) if term_en else ""
        ko_key = normalize_key(term_ko) if term_ko else ""
        # ① 정확 일치. AI 의 same_as 가 다른 키를 가리켜도 코드가 이긴다(D-65). 사람이 승인한 표기·별칭이 AI 추측보다 믿을 만하다
        found = await self._find_by_lookup(en_key, ko_key)
        if found is not None:
            return found, "EXACT"
        # ② AI 의 same_as. 입력 목록에 있던 키만 믿는다(목록 밖 키는 지어낸 것일 수 있다).
        #    맞춘 표기가 그 표제어의 조회 키에 없으면 별칭으로 제안만 하고, 승인 전에는 ①에 쓰지 않는다
        if candidate.same_as is not None and candidate.same_as in known_by_key:
            headword = await self._repo.get_headword(candidate.same_as)
            if headword is not None:
                headword = await self._follow_merge(headword)
                await self._propose_aliases(headword, ((term_ko, ko_key), (term_en, en_key)), source)
                return headword, "SAME_AS"
        # ③ 비슷한 표제어는 사람 참고용으로 새 표제어에 남긴다. 사전이 full_list_max 이하라 AI 가 이미 전체 목록을 봤으므로
        #    여기서 다시 판정하지 않는다(사전이 더 커지면 2단계 호출 GLOSSARY_MATCH, Q-20)
        # ④ 새 표제어. PROPOSED 라 승인 전에는 표시되지 않는다
        key = en_key or ko_key
        headword = GlossaryHeadword(
            headword_key=key,
            term_ko=term_ko or term_en or "",
            term_en=term_en,
            lookup_keys=[each for each in dict.fromkeys((en_key, ko_key)) if each],
            similar_headwords=similar_headwords(key, all_keys),
            status=GlossaryStatus.PROPOSED,
            proposed_by=source,
            created_at=self._now(),
            updated_at=self._now(),
        )
        if await self._repo.add_headword_if_missing(headword):
            all_keys.append(key)
            return headword, "NEW"
        # 다른 글이 먼저 만들었거나 조회 키가 다른 표제어에 속한다. 정확 일치로 다시 찾는다
        found = await self._find_by_lookup(en_key, ko_key)
        if found is None:
            found = await self._repo.get_headword(key)
        return (found, "EXACT") if found is not None else None

    async def _find_by_lookup(self, *keys: str) -> GlossaryHeadword | None:
        """여러 키 중 처음 맞는 표제어. MERGED 면 흡수한 표제어로 따라간다."""
        for key in keys:
            if not key:
                continue
            found = await self._repo.find_headword_by_lookup(key)
            if found is not None:
                return await self._follow_merge(found)
        return None

    async def _follow_merge(self, headword: GlossaryHeadword) -> GlossaryHeadword:
        """병합 사슬을 끝까지 따라간다. 끊긴 사슬(대상 없음)은 check 가 찾으므로 여기서는 멈추고 마지막 것을 돌려준다."""
        while headword.status == GlossaryStatus.MERGED and headword.merged_into:
            target = await self._repo.get_headword(headword.merged_into)
            if target is None:
                break
            headword = target
        return headword

    async def _propose_aliases(
        self, headword: GlossaryHeadword, terms: Iterable[tuple[str | None, str]], source: ProposalSource
    ) -> None:
        """same_as 로 맞춘 표기가 표제어의 조회 키에 없으면 별칭으로 제안한다. 승인 전에는 조회에 쓰지 않는다."""
        changed = False
        for text, key in terms:
            if not text or not key or key in headword.lookup_keys:
                continue
            if any(proposal.alias == text for proposal in headword.proposed_aliases):
                continue
            headword.proposed_aliases.append(AliasProposal(alias=text, source=source))
            changed = True
        if changed:
            headword.updated_at = self._now()
            await self._repo.save_headword(headword)

    async def _resolve_sense(
        self,
        candidate: GlossaryCandidate,
        headword: GlossaryHeadword,
        senses: list[GlossarySense],
        resolution: ItemResolutionKind,
        source: ProposalSource,
    ) -> tuple[str | None, ItemResolutionKind, str | None, list[str]]:
        """뜻 선택 (08 문서 2.2절 표). (뜻 키, resolution, 보류 사유, 후보 뜻 키)."""
        sense_keys = {sense.sense_key for sense in senses}
        # AI 가 준 sense_key 는 정해진 표제어의 뜻일 때만 믿는다. 다른 표제어의 뜻이면 무시하고 아래 규칙을 탄다
        if candidate.sense_key in sense_keys:
            return candidate.sense_key, resolution, None, []
        if candidate.new_sense is not None:
            proposal = candidate.new_sense
            definition = proposal.definition.strip()
            # 같은 표제어에 같은 정의가 이미 있으면(같은 글의 두 후보가 둘 다 제안한 경우) 하나만 남긴다
            same = next((sense for sense in senses if sense.definition.strip() == definition), None)
            if same is not None:
                return same.sense_key, resolution, None, []
            # "영역만 다른" 제안은 코드가 가릴 수 없다. PROPOSED 로만 두고 pending·show 가 기존 뜻을 나란히 보여
            # 사람이 승인 대신 merge-sense 로 합친다. 모르는 영역 슬러그는 버리고 남는 것이 없으면 other (domains 불변식)
            domains = [slug for slug in dict.fromkeys(proposal.domains) if slug in DOMAINS] or [OTHER_DOMAIN]
            sense = GlossarySense(
                sense_key=await self._repo.next_sense_key(),
                headword_key=headword.headword_key,
                domains=domains,
                definition=definition,
                distinguisher=proposal.distinguisher.strip(),
                status=GlossaryStatus.PROPOSED,
                proposed_by=source,
                created_at=self._now(),
                updated_at=self._now(),
            )
            await self._repo.add_sense(sense)
            return sense.sense_key, "NEW", None, []
        if len(senses) == 1:
            return senses[0].sense_key, resolution, None, []  # 뜻이 하나뿐이면 AI 가 안 골라도 그것이다
        if not senses:
            reason = "새 표제어인데 뜻 제안 없음" if resolution == "NEW" else "표제어에 뜻이 없음"
            return None, resolution, reason, []
        # 뜻이 둘 이상인데 AI 가 안 골랐다. 사람이 고르도록 보류하고, 후보는 AI 가 준 것 중 이 표제어의 뜻만 남긴다
        return None, resolution, "뜻 미정", [key for key in candidate.sense_candidates if key in sense_keys]

    # ── 조회·승인 (CLI) ───────────────────────────────────────────────

    async def pending(self, *, draft_id: str | None = None, post_id: str | None = None) -> dict[str, Any]:
        """PROPOSED 표제어·뜻·별칭과 보류 항목. draft_id 나 post_id 를 주면 그 글의 제안만."""
        if post_id is not None and draft_id is None:
            post_glossary = await self._repo.get_post_glossary(post_id)
            draft_id = post_glossary.draft_id if post_glossary is not None else None
            if draft_id is None:
                raise NotFoundError(f"post {post_id} has no glossary")

        def mine(source: ProposalSource | None) -> bool:
            """글 단위로 거를 때 이 제안이 그 글의 것인지. 사람이 CLI 로 만든 것(source 없음)은 글 필터에 걸리지 않는다."""
            return draft_id is None or (source is not None and source.draft_id == draft_id)

        headwords = [
            each for each in await self._repo.list_headwords([GlossaryStatus.PROPOSED]) if mine(each.proposed_by)
        ]
        senses = [
            each for each in await self._repo.list_senses(statuses=[GlossaryStatus.PROPOSED]) if mine(each.proposed_by)
        ]
        aliases = [
            {"headword_key": each.headword_key, "alias": proposal.alias, "source": proposal.source.model_dump()}
            for each in await self._repo.list_headwords()
            for proposal in each.proposed_aliases
            if mine(proposal.source)
        ]
        pending_items = [
            {
                "post_id": each.post_id,
                "draft_id": each.draft_id,
                "pending": [term.model_dump() for term in each.pending],
            }
            for each in await self._repo.list_post_glossaries()
            if each.pending and (draft_id is None or each.draft_id == draft_id)
        ]
        # 아직 발행되지 않아 post_glossary 가 없는 글의 보류는 글 문서(PostDraft.glossary)에만 있다. 거기서 보충한다
        if (
            draft_id is not None
            and self._drafts is not None
            and not any(each["draft_id"] == draft_id for each in pending_items)
        ):
            draft = await self._drafts.get(draft_id)
            if draft is not None and draft.glossary is not None and draft.glossary.pending:
                pending_items.append(
                    {
                        "post_id": None,
                        "draft_id": draft_id,
                        "pending": [term.model_dump() for term in draft.glossary.pending],
                    }
                )
        return {
            "headwords": [each.model_dump(mode="json") for each in headwords],
            "senses": [each.model_dump(mode="json") for each in senses],
            "aliases": aliases,
            "pending_items": pending_items,
        }

    async def show(self, post_or_draft_id: str) -> dict[str, Any]:
        """그 글의 항목(표시 여부 포함)·보류·버린 것. 게시글 id → 글 id → 발행 전 글 문서 순으로 찾고, 어디에도 없으면 NotFoundError."""
        post_glossary = await self._repo.get_post_glossary(post_or_draft_id)
        if post_glossary is None:
            post_glossary = await self._repo.get_post_glossary_by_draft(post_or_draft_id)
        record: GlossaryRecord | PostGlossary | None = post_glossary
        if record is None and self._drafts is not None:
            draft = await self._drafts.get(post_or_draft_id)
            record = draft.glossary if draft is not None else None
        if record is None:
            raise NotFoundError(f"{post_or_draft_id} has no glossary")
        senses = {
            sense.sense_key: sense
            for sense in await self._repo.list_senses_by_keys(item.sense_key for item in record.items)
        }
        headwords = {
            each.headword_key: each
            for each in await self._repo.list_headwords_by_keys(sense.headword_key for sense in senses.values())
        }
        items = []
        for item in record.items:
            sense = senses.get(item.sense_key)
            headword = headwords.get(sense.headword_key) if sense is not None else None
            items.append(
                item.model_dump()
                | {
                    "headword_key": sense.headword_key if sense else None,
                    "term_ko": (sense.term_ko if sense and sense.term_ko else headword.term_ko) if headword else None,
                    "sense_status": sense.status if sense else None,
                    "headword_status": headword.status if headword else None,
                    "visible": bool(sense and headword and sense.is_visible and headword.is_visible),
                }
            )
        return {
            "id": post_or_draft_id,
            "items": items,
            "pending": [term.model_dump() for term in record.pending],
            "discarded": [term.model_dump() for term in record.discarded] if isinstance(record, GlossaryRecord) else [],
            "skipped": record.skipped if isinstance(record, GlossaryRecord) else None,
        }

    async def approve_headword(
        self, headword_key: str, *, approved_by: str, term_ko: str | None = None, term_en: str | None = None
    ) -> GlossaryHeadword:
        """표제어 승인. term_ko·term_en 을 고쳐 승인할 수 있다. 없으면 NotFoundError, MERGED 면 InvalidRequestError.

        고친 표기가 다른 표제어의 조회 키와 겹치면 저장이 DuplicateKeyError 로 실패한다(unique 인덱스).
        """
        headword = await self._headword(headword_key)
        if headword.status == GlossaryStatus.MERGED:
            raise InvalidRequestError(f"headword {headword_key} was merged into {headword.merged_into}")
        # 키는 식별자라 바꾸지 않는다(뜻이 표제어 키로, 발행 글이 뜻 키로 가리킨다). 표기를 고치면 조회 키만 따라간다
        if term_ko is not None:
            headword.term_ko = term_ko.strip()
        if term_en is not None:
            headword.term_en = term_en.strip() or None
        headword.lookup_keys = _lookup_keys(headword)
        headword.status = GlossaryStatus.APPROVED
        headword.approved_by, headword.approved_at = approved_by, self._now()
        headword.updated_at = self._now()
        await self._repo.save_headword(headword)
        return headword

    async def reject_headword(self, headword_key: str) -> GlossaryHeadword:
        """표제어 거절. 뜻은 건드리지 않는다: 표제어가 REJECTED 면 읽기 API 가 그 뜻을 보이지 않는다. 조회 키는 남아 재제안을 막는다."""
        headword = await self._headword(headword_key)
        headword.status = GlossaryStatus.REJECTED
        headword.updated_at = self._now()
        await self._repo.save_headword(headword)
        return headword

    async def approve_sense(
        self,
        sense_key: str,
        *,
        approved_by: str,
        definition: str | None = None,
        distinguisher: str | None = None,
        domains: list[str] | None = None,
        term_ko: str | None = None,
    ) -> GlossarySense:
        """정의·영역을 고쳐 승인한다. 표제어가 PROPOSED 면 함께 승인한다. 승인된 뜻이 둘 이상이면 모두 구분 문구가 있어야 한다."""
        sense = await self._sense(sense_key)
        if definition is not None:
            sense.definition = definition.strip()
        if distinguisher is not None:
            sense.distinguisher = distinguisher.strip()
        if term_ko is not None:
            sense.term_ko = term_ko.strip() or None
        if domains is not None:
            unknown = [slug for slug in domains if slug not in DOMAINS]
            if unknown:
                raise InvalidRequestError(f"unknown domains {unknown}; allowed: {', '.join(DOMAINS)}")
            sense.domains = list(dict.fromkeys(domains)) or [OTHER_DOMAIN]
        headword = await self._headword(sense.headword_key)
        # 둘째 뜻을 승인하려면 이 뜻과 이미 승인된 모든 뜻에 구분 문구가 있어야 한다(2.1절 불변식). 툴팁이 영역 라벨 옆에 보인다
        siblings = [
            each
            for each in await self._repo.list_senses(headword.headword_key, [GlossaryStatus.APPROVED])
            if each.sense_key != sense.sense_key
        ]
        if siblings and not sense.distinguisher:
            raise InvalidRequestError(
                f"sense {sense_key} needs a distinguisher: headword {headword.headword_key} already has an approved sense"
            )
        lacking = [each.sense_key for each in siblings if not each.distinguisher]
        if lacking:
            raise InvalidRequestError(
                f"approved senses {lacking} need a distinguisher before a second sense is approved"
            )
        # 표제어가 아직 PROPOSED 면 함께 승인한다. 뜻만 승인되고 표제어가 남으면 아무것도 보이지 않아 사람이 헷갈린다
        if headword.status == GlossaryStatus.PROPOSED:
            await self.approve_headword(headword.headword_key, approved_by=approved_by)
        elif headword.status != GlossaryStatus.APPROVED:
            raise InvalidRequestError(f"headword {headword.headword_key} is {headword.status}")
        sense.status = GlossaryStatus.APPROVED
        sense.approved_by, sense.approved_at = approved_by, self._now()
        sense.updated_at = self._now()
        await self._repo.save_sense(sense)
        return sense

    async def reject_sense(self, sense_key: str) -> GlossarySense:
        """뜻 거절(APPROVED 철회 포함). 글 항목은 남고 읽기 API 가 그 항목을 건너뛴다. 뜻 키는 지우지 않는다(옛 기록이 적고 있다)."""
        sense = await self._sense(sense_key)
        sense.status = GlossaryStatus.REJECTED
        sense.updated_at = self._now()
        await self._repo.save_sense(sense)
        return sense

    async def approve_all(self, post_or_draft_id: str, *, approved_by: str) -> dict[str, Any]:
        """그 글이 제안한 표제어·뜻·별칭을 한 번에 승인한다. 불변식에 걸린 것은 남기고 사유를 돌려준다."""
        post_glossary = await self._repo.get_post_glossary(post_or_draft_id)
        draft_id = post_glossary.draft_id if post_glossary is not None else post_or_draft_id
        approved: dict[str, list[str]] = {"headwords": [], "senses": [], "aliases": []}
        skipped: list[str] = []
        for headword in await self._repo.list_headwords([GlossaryStatus.PROPOSED]):
            if headword.proposed_by is not None and headword.proposed_by.draft_id == draft_id:
                await self.approve_headword(headword.headword_key, approved_by=approved_by)
                approved["headwords"].append(headword.headword_key)
        for sense in await self._repo.list_senses(statuses=[GlossaryStatus.PROPOSED]):
            if sense.proposed_by is None or sense.proposed_by.draft_id != draft_id:
                continue
            try:
                await self.approve_sense(sense.sense_key, approved_by=approved_by)
            except InvalidRequestError as exc:
                skipped.append(f"{sense.sense_key}: {exc.message}")
            else:
                approved["senses"].append(sense.sense_key)
        for headword in await self._repo.list_headwords():
            for proposal in list(headword.proposed_aliases):
                if proposal.source.draft_id != draft_id:
                    continue
                try:
                    await self.add_alias(headword.headword_key, proposal.alias)
                except InvalidRequestError as exc:
                    skipped.append(f"{headword.headword_key} alias {proposal.alias!r}: {exc.message}")
                else:
                    approved["aliases"].append(f"{headword.headword_key}:{proposal.alias}")
        return {"draft_id": draft_id, "approved": approved, "skipped": skipped}

    async def add_alias(self, headword_key: str, alias: str) -> GlossaryHeadword:
        """별칭 승인. proposed_aliases 에 있으면 거기서 옮긴다. 다른 표제어가 그 키를 가지면 거절한다."""
        headword = await self._headword(headword_key)
        alias = alias.strip()
        key = normalize_key(alias)
        if not key:
            raise InvalidRequestError("alias is empty")
        # 다른 표제어가 가진 키면 여기서 먼저 거절한다. unique 인덱스가 어차피 막지만 사유를 사람이 읽을 수 있게 한다
        owner = await self._repo.find_headword_by_lookup(key)
        if owner is not None and owner.headword_key != headword.headword_key:
            raise InvalidRequestError(f"alias {alias!r} already belongs to headword {owner.headword_key}")
        headword.proposed_aliases = [each for each in headword.proposed_aliases if each.alias != alias]
        # 표기(term_ko·term_en)와 같은 키면 aliases 에 넣지 않는다. lookup_keys 가 이미 품고 있어 중복 표기만 는다
        if alias not in headword.aliases and key not in headword.lookup_keys:
            headword.aliases.append(alias)
        headword.lookup_keys = _lookup_keys(headword)
        headword.updated_at = self._now()
        await self._repo.save_headword(headword)
        return headword

    async def remove_alias(self, headword_key: str, alias: str) -> GlossaryHeadword:
        """별칭 삭제(제안·승인 둘 다). lookup_keys 를 다시 계산해 그 표기를 다른 표제어가 가져갈 수 있게 한다. 없으면 NotFoundError."""
        headword = await self._headword(headword_key)
        headword.aliases = [each for each in headword.aliases if each != alias]
        headword.proposed_aliases = [each for each in headword.proposed_aliases if each.alias != alias]
        headword.lookup_keys = _lookup_keys(headword)
        headword.updated_at = self._now()
        await self._repo.save_headword(headword)
        return headword

    async def resolve_pending(self, post_id: str, *, surface: str, sense_key: str) -> PostGlossary:
        """보류 항목에 뜻을 붙인다 (HUMAN). 뜻은 그 표제어의 것이어야 한다."""
        post_glossary = await self._post_glossary(post_id)
        term = next((each for each in post_glossary.pending if each.surface == surface), None)
        if term is None:
            raise NotFoundError(f"post {post_id} has no pending term with surface {surface!r}")
        sense = await self._sense(sense_key)
        if term.headword_key is not None and sense.headword_key != term.headword_key:
            raise InvalidRequestError(
                f"sense {sense_key} belongs to headword {sense.headword_key}, not {term.headword_key}"
            )
        if any(item.sense_key == sense_key for item in post_glossary.items):
            raise InvalidRequestError(f"post {post_id} already points at sense {sense_key}")
        post_glossary.pending = [each for each in post_glossary.pending if each.surface != surface]
        post_glossary.items.append(
            PostGlossaryItem(sense_key=sense_key, surface=surface, section_id=term.section_id, resolution="HUMAN")
        )
        post_glossary.updated_at = self._now()
        await self._repo.save_post_glossary(post_glossary)
        await self._repo.add_post_to_senses([sense_key], post_id)
        return post_glossary

    async def drop(self, post_id: str, *, surface: str) -> PostGlossary:
        """항목·보류를 지운다."""
        post_glossary = await self._post_glossary(post_id)
        before = len(post_glossary.items) + len(post_glossary.pending)
        post_glossary.items = [each for each in post_glossary.items if each.surface != surface]
        post_glossary.pending = [each for each in post_glossary.pending if each.surface != surface]
        if len(post_glossary.items) + len(post_glossary.pending) == before:
            raise NotFoundError(f"post {post_id} has no term with surface {surface!r}")
        post_glossary.updated_at = self._now()
        await self._repo.save_post_glossary(post_glossary)
        return post_glossary

    # ── 정리 ─────────────────────────────────────────────────────────

    async def dedupe_candidates(self) -> list[dict[str, Any]]:
        """비슷한 표제어 쌍. 코드의 n-gram 유사도만 쓴다. 사람이 읽고 merge 로 적용한다. DB 는 바꾸지 않는다."""
        headwords = await self._repo.list_headwords(_KNOWN_STATUSES)
        pairs = []
        for index, left in enumerate(headwords):
            for right in headwords[index + 1 :]:
                score = max(
                    similarity(a, b)
                    for a in left.lookup_keys or [left.headword_key]
                    for b in right.lookup_keys or [right.headword_key]
                )
                if score >= _DEDUPE_THRESHOLD:
                    pairs.append(
                        {
                            "keep": left.headword_key,
                            "merge": right.headword_key,
                            "score": round(score, 3),
                            "terms": [f"{left.term_ko} ({left.term_en})", f"{right.term_ko} ({right.term_en})"],
                        }
                    )
        return sorted(pairs, key=lambda pair: -pair["score"])

    async def merge_headwords(self, merge_key: str, *, into_key: str) -> GlossaryHeadword:
        """흡수되는 표제어의 키·별칭을 남는 표제어로 옮기고 뜻의 headword_key 를 바꾼 뒤 MERGED 로 둔다 (08 문서 2.4절)."""
        if merge_key == into_key:
            raise InvalidRequestError("cannot merge a headword into itself")
        merged = await self._headword(merge_key)
        keep = await self._headword(into_key)
        if keep.status == GlossaryStatus.MERGED:
            raise InvalidRequestError(f"headword {into_key} was itself merged into {keep.merged_into}")
        moved_aliases = [merged.term_ko, *([merged.term_en] if merged.term_en else []), *merged.aliases]
        # ① 흡수되는 쪽의 조회 키를 비운다 (unique 인덱스 때문에 두 쓰기다)
        merged.lookup_keys = []
        merged.status = GlossaryStatus.MERGED
        merged.merged_into = into_key
        merged.updated_at = self._now()
        await self._repo.save_headword(merged)
        # ② 남는 쪽에 더한다
        for alias in moved_aliases:
            if alias and alias not in (keep.term_ko, keep.term_en) and alias not in keep.aliases:
                keep.aliases.append(alias)
        keep.proposed_aliases += [each for each in merged.proposed_aliases if each.alias not in keep.aliases]
        keep.lookup_keys = _lookup_keys(keep)
        keep.updated_at = self._now()
        await self._repo.save_headword(keep)
        # ③ 뜻을 옮긴다. 뜻 키는 그대로다
        for sense in await self._repo.list_senses(merge_key):
            sense.headword_key = into_key
            sense.updated_at = self._now()
            await self._repo.save_sense(sense)
        return keep

    async def merge_senses(self, from_key: str, *, into_key: str) -> GlossarySense:
        """같은 뜻이 둘이면 글 항목을 옮기고 글 목록을 합친 뒤 from 을 REJECTED 로 둔다."""
        if from_key == into_key:
            raise InvalidRequestError("cannot merge a sense into itself")
        source = await self._sense(from_key)
        target = await self._sense(into_key)
        for post_glossary in await self._repo.list_post_glossaries_by_sense(from_key):
            # 글이 두 뜻을 다 가리키면 from 쪽 항목을 지우고(글 안 sense_key 유일), 아니면 from 을 into 로 바꾼다
            if any(item.sense_key == into_key for item in post_glossary.items):
                post_glossary.items = [item for item in post_glossary.items if item.sense_key != from_key]
            else:
                for item in post_glossary.items:
                    if item.sense_key == from_key:
                        item.sense_key = into_key
            post_glossary.updated_at = self._now()
            await self._repo.save_post_glossary(post_glossary)
        target.post_ids = list(dict.fromkeys([*target.post_ids, *source.post_ids]))
        target.updated_at = self._now()
        await self._repo.save_sense(target)
        # from 은 지우지 않고 REJECTED 로 둔다. 글 문서(PostDraft.glossary)와 영수증이 그 키를 적고 있다
        source.status = GlossaryStatus.REJECTED
        source.updated_at = self._now()
        await self._repo.save_sense(source)
        return target

    async def check(self) -> list[str]:
        """무결성: 고아 키, MERGED 미완, 뜻이 가리키는 표제어 없음, 둘 이상 승인된 뜻에 구분 문구 없음, 글 항목이 가리키는 뜻 없음."""
        problems: list[str] = []
        headwords = await self._repo.list_headwords()
        by_key = {each.headword_key: each for each in headwords}
        for headword in headwords:
            if headword.status == GlossaryStatus.MERGED and headword.lookup_keys:
                problems.append(f"headword {headword.headword_key} is MERGED but still owns lookup keys")
            if headword.status == GlossaryStatus.MERGED and headword.merged_into not in by_key:
                problems.append(f"headword {headword.headword_key} is merged into a missing headword")
            if headword.status != GlossaryStatus.MERGED and set(_lookup_keys(headword)) != set(headword.lookup_keys):
                problems.append(f"headword {headword.headword_key} lookup keys are stale")
        senses = await self._repo.list_senses()
        approved_by_headword: dict[str, list[GlossarySense]] = {}
        for sense in senses:
            owner = by_key.get(sense.headword_key)
            if owner is None or owner.status == GlossaryStatus.MERGED:
                problems.append(f"sense {sense.sense_key} points at missing or merged headword {sense.headword_key}")
            if sense.status == GlossaryStatus.APPROVED:
                approved_by_headword.setdefault(sense.headword_key, []).append(sense)
            if any(slug not in DOMAINS for slug in sense.domains):
                problems.append(f"sense {sense.sense_key} has an unknown domain")
        for headword_key, approved in approved_by_headword.items():
            if len(approved) > 1 and any(not each.distinguisher for each in approved):
                problems.append(f"headword {headword_key} has approved senses without a distinguisher")
        known_senses = {sense.sense_key for sense in senses}
        for post_glossary in await self._repo.list_post_glossaries():
            for item in post_glossary.items:
                if item.sense_key not in known_senses:
                    problems.append(f"post {post_glossary.post_id} points at missing sense {item.sense_key}")
        return problems

    async def _headword(self, headword_key: str) -> GlossaryHeadword:
        headword = await self._repo.get_headword(headword_key)
        if headword is None:
            raise NotFoundError(f"headword {headword_key} does not exist")
        return headword

    async def _sense(self, sense_key: str) -> GlossarySense:
        sense = await self._repo.get_sense(sense_key)
        if sense is None:
            raise NotFoundError(f"sense {sense_key} does not exist")
        return sense

    async def _post_glossary(self, post_id: str) -> PostGlossary:
        post_glossary = await self._repo.get_post_glossary(post_id)
        if post_glossary is None:
            raise NotFoundError(f"post {post_id} has no glossary")
        return post_glossary


def _lookup_keys(headword: GlossaryHeadword) -> list[str]:
    """표기·별칭의 정규화 키. 저장 전에 다시 계산한다 (08 문서 2.1절 불변식)."""
    texts = [headword.term_ko, headword.term_en or "", *headword.aliases]
    return [key for key in dict.fromkeys(normalize_key(text) for text in texts if text) if key]


def _published_version(draft: PostDraft) -> DraftVersion | None:
    """발행된 버전, 발행 전이면 발행 가능한 버전. 보충(backfill)의 추출 대상이다."""
    if draft.publication is not None:
        return next((v for v in draft.versions if v.version_number == draft.publication.final_version), None)
    return draft.publishable_version


def build_post_glossary(
    draft: PostDraft,
    record: GlossaryRecord,
    prompt_template_version: str | None,
    pipeline_version: str | None = None,
) -> PostGlossary | None:
    """발행 뒤 글 기록을 게시글 id 로 옮긴 문서. 항목도 보류도 없으면 만들지 않는다."""
    if draft.publication is None or not (record.items or record.pending):
        return None
    return PostGlossary(
        post_id=draft.publication.external_post_id,
        draft_id=draft.draft_id,
        round_id=draft.round_id,
        content_version=record.target_version,
        call_id=record.call_id,
        prompt_template_version=prompt_template_version,
        pipeline_version=pipeline_version,
        items=list(record.items),
        pending=list(record.pending),
        extracted_at=record.extracted_at,
        updated_at=record.extracted_at,
    )


def headword_key_for(term_ko: str, term_en: str | None) -> str:
    """CLI 가 사람이 준 표기로 키를 만들 때 쓴다."""
    return headword_key_of(term_ko, term_en)
