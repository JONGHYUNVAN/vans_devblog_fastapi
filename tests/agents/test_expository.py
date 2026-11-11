"""설명문 파이프라인의 프롬프트·라우트 파일 읽기와 프롬프트 채우기·출력 꺼내기."""

import json

import pytest

from app.agents.expository import STAGES, load_prompts, load_routes, parse_output, render_prompt


def test_load_prompts_reads_every_stage_with_inputs_and_schema():
    prompts = load_prompts()
    assert list(prompts) == [stage.key for stage in STAGES]
    for prompt in prompts.values():
        assert prompt["inputs"]
        assert prompt["expected_output_schema"]["type"] == "object"


def test_load_routes_covers_every_stage():
    routes = load_routes()
    assert all(routes[stage.key] for stage in STAGES)


def test_render_prompt_replaces_the_inputs_object_in_place():
    prompt = {"task": "t", "inputs": {"items": "{{items}}"}, "constraints": []}
    rendered = json.loads(render_prompt(prompt, {"items": ["a", "b"]}))
    assert rendered["inputs"] == {"items": ["a", "b"]}
    assert list(rendered) == ["task", "inputs", "constraints"]


def test_render_prompt_raises_when_input_keys_differ():
    with pytest.raises(ValueError):
        render_prompt({"inputs": {"a": "{{a}}"}}, {"b": 1})


def test_parse_output_reads_json_inside_fences_with_trailing_text():
    assert parse_output('```json\n{"a": [1, {"b": "}"}]}\n```\nDone.') == {"a": [1, {"b": "}"}]}


def test_parse_output_raises_when_there_is_no_object():
    with pytest.raises(ValueError):
        parse_output("no json here")
