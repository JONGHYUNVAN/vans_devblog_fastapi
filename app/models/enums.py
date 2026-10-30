"""파이프라인 도메인 열거형 (설계 02 문서 1절)."""

from enum import StrEnum


class AgentSlot(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class RoundStatus(StrEnum):
    TOPIC_SELECTION = "TOPIC_SELECTION"  # 생성 즉시. 주제 제안·중복 판별 진행 중 (배리어). 한 번에 한 라운드만
    IN_PROGRESS = "IN_PROGRESS"  # 주제 확정, 글 3편이 독립적으로 진행
    COMPLETED = "COMPLETED"  # 3편 모두 PUBLISHED 또는 REJECTED 로 종결
    ABORTED = "ABORTED"  # 관리자 중단 또는 주제 단계 실패


class PostStatus(StrEnum):
    TOPIC_LOCKED = "TOPIC_LOCKED"  # 글 문서가 생기는 첫 상태. 주제 제안 중에는 글 문서가 없다
    DRAFTING = "DRAFTING"  # 작성자 호출 진행 중 (v1 또는 수정본)
    VALIDATING = "VALIDATING"  # 결정론적 게이트 (LLM 호출 없음)
    IN_REVIEW = "IN_REVIEW"  # FULL 리뷰 진행
    IN_SPOT_CHECK = "IN_SPOT_CHECK"  # SPOT_CHECK 리뷰 진행
    IN_ARBITRATION = "IN_ARBITRATION"  # 제3 슬롯 중재 진행
    REVISING = "REVISING"  # 작성자 호출 전 대기. revision_cause 가 리뷰·게이트·사람 중 무엇인지를 말한다
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    PUBLISHED = "PUBLISHED"  # 종결
    ESCALATED = "ESCALATED"  # 사람 판단 대기
    REJECTED = "REJECTED"  # 종결(폐기). 사람 결정 또는 라운드 중단
    FAILED = "FAILED"  # 재시도 소진·글 예산 초과·내부 오류. 사람이 재개하거나 폐기한다


class ReviewScope(StrEnum):
    FULL = "FULL"
    SPOT_CHECK = "SPOT_CHECK"
    # 중재는 PeerReview 가 아니다. Arbitration 엔티티로 남긴다.


class ReviewVerdict(StrEnum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class IssueSeverity(StrEnum):
    CRITICAL = "CRITICAL"  # 기술적 오류, 코드 버그, 사실 오류. 발행 차단
    MAJOR = "MAJOR"  # 논리 비약, 구조 결함, 필수 배경 누락. 발행 차단
    MINOR = "MINOR"  # 표현, 문맥, 가독성 개선. 차단하지 않음
    NITPICK = "NITPICK"  # 취향. 차단하지 않음, 작성자 재량

    @property
    def is_blocking(self) -> bool:
        return self in (IssueSeverity.CRITICAL, IssueSeverity.MAJOR)


class ResolutionStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED_WITH_REASON = "REJECTED_WITH_REASON"
    DEFERRED = "DEFERRED"  # MINOR·NITPICK 에만 허용. "이번 버전에서 다루지 않음"


class ArbitrationOutcome(StrEnum):
    UPHOLD_REVIEWER = "UPHOLD_REVIEWER"  # 리뷰어가 맞다. 작성자가 수정해야 한다
    UPHOLD_AUTHOR = "UPHOLD_AUTHOR"  # 작성자 기각이 타당하다. 해당 항목 종결


class ReviewLens(StrEnum):
    LOGIC_READABILITY = "LOGIC_READABILITY"  # 논리 전개, 코드 정합성, 가독성
    FACT_STRUCTURE = "FACT_STRUCTURE"  # 기술 스펙 사실 확인, 버전, 누락된 배경·엣지 케이스
    CRITICAL_PRACTICAL = "CRITICAL_PRACTICAL"  # 반론, 실무 효용, 클리셰 제거


class RevisionCause(StrEnum):
    """REVISING 으로 들어온 이유. 나갈 때 iteration 을 올릴지 가른다."""

    REVIEW = "REVIEW"  # 리뷰 평가가 수정을 요구. 나갈 때 iteration += 1
    GATE = "GATE"  # 결정론적 게이트 실패. iteration 유지
    HUMAN = "HUMAN"  # 사람이 ESCALATED 에서 1회 재수정을 허가. iteration 유지


class EscalationReason(StrEnum):
    MAX_ITERATIONS_REACHED = "MAX_ITERATIONS_REACHED"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    PUBLISH_UNCERTAIN = "PUBLISH_UNCERTAIN"  # 발행 호출 결과를 모름. 중복 게시를 막으려고 자동 재시도하지 않는다


class EscalationDecisionKind(StrEnum):
    """ESCALATED 글에 대한 사람의 결정 (설계 03 문서 23~25행)."""

    PUBLISH_AS_IS = "PUBLISH_AS_IS"
    REVISE = "REVISE"
    REJECT = "REJECT"
    CONFIRM_PUBLISHED = "CONFIRM_PUBLISHED"  # PUBLISH_UNCERTAIN 에서만. 사람이 블로그에서 게시글을 찾았다


class FailureCode(StrEnum):
    """FAILED 로 들어온 이유. 재개 방식이 갈린다 (설계 03 문서 26행)."""

    LLM_RETRIES_EXHAUSTED = "LLM_RETRIES_EXHAUSTED"
    GATE_RETRIES_EXHAUSTED = "GATE_RETRIES_EXHAUSTED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"  # 호출 전 소프트 예산 검사 실패. 재개 때 상한을 올릴 수 있다
    INTERNAL_ERROR = "INTERNAL_ERROR"  # 코루틴 안의 예상하지 못한 예외. 같은 오류로 호출을 되풀이하지 않으려고 멈춘다


class RoundAbortReason(StrEnum):
    MANUAL = "MANUAL"
    TOPIC_ATTEMPTS_EXHAUSTED = "TOPIC_ATTEMPTS_EXHAUSTED"
    LLM_FAILED = "LLM_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class TopicRegistryStatus(StrEnum):
    RESERVED = "RESERVED"  # 세 주제가 모두 확정된 뒤에만 등록
    PUBLISHED = "PUBLISHED"
    RELEASED = "RELEASED"  # 폐기·라운드 중단·주제 단계 실패


class CallKind(StrEnum):
    PROPOSE_TOPIC = "PROPOSE_TOPIC"
    WRITE_DRAFT = "WRITE_DRAFT"
    REVISE_DRAFT = "REVISE_DRAFT"
    REVIEW = "REVIEW"
    ARBITRATE = "ARBITRATE"
