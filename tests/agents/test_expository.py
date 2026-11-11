"""설명문 파이프라인: 버전 문서에서 단계 프롬프트·모델 꺼내기, 프롬프트 채우기, 출력 꺼내기."""

import json

import pytest

from app.agents.expository import (
    STAGE_KEYS,
    parse_output,
    parse_prompt,
    render_prompt,
    stage_models,
    stage_prompts,
)
from app.models.pipeline import EXPOSITORY_FAMILY
from tests.factories import make_expository_body


def test_stage_prompts_reads_every_stage_with_inputs_and_schema():
    prompts = stage_prompts(make_expository_body().prompts)
    assert tuple(prompts) == STAGE_KEYS
    for prompt in prompts.values():
        assert prompt["inputs"]
        assert prompt["expected_output_schema"]["type"] == "object"


def test_stage_prompts_raises_when_a_stage_is_missing():
    body = make_expository_body()
    del body.prompts[EXPOSITORY_FAMILY]["5_write"]
    with pytest.raises(ValueError, match="5_write"):
        stage_prompts(body.prompts)


def test_parse_prompt_raises_without_inputs_or_schema():
    with pytest.raises(ValueError):
        parse_prompt("not json")
    with pytest.raises(ValueError):
        parse_prompt('{"inputs": {}}')


def test_stage_models_picks_the_first_candidate_with_a_key():
    routes = make_expository_body().routes
    assert stage_models(routes, ["google", "anthropic"])["4_outline_fact_check"].provider == "google"
    # 키가 anthropic 하나뿐이면 사실 확인의 google 첫 후보를 건너뛴다
    models = stage_models(routes, ["anthropic"])
    assert tuple(models) == STAGE_KEYS
    assert models["4_outline_fact_check"].provider == "anthropic"


def test_stage_models_raises_when_no_candidate_has_a_key():
    with pytest.raises(ValueError, match="EXPOSITORY:1_topic_thesis"):
        stage_models(make_expository_body().routes, [])


def test_render_prompt_replaces_the_inputs_object_in_place():
    prompt = {"task": "t", "inputs": {"items": "{{items}}"}, "constraints": []}
    rendered = json.loads(render_prompt(prompt, {"items": ["a", "b"]}))
    assert rendered["inputs"] == {"items": ["a", "b"]}
    assert list(rendered) == ["task", "inputs", "constraints"]


def test_render_prompt_raises_when_input_keys_differ():
    with pytest.raises(ValueError):
        render_prompt({"inputs": {"a": "{{a}}"}}, {"b": 1})


def test_parse_output_reads_json_inside_fences_with_trailing_text():
    text = "```json\n" + json.dumps({"a": [1, {"b": "}"}]}) + "\n```\nDone."
    assert parse_output(text) == {"a": [1, {"b": "}"}]}


def test_parse_output_raises_when_there_is_no_object():
    with pytest.raises(ValueError):
        parse_output("no json here")
