"""설명문 파이프라인의 글 양식: 양식 읽기, 본론 수 범위, 섹션 id, 단계 지시문에 들어가는 모양."""

import copy
import json

import pytest

from app.agents.expository_forms import (
    focus_range,
    form_choices,
    outline_view,
    parse_form,
    repeatable_roles,
    review_view,
    section_ids,
    stage_forms,
    write_view,
)
from tests.factories import (
    CONCEPT_SECTION_IDS,
    expository_section_ids,
    make_expository_body,
    make_expository_concept_outline,
    make_expository_forms,
    make_expository_outline,
)

FORMS = make_expository_forms()


def _broken(form_id: str, change) -> str:
    """저장소 양식 하나를 고친 본문."""
    form = copy.deepcopy(FORMS[form_id])
    change(form)
    return json.dumps(form)


def test_stage_forms_reads_the_repository_forms():
    assert sorted(FORMS) == ["concept", "diagnosis", "procedure", "taxonomy"]
    for form in FORMS.values():
        # 어느 양식이든 intro 로 시작하고, 본론이 아닌 맺음 섹션 하나로 끝난다
        assert form["regions"][0]["section"] == "intro"
        assert not form["regions"][-1]["focus"]


def test_stage_forms_raises_when_the_version_has_no_form_or_a_broken_one():
    body = make_expository_body()
    with pytest.raises(ValueError, match="글 양식 concept: "):
        stage_forms({"expository_form": {"concept": "{}"}})
    del body.prompts["expository_form"]
    with pytest.raises(ValueError, match="글 양식이 없다"):
        stage_forms(body.prompts)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda form: form.pop("thesis"), "thesis"),
        (lambda form: form.update(overview_header=["Aspect"]), "overview_header"),
        (lambda form: form["regions"][1].pop("max"), "max"),
        (lambda form: form["regions"][1].update(section="code"), "section"),
        (lambda form: form["regions"][1].update(sentences={"heading": [1, 2]}), "sentences"),
        (lambda form: form["regions"][2].update(role="structure"), "role"),
        (lambda form: form["regions"][0].update(section="opening"), "첫 region"),
        (lambda form: form["regions"][-1].update(focus=True), "마지막 region"),
        (lambda form: [region.update(focus=False) for region in form["regions"]], "본론 region"),
    ],
)
def test_parse_form_names_what_is_wrong(change, message):
    with pytest.raises(ValueError, match=message):
        parse_form(_broken("concept", change))


def test_focus_range_and_repeatable_roles_follow_the_region_counts():
    assert focus_range(FORMS["concept"]) == (3, 4)  # 구성 요소·작동 원리·배경 + 선택 region 하나
    assert focus_range(FORMS["diagnosis"]) == (3, None)
    assert focus_range(FORMS["taxonomy"]) == (3, None)  # 분류 기준 1 + 유형 2개 이상
    assert repeatable_roles(FORMS["concept"]) == []  # 뼈대 검토를 부르지 않는 양식
    assert repeatable_roles(FORMS["diagnosis"]) == ["cause"]
    assert repeatable_roles(FORMS["procedure"]) == ["step"]
    assert repeatable_roles(FORMS["taxonomy"]) == ["type"]


def test_section_ids_number_only_the_roles_that_may_repeat():
    diagnosis = make_expository_outline("A thesis.", section_count=4)
    assert section_ids(FORMS["diagnosis"], diagnosis["regions"]) == expository_section_ids(4)
    concept = make_expository_concept_outline("A thesis.")
    assert section_ids(FORMS["concept"], concept["regions"]) == CONCEPT_SECTION_IDS


def test_form_choices_carry_the_shapes_and_the_focus_count_for_the_topic_step():
    choices = {choice["id"]: choice for choice in form_choices(FORMS)}
    assert set(choices["concept"]) == {
        "id",
        "label",
        "use_when",
        "topic",
        "thesis",
        "focus_points",
        "focus_points_min",
        "focus_points_max",
    }
    assert (choices["concept"]["focus_points_min"], choices["concept"]["focus_points_max"]) == (3, 4)
    assert choices["procedure"]["focus_points_max"] is None


def test_views_give_each_step_only_what_it_needs():
    outline = outline_view("diagnosis", FORMS["diagnosis"])
    cause = outline["regions"][2]
    assert (cause["role"], cause["min"], cause["max"], cause["focus"]) == ("cause", 3, None, True)
    assert cause["sentences"] == {"elaboration": [1, 1], "example": [1, 1]}
    assert "write" not in cause and cause["guidance"] == FORMS["diagnosis"]["regions"][2]["outline"]
    assert outline["rules"] == FORMS["diagnosis"]["outline_rules"]

    review = review_view("diagnosis", FORMS["diagnosis"])
    assert review["repeatable_roles"] == ["cause"] and review["review_rules"][0].startswith("weak_relevance")

    regions = make_expository_outline("A thesis.")["regions"]
    write = write_view("diagnosis", FORMS["diagnosis"], regions)
    assert [section["section_id"] for section in write["sections"]] == expository_section_ids()
    assert write["sections"][2] == {
        "section_id": "mech-1",
        "role": "cause",
        "guidance": FORMS["diagnosis"]["regions"][2]["write"],
    }
    assert write["rules"] == FORMS["diagnosis"]["write_rules"]
