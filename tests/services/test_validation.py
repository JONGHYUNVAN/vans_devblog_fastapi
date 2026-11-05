"""ValidationGate 테스트. 설계 04 문서 3절 표의 검사마다 실패 1건과 통과 1건."""

from types import SimpleNamespace

import pytest

from app.models.enums import AgentSlot, ArbitrationOutcome, IssueSeverity, ResolutionStatus, RevisionCause
from app.models.post_draft import ItemResolution, PostDraft
from app.models.sections import find_anchors
from app.services.evaluation import evaluate
from app.services.validation import ValidationGate
from tests.factories import (
    MAX_ITERATIONS,
    accept,
    add_reviews,
    make_arbitration,
    make_draft,
    make_topic,
    reject,
    write_next_version,
)

B = AgentSlot.B
GATE = ValidationGate(min_chars=1, max_chars=100_000)


def body(*, code: str = "print(1)", language: str = "python", code_anchor: str = "code-1", extra: str = "") -> str:
    return (
        "<!-- sec:intro -->\n서론\n\n<!-- sec:body-1 -->\n본론\n\n"
        f"<!-- sec:{code_anchor} -->\n```{language}\n{code}\n```\n\n"
        f"<!-- sec:tradeoff -->\n트레이드오프\n\n<!-- sec:conclusion -->\n결론\n{extra}"
    )


def submit(draft: PostDraft, markdown: str, resolutions: list[ItemResolution] | None = None) -> None:
    draft.start_drafting(MAX_ITERATIONS)
    draft.submit_version(
        title="제목",
        summary="요약",
        content_markdown=markdown,
        section_ids=find_anchors(markdown),
        resolutions=resolutions or [],
        author_note="",
        call_id="call",
    )


def validating(markdown: str) -> PostDraft:
    draft = make_draft()
    submit(draft, markdown)
    return draft


def reviewed(*severities: IssueSeverity):
    """v1 FULL 리뷰에서 B 가 severities 를 낸 뒤 REVISING(REVIEW). 두 번째 값은 미결 차단 항목."""
    draft = make_draft()
    write_next_version(draft)
    add_reviews(draft, {B: severities})
    draft.apply_evaluation(evaluate(draft, MAX_ITERATIONS))
    return draft, draft.open_blocking_items(1)


def test_valid_body_passes():
    assert GATE.validate(validating(body())) == []


def test_missing_required_anchor_and_duplicate_anchor_fail():
    errors = GATE.validate(validating(body().replace("<!-- sec:tradeoff -->", "<!-- sec:intro -->")))

    assert "필수 앵커가 없다: tradeoff" in errors
    assert "앵커가 중복된다: intro" in errors


def test_body_without_code_block_fails():
    markdown = (
        "<!-- sec:intro -->\n서론\n<!-- sec:body-1 -->\n본론\n<!-- sec:tradeoff -->\nt\n<!-- sec:conclusion -->\nc"
    )

    assert GATE.validate(validating(markdown)) == ["코드 블록이 0개다. 1개 이상이어야 한다"]


def test_unclosed_fence_fails():
    markdown = body().replace("print(1)\n```", "print(1)")

    assert any("짝이 맞지 않는다" in error for error in GATE.validate(validating(markdown)))


def test_code_block_without_language_or_empty_fails():
    errors = GATE.validate(validating(body(language="", code="   ")))

    assert any("언어 태그가 없다" in error for error in errors)
    assert any("비어 있다" in error for error in errors)


def test_code_block_needs_code_anchor_directly_above():
    errors = GATE.validate(validating(body(code_anchor="body-2")))

    assert any("바로 위에 code-N 앵커가 없다" in error for error in errors)


def test_code_anchor_without_code_block_fails():
    errors = GATE.validate(validating(body(extra="\n<!-- sec:code-2 -->\n문단\n")))

    assert "앵커 code-2 바로 아래에 코드 블록이 없다" in errors


def test_python_and_json_syntax_are_checked_but_snippet_is_not():
    assert any("문법 오류" in error for error in GATE.validate(validating(body(code="def ("))))
    assert any("문법 오류" in error for error in GATE.validate(validating(body(code="{bad}", language="json"))))
    assert GATE.validate(validating(body(code="def (", language="python-snippet"))) == []


def test_length_bounds():
    short = ValidationGate(min_chars=1_000, max_chars=2_000).validate(validating(body()))
    long = ValidationGate(min_chars=1, max_chars=50).validate(validating(body()))

    assert short[0].endswith("1000자 이상이어야 한다")
    assert long[0].endswith("50자 이하여야 한다")


@pytest.mark.parametrize(
    "extra",
    ["\n<div>html</div>\n", "\n![그림](a.png)\n", "\n각주[^1]\n", "\n<!-- sec:code-2 -->\n```mermaid\ngraph TD\n```\n"],
)
def test_unconvertible_syntax_fails(extra):
    errors = GATE.validate(validating(body(extra=extra)))

    assert any(error.startswith("발행 형식으로 변환할 수 없다") for error in errors)


def test_review_revision_must_resolve_every_blocking_item_exactly_once():
    draft, items = reviewed(IssueSeverity.CRITICAL, IssueSeverity.MAJOR)
    submit(draft, body(), accept(items[0]) + accept(items[0]))

    errors = GATE.validate(draft)

    assert f"해소가 중복된다: {items[0].item_id}" in errors
    assert f"차단 항목의 해소가 없다: {items[1].item_id}" in errors


def test_review_revision_rejects_unknown_ids_and_deferred_blocking_items():
    draft, items = reviewed(IssueSeverity.CRITICAL)
    resolutions = [
        ItemResolution(item_id=items[0].item_id, status=ResolutionStatus.DEFERRED),
        ItemResolution(item_id="ghost", status=ResolutionStatus.ACCEPTED),
    ]
    submit(draft, body(), resolutions)

    errors = GATE.validate(draft)

    assert f"차단 항목은 미룰 수 없다: {items[0].item_id}" in errors
    assert "리뷰에 없는 항목이다: ghost" in errors


def test_minor_item_may_be_deferred_but_not_rejected():
    draft, items = reviewed(IssueSeverity.CRITICAL, IssueSeverity.MINOR)
    minor_id = next(
        item.item_id for review in draft.reviews for item in review.items if item.severity == IssueSeverity.MINOR
    )
    submit(draft, body(), accept(*items) + [ItemResolution(item_id=minor_id, status=ResolutionStatus.DEFERRED)])
    assert GATE.validate(draft) == []

    draft, items = reviewed(IssueSeverity.CRITICAL, IssueSeverity.MINOR)
    minor_id = next(
        item.item_id for review in draft.reviews for item in review.items if item.severity == IssueSeverity.MINOR
    )
    rejected_minor = ItemResolution(item_id=minor_id, status=ResolutionStatus.REJECTED_WITH_REASON, reason="근거")
    submit(draft, body(), accept(*items) + [rejected_minor])

    assert GATE.validate(draft) == [f"비차단 항목은 ACCEPTED 또는 DEFERRED 만 가능하다: {minor_id}"]


def test_upheld_reviewer_item_must_be_accepted():
    draft, items = reviewed(IssueSeverity.CRITICAL)
    write_next_version(draft, reject(*items))
    add_reviews(draft, resolved={items[0].item_id: False})
    draft.apply_evaluation(evaluate(draft, MAX_ITERATIONS))
    draft.add_arbitration(make_arbitration(draft, items[0], ArbitrationOutcome.UPHOLD_REVIEWER))
    draft.apply_evaluation(evaluate(draft, MAX_ITERATIONS))
    submit(draft, body(), reject(*items))

    errors = GATE.validate(draft)

    assert errors == [f"중재에서 리뷰어가 맞다고 판정된 항목은 수용해야 한다: {items[0].item_id}"]


def test_vanished_anchor_needs_section_id_after_pointing_at_existing_anchor():
    draft, items = reviewed(IssueSeverity.CRITICAL)  # 항목은 body-1 을 가리킨다
    without_body = body().replace("<!-- sec:body-1 -->", "<!-- sec:body-9 -->")
    submit(draft, without_body, accept(*items))
    assert any("section_id_after 가 없다" in error for error in GATE.validate(draft))

    draft, items = reviewed(IssueSeverity.CRITICAL)
    moved = [ItemResolution(item_id=items[0].item_id, status=ResolutionStatus.ACCEPTED, section_id_after="body-9")]
    submit(draft, without_body, moved)
    assert GATE.validate(draft) == ["필수 앵커가 없다: body-1"]

    draft, items = reviewed(IssueSeverity.CRITICAL)
    dangling = [ItemResolution(item_id=items[0].item_id, status=ResolutionStatus.ACCEPTED, section_id_after="nowhere")]
    submit(draft, without_body, dangling)
    assert any("section_id_after nowhere 가 본문에 없다" in error for error in GATE.validate(draft))


def test_human_revision_skips_resolution_and_anchor_checks():
    draft, _ = reviewed(IssueSeverity.CRITICAL)
    draft.revision_cause = RevisionCause.HUMAN
    submit(draft, body().replace("<!-- sec:body-1 -->", "<!-- sec:body-9 -->") + "\n<!-- sec:body-1 -->\n")

    assert GATE.validate(draft) == []


def test_external_link_format_only_warns(caplog):
    draft = validating(body(extra="\n[문서](ftp://x)\n"))

    assert GATE.validate(draft) == []
    assert "not http(s)" in caplog.text


# ---------- 수준별 구조 ----------


def test_level_100_post_does_not_need_a_tradeoff_section_but_others_do():
    without_tradeoff = body().replace("<!-- sec:tradeoff -->\n트레이드오프\n\n", "")
    entry = make_draft(topic=make_topic(target_level=100))
    submit(entry, without_tradeoff)
    assert GATE.validate(entry) == []

    for level in (200, 300, None):
        other = make_draft(topic=make_topic(target_level=level))
        submit(other, without_tradeoff)
        assert GATE.validate(other) == ["필수 앵커가 없다: tradeoff"]


# ---------- TypeScript 문법 (tree-sitter) ----------


def _fake_parser(monkeypatch, has_error: bool) -> list[str]:
    grammars: list[str] = []

    class Parser:
        def parse(self, source: bytes):
            return SimpleNamespace(root_node=SimpleNamespace(has_error=has_error))

    def get_parser(grammar: str) -> Parser:
        grammars.append(grammar)
        return Parser()

    monkeypatch.setattr("tree_sitter_language_pack.get_parser", get_parser)
    return grammars


@pytest.mark.parametrize(("language", "grammar"), [("typescript", "typescript"), ("ts", "typescript"), ("tsx", "tsx")])
def test_typescript_tree_with_error_fails_the_gate(monkeypatch, language, grammar):
    grammars = _fake_parser(monkeypatch, has_error=True)

    errors = GATE.validate(validating(body(language=language, code="const x = ;")))

    assert grammars == [grammar]
    assert len(errors) == 1 and "snippet" in errors[0]


def test_real_typescript_grammar_accepts_valid_code_and_flags_broken_code():
    # 가짜가 아니라 실제 문법으로 has_error 가 기대대로인지 본다. 코드 그래프 테스트와 같은 문법을 쓴다
    assert GATE.validate(validating(body(language="typescript", code="export const x: number = 1;"))) == []
    assert GATE.validate(validating(body(language="tsx", code="export const A = () => <div>a</div>;"))) == []

    errors = GATE.validate(validating(body(language="typescript", code="export const x: number = ;\nclass {")))

    assert len(errors) == 1 and "snippet" in errors[0]


def test_typescript_snippet_is_not_parsed(monkeypatch):
    grammars = _fake_parser(monkeypatch, has_error=True)

    assert GATE.validate(validating(body(language="typescript-snippet", code="foo(): void {}"))) == []
    assert grammars == []


def test_missing_grammar_skips_the_check_instead_of_failing_the_gate(monkeypatch):
    def unavailable(grammar: str):
        raise OSError("cannot download grammar")

    monkeypatch.setattr("tree_sitter_language_pack.get_parser", unavailable)

    assert GATE.validate(validating(body(language="typescript", code="const x = ;"))) == []
