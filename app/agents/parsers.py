"""LLM 출력 계약 모델의 문맥 검사와 도메인 변환 (설계 05 문서, 04 문서 7절).

JSON → 계약 모델 파싱은 AgentCallService.call 이 한다. 여기서는 입력 문맥이 있어야 하는 검사를 check 함수로 만들어
call 의 check 인자로 넘기고(위반은 LLMOutputError 로 재시도), 통과한 출력을 도메인 값 객체로 바꾼다.
"""

import logging
from collections.abc import Callable, Iterable, Mapping
from urllib.parse import urlparse

from app.agents.contracts import (
    AssessLevelOutput,
    CitationRef,
    FactCheckOutput,
    FactClaimOutput,
    FactSpotCheckOutput,
    FinalReadOutput,
    ProposeTopicOutput,
    ReviewItemOutput,
    ReviewOutput,
    SpotCheckOutput,
)
from app.core.exceptions import LLMOutputError
from app.models.enums import ClaimStatus, IssueSeverity, ReviewLens, ReviewScope
from app.models.post_draft import FactClaim, ReviewItem
from app.models.topic import CategoryOption

logger = logging.getLogger(__name__)

_EVIDENCE_REQUIRED_LEVEL = 400  # 측정값과 운영 기록 없이는 고를 수 없다
# 주장 판정 → 지적 심각도의 기본값. 라운드 버전 문서의 fact_check.severity 가 같은 값으로 덮는다 (09 문서 2.4절)
_DEFAULT_CLAIM_SEVERITY: dict[ClaimStatus, IssueSeverity] = {
    ClaimStatus.REFUTED: IssueSeverity.CRITICAL,
    ClaimStatus.UNVERIFIABLE: IssueSeverity.MINOR,
}


def topic_proposal_check(
    categories: Iterable[CategoryOption], *, level_cap: int | None = None, has_evidence: bool = False
) -> Callable[[ProposeTopicOutput], None]:
    """제안마다 카테고리 쌍이 라운드 목록에 있고, 핵심 메커니즘·다루지 않을 것·수준·전제가 있어야 한다.

    근거(evidence)가 없는 라운드는 400 을 고를 수 없고, level_cap(페르소나의 max_level_without_evidence) 이 있으면 거기까지다.
    """
    pairs = {(category.main_category_id, category.sub_category_id) for category in categories}
    persona_cap = None if has_evidence else level_cap

    def check(output: ProposeTopicOutput) -> None:
        for index, proposal in enumerate(output.proposals, start=1):
            if (proposal.main_category_id, proposal.sub_category_id) not in pairs:
                raise LLMOutputError(
                    f"proposal {index} uses category pair ({proposal.main_category_id}, {proposal.sub_category_id}) "
                    "not in the round's categories"
                )
            if not proposal.core_mechanism.strip():
                raise LLMOutputError(f"proposal {index} has no core_mechanism")
            if not any(entry.strip() for entry in proposal.out_of_scope):
                raise LLMOutputError(f"proposal {index} has no out_of_scope entry")
            if proposal.target_level is None or not proposal.prerequisites.strip():
                raise LLMOutputError(f"proposal {index} needs target_level and prerequisites")
            if proposal.target_level >= _EVIDENCE_REQUIRED_LEVEL and not has_evidence:
                raise LLMOutputError(f"proposal {index} targets level 400 but the round has no evidence")
            if persona_cap is not None and proposal.target_level > persona_cap:
                raise LLMOutputError(
                    f"proposal {index} targets level {proposal.target_level}; without evidence the limit is {persona_cap}"
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
    """SPOT_CHECK: item_checks 가 my_previous_items 와 정확히 일치(누락·중복·입력 외 ID 없음)해야 한다.

    신규 항목은 CRITICAL 만 앵커·렌즈를 검사한다. 다른 심각도는 spot_check_items 가 버리므로 그 때문에 호출이 실패하면 안 된다.
    """
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
        _check_items(
            [item for item in output.new_critical_items if item.severity == IssueSeverity.CRITICAL],
            known_sections,
            known_lenses,
        )

    return check


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _check_quotes(quotes: Iterable[str], markdown: str) -> None:
    """모델이 인용한 구절이 본문에 그대로 있어야 한다. 공백 차이만 무시한다."""
    body = _normalized(markdown)
    for quote in quotes:
        if _normalized(quote) not in body:
            raise LLMOutputError(f"quote is not in the body: {quote[:80]}")


def final_read_check(markdown: str, section_ids: Iterable[str]) -> Callable[[FinalReadOutput], None]:
    """전체 읽기: 지적마다 있는 앵커를 가리키고 본문을 그대로 인용해야 한다."""
    known_sections = set(section_ids)

    def check(output: FinalReadOutput) -> None:
        for finding in output.findings:
            if finding.section_id not in known_sections:
                raise LLMOutputError(f"finding points at unknown section {finding.section_id}")
        _check_quotes((finding.quote for finding in output.findings), markdown)

    return check


def level_assessment_check(markdown: str) -> Callable[[AssessLevelOutput], None]:
    """수준 평가: 근거 인용이 본문에 그대로 있어야 한다. 목표 수준을 모른 채 본문으로만 판정하게 하기 위해서다(D-54)."""

    def check(output: AssessLevelOutput) -> None:
        _check_quotes((evidence.quote for evidence in output.evidence), markdown)

    return check


def fact_output_check(section_ids: Iterable[str], lenses: Iterable[ReviewLens]) -> Callable[[FactCheckOutput], None]:
    """FULL 사실 확인 변환: 지적의 앵커·렌즈와 주장의 앵커가 본문에 있어야 한다."""
    known_sections, known_lenses = set(section_ids), set(lenses)

    def check(output: FactCheckOutput) -> None:
        _check_items(output.items, known_sections, known_lenses)
        _check_claims(output.claims, known_sections)

    return check


def fact_spot_check_output_check(
    section_ids: Iterable[str], expected_item_ids: Iterable[str], lenses: Iterable[ReviewLens]
) -> Callable[[FactSpotCheckOutput], None]:
    """SPOT_CHECK 사실 확인 변환: SPOT_CHECK 검사에 주장의 앵커 검사를 더한다."""
    known_sections = set(section_ids)
    base = spot_check_output_check(known_sections, expected_item_ids, lenses)

    def check(output: FactSpotCheckOutput) -> None:
        base(output)
        _check_claims(output.claims, known_sections)

    return check


def _check_claims(claims: Iterable[FactClaimOutput], section_ids: set[str]) -> None:
    for claim in claims:
        if claim.section_id not in section_ids:
            raise LLMOutputError(f"claim points at unknown section {claim.section_id}")


def _host(value: str) -> str:
    """URL·도메인의 호스트. 페이지 제목처럼 주소가 아니면 빈 문자열이다."""
    if " " in value:
        return ""
    try:
        host = urlparse(value if "://" in value else f"https://{value}").hostname or ""
    except ValueError:
        return ""
    return host.removeprefix("www.") if "." in host else ""


def _is_cited(url: str, citations: list[CitationRef]) -> bool:
    """검색 호출의 출처에 있는 URL 인지. Gemini 출처 uri 는 리다이렉트 주소이고 title 이 도메인이라 그 호스트로만 맞춰 본다.

    출처 url 의 호스트로 맞추면 같은 사이트의 지어낸 경로가 통과한다.
    """
    normalized = url.rstrip("/")
    if any(citation.url.rstrip("/") == normalized for citation in citations):
        return True
    host = _host(url)
    return bool(host) and any(host == _host(citation.title) for citation in citations)


def normalize_claims(claims: Iterable[FactClaimOutput], citations: list[CitationRef]) -> list[FactClaim]:
    """검색 호출의 출처에 없는 URL 은 지운다. 변환 모델이 URL 을 지어낼 수 있어서다.

    URL 이 없는 SUPPORTED·REFUTED 는 UNVERIFIABLE 로 내린다. 근거 없이 글을 깎거나 통과시키지 않는다.
    """
    normalized = []
    for claim in claims:
        url = claim.source_url if claim.source_url and _is_cited(claim.source_url, citations) else None
        status = claim.status if url or claim.status == ClaimStatus.UNVERIFIABLE else ClaimStatus.UNVERIFIABLE
        normalized.append(
            FactClaim(
                section_id=claim.section_id,
                claim_type=claim.claim_type,
                claim=claim.claim,
                status=status,
                source_url=url,
                note=claim.note,
            )
        )
    return normalized


def claim_items(
    claims: Iterable[FactClaim],
    *,
    review_id: str,
    lens: ReviewLens,
    scope: ReviewScope,
    start: int = 1,
    severity: Mapping[ClaimStatus, IssueSeverity] | None = None,
) -> list[ReviewItem]:
    """심각도는 코드가 정한다: 기본은 REFUTED → CRITICAL, UNVERIFIABLE → MINOR(오탐률을 재기 전까지 막지 않는다), SUPPORTED 는 지적이 아니다.

    severity 는 라운드 버전 문서의 표(fact_check.severity)다. SPOT_CHECK 는 신규 CRITICAL 만 지적으로 남기고 나머지는 리뷰의 claims 에만 남는다.
    """
    table = severity if severity is not None else _DEFAULT_CLAIM_SEVERITY
    items = []
    for claim in claims:
        level = table.get(claim.status)
        if level is None or (scope == ReviewScope.SPOT_CHECK and level != IssueSeverity.CRITICAL):
            continue
        severity_of_claim = level
        source = f" (출처: {claim.source_url})" if claim.source_url else ""
        items.append(
            ReviewItem(
                item_id=f"{review_id}:{start + len(items)}",
                source_review_id=review_id,
                section_id=claim.section_id,
                lens=lens,
                severity=severity_of_claim,
                comment=f"[{claim.status}] {claim.claim} — {claim.note}{source}",
                is_new_in_version=scope == ReviewScope.SPOT_CHECK,
                from_fact_check=True,
            )
        )
    return items


def review_items(
    outputs: Iterable[ReviewItemOutput], *, review_id: str, is_new_in_version: bool = False
) -> list[ReviewItem]:
    """출력 항목에 item_id("{review_id}:{n}")·source_review_id 를 붙인다."""
    return [
        ReviewItem(
            item_id=f"{review_id}:{number}",
            source_review_id=review_id,
            section_id=output.section_id,
            lens=output.lens,
            severity=output.severity,
            comment=output.comment,
            suggested_fix=output.suggested_fix,
            is_new_in_version=is_new_in_version,
        )
        for number, output in enumerate(outputs, start=1)
    ]


def spot_check_items(output: SpotCheckOutput, *, review_id: str) -> list[ReviewItem]:
    """신규 CRITICAL 만 남긴다. 다른 심각도는 버리고 로그를 남긴다. 호출은 실패하지 않는다 (04 문서 7절)."""
    criticals = []
    for item in output.new_critical_items:
        if item.severity == IssueSeverity.CRITICAL:
            criticals.append(item)
        else:
            logger.info(
                "spot check %s dropped %s item at %s: %s", review_id, item.severity, item.section_id, item.comment
            )
    return review_items(criticals, review_id=review_id, is_new_in_version=True)
