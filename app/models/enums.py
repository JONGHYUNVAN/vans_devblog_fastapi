"""파이프라인 도메인 열거형 (설계 02 문서 1절)."""

from enum import StrEnum
from typing import Literal

PostLevel = Literal[100, 200, 300, 400]  # 글의 전문성 수준 (models/level.py 기준표)


class AgentSlot(StrEnum):
    """라운드의 세 글 자리(A·B·C). 슬롯마다 페르소나와 작성 모델이 붙고(ModelBinding) 글 id 는 `{round_id}:{slot}` 이다."""

    A = "A"
    B = "B"
    C = "C"


class RoundStatus(StrEnum):
    """라운드 상태 (03 문서 3절 전이표). TOPIC_SELECTION 은 한 번에 하나만 있고, IN_PROGRESS 부터 글 3편이 독립적으로 돈다."""

    TOPIC_SELECTION = "TOPIC_SELECTION"  # 생성 즉시. 주제 제안·중복 판별 진행 중 (배리어). 한 번에 한 라운드만
    IN_PROGRESS = "IN_PROGRESS"  # 주제 확정, 글 3편이 독립적으로 진행
    COMPLETED = "COMPLETED"  # 3편 모두 PUBLISHED 또는 REJECTED 로 종결
    ABORTED = "ABORTED"  # 관리자 중단 또는 주제 단계 실패


class PostStatus(StrEnum):
    """글 상태 (03 문서 전이표). 전이는 PostDraft 메서드만 한다. ESCALATED·FAILED·종결에서는 코루틴이 돌지 않고 사람 결정만 상태를 바꾼다."""

    TOPIC_LOCKED = "TOPIC_LOCKED"  # 글 문서가 생기는 첫 상태. 주제 제안 중에는 글 문서가 없다
    DRAFTING = "DRAFTING"  # 작성자 호출 진행 중 (v1 또는 수정본)
    VALIDATING = "VALIDATING"  # 결정론적 게이트 (LLM 호출 없음)
    IN_REVIEW = "IN_REVIEW"  # FULL 리뷰 진행
    IN_SPOT_CHECK = "IN_SPOT_CHECK"  # SPOT_CHECK 리뷰 진행
    IN_ARBITRATION = "IN_ARBITRATION"  # 제3 슬롯 중재 진행
    REVISING = "REVISING"  # 작성자 호출 전 대기. revision_cause 가 리뷰·게이트·사람 중 무엇인지를 말한다
    POST_REVIEW = "POST_REVIEW"  # 리뷰 통과 뒤 발행 전 단계. post_review_stage 가 어디까지 했는지 말한다
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    PUBLISHED = "PUBLISHED"  # 종결
    ESCALATED = "ESCALATED"  # 사람 판단 대기
    REJECTED = "REJECTED"  # 종결(폐기). 사람 결정 또는 라운드 중단
    FAILED = "FAILED"  # 재시도 소진·글 예산 초과·내부 오류. 사람이 재개하거나 폐기한다


class ReviewScope(StrEnum):
    """리뷰 범위. 리뷰를 마친 이전 버전이 없으면 FULL, 있으면 SPOT_CHECK(본인 미결 항목과 신규 CRITICAL 만)다(D-21)."""

    FULL = "FULL"
    SPOT_CHECK = "SPOT_CHECK"
    # 중재는 PeerReview 가 아니다. Arbitration 엔티티로 남긴다.


class ReviewVerdict(StrEnum):
    """리뷰어가 적어 낸 판정. 기록만 하고 흐름은 severity 집계(derived_verdict)로 정한다(D-03)."""

    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class IssueSeverity(StrEnum):
    """지적의 심각도. CRITICAL·MAJOR 는 발행을 막고 수정 입력의 차단 집합을 이룬다(04 문서 5절)."""

    CRITICAL = "CRITICAL"  # 기술적 오류, 코드 버그, 사실 오류. 발행 차단
    MAJOR = "MAJOR"  # 논리 비약, 구조 결함, 필수 배경 누락. 발행 차단
    MINOR = "MINOR"  # 표현, 문맥, 가독성 개선. 차단하지 않음
    NITPICK = "NITPICK"  # 취향. 차단하지 않음, 작성자 재량

    @property
    def is_blocking(self) -> bool:
        """발행을 막는 등급인지. 차단 집합·게이트의 해소 대조·교착 판정이 이 기준을 쓴다."""
        return self in (IssueSeverity.CRITICAL, IssueSeverity.MAJOR)


class ResolutionStatus(StrEnum):
    """수정본이 지적 하나에 내린 처리. 차단 항목은 DEFERRED 가 안 되고 기각은 사유가 필수다(게이트가 대조한다, 04 문서 3절)."""

    ACCEPTED = "ACCEPTED"
    REJECTED_WITH_REASON = "REJECTED_WITH_REASON"
    DEFERRED = "DEFERRED"  # MINOR·NITPICK 에만 허용. "이번 버전에서 다루지 않음"
    OUT_OF_SCOPE = (
        "OUT_OF_SCOPE"  # 차단 항목에만 허용. 글의 범위 밖이라 본문을 넓히지 않고 범위 선언에 남긴다. 사유 필수
    )


class ArbitrationOutcome(StrEnum):
    """교착 항목의 중재 판정(D-10). UPHOLD_REVIEWER 는 다음 버전에서 수용 필수, UPHOLD_AUTHOR 는 항목 종결이다(D-48)."""

    UPHOLD_REVIEWER = "UPHOLD_REVIEWER"  # 리뷰어가 맞다. 작성자가 수정해야 한다
    UPHOLD_AUTHOR = "UPHOLD_AUTHOR"  # 작성자 기각이 타당하다. 해당 항목 종결


class ReviewLens(StrEnum):
    """세 리뷰 관점(D-19). 글마다 렌즈마다 리뷰어 하나가 붙고, 세 페르소나의 주 렌즈는 서로 달라야 한다(R-10)."""

    LOGIC_READABILITY = "LOGIC_READABILITY"  # 논리 전개, 코드 정합성, 가독성
    FACT_STRUCTURE = "FACT_STRUCTURE"  # 기술 스펙 사실 확인, 버전, 누락된 배경·엣지 케이스
    CRITICAL_PRACTICAL = "CRITICAL_PRACTICAL"  # 반론, 실무 효용, 클리셰 제거


class RevisionCause(StrEnum):
    """REVISING 으로 들어온 이유. 나갈 때 iteration 을 올릴지 가른다."""

    REVIEW = "REVIEW"  # 리뷰 평가가 수정을 요구. 나갈 때 iteration += 1
    GATE = "GATE"  # 결정론적 게이트 실패. iteration 유지
    HUMAN = "HUMAN"  # 사람이 ESCALATED 에서 1회 재수정을 허가. iteration 유지
    POLISH = "POLISH"  # origin_cause 전용. 발행 전 단계의 다듬기가 만든 버전. REVISING 을 거치지 않는다


class EscalationReason(StrEnum):
    """ESCALATED 로 보낸 이유. 사람이 고를 수 있는 결정(EscalationDecisionKind)을 가른다: CONFIRM_PUBLISHED 는 PUBLISH_UNCERTAIN 에만."""

    MAX_ITERATIONS_REACHED = "MAX_ITERATIONS_REACHED"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    PUBLISH_UNCERTAIN = "PUBLISH_UNCERTAIN"  # 발행 호출 결과를 모름. 중복 게시를 막으려고 자동 재시도하지 않는다
    FINAL_READ_FLAGGED = (
        "FINAL_READ_FLAGGED"  # 발행 전 전체 읽기가 차단 결함을 찾았다. 수정 루프로 돌리지 않고 사람이 정한다
    )


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
    OUTPUT_TRUNCATED = "OUTPUT_TRUNCATED"  # 벤더 출력 한도까지 올려도 응답이 잘렸다. 같은 한도로 되풀이하지 않는다


class RoundAbortReason(StrEnum):
    """라운드 중단 사유. 중단된 라운드의 글은 REJECTED 가 되고 주제는 RELEASED 다(03 문서 30행, 04 문서 1절 표)."""

    MANUAL = "MANUAL"
    TOPIC_ATTEMPTS_EXHAUSTED = "TOPIC_ATTEMPTS_EXHAUSTED"
    LLM_FAILED = "LLM_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class TopicRegistryStatus(StrEnum):
    """주제 레지스트리 항목의 상태(04 문서 2절). 회피 목록·중복 판별은 RESERVED·PUBLISHED 만 보고, RELEASED 는 다시 고를 수 있다."""

    RESERVED = "RESERVED"  # 세 주제가 모두 확정된 뒤에만 등록
    PUBLISHED = "PUBLISHED"
    RELEASED = "RELEASED"  # 폐기·라운드 중단·주제 단계 실패


class CallKind(StrEnum):
    """LLM 호출 종류. 영수증(agent_calls.kind)과 라우트 id 의 앞부분이 이 값이다. POLISH_FORMAT 은 v4 에서 부르지 않지만 옛 영수증 때문에 남긴다."""

    PROPOSE_TOPIC = "PROPOSE_TOPIC"
    WRITE_DRAFT = "WRITE_DRAFT"
    REVISE_DRAFT = "REVISE_DRAFT"
    REVIEW = "REVIEW"
    ARBITRATE = "ARBITRATE"
    FACT_CHECK = "FACT_CHECK"  # 웹 검색으로 사실을 확인한다. 응답은 글과 출처다
    FACT_TRANSFORM = "FACT_TRANSFORM"  # FACT_CHECK 응답을 계약 JSON 으로 옮긴다
    FINAL_READ = "FINAL_READ"
    ASSESS_LEVEL = "ASSESS_LEVEL"
    POLISH_FORMAT = "POLISH_FORMAT"
    POLISH_TONE = "POLISH_TONE"  # 작성 벤더가 아닌 모델의 문단 패치 제안. 그대로 적용하지 않는다 (D-91)
    POLISH_REVISE = "POLISH_REVISE"  # 작성 모델이 다듬기 제안을 검토해 직접 고친 패치. 이것만 적용한다 (D-91)
    GLOSSARY = "GLOSSARY"  # 발행본에서 전제 밖 용어를 뽑고 기존 표제어·뜻과 맞춘다 (설계 08 문서 2절)


class PostReviewStage(StrEnum):
    """POST_REVIEW 안에서 다음에 할 일. 끝낸 호출을 재개 때 되풀이하지 않게 호출과 함께 저장한다."""

    POLISH = "POLISH"
    FINAL_READ = "FINAL_READ"
    ASSESS_LEVEL = "ASSESS_LEVEL"
    GLOSSARY = "GLOSSARY"  # 용어 추출. 실패해도 건너뛴다 (08 문서 D-64)
    DONE = "DONE"


class GlossaryStatus(StrEnum):
    """표제어·뜻의 승인 상태 (설계 08 문서 2.1절). 표시는 APPROVED 만."""

    PROPOSED = "PROPOSED"  # 추출이 제안. 표시하지 않는다
    APPROVED = "APPROVED"  # 사람 승인. 표시한다
    REJECTED = "REJECTED"  # 사람 거절. 표시하지 않는다
    MERGED = "MERGED"  # 표제어만. merged_into 를 따라간다


class ProvenanceStageKind(StrEnum):
    """제작 기록의 단계 (설계 08 문서 3.3절)."""

    TOPIC = "TOPIC"  # 주제 제안
    WRITE = "WRITE"  # 작성 (v1)
    REVIEW = "REVIEW"  # 렌즈별 리뷰
    FACT_CHECK = "FACT_CHECK"  # 사실 확인 (검색 + 변환)
    ARBITRATION = "ARBITRATION"  # 중재 (검색 포함)
    REVISION = "REVISION"  # 수정 (v2…)
    POLISH = "POLISH"
    FINAL_READ = "FINAL_READ"
    ASSESS_LEVEL = "ASSESS_LEVEL"
    GLOSSARY = "GLOSSARY"


class FinalReadKind(StrEnum):
    """전체 읽기가 낸 지적의 종류. 격리 여부는 모델의 판정이 아니라 이 종류로 코드가 정한다."""

    BODY_CODE_CONTRADICTION = "BODY_CODE_CONTRADICTION"  # 본문과 코드가 어긋난다. 차단
    CORE_MECHANISM_MISSING = "CORE_MECHANISM_MISSING"  # 제목과 핵심 메커니즘을 본문·코드가 보여 주지 않는다. 차단
    OBSERVATION = "OBSERVATION"  # 그 밖의 관찰. 차단하지 않고 사람용 요약에 남긴다

    @property
    def is_blocking(self) -> bool:
        """OBSERVATION 만 차단하지 않는다. 라운드 버전의 final_read.blocking_kinds 가 있으면 record_final_read 는 그쪽을 쓴다."""
        return self != FinalReadKind.OBSERVATION


class ClaimType(StrEnum):
    """외부 문서로 참·거짓을 가릴 수 있는 주장의 종류. 설계 판단·의견은 추출하지 않는다."""

    API = "API"  # 프레임워크·라이브러리 API 동작
    CONFIG_OPTION = "CONFIG_OPTION"  # 설정 옵션 이름
    VERSION = "VERSION"  # 특정 버전에서 달라졌다는 서술
    DB_SYNTAX = "DB_SYNTAX"  # DB 문법
    PROTOCOL_OR_FIGURE = "PROTOCOL_OR_FIGURE"  # 프로토콜·전달 보장·벤치마크 수치


class ClaimStatus(StrEnum):
    """사실 확인 주장의 판정. 기본은 REFUTED → CRITICAL 지적, UNVERIFIABLE → MINOR 지적(막지 않는다), SUPPORTED 는 지적이 아니다(claim_items)."""

    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
    UNVERIFIABLE = "UNVERIFIABLE"
