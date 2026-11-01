"""파서 테스트. 문맥 검사의 위반 케이스마다 LLMOutputError, SPOT_CHECK 의 비 CRITICAL 버림, 도메인 변환."""

import logging

import pytest
from pydantic import ValidationError

from app.agents.contracts import DraftOutput, ProposeTopicOutput, ReviewItemOutput, ReviewOutput, SpotCheckOutput
from app.agents.parsers import (
    review_items,
    review_output_check,
    spot_check_items,
    spot_check_output_check,
    topic_proposal_check,
)
from app.core.exceptions import LLMOutputError
from app.models.enums import AgentSlot, IssueSeverity, ReviewLens, ReviewVerdict
from app.models.post_draft import ItemCheck, VerificationChecklist
from tests.factories import make_category, make_topic

CHECKLIST = VerificationChecklist(checked_claims=["확인"])
SECTIONS = ["intro", "body-1", "conclusion"]
LENSES = [ReviewLens.FACT_STRUCTURE]


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


def test_spot_check_output_check_validates_new_item_anchor():
    output = SpotCheckOutput(item_checks=[], new_critical_items=[_item("gone")], checklist=CHECKLIST)

    with pytest.raises(LLMOutputError, match="unknown section"):
        spot_check_output_check(SECTIONS, [], LENSES)(output)


def test_review_items_assign_ids_in_order():
    items = review_items([_item(), _item("intro")], review_id="d:v1:B:FULL", reviewer_slot=AgentSlot.B)

    assert [item.item_id for item in items] == ["d:v1:B:FULL:1", "d:v1:B:FULL:2"]
    assert items[0].source_review_id == "d:v1:B:FULL"
    assert items[1].reviewer_slot == AgentSlot.B
    assert not items[0].is_new_in_version


def test_spot_check_items_keep_only_critical_and_log_the_rest(caplog):
    output = SpotCheckOutput(
        item_checks=[],
        new_critical_items=[_item(severity=IssueSeverity.MAJOR), _item("intro")],
        checklist=CHECKLIST,
    )

    with caplog.at_level(logging.INFO, logger="app.agents.parsers"):
        items = spot_check_items(output, review_id="r", reviewer_slot=AgentSlot.B)

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
