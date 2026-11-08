"""GlossaryService: 추출 단계(시나리오 ⑭~⑯), 등록 확인·뜻 선택, 승인·별칭·병합·무결성·보충 (설계 08 문서 2.2~2.5절).

가짜 LLM(ScriptedLLMClient)과 FakeAsyncDatabase 위의 실제 리포지토리로 돈다. 용어 라우트는 선택 라우트라 with_glossary 로 켠다.
"""

from datetime import UTC, datetime

import pytest

from app.core.exceptions import InvalidRequestError, LLMTransportError, NotFoundError
from app.models.enums import AgentSlot, GlossaryStatus, PostStatus
from app.models.glossary import GlossaryHeadword, GlossarySense, PostGlossary, PostGlossaryItem
from app.services.glossary_views import GlossaryViewService
from tests.factories import (
    GLOSSARY_BODY,
    draft_output,
    glossary_candidate,
    glossary_output,
    new_sense,
    review_output,
)
from tests.fakes import FakePostBE, PipelineHarness

pytestmark = pytest.mark.anyio

A = AgentSlot.A
T = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)


def _scripts(*candidates: dict) -> dict:
    return {
        "WRITE_DRAFT": [draft_output(GLOSSARY_BODY)],
        "REVIEW_FULL": review_output(),
        "GLOSSARY": [glossary_output(*candidates)],
    }


def _headword(
    key: str, term_ko: str, term_en: str | None, lookup_keys: list[str] | None = None, **fields
) -> GlossaryHeadword:
    lookup = [k for k in {key, term_ko.replace(" ", ""), (term_en or "").replace(" ", "").lower()} if k]
    return GlossaryHeadword(
        headword_key=key,
        term_ko=term_ko,
        term_en=term_en,
        lookup_keys=lookup if lookup_keys is None else lookup_keys,
        status=GlossaryStatus.APPROVED,
        created_at=T,
        updated_at=T,
        **fields,
    )


def _sense(
    key: str,
    headword_key: str,
    definition: str = "정의",
    domains=("backend",),
    status: GlossaryStatus = GlossaryStatus.APPROVED,
    **fields,
) -> GlossarySense:
    return GlossarySense(
        sense_key=key,
        headword_key=headword_key,
        domains=list(domains),
        definition=definition,
        status=status,
        created_at=T,
        updated_at=T,
        **fields,
    )


async def _seed_stateless(harness: PipelineHarness) -> None:
    await harness.glossary_repository.add_headword_if_missing(_headword("stateless", "무상태", "stateless"))
    await harness.glossary_repository.add_sense(_sense("s90001", "stateless", "서버가 상태를 들지 않는 설계"))


async def _drive_with_glossary(harness: PipelineHarness):
    await harness.seed_round(with_glossary=True)
    return await harness.drive(A)


# ---------- 추출 단계 ----------


async def test_extraction_proposes_a_new_headword_and_sense_without_blocking_publication():  # ⑭
    harness = PipelineHarness(_scripts(glossary_candidate(new_sense=new_sense())))

    draft = await _drive_with_glossary(harness)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert harness.llm.all_kinds[-4:] == ["POLISH_TONE", "FINAL_READ", "ASSESS_LEVEL", "GLOSSARY"]
    record = draft.glossary
    assert record is not None and record.skipped is None and record.call_id
    [item] = record.items
    assert (item.sense_key, item.surface, item.section_id, item.resolution) == (
        "s00001",
        "스테이트리스는",
        "intro",
        "NEW",
    )
    headword = await harness.glossary_repository.get_headword("stateless")
    assert headword is not None and headword.status == GlossaryStatus.PROPOSED
    assert sorted(headword.lookup_keys) == ["stateless", "무상태"]
    assert headword.proposed_by is not None and headword.proposed_by.draft_id == draft.draft_id
    sense = await harness.glossary_repository.get_sense("s00001")
    assert sense is not None and (sense.status, sense.domains, sense.headword_key) == (
        GlossaryStatus.PROPOSED,
        ["backend"],
        "stateless",
    )
    assert record.pending == [] and record.discarded == []


async def test_call_failure_skips_the_stage_and_the_draft_still_reaches_ready():  # ⑮
    harness = PipelineHarness(_scripts() | {"GLOSSARY": [LLMTransportError("boom"), LLMTransportError("boom")]})

    draft = await _drive_with_glossary(harness)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert draft.glossary is not None and draft.glossary.skipped is not None
    assert draft.glossary.skipped.startswith("LLMRetriesExhaustedError")
    assert draft.glossary.items == []


async def test_sentence_definition_goes_back_to_the_model_and_its_noun_phrase_rewrite_is_kept():  # D-88
    harness = PipelineHarness(
        _scripts()
        | {
            "GLOSSARY": [
                glossary_output(glossary_candidate(new_sense=new_sense(definition="상태를 저장하지 않는 설계이다."))),
                glossary_output(glossary_candidate(new_sense=new_sense(definition="상태를 저장하지 않는 설계."))),
            ]
        }
    )

    draft = await _drive_with_glossary(harness)

    assert draft.glossary is not None and draft.glossary.skipped is None
    assert harness.llm.all_kinds[-2:] == ["GLOSSARY", "GLOSSARY"]
    # 재시도 안내에 사유가 붙어 모델이 무엇을 고칠지 안다. 코드는 어미를 바꾸지 않고 끝 마침표만 뗀다
    assert "dictionary-style noun phrase" in harness.llm.calls[-1].messages[-1].content
    sense = await harness.glossary_repository.get_sense("s00001")
    assert sense is not None and sense.definition == "상태를 저장하지 않는 설계"


async def test_round_without_the_route_skips_without_calling():  # ⑯
    harness = PipelineHarness(_scripts())
    await harness.seed_round()  # 기본 라우트에는 GLOSSARY 가 없다 (이 기능 전 스냅샷과 같다)

    draft = await harness.drive(A)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert draft.glossary is not None and draft.glossary.skipped == "no GLOSSARY route"
    assert "GLOSSARY" not in harness.llm.all_kinds


async def test_resuming_after_the_stage_does_not_extract_again():  # ⑰
    harness = PipelineHarness(_scripts(glossary_candidate(new_sense=new_sense())))
    draft = await _drive_with_glossary(harness)

    again = await harness.drive(A)  # 승인 대기라 아무것도 하지 않는다

    assert again.glossary == draft.glossary
    assert harness.llm.all_kinds.count("GLOSSARY") == 1


# ---------- 등록 확인 ----------


async def test_exact_match_wins_over_the_ai_same_as_and_reuses_the_only_sense():
    harness = PipelineHarness(_scripts(glossary_candidate(term_en="Stateless", same_as="token", sense_key=None)))
    await _seed_stateless(harness)
    await harness.glossary_repository.add_headword_if_missing(_headword("token", "토큰", "token"))
    await harness.glossary_repository.add_sense(_sense("s90002", "token", "인증용 서명 문자열", ("auth",)))

    draft = await _drive_with_glossary(harness)

    [item] = draft.glossary.items
    assert (item.sense_key, item.resolution) == ("s90001", "EXACT")
    assert (await harness.glossary_repository.get_headword("stateless")).status == GlossaryStatus.APPROVED
    assert await harness.glossary_repository.list_headwords([GlossaryStatus.PROPOSED]) == []


async def test_same_as_inside_the_list_proposes_an_alias_for_the_new_spelling():
    harness = PipelineHarness(
        _scripts(
            glossary_candidate(
                term_ko="옵티미스틱 락",
                term_en=None,
                surface="낙관적 잠금을",
                section_id="body-1",
                same_as="optimisticlocking",
            )
        )
    )
    await harness.glossary_repository.add_headword_if_missing(
        _headword("optimisticlocking", "낙관적 잠금", "optimistic locking")
    )
    await harness.glossary_repository.add_sense(_sense("s90003", "optimisticlocking", "충돌을 낙관하는 잠금", ("db",)))

    draft = await _drive_with_glossary(harness)

    [item] = draft.glossary.items
    assert (item.sense_key, item.resolution) == ("s90003", "SAME_AS")
    headword = await harness.glossary_repository.get_headword("optimisticlocking")
    assert [proposal.alias for proposal in headword.proposed_aliases] == ["옵티미스틱 락"]
    assert "옵티미스틱락" not in headword.lookup_keys  # 승인 전에는 조회에 쓰지 않는다


async def test_same_as_outside_the_list_is_ignored_and_a_new_headword_is_proposed():
    harness = PipelineHarness(_scripts(glossary_candidate(same_as="nope", new_sense=new_sense())))

    draft = await _drive_with_glossary(harness)

    [item] = draft.glossary.items
    assert item.resolution == "NEW"
    assert (await harness.glossary_repository.get_headword("stateless")).status == GlossaryStatus.PROPOSED


async def test_candidate_rules_discard_code_only_surfaces_unknown_anchors_prerequisites_and_duplicates():
    harness = PipelineHarness(
        _scripts(
            glossary_candidate(surface="stateless", new_sense=new_sense()),  # 코드 안에만 있다
            glossary_candidate(term_ko="긴것", term_en="long", surface="x" * 41, new_sense=new_sense()),
            glossary_candidate(
                term_ko="토큰", term_en="token", surface="토큰은", section_id="nope", new_sense=new_sense()
            ),
            glossary_candidate(term_ko="토큰", term_en="token", surface="토큰은", new_sense=new_sense()),  # 전제 지식
            glossary_candidate(new_sense=new_sense()),
            glossary_candidate(new_sense=new_sense()),  # 표면형 중복
            glossary_candidate(surface="설계다", new_sense=new_sense()),  # 같은 뜻을 다른 표면형으로
            glossary_candidate(term_ko="", term_en=None, surface="서명", new_sense=new_sense()),
        )
    )
    await harness.seed_round(with_glossary=True)
    draft = await harness.draft(A)
    draft.topic.prerequisites = "JWT 토큰 구조를 안다"
    await harness.drafts.save(draft)

    draft = await harness.drive(A)

    assert [item.surface for item in draft.glossary.items] == ["스테이트리스는"]
    assert [(each.surface, each.reason) for each in draft.glossary.discarded] == [
        ("stateless", "본문에 없음"),
        ("x" * 41, "표면형 형식"),
        ("토큰은", "앵커 없음"),
        ("토큰은", "전제 지식"),
        ("스테이트리스는", "표면형 중복"),
        ("설계다", "뜻 중복"),
        ("서명", "표기 없음"),
    ]


# ---------- 뜻 선택 ----------


async def test_two_senses_and_an_unsure_ai_leave_the_term_pending_with_valid_candidates_only():
    harness = PipelineHarness(
        _scripts(
            glossary_candidate(
                term_ko="토큰",
                term_en="token",
                surface="토큰은",
                sense_key=None,
                sense_candidates=["s90002", "s90009", "s90001"],
            )
        )
    )
    await _seed_stateless(harness)
    await harness.glossary_repository.add_headword_if_missing(_headword("token", "토큰", "token"))
    await harness.glossary_repository.add_sense(_sense("s90002", "token", "인증용 서명 문자열", ("auth",)))
    await harness.glossary_repository.add_sense(_sense("s90009", "token", "언어 모델의 입력 단위", ("llm",)))

    draft = await _drive_with_glossary(harness)

    assert draft.glossary.items == []
    [pending] = draft.glossary.pending
    assert (pending.headword_key, pending.sense_candidates, pending.reason) == (
        "token",
        ["s90002", "s90009"],
        "뜻 미정",
    )


async def test_a_different_domain_alone_is_not_a_new_sense_but_a_different_definition_is():
    same_definition = "충돌을 낙관하는 잠금"
    harness = PipelineHarness(
        _scripts(
            glossary_candidate(
                term_ko="낙관적 잠금",
                term_en="optimistic locking",
                surface="낙관적 잠금을",
                section_id="body-1",
                new_sense=new_sense(("backend",), same_definition),
            ),
            glossary_candidate(
                term_ko="토큰",
                term_en="token",
                surface="토큰은",
                new_sense=new_sense(("llm",), "언어 모델의 입력 단위"),
            ),
        )
    )
    await harness.glossary_repository.add_headword_if_missing(
        _headword("optimisticlocking", "낙관적 잠금", "optimistic locking")
    )
    await harness.glossary_repository.add_sense(_sense("s90003", "optimisticlocking", same_definition, ("db",)))
    await harness.glossary_repository.add_headword_if_missing(_headword("token", "토큰", "token"))
    await harness.glossary_repository.add_sense(_sense("s90002", "token", "인증용 서명 문자열", ("auth",)))

    draft = await _drive_with_glossary(harness)

    locking, token = draft.glossary.items
    assert (locking.sense_key, locking.resolution) == ("s90003", "EXACT")  # 같은 정의는 영역이 달라도 같은 뜻
    assert (token.sense_key, token.resolution) == ("s00001", "NEW")  # 정의가 다르면 새 뜻(PROPOSED)
    assert sorted(each.sense_key for each in await harness.glossary_repository.list_senses("token")) == [
        "s00001",
        "s90002",
    ]


# ---------- 승인·별칭·보류 ----------


async def test_second_approved_sense_needs_distinguishers_on_both():
    harness = PipelineHarness()
    repo = harness.glossary_repository
    await repo.add_headword_if_missing(_headword("token", "토큰", "token"))
    await repo.add_sense(_sense("s90002", "token", "인증용 서명 문자열", ("auth",), status=GlossaryStatus.PROPOSED))
    await repo.add_sense(_sense("s90009", "token", "언어 모델의 입력 단위", ("llm",), status=GlossaryStatus.PROPOSED))

    await harness.glossary.approve_sense("s90002", approved_by="사람")
    with pytest.raises(InvalidRequestError, match="needs a distinguisher"):
        await harness.glossary.approve_sense("s90009", approved_by="사람")
    with pytest.raises(InvalidRequestError, match="need a distinguisher before"):
        await harness.glossary.approve_sense("s90009", approved_by="사람", distinguisher="인증 토큰이 아니라")
    await harness.glossary.approve_sense("s90002", approved_by="사람", distinguisher="언어 모델 토큰이 아니라")
    sense = await harness.glossary.approve_sense(
        "s90009", approved_by="사람", distinguisher="인증 토큰이 아니라", domains=["llm", "other"]
    )

    assert (sense.status, sense.domains, sense.approved_by) == (GlossaryStatus.APPROVED, ["llm", "other"], "사람")
    with pytest.raises(InvalidRequestError, match="unknown domains"):
        await harness.glossary.approve_sense("s90009", approved_by="사람", domains=["nope"])


async def test_sentence_definition_is_approved_only_after_it_is_rewritten_as_a_noun_phrase():  # D-88
    harness = PipelineHarness()
    repo = harness.glossary_repository
    await repo.add_headword_if_missing(_headword("aggregate", "애그리거트", "aggregate"))
    # 규칙 전에 제안된 정의. 독자에게 "~이다." 로 보이지 않게 승인에서 막는다
    await repo.add_sense(
        _sense("s90020", "aggregate", "루트로만 접근하는 객체들의 묶음이다.", ("db",), status=GlossaryStatus.PROPOSED)
    )

    with pytest.raises(InvalidRequestError, match="dictionary-style"):
        await harness.glossary.approve_sense("s90020", approved_by="사람")
    assert (await repo.get_sense("s90020")).status == GlossaryStatus.PROPOSED
    sense = await harness.glossary.approve_sense(
        "s90020", approved_by="사람", definition="루트로만 접근하는 객체들의 묶음."
    )

    assert (sense.status, sense.definition) == (GlossaryStatus.APPROVED, "루트로만 접근하는 객체들의 묶음")


async def test_approving_a_sense_approves_its_proposed_headword_and_approve_all_reports_skips():
    harness = PipelineHarness(
        _scripts(
            glossary_candidate(new_sense=new_sense()),
            glossary_candidate(
                term_ko="토큰",
                term_en="token",
                surface="토큰은",
                new_sense=new_sense(("auth",), "인증용 서명 문자열"),
            ),
        )
    )
    await harness.glossary_repository.add_headword_if_missing(_headword("token", "토큰", "token"))
    await harness.glossary_repository.add_sense(_sense("s90002", "token", "언어 모델의 입력 단위", ("llm",)))
    draft = await _drive_with_glossary(harness)

    result = await harness.glossary.approve_all(draft.draft_id, approved_by="사람")

    assert result["approved"]["headwords"] == ["stateless"]
    assert result["approved"]["senses"] == ["s00001"]  # 무상태의 뜻. 토큰의 새 뜻은 구분 문구가 없어 남는다
    assert len(result["skipped"]) == 1 and "s00002" in result["skipped"][0]
    assert (await harness.glossary_repository.get_headword("stateless")).status == GlossaryStatus.APPROVED
    assert (await harness.glossary_repository.get_sense("s00002")).status == GlossaryStatus.PROPOSED


async def test_alias_belongs_to_one_headword_only():
    harness = PipelineHarness()
    repo = harness.glossary_repository
    await repo.add_headword_if_missing(_headword("stateless", "무상태", "stateless"))
    await repo.add_headword_if_missing(_headword("token", "토큰", "token"))

    headword = await harness.glossary.add_alias("stateless", "스테이트리스")
    assert headword.aliases == ["스테이트리스"] and "스테이트리스" in headword.lookup_keys
    with pytest.raises(InvalidRequestError, match="already belongs"):
        await harness.glossary.add_alias("token", "Stateless")
    assert (await harness.glossary.remove_alias("stateless", "스테이트리스")).aliases == []


async def test_pending_lists_proposals_of_one_draft_and_resolve_drop_edit_the_post_glossary():
    harness = PipelineHarness(
        _scripts(
            glossary_candidate(new_sense=new_sense()),
            glossary_candidate(term_ko="토큰", term_en="token", surface="토큰은"),
        ),
        publisher=FakePostBE(["post-42"]),
    )
    await harness.glossary_repository.add_headword_if_missing(_headword("token", "토큰", "token"))
    await harness.glossary_repository.add_sense(_sense("s90002", "token", "인증용 서명 문자열", ("auth",)))
    await harness.glossary_repository.add_sense(_sense("s90009", "token", "언어 모델의 입력 단위", ("llm",)))
    draft = await _drive_with_glossary(harness)
    pending = await harness.glossary.pending(draft_id=draft.draft_id)
    assert [each["headword_key"] for each in pending["headwords"]] == ["stateless"]
    assert [each["sense_key"] for each in pending["senses"]] == ["s00001"]
    assert pending["pending_items"][0]["post_id"] is None  # 아직 발행 전이라 글 기록에서 읽는다
    draft.record_approval("사람", requires_approval=True)
    await harness.drafts.save(draft)
    draft = await harness.drive(A)
    assert draft.status == PostStatus.PUBLISHED

    post_glossary = await harness.glossary.resolve_pending("post-42", surface="토큰은", sense_key="s90002")
    assert [item.sense_key for item in post_glossary.items] == ["s00001", "s90002"] and post_glossary.pending == []
    assert "post-42" in (await harness.glossary_repository.get_sense("s90002")).post_ids
    with pytest.raises(NotFoundError):
        await harness.glossary.resolve_pending("post-42", surface="없음", sense_key="s90009")
    with pytest.raises(NotFoundError):
        await harness.glossary.drop("post-42", surface="없음")
    post_glossary = await harness.glossary.drop("post-42", surface="토큰은")
    assert [item.sense_key for item in post_glossary.items] == ["s00001"]
    shown = await harness.glossary.show("post-42")
    assert [(item["sense_key"], item["visible"]) for item in shown["items"]] == [("s00001", False)]


# ---------- 읽기 API 의 표시 규칙 ----------


async def test_read_api_shows_only_approved_items_with_the_other_senses_of_the_headword():
    harness = PipelineHarness(
        _scripts(
            glossary_candidate(new_sense=new_sense()),
            glossary_candidate(term_ko="토큰", term_en="token", surface="토큰은", sense_key="s90002"),
        ),
        publisher=FakePostBE(["post-42"]),
    )
    await harness.glossary_repository.add_headword_if_missing(_headword("token", "토큰", "token"))
    await harness.glossary_repository.add_sense(
        _sense("s90002", "token", "인증용 서명 문자열", ("auth",), distinguisher="언어 모델 토큰이 아니라")
    )
    await harness.glossary_repository.add_sense(
        _sense("s90009", "token", "언어 모델의 입력 단위", ("llm",), distinguisher="인증 토큰이 아니라")
    )
    draft = await _drive_with_glossary(harness)
    draft.record_approval("사람", requires_approval=True)
    await harness.drafts.save(draft)
    await harness.drive(A)
    views = GlossaryViewService(repository=harness.glossary_repository)

    before = await views.get_post_glossary("post-42")
    assert [item.sense_key for item in before.items] == ["s90002"]  # 무상태는 PROPOSED 라 아직 보이지 않는다
    [token] = before.items
    assert (token.term_ko, token.distinguisher, [d.label for d in token.domains]) == (
        "토큰",
        "언어 모델 토큰이 아니라",
        ["인증"],
    )
    assert [other.sense_key for other in token.other_senses] == ["s90009"]

    await harness.glossary.approve_all(draft.draft_id, approved_by="사람")
    after = await views.get_post_glossary("post-42")
    assert [(item.sense_key, item.other_senses) for item in after.items] == [
        ("s00001", []),
        ("s90002", token.other_senses),
    ]
    terms = await views.list_terms(q="state")
    assert [each.key for each in terms.items] == ["stateless"] and terms.total == 1
    with pytest.raises(NotFoundError):
        await views.get_post_glossary("nope")


# ---------- 정리 ----------


async def test_merge_headwords_moves_keys_and_senses_and_keeps_sense_keys():
    harness = PipelineHarness()
    repo = harness.glossary_repository
    await repo.add_headword_if_missing(_headword("optimisticlocking", "낙관적 잠금", "optimistic locking"))
    await repo.add_headword_if_missing(_headword("낙관적락", "낙관적 락", None))
    await repo.add_sense(_sense("s90003", "optimisticlocking", "충돌을 낙관하는 잠금", ("db",), distinguisher="DB 쪽"))
    await repo.add_sense(_sense("s90004", "낙관적락", "앱에서 거는 같은 뜻", ("backend",), distinguisher="앱 쪽"))
    pairs = await harness.glossary.dedupe_candidates()
    assert pairs == []  # 문자만 보면 다른 표기라 코드가 잡지 못한다. 사람이 merge 를 고른다

    keep = await harness.glossary.merge_headwords("낙관적락", into_key="optimisticlocking")

    assert "낙관적 락" in keep.aliases and "낙관적락" in keep.lookup_keys
    merged = await repo.get_headword("낙관적락")
    assert (merged.status, merged.merged_into, merged.lookup_keys) == (GlossaryStatus.MERGED, "optimisticlocking", [])
    assert {s.sense_key: s.headword_key for s in await repo.list_senses("optimisticlocking")} == {
        "s90003": "optimisticlocking",
        "s90004": "optimisticlocking",
    }
    assert (await repo.find_headword_by_lookup("낙관적락")).headword_key == "optimisticlocking"
    assert await harness.glossary.check() == []
    with pytest.raises(InvalidRequestError):
        await harness.glossary.merge_headwords("optimisticlocking", into_key="optimisticlocking")


async def test_merge_senses_repoints_post_items_and_rejects_the_source():
    harness = PipelineHarness()
    repo = harness.glossary_repository
    await repo.add_headword_if_missing(_headword("optimisticlocking", "낙관적 잠금", "optimistic locking"))
    await repo.add_sense(_sense("s90003", "optimisticlocking", "충돌을 낙관하는 잠금", ("db",), post_ids=["p1"]))
    await repo.add_sense(_sense("s90004", "optimisticlocking", "같은 뜻", ("backend",), post_ids=["p2"]))
    for post_id, keys in (("p1", ["s90003"]), ("p2", ["s90004"]), ("p3", ["s90003", "s90004"])):
        await repo.save_post_glossary(
            PostGlossary(
                post_id=post_id,
                draft_id=f"d-{post_id}",
                round_id="r",
                content_version=1,
                items=[PostGlossaryItem(sense_key=k, surface=k, section_id="intro", resolution="HUMAN") for k in keys],
            )
        )

    target = await harness.glossary.merge_senses("s90004", into_key="s90003")

    assert sorted(target.post_ids) == ["p1", "p2"]
    assert (await repo.get_sense("s90004")).status == GlossaryStatus.REJECTED
    assert [i.sense_key for i in (await repo.get_post_glossary("p2")).items] == ["s90003"]
    assert [i.sense_key for i in (await repo.get_post_glossary("p3")).items] == ["s90003"]
    assert await harness.glossary.check() == []


async def test_check_reports_stale_keys_missing_headwords_and_distinguishers():
    harness = PipelineHarness()
    repo = harness.glossary_repository
    await repo.add_headword_if_missing(_headword("token", "토큰", "token", lookup_keys=["token"]))
    await repo.add_sense(_sense("s90002", "token", "인증용 서명 문자열", ("auth",)))
    await repo.add_sense(_sense("s90009", "token", "언어 모델의 입력 단위", ("llm",)))
    await repo.add_sense(_sense("s90010", "ghost", "없는 표제어의 뜻", ("nope",)))
    await repo.add_sense(_sense("s90011", "token", "인증에 쓰는 문자열이다.", ("auth",), distinguisher="인증 쪽"))
    await repo.save_post_glossary(
        PostGlossary(
            post_id="p1",
            draft_id="d1",
            round_id="r",
            content_version=1,
            items=[PostGlossaryItem(sense_key="s99999", surface="x", section_id="intro", resolution="HUMAN")],
        )
    )

    problems = await harness.glossary.check()

    assert [problem.split(" ")[1] for problem in problems] == ["token", "s90010", "s90010", "s90011", "token", "p1"]
    assert (
        "sense s90011 definition is not dictionary style: 인증에 쓰는 문자열이다." in problems
    )  # 규칙 전 정의를 찾는 목록


# ---------- 보충 ----------


async def test_backfill_rewrites_the_post_glossary_of_a_published_draft():
    harness = PipelineHarness(_scripts(glossary_candidate(new_sense=new_sense())), publisher=FakePostBE(["post-42"]))
    draft = await _drive_with_glossary(harness)
    draft.record_approval("사람", requires_approval=True)
    await harness.drafts.save(draft)
    await harness.drive(A)
    assert [i.surface for i in (await harness.glossary_repository.get_post_glossary("post-42")).items] == [
        "스테이트리스는"
    ]
    harness.llm.scripts["GLOSSARY"] = [
        glossary_output(
            glossary_candidate(term_ko="토큰", term_en="token", surface="토큰은", new_sense=new_sense(("auth",)))
        )
    ]

    record = await harness.glossary.backfill(draft.draft_id)

    assert [i.surface for i in record.items] == ["토큰은"]
    post_glossary = await harness.glossary_repository.get_post_glossary("post-42")
    assert [i.surface for i in post_glossary.items] == ["토큰은"] and post_glossary.draft_id == draft.draft_id
    assert (await harness.drafts.get(draft.draft_id)).glossary == record
    assert await harness.glossary.list_missing_draft_ids() == []
