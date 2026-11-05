"""파서 테스트. 문맥 검사의 위반 케이스마다 LLMOutputError, SPOT_CHECK 의 비 CRITICAL 버림, 도메인 변환."""

import logging

import pytest
from pydantic import ValidationError

from app.agents.contracts import (
    CitationRef,
    DraftOutput,
    FactClaimOutput,
    FinalReadFindingOutput,
    FinalReadOutput,
    ProposeTopicOutput,
    ReviewItemOutput,
    ReviewOutput,
    SpotCheckOutput,
)
from app.agents.parsers import (
    claim_items,
    final_read_check,
    normalize_claims,
    review_items,
    review_output_check,
    spot_check_items,
    spot_check_output_check,
    topic_proposal_check,
)
from app.core.exceptions import LLMOutputError
from app.models.enums import IssueSeverity, ReviewLens, ReviewScope, ReviewVerdict
from app.models.post_draft import ItemCheck, VerificationChecklist
from tests.factories import make_category, make_topic

CHECKLIST = VerificationChecklist(checked_claims=["확인"])
SECTIONS = ["intro", "body-1", "conclusion"]
LENSES = [ReviewLens.FACT_STRUCTURE]


def test_review_item_accepts_section_id_written_as_the_anchor() -> None:
    assert _item("sec:body-3").section_id == "body-3"


def _item(section_id: str = "body-1", severity: IssueSeverity = IssueSeverity.CRITICAL, lens=ReviewLens.FACT_STRUCTURE):
    return ReviewItemOutput(section_id=section_id, lens=lens, severity=severity, comment="지적")


def test_topic_proposal_check_passes_known_pairs_and_rejects_unknown():
    output = ProposeTopicOutput(proposals=[make_topic()] * 3)
    topic_proposal_check([make_category()])(output)

    bad = make_topic().model_copy(update={"sub_category_id": "sub-9"})
    with pytest.raises(LLMOutputError, match="proposal 2"):
        topic_proposal_check([make_category()])(ProposeTopicOutput(proposals=[make_topic(), bad, make_topic()]))


def test_review_output_check_rejects_unknown_anchor_and_unassigned_lens():
    check = review_output_check(SECTIONS, LENSES)
    check(ReviewOutput(verdict=ReviewVerdict.APPROVE, items=[_item()], checklist=CHECKLIST, overall_feedback=""))

    with pytest.raises(LLMOutputError, match="unknown section"):
        check(
            ReviewOutput(verdict=ReviewVerdict.APPROVE, items=[_item("nope")], checklist=CHECKLIST, overall_feedback="")
        )
    with pytest.raises(LLMOutputError, match="lens"):
        check(
            ReviewOutput(
                verdict=ReviewVerdict.APPROVE,
                items=[_item(lens=ReviewLens.LOGIC_READABILITY)],
                checklist=CHECKLIST,
                overall_feedback="",
            )
        )


@pytest.mark.parametrize(
    ("ids", "reason"),
    [(["r:1"], "missing"), (["r:1", "r:2", "r:2"], "duplicate"), (["r:1", "r:3"], "unknown")],
)
def test_spot_check_output_check_requires_exact_item_checks(ids, reason):
    output = SpotCheckOutput(
        item_checks=[ItemCheck(item_id=item_id, resolved=True) for item_id in ids],
        new_critical_items=[],
        checklist=CHECKLIST,
    )

    with pytest.raises(LLMOutputError, match=reason):
        spot_check_output_check(SECTIONS, ["r:1", "r:2"], LENSES)(output)


def test_spot_check_output_check_validates_new_item_anchor_for_critical_only():
    output = SpotCheckOutput(item_checks=[], new_critical_items=[_item("gone")], checklist=CHECKLIST)

    with pytest.raises(LLMOutputError, match="unknown section"):
        spot_check_output_check(SECTIONS, [], LENSES)(output)

    # 어차피 버릴 MAJOR 의 앵커·렌즈 때문에 호출이 실패하면 안 된다 (04 문서 7절)
    dropped = SpotCheckOutput(
        item_checks=[],
        new_critical_items=[_item("gone", severity=IssueSeverity.MAJOR, lens=ReviewLens.LOGIC_READABILITY)],
        checklist=CHECKLIST,
    )
    spot_check_output_check(SECTIONS, [], LENSES)(dropped)


def test_review_items_assign_ids_in_order():
    items = review_items([_item(), _item("intro")], review_id="d:v1:B:FULL")

    assert [item.item_id for item in items] == ["d:v1:B:FULL:1", "d:v1:B:FULL:2"]
    assert items[0].source_review_id == "d:v1:B:FULL"
    assert not items[0].is_new_in_version


def test_spot_check_items_keep_only_critical_and_log_the_rest(caplog):
    output = SpotCheckOutput(
        item_checks=[],
        new_critical_items=[_item(severity=IssueSeverity.MAJOR), _item("intro")],
        checklist=CHECKLIST,
    )

    with caplog.at_level(logging.INFO, logger="app.agents.parsers"):
        items = spot_check_items(output, review_id="r")

    assert [(item.section_id, item.is_new_in_version) for item in items] == [("intro", True)]
    assert "dropped MAJOR" in caplog.text


def test_schema_rejects_empty_checklist_long_title_and_empty_summary():
    with pytest.raises(ValidationError):
        VerificationChecklist()
    with pytest.raises(ValidationError):
        DraftOutput(title="x" * 101, content_markdown="본문", summary="요약")
    with pytest.raises(ValidationError):
        DraftOutput(title="제목", content_markdown="본문", summary="")
    with pytest.raises(ValidationError):
        ItemCheck(item_id="r:1", resolved=False, note="")


# ---------- 주제 제안 검사 ----------


def _proposals(**fields) -> ProposeTopicOutput:
    return ProposeTopicOutput(proposals=[make_topic(**fields) for _ in range(3)])


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"core_mechanism": " "}, "core_mechanism"),
        ({"out_of_scope": []}, "out_of_scope"),
        ({"target_level": None}, "target_level"),
        ({"prerequisites": ""}, "prerequisites"),
    ],
)
def test_topic_check_needs_scope_and_level_fields(fields, message):
    with pytest.raises(LLMOutputError, match=message):
        topic_proposal_check([make_category()])(_proposals(**fields))


def test_level_400_needs_evidence():
    check = topic_proposal_check([make_category()])
    with pytest.raises(LLMOutputError, match="level 400"):
        check(_proposals(target_level=400))

    topic_proposal_check([make_category()], has_evidence=True)(_proposals(target_level=400))


def test_troubleshooter_without_evidence_stops_at_200():
    check = topic_proposal_check([make_category()], persona_id="troubleshooter")
    with pytest.raises(LLMOutputError, match="limit is 200"):
        check(_proposals(target_level=300))
    check(_proposals(target_level=200))

    topic_proposal_check([make_category()], persona_id="troubleshooter", has_evidence=True)(
        _proposals(target_level=300)
    )
    topic_proposal_check([make_category()], persona_id="architect")(_proposals(target_level=300))


# ---------- 인용·주장 ----------


def test_final_read_check_needs_a_known_section_and_a_real_quote():
    check = final_read_check("<!-- sec:body-1 -->\n값이   없으면\n돌아간다", ["body-1"])

    def output(section_id="body-1", quote="값이 없으면 돌아간다"):
        finding = FinalReadFindingOutput(kind="OBSERVATION", section_id=section_id, quote=quote, comment="c")
        return FinalReadOutput(findings=[finding], overall="")

    check(output())  # 공백 차이는 무시한다
    with pytest.raises(LLMOutputError, match="unknown section"):
        check(output(section_id="body-9"))
    with pytest.raises(LLMOutputError, match="not in the body"):
        check(output(quote="없는 말"))


def _claim(status="SUPPORTED", source_url="https://docs.test/a") -> FactClaimOutput:
    return FactClaimOutput(section_id="body-1", claim_type="API", claim="c", status=status, source_url=source_url)


def test_normalize_claims_removes_urls_outside_the_citations_and_downgrades():
    citations = [CitationRef(url="https://docs.test/a"), CitationRef(url="https://r.test/1", title="www.mongodb.com")]

    claims = normalize_claims(
        [
            _claim(),
            _claim(source_url="https://mongodb.com/x"),  # 호스트(title)로 맞는다
            _claim("REFUTED", "https://made-up.test"),
            _claim("SUPPORTED", None),
            _claim("UNVERIFIABLE", None),
        ],
        citations,
    )

    assert [claim.status for claim in claims] == [
        "SUPPORTED",
        "SUPPORTED",
        "UNVERIFIABLE",
        "UNVERIFIABLE",
        "UNVERIFIABLE",
    ]
    assert claims[2].source_url is None


def test_page_title_citations_do_not_crash_or_match_by_host():
    citations = [CitationRef(url="https://docs.test/a", title="[Spring] 트랜잭션: 개요")]

    [claim] = normalize_claims([_claim(source_url="https://docs.test/made-up")], citations)
    assert (claim.status, claim.source_url) == ("UNVERIFIABLE", None)  # 같은 사이트의 지어낸 경로는 통과하지 않는다

    [claim] = normalize_claims([_claim(source_url="https://docs.test/a/")], citations)
    assert claim.status == "SUPPORTED"


def test_claim_items_derive_severity_in_code():
    claims = normalize_claims(
        [_claim("REFUTED"), _claim("UNVERIFIABLE", None), _claim()], [CitationRef(url="https://docs.test/a")]
    )

    full = claim_items(claims, review_id="r", lens=ReviewLens.FACT_STRUCTURE, scope=ReviewScope.FULL, start=3)
    assert [(item.item_id, item.severity, item.from_fact_check) for item in full] == [
        ("r:3", IssueSeverity.CRITICAL, True),
        ("r:4", IssueSeverity.MINOR, True),
    ]

    spot = claim_items(claims, review_id="r", lens=ReviewLens.FACT_STRUCTURE, scope=ReviewScope.SPOT_CHECK)
    assert [(item.severity, item.is_new_in_version) for item in spot] == [(IssueSeverity.CRITICAL, True)]
