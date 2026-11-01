"""LLM 출력 계약 모델의 문맥 검사와 도메인 변환 (설계 05 문서, 04 문서 7절).

JSON → 계약 모델 파싱은 AgentCallService.call 이 한다. 여기서는 입력 문맥이 있어야 하는 검사를 check 함수로 만들어
call 의 check 인자로 넘기고(위반은 LLMOutputError 로 재시도), 통과한 출력을 도메인 값 객체로 바꾼다.
"""

import logging
from collections.abc import Callable, Iterable

from app.agents.contracts import ProposeTopicOutput, ReviewItemOutput, ReviewOutput, SpotCheckOutput
from app.core.exceptions import LLMOutputError
from app.models.enums import AgentSlot, IssueSeverity, ReviewLens
from app.models.post_draft import ReviewItem
from app.models.topic import CategoryOption

logger = logging.getLogger(__name__)


def topic_proposal_check(categories: Iterable[CategoryOption]) -> Callable[[ProposeTopicOutput], None]:
    """제안마다 (main_category_id, sub_category_id) 가 라운드 카테고리 목록에 있어야 한다."""
    pairs = {(category.main_category_id, category.sub_category_id) for category in categories}

    def check(output: ProposeTopicOutput) -> None:
        for index, proposal in enumerate(output.proposals, start=1):
            if (proposal.main_category_id, proposal.sub_category_id) not in pairs:
                raise LLMOutputError(
                    f"proposal {index} uses category pair ({proposal.main_category_id}, {proposal.sub_category_id}) "
                    "not in the round's categories"
                )

    return check


def _check_items(items: Iterable[ReviewItemOutput], section_ids: set[str], lenses: set[ReviewLens]) -> None:
    for item in items:
        if item.section_id not in section_ids:
            raise LLMOutputError(f"review item points at unknown section {item.section_id}")
        if item.lens not in lenses:
            raise LLMOutputError(f"review item uses lens {item.lens} not assigned to this reviewer")


def review_output_check(section_ids: Iterable[str], lenses: Iterable[ReviewLens]) -> Callable[[ReviewOutput], None]:
    """FULL 리뷰: 지적의 앵커가 본문에 있고 렌즈가 배정된 것이어야 한다. 빈 체크리스트는 스키마가 막는다."""
    known_sections, known_lenses = set(section_ids), set(lenses)

    def check(output: ReviewOutput) -> None:
        _check_items(output.items, known_sections, known_lenses)

    return check


def spot_check_output_check(
    section_ids: Iterable[str], expected_item_ids: Iterable[str], lenses: Iterable[ReviewLens]
) -> Callable[[SpotCheckOutput], None]:
    """SPOT_CHECK: item_checks 가 my_previous_items 와 정확히 일치(누락·중복·입력 외 ID 없음)해야 한다."""
    known_sections, known_lenses, expected = set(section_ids), set(lenses), set(expected_item_ids)

    def check(output: SpotCheckOutput) -> None:
        ids = [item_check.item_id for item_check in output.item_checks]
        if len(set(ids)) != len(ids):
            raise LLMOutputError("item_checks has duplicate item ids")
        missing, unknown = expected - set(ids), set(ids) - expected
        if missing or unknown:
            raise LLMOutputError(
                f"item_checks must cover exactly the previous items: missing {sorted(missing)}, unknown {sorted(unknown)}"
            )
        _check_items(output.new_critical_items, known_sections, known_lenses)

    return check


def review_items(
    outputs: Iterable[ReviewItemOutput], *, review_id: str, reviewer_slot: AgentSlot, is_new_in_version: bool = False
) -> list[ReviewItem]:
    """출력 항목에 item_id("{review_id}:{n}")·source_review_id·reviewer_slot 을 붙인다."""
    return [
        ReviewItem(
            item_id=f"{review_id}:{number}",
            source_review_id=review_id,
            reviewer_slot=reviewer_slot,
            section_id=output.section_id,
            lens=output.lens,
            severity=output.severity,
            comment=output.comment,
            suggested_fix=output.suggested_fix,
            is_new_in_version=is_new_in_version,
        )
        for number, output in enumerate(outputs, start=1)
    ]


def spot_check_items(output: SpotCheckOutput, *, review_id: str, reviewer_slot: AgentSlot) -> list[ReviewItem]:
    """신규 CRITICAL 만 남긴다. 다른 심각도는 버리고 로그를 남긴다. 호출은 실패하지 않는다 (04 문서 7절)."""
    criticals = []
    for item in output.new_critical_items:
        if item.severity == IssueSeverity.CRITICAL:
            criticals.append(item)
        else:
            logger.info(
                "spot check %s dropped %s item at %s: %s", review_id, item.severity, item.section_id, item.comment
            )
    return review_items(criticals, review_id=review_id, reviewer_slot=reviewer_slot, is_new_in_version=True)
