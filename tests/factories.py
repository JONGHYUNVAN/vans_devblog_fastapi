"""테스트 데이터 생성 함수. 모델 테스트와 서비스 테스트가 함께 쓴다."""

import io
import json
import tarfile
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agents.expository_forms import stage_forms
from app.agents.personas import PERSONAS
from app.models.agent_call import AgentCall, ChatMessage, TokenUsage
from app.models.enums import (
    AgentSlot,
    ArbitrationOutcome,
    CallKind,
    IssueSeverity,
    PostReviewStage,
    PostStatus,
    ResolutionStatus,
    ReviewLens,
    ReviewScope,
    ReviewVerdict,
)
from app.models.model_binding import CallModel, ModelBinding
from app.models.pipeline import (
    BODY_FIELDS,
    EXPOSITORY_FAMILY,
    EXPOSITORY_FORM_FAMILY,
    Parameters,
    PipelineVersion,
    PipelineVersionBody,
    PromptSet,
    content_hash_of,
)
from app.models.post_draft import (
    Arbitration,
    DraftVersion,
    ItemCheck,
    ItemResolution,
    PeerReview,
    PostDraft,
    ReviewAssignment,
    ReviewItem,
    VerificationChecklist,
)
from app.models.publishing_round import PublishingRound
from app.models.routing import RoutingSnapshot
from app.models.topic import CategoryOption, TopicProposal, TopicRegistryEntry
from app.services.evaluation import derive_verdict
from app.services.pipeline_versions import read_expository_directory, split_route_documents

# 기본 바인딩의 주 렌즈 (설계 03 문서 4절 표)
LENSES = {
    AgentSlot.A: ReviewLens.LOGIC_READABILITY,
    AgentSlot.B: ReviewLens.FACT_STRUCTURE,
    AgentSlot.C: ReviewLens.CRITICAL_PRACTICAL,
}
MAX_ITERATIONS = 2
MAX_VALIDATION_RETRIES = 2
MAX_PUBLISH_ATTEMPTS = 3
SECTION_IDS = ["intro", "body-1", "code-1", "tradeoff", "conclusion"]


def make_topic(**overrides) -> TopicProposal:
    fields = {
        "title": "MongoDB 낙관적 잠금 실전",
        "audience": "백엔드 개발자",
        "keywords": ["mongodb", "concurrency", "locking"],
        "outline": ["문제", "해결", "트레이드오프"],
        "rationale": "운영에서 겪은 경쟁 조건",
        "core_mechanism": "revision 조건부 갱신으로 동시 저장을 막는다",
        "out_of_scope": ["다중 리전 복제"],
        "target_level": 300,
        "prerequisites": "MongoDB 기본 사용 경험",
        "main_category_id": "main-1",
        "sub_category_id": "sub-1",
    }
    return TopicProposal(**(fields | overrides))


def build_review_assignments(
    author: AgentSlot, primary_lenses: Mapping[AgentSlot, ReviewLens]
) -> list[ReviewAssignment]:
    """라우팅 전의 배정 모양: 작성자를 뺀 두 슬롯이 렌즈 셋을 나눠 맡는다. 저장된 옛 글을 흉내 낼 때 쓴다."""
    reviewers = [slot for slot in AgentSlot if slot != author]
    candidates = [slot for slot in reviewers if primary_lenses[slot] != ReviewLens.CRITICAL_PRACTICAL]
    if len(candidates) > 1:
        candidates = [slot for slot in candidates if primary_lenses[slot] == ReviewLens.FACT_STRUCTURE]
    return [
        ReviewAssignment(
            slot=slot,
            primary_lens=primary_lenses[slot],
            secondary_lens=primary_lenses[author] if slot == candidates[0] else None,
        )
        for slot in reviewers
    ]


def make_draft(author: AgentSlot = AgentSlot.A, **overrides) -> PostDraft:
    """기본은 옛 모양(두 슬롯 리뷰어)이다. 옛 글을 계속 읽을 수 있어야 하고, 지금 글과 같은 코드로 돈다."""
    fields = {
        "draft_id": f"round-1:{author}",
        "round_id": "round-1",
        "author_slot": author,
        "token_limit": 100_000,
        "topic": make_topic(),
        "reviewer_assignments": build_review_assignments(author, LENSES),
    }
    return PostDraft(**(fields | overrides))


def accept(*items: ReviewItem) -> list[ItemResolution]:
    return [ItemResolution(item_id=item.item_id, status=ResolutionStatus.ACCEPTED) for item in items]


def reject(*items: ReviewItem) -> list[ItemResolution]:
    return [
        ItemResolution(item_id=item.item_id, status=ResolutionStatus.REJECTED_WITH_REASON, reason="근거가 있다")
        for item in items
    ]


def submit(draft: PostDraft, resolutions: Iterable[ItemResolution] = ()) -> DraftVersion:
    number = len(draft.versions) + 1
    return draft.submit_version(
        title=f"제목 v{number}",
        summary="요약",
        content_markdown="<!-- sec:intro -->\n본문",
        section_ids=SECTION_IDS,
        resolutions=list(resolutions),
        author_note="",
        call_id=f"call-write-{number}",
    )


def write_next_version(draft: PostDraft, resolutions: Iterable[ItemResolution] = ()) -> DraftVersion:
    """REVISING·TOPIC_LOCKED 에서 다음 버전을 써서 게이트를 통과시킨다."""
    draft.start_drafting(MAX_ITERATIONS)
    version = submit(draft, resolutions)
    draft.pass_validation()
    return version


def make_review(
    draft: PostDraft,
    slot: AgentSlot,
    severities: Iterable[IssueSeverity] = (),
    checks: Mapping[str, bool] | None = None,
) -> PeerReview:
    """현재 버전·범위의 리뷰. severities 마다 지적 하나, checks 는 item_id → resolved. slot 은 옛 글의 리뷰어 슬롯이다."""
    version_number = draft.current_version.version_number
    scope = draft.current_review_scope
    review_id = f"{draft.draft_id}:v{version_number}:{slot}:{scope}"
    assignment = draft.assignment_of(LENSES[slot])
    items = [
        ReviewItem(
            item_id=f"{review_id}:{n}",
            source_review_id=review_id,
            reviewer_slot=slot,
            section_id="body-1",
            lens=assignment.primary_lens,
            severity=severity,
            comment="지적",
            is_new_in_version=scope == ReviewScope.SPOT_CHECK,
        )
        for n, severity in enumerate(severities, start=1)
    ]
    item_checks = [
        ItemCheck(item_id=item_id, resolved=resolved, note="" if resolved else "아직 고쳐지지 않았다")
        for item_id, resolved in (checks or {}).items()
    ]
    return PeerReview(
        review_id=review_id,
        target_version=version_number,
        reviewer_slot=slot,
        lens=assignment.primary_lens,
        secondary_lens=assignment.secondary_lens,
        scope=scope,
        reported_verdict=ReviewVerdict.APPROVE if scope == ReviewScope.FULL else None,
        derived_verdict=derive_verdict(scope, items, item_checks),
        items=items,
        item_checks=item_checks,
        checklist=VerificationChecklist(checked_claims=["버전 표기 확인"]),
        overall_feedback="",
        call_id=f"call-{review_id}",
    )


def add_reviews(
    draft: PostDraft,
    severities: Mapping[AgentSlot, Iterable[IssueSeverity]] | None = None,
    resolved: Mapping[str, bool] | None = None,
) -> None:
    """필수 리뷰어 전원의 리뷰를 넣는다. SPOT_CHECK 는 resolved 에 없는 이전 항목을 해소된 것으로 본다."""
    severities = severities or {}
    resolved = resolved or {}
    slot_of = {lens: slot for slot, lens in LENSES.items()}
    for lens in draft.required_reviewers():
        slot = slot_of[lens]
        checks = {item.item_id: resolved.get(item.item_id, True) for item in draft.previous_items_of(lens)}
        draft.add_review(make_review(draft, slot, severities.get(slot, ()), checks))


def finish_post_review(draft: PostDraft) -> None:
    """POST_REVIEW 를 호출 없이 끝내 READY_TO_PUBLISH 로 보낸다. 발행 단계만 보는 테스트가 쓴다."""
    draft.status = PostStatus.READY_TO_PUBLISH
    draft.post_review_stage = PostReviewStage.DONE


def make_arbitration(draft: PostDraft, item: ReviewItem, outcome: ArbitrationOutcome) -> Arbitration:
    arbiter = next(slot for slot in AgentSlot if slot not in (draft.author_slot, item.reviewer_slot))
    version_number = draft.current_version.version_number
    return Arbitration(
        arbitration_id=f"{item.item_id}:arb:v{version_number}",
        target_version=version_number,
        item_id=item.item_id,
        arbiter_slot=arbiter,
        outcome=outcome,
        reasoning="판단 근거",
        call_id=f"call-arb-{item.item_id}",
    )


# ---------- 라운드·영수증·레지스트리 (로드맵 2단계) ----------


# 기본 바인딩의 페르소나. 주 렌즈가 LENSES 와 같다 (설계 05 문서 1절)
PERSONA_IDS = {AgentSlot.A: "architect", AgentSlot.B: "systems_engineer", AgentSlot.C: "troubleshooter"}


def make_binding(**overrides) -> ModelBinding:
    fields = {
        "provider": "fake",
        "model_id": "fake-1",
        "persona_id": "architect",
        "temperature": 0.7,
        "max_output_tokens": 1000,
    }
    return ModelBinding(**(fields | overrides))


# 시나리오 테스트의 가짜 벤더. 작성 벤더를 피하는 선택이 돌도록 셋을 둔다. 모두 같은 ScriptedLLMClient 로 간다
FAKE_VENDORS = {AgentSlot.A: "fake-a", AgentSlot.B: "fake-b", AgentSlot.C: "fake-c"}


def fake_model(vendor: str = "fake-a") -> dict[str, Any]:
    return {"provider": vendor, "model_id": f"{vendor}-1", "max_output_tokens": 1000}


def glossary_route(vendor: str = "fake-a") -> dict[str, Any]:
    """용어 추출 라우트. 선택 라우트라 기본 문서에는 없고, 용어 시나리오만 넣는다 (설계 08 문서 D-64)."""
    return {"_id": "GLOSSARY", "candidates": [fake_model(vendor)]}


def route_documents(with_glossary: bool = False) -> list[dict[str, Any]]:
    """agent_routes 문서 전체. 슬롯마다 자기 벤더로 쓰고, 리뷰·중재는 세 벤더를 후보로 둔다."""
    everyone = [fake_model(vendor) for vendor in FAKE_VENDORS.values()]
    documents: list[dict[str, Any]] = [{"_id": "SLOTS", **{slot.value: PERSONA_IDS[slot] for slot in AgentSlot}}]
    for slot, persona_id in PERSONA_IDS.items():
        own = [fake_model(FAKE_VENDORS[slot])]
        documents += [
            {"_id": f"PROPOSE_TOPIC:{persona_id}", "candidates": own},
            {"_id": f"WRITE_DRAFT:{persona_id}", "candidates": own},
            {"_id": f"REVISE_DRAFT:{persona_id}", "candidates": own},
        ]
    # 검색 렌즈는 첫 후보만 쓴다(D-92). 작성 A 시나리오의 사실 확인 모델이 작성 벤더 밖(fake-c)이도록 fake-c 를 앞에 둔다
    search_first = [fake_model(vendor) for vendor in ("fake-c", "fake-a", "fake-b")]
    documents += [
        {"_id": f"REVIEW:{lens}", "candidates": search_first if lens == ReviewLens.FACT_STRUCTURE else everyone}
        for lens in ReviewLens
    ]
    documents += [
        {"_id": route_id, "candidates": everyone}
        for route_id in ("ARBITRATE", "FINAL_READ", "ASSESS_LEVEL", "POLISH_TONE", "FACT_TRANSFORM")
    ]
    if with_glossary:
        documents.append(glossary_route())
    return documents


def make_routing(with_glossary: bool = False) -> RoutingSnapshot:
    routes = {
        document["_id"]: [CallModel.model_validate(each) for each in document["candidates"]]
        for document in route_documents(with_glossary)
        if document["_id"] != "SLOTS"
    }
    return RoutingSnapshot(routes=routes, slot_personas=PERSONA_IDS)


def make_call_model(**overrides) -> CallModel:
    return CallModel(**({"provider": "fake", "model_id": "fake-1", "max_output_tokens": 1000} | overrides))


# ---------- 파이프라인 버전 (설계 09 문서, 이행 1a) ----------

# 시나리오 테스트의 스텁 프롬프트 문구. ScriptedLLMClient 가 시스템 프롬프트의 이 문구로 호출 종류를 가린다(fakes._KIND_MARKERS).
# 실제 템플릿 문구에 묶이지 않도록 스텁을 쓴다 (09 문서 D-78). propose_topic 만 $writer_bias 를 치환한다(R-08)
STUB_PHRASES = {
    "propose_topic": "주제를 세 개 제안한다 (탐색 도메인: $writer_bias)",
    "write_draft": "글 한 편을 한국어로 쓴다",
    "revise_draft": "수정본을 낸다",
    "review_full": "`content_markdown` 을 `lens` 로 리뷰한다",
    "review_spot_check": "해소됐는지 확인한다",
    "arbitrate": "리뷰 분쟁을 판정한다",
    "arbitrate_search": "분쟁의 근거를 찾는다",
    "fact_check": "사실 확인 담당이다",
    "fact_transform": "JSON 으로 옮기는 변환기다",
    "final_read": "마지막 독자다",
    "assess_level": "전문성 수준을 판정한다",
    "polish_tone": "교정자다",
    "polish_revise": "다듬기 제안을 검토해 직접 고친다",
    "glossary": "glossary extractor",
}


def stub_prompts() -> PromptSet:
    """가족 디렉터리가 없는 가짜 벤더(fake, fake-a/b/c)가 common 으로 내려오므로 common 에만 둔다."""
    return {
        "common": {
            name: f"[template:{name}] {phrase}. 입력(JSON)을 읽고 JSON 으로만 답한다."
            for name, phrase in STUB_PHRASES.items()
        }
    }


def make_pipeline_body(
    *, with_glossary: bool = False, prompts: PromptSet | None = None, **parameter_overrides: Any
) -> PipelineVersionBody:
    """가짜 벤더 라우트·기본 페르소나·스텁 프롬프트의 본문. parameter_overrides 는 Parameters 필드(중첩은 dict)다."""
    slots, level_criteria, routes = split_route_documents(route_documents(with_glossary))
    # 운영 기본값은 자동 발행이지만 픽스처는 승인 흐름을 전제로 쓴 테스트가 많아 승인을 켠 채로 둔다
    parameter_overrides["publish"] = {"requires_approval": True, **parameter_overrides.get("publish", {})}
    return PipelineVersionBody(
        slots=slots,
        personas={key: value.model_copy(deep=True) for key, value in PERSONAS.items()},
        routes=routes,
        level_criteria=level_criteria,
        prompts=prompts if prompts is not None else stub_prompts(),
        parameters=Parameters.model_validate(parameter_overrides),
    )


def make_pipeline_version(
    version_id: str = "v1",
    seq: int = 1,
    *,
    parent_version_id: str | None = None,
    body: PipelineVersionBody | None = None,
    **body_overrides: Any,
) -> PipelineVersion:
    body = body if body is not None else make_pipeline_body(**body_overrides)
    return PipelineVersion(
        **body.model_dump(include=BODY_FIELDS),
        version_id=version_id,
        seq=seq,
        parent_version_id=parent_version_id,
        content_hash=content_hash_of(body),
        created_at=datetime(2026, 11, 7, 0, 0, tzinfo=UTC),
        created_by="test",
        source="import",
    )


def make_category() -> CategoryOption:
    return CategoryOption(
        main_category_id="main-1", main_label="Backend", sub_category_id="sub-1", sub_label="Database"
    )


def make_round(round_id: str = "round-1", round_seq: int = 1, **overrides) -> PublishingRound:
    fields = {
        "round_id": round_id,
        "round_seq": round_seq,
        "slot_bindings": {slot: make_binding(persona_id=PERSONA_IDS[slot]) for slot in AgentSlot},
        "categories": [make_category()],
    }
    return PublishingRound(**(fields | overrides))


def make_call(
    call_id: str = "call-1",
    draft_id: str | None = "round-1:A",
    kind: CallKind = CallKind.WRITE_DRAFT,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    output_parsed_ok: bool = True,
    started_second: int = 0,
    **overrides: Any,
) -> AgentCall:
    started_at = datetime(2026, 11, 2, 0, 0, started_second, tzinfo=UTC)
    fields: dict[str, Any] = {
        "call_id": call_id,
        "round_id": "round-1",
        "draft_id": draft_id,
        "kind": kind,
        "slot": AgentSlot.A,
        "model_binding": CallModel(provider="fake", model_id="fake-1", max_output_tokens=1000),
        "prompt_template_version": "v1",
        "attempt_number": 1,
        "messages": [ChatMessage(role="user", content="써라")],
        "output_raw": "{}" if output_parsed_ok else None,
        "output_parsed_ok": output_parsed_ok,
        "usage": TokenUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
        "latency_ms": 10,
        "started_at": started_at,
        "finished_at": started_at,
    }
    return AgentCall(**(fields | overrides))


def make_registry_entry(
    entry_id: str = "round-1:A", title: str = "MongoDB 낙관적 잠금 실전", **overrides
) -> TopicRegistryEntry:
    fields = {
        "entry_id": entry_id,
        "title": title,
        "keywords": ["mongodb", "concurrency", "locking"],
        "draft_id": entry_id,
        "round_id": entry_id.split(":")[0],
    }
    return TopicRegistryEntry(**(fields | overrides))


# ---------- 파이프라인 시나리오용 LLM 응답 (로드맵 4단계) ----------

# 게이트를 통과하는 최소 본문. 길이 하한은 하네스가 1 로 둔다
VALID_BODY = (
    "<!-- sec:intro -->\n서론\n\n<!-- sec:body-1 -->\n본론\n\n<!-- sec:code-1 -->\n```python\nprint(1)\n```\n\n"
    "<!-- sec:tradeoff -->\n트레이드오프\n\n<!-- sec:conclusion -->\n결론\n"
)
CHECKLIST = {"checked_claims": ["버전 표기 확인"], "checked_code_blocks": [], "unverifiable": []}
FACT_CITATION = "https://docs.example.com/mongodb"


def fact_search_text() -> str:
    return "사실 확인 결과: 주장은 문서와 일치한다."


def fact_transform_output(
    *claims: dict[str, Any], items: Iterable[dict[str, Any]] = ()
) -> Callable[[dict[str, Any]], str]:
    """변환 호출. 입력의 scope 로 FULL·SPOT_CHECK 계약을 고른다. SPOT_CHECK 의 이전 항목은 해소된 것으로 본다."""

    def build(call_input: dict[str, Any]) -> str:
        if call_input["scope"] == "SPOT_CHECK":
            checks = [{"item_id": item_id, "resolved": True, "note": ""} for item_id in call_input["expected_item_ids"]]
            payload = {
                "item_checks": checks,
                "new_critical_items": list(items),
                "checklist": CHECKLIST,
                "claims": list(claims),
            }
        else:
            payload = {
                "verdict": "APPROVE",
                "items": list(items),
                "checklist": CHECKLIST,
                "overall_feedback": "총평",
                "claims": list(claims),
            }
        return json.dumps(payload, ensure_ascii=False)

    return build


def fact_claim(
    status: str = "SUPPORTED", source_url: str | None = FACT_CITATION, section_id: str = "body-1"
) -> dict[str, Any]:
    return {
        "section_id": section_id,
        "claim_type": "API",
        "claim": "findOneAndUpdate 는 원자적이다",
        "status": status,
        "source_url": source_url,
        "note": "근거",
    }


def final_read_output(*findings: dict[str, Any]) -> str:
    return json.dumps({"findings": list(findings), "overall": "총평"}, ensure_ascii=False)


def final_read_finding(kind: str = "OBSERVATION", quote: str = "본론", section_id: str = "body-1") -> dict[str, Any]:
    return {"kind": kind, "section_id": section_id, "quote": quote, "comment": "관찰"}


def assess_level_output(level: int = 200, quote: str = "본론") -> str:
    return json.dumps(
        {"level": level, "evidence": [{"criterion": "방법과 이유", "quote": quote}], "reasoning": "근거"},
        ensure_ascii=False,
    )


def polish_output(*patches: tuple[int, str]) -> str:
    return json.dumps({"patches": [{"index": index, "text": text} for index, text in patches]}, ensure_ascii=False)


# 용어 시나리오 본문. 코드 안의 stateless 는 용어가 아니다 (설계 08 문서 2.3절 surface 규칙)
GLOSSARY_BODY = (
    "<!-- sec:intro -->\n스테이트리스는 서버가 상태를 들지 않는 설계다. 토큰은 서명 문자열이다.\n\n"
    "<!-- sec:body-1 -->\n본론: 낙관적 잠금을 revision 으로 건다. `stateless` 는 코드 안 단어다.\n\n"
    "<!-- sec:code-1 -->\n```python\nprint(1)\n```\n\n<!-- sec:tradeoff -->\n트레이드오프\n\n<!-- sec:conclusion -->\n결론\n"
)


def new_sense(
    domains: Iterable[str] = ("backend",),
    definition: str = "요청 사이에 서버가 클라이언트 상태를 저장하지 않는 설계",
    distinguisher: str = "",
) -> dict[str, Any]:
    return {"domains": list(domains), "definition": definition, "distinguisher": distinguisher}


def glossary_candidate(
    term_ko: str = "무상태",
    term_en: str | None = "stateless",
    surface: str = "스테이트리스는",
    section_id: str = "intro",
    **fields: Any,
) -> dict[str, Any]:
    return {"term_ko": term_ko, "term_en": term_en, "surface": surface, "section_id": section_id, **fields}


def glossary_output(*candidates: dict[str, Any]) -> str:
    return json.dumps({"candidates": list(candidates)}, ensure_ascii=False)


def draft_output(content: str = VALID_BODY, title: str = "제목") -> str:
    return json.dumps({"title": title, "content_markdown": content, "summary": "요약"}, ensure_ascii=False)


def revision_output(
    resolutions: Iterable[dict[str, Any]] = (), content: str = VALID_BODY, title: str = "수정본"
) -> str:
    return json.dumps(
        {
            "title": title,
            "content_markdown": content,
            "summary": "요약",
            "resolutions": list(resolutions),
            "author_note": "반영했다",
        },
        ensure_ascii=False,
    )


def resolution(item_id: str, status: str = "ACCEPTED", **fields: Any) -> dict[str, Any]:
    if status == "REJECTED_WITH_REASON":
        fields.setdefault("reason", "근거가 있다")
    return {"item_id": item_id, "status": status, **fields}


def review_output(*severities: str, section_id: str = "body-1") -> Callable[[dict[str, Any]], str]:
    """FULL 리뷰. 지적의 렌즈는 입력의 주 렌즈로 맞춘다."""

    def build(call_input: dict[str, Any]) -> str:
        items = [
            {"section_id": section_id, "lens": call_input["lens"], "severity": severity, "comment": "지적"}
            for severity in severities
        ]
        verdict = "REQUEST_CHANGES" if any(s in ("CRITICAL", "MAJOR") for s in severities) else "APPROVE"
        return json.dumps(
            {"verdict": verdict, "items": items, "checklist": CHECKLIST, "overall_feedback": "총평"}, ensure_ascii=False
        )

    return build


def spot_check_output(
    resolved: bool | Mapping[str, bool] = True, new_criticals: int = 0, section_id: str = "body-1"
) -> Callable[[dict[str, Any]], str]:
    """SPOT_CHECK. item_checks 는 입력의 my_previous_items 전부에 대해 만든다."""

    def build(call_input: dict[str, Any]) -> str:
        checks = []
        for item in call_input["my_previous_items"]:
            is_resolved = resolved if isinstance(resolved, bool) else resolved.get(item["item_id"], True)
            checks.append(
                {
                    "item_id": item["item_id"],
                    "resolved": is_resolved,
                    "note": "" if is_resolved else "아직 고쳐지지 않았다",
                }
            )
        criticals = [
            {"section_id": section_id, "lens": call_input["lens"], "severity": "CRITICAL", "comment": "새 지적"}
            for _ in range(new_criticals)
        ]
        return json.dumps(
            {"item_checks": checks, "new_critical_items": criticals, "checklist": CHECKLIST}, ensure_ascii=False
        )

    return build


def arbitrate_output(outcome: str) -> str:
    return json.dumps({"outcome": outcome, "reasoning": "판단 근거"}, ensure_ascii=False)


def proposal(title: str, keywords: list[str]) -> dict[str, Any]:
    return make_topic(title=title, keywords=keywords, target_level=200).model_dump()


def proposals_output(*topics: tuple[str, list[str]]) -> str:
    return json.dumps({"proposals": [proposal(title, keywords) for title, keywords in topics]}, ensure_ascii=False)


CODE_GRAPH_TREES = Path(__file__).parent / "golden" / "code_graph"


def make_tarball(tree: str, commit_sha: str = "a" * 40) -> bytes:
    """tests/golden/code_graph/{tree} 를 codeload 와 같은 모양(최상위 {tree}-main, pax 전역 헤더 comment 에 SHA)의 tar.gz 로."""
    buffer = io.BytesIO()
    with tarfile.open(
        fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT, pax_headers={"comment": commit_sha}
    ) as archive:
        archive.add(CODE_GRAPH_TREES / tree, arcname=f"{tree}-main")
    return buffer.getvalue()


# 설명문 파이프라인(pipeline/expository) 단계 출력. 검사를 통과하는 최소 모양이다

EXPOSITORY_DIR = Path(__file__).resolve().parents[1] / "pipeline" / "expository"


def make_expository_body(**parameter_overrides: Any) -> PipelineVersionBody:
    """픽스처 본문에 저장소 pipeline/expository 의 프롬프트·라우트를 넣은 것. 버전에 넣는 입력 파일도 같이 검사하게 된다."""
    body = make_pipeline_body(**parameter_overrides)
    prompts, forms, routes = read_expository_directory(EXPOSITORY_DIR)
    body.prompts = {**body.prompts, EXPOSITORY_FAMILY: prompts, EXPOSITORY_FORM_FAMILY: forms}
    body.routes = {**body.routes, **routes}
    return body


def make_expository_forms() -> dict[str, dict[str, Any]]:
    """저장소 pipeline/expository/forms 의 글 양식(id → 양식). 넣는 입력 파일도 같이 검사하게 된다."""
    return stage_forms(make_expository_body().prompts)


def make_expository_topic() -> dict[str, Any]:
    return {
        "form": "diagnosis",
        "topic": "Why self-invocation bypasses Spring @Transactional",
        "core_argument": "Moving methods that need a transaction boundary into a separate bean prevents the problem.",
        "focus_points": ["Proxy creation", "Internal calls through this", "Thread-bound transaction resources"],
        "out_of_scope": ["Distributed transactions"],
        "keywords": ["spring", "transaction", "proxy"],
        "main_category_id": "main-1",
        "sub_category_id": "sub-1",
    }


def make_expository_region(
    role: str, heading: str, key_sentence: str, elaboration: str, example: str = "", takeaway: str = ""
) -> dict[str, str]:
    """뼈대 region 하나. 슬롯 이름은 모든 양식이 같고, 역할이 쓰지 않는 슬롯은 빈 문자열이다."""
    return {
        "role": role,
        "heading": heading,
        "key_sentence": key_sentence,
        "elaboration": elaboration,
        "example": example,
        "takeaway": takeaway,
    }


def make_expository_section(number: int) -> dict[str, str]:
    """문제 원인 양식의 본론(cause) region 하나."""
    return make_expository_region(
        "cause",
        f"Mechanism {number}",
        f"The container wraps bean {number} in a proxy that runs advice before it delegates.",
        "Therefore only calls that enter through the proxy get the advice.",
        f"When placeOrder calls saveFailureLog through this, due to rule {number}, the setting is never read.",
        f"Advice runs only on proxy entry {number}",
    )


def make_expository_outline(core_argument: str, section_count: int = 3) -> dict[str, Any]:
    """문제 원인(diagnosis) 양식의 뼈대. cause region 이 section_count 개다."""
    return {
        "form": "diagnosis",
        "core_message": core_argument,
        "regions": [
            make_expository_region(
                "problem",
                "A failure log that disappears",
                "The failure log row disappears when the order transaction rolls back.",
                "OrderService calls saveFailureLog on itself inside placeOrder.",
            ),
            make_expository_region(
                "concept",
                "Proxies",
                "A proxy is the object the container registers in place of the bean.",
                "It runs advice before it delegates to the target.",
            ),
            *[make_expository_section(number) for number in range(1, section_count + 1)],
            make_expository_region(
                "fix",
                "A separate bean",
                "Move saveFailureLog into a separate OrderFailureLogger bean.",
                "The call then enters through the proxy of that bean.",
                "The failure log row now commits in its own transaction.",
            ),
            make_expository_region(
                "exceptions",
                "Helpers that join the caller",
                "A helper that joins the outer transaction can stay a private method.",
                "It needs no boundary of its own.",
            ),
            make_expository_region(
                "synthesis",
                "Where the boundary lives",
                "A transaction boundary works only where calls cross a proxy.",
                "Check which calls stay inside the object.",
            ),
        ],
    }


def make_expository_concept_topic() -> dict[str, Any]:
    """개념·구조(concept) 양식의 주제. 사람이 준 1단계 결과의 모양이다(키워드·카테고리 없음)."""
    return {
        "form": "concept",
        "topic": "How a B+tree index is laid out and searched",
        "core_argument": "A B+tree keeps rows only in its leaves so that every lookup costs the same number of page reads.",
        "focus_points": ["Root, internal, and leaf pages", "Descending from the root", "Why rows live only in leaves"],
        "out_of_scope": ["Hash indexes"],
    }


def make_expository_concept_outline(core_argument: str) -> dict[str, Any]:
    """개념·구조 양식의 뼈대: 정의 → 구성 요소 → 작동 원리 → 배경 → 종합. 문제 상황과 해결 region 이 없다."""
    body = [("structure", "Pages of the tree"), ("mechanism", "A lookup"), ("rationale", "Rows only in leaves")]
    return {
        "form": "concept",
        "core_message": core_argument,
        "regions": [
            make_expository_region(
                "definition",
                "A B+tree index",
                "A B+tree index is a balanced tree of pages that maps key values to rows.",
                "It has root, internal, and leaf pages. A lookup descends from the root to one leaf.",
            ),
            *[
                make_expository_region(
                    role,
                    heading,
                    f"The {role} of the tree follows one rule.",
                    "Internal pages hold separator keys. Leaf pages hold the rows.",
                    "A table of four rows fits in one leaf page.",
                    f"One rule for the {role}",
                )
                for role, heading in body
            ],
            make_expository_region(
                "synthesis",
                "What the layout buys",
                "Keeping rows in leaves makes every lookup the same depth.",
                "Structure, lookup, and the reason for the layout describe one design.",
            ),
        ],
    }


def expository_section_ids(section_count: int = 3) -> list[str]:
    mechanisms = [f"mech-{number}" for number in range(1, section_count + 1)]
    return ["intro", "concept", *mechanisms, "application", "exceptions", "conclusion"]


CONCEPT_SECTION_IDS = ["intro", "structure", "mechanism", "rationale", "conclusion"]


def make_expository_markdown(
    sentence: str, section_count: int = 3, glossary: bool = False, ids: list[str] | None = None
) -> str:
    """섹션마다 제목 하나와 문단 하나. 뒤에서 셋째 섹션(문제 원인 양식의 application)에 code-1 코드 블록이 있다.

    sentence 의 {id} 는 섹션 id 로 바뀐다. ids 를 주면 그 섹션들로 만든다(다른 양식의 글).
    """
    parts: list[str] = []
    base = ids if ids is not None else expository_section_ids(section_count)
    for number, section_id in enumerate(base + (["glossary"] if glossary else []), start=1):
        parts.append(f"<!-- sec:{section_id} -->\n## {number}. {section_id}\n\n{sentence.format(id=section_id)}\n")
        if section_id == base[-3]:
            parts.append("<!-- sec:code-1 -->\n```java-snippet\n@Service\nclass OrderFailureLogger {}\n```\n")
    return "\n".join(parts)


def make_expository_english_article(section_count: int = 3, ids: list[str] | None = None) -> str:
    sentence = "The {id} part explains how the proxy decides which calls get the transaction. It names no options."
    return make_expository_markdown(sentence, section_count, ids=ids)


def make_expository_korean_article(section_count: int = 3, glossary: bool = True) -> str:
    sentence = (
        "{id} 절은 프록시가 어떤 호출에 트랜잭션을 붙이는지 설명합니다. 같은 객체 안의 호출은 프록시를 거치지 않습니다."
    )
    return make_expository_markdown(sentence, section_count, glossary)


def make_expository_summaries(section_count: int = 3) -> list[dict[str, Any]]:
    rows = [{"item": "Proxy", "explanation": "Runs advice first."}, {"item": "Target", "explanation": "Does the work."}]
    return [
        {"section_id": section_id, "title": f"Part {section_id}", "rows": rows}
        for section_id in expository_section_ids(section_count)[:-1]
    ]


def make_expository_terms() -> list[dict[str, Any]]:
    return [
        {
            "term_en": "proxy",
            "term_ko": "프록시",
            "keep_english": False,
            "definition": "an object that stands in for another",
        },
        {"term_en": "propagation", "term_ko": "전파 속성", "keep_english": False, "definition": ""},
    ]
