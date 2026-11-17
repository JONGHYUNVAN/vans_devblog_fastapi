"""설명문 파이프라인: 버전 문서에서 단계 프롬프트·모델 꺼내기, 프롬프트 채우기, 출력 꺼내기."""

import json

import pytest

from app.agents.expository import (
    DOCUMENT_STAGE_KEYS,
    STAGE_KEYS,
    SYSTEM_INSTRUCTION,
    parse_document,
    parse_output,
    parse_prompt,
    render_messages,
    render_prompt,
    stage_models,
    stage_prompts,
)
from app.models.pipeline import EXPOSITORY_FAMILY
from tests.factories import make_expository_body

# D-103 전의 프롬프트 모양: 모든 입력이 inputs 이고 출력은 JSON 이다
OLD_PROMPT = {"task": "t", "inputs": {"items": "{{items}}"}, "expected_output_schema": {"type": "object"}}
DOCUMENT_PROMPT = {
    "task": "Rewrite documents.content_markdown.",
    "inputs": {"terms": "{{terms}}"},
    "documents": {"content_markdown": "{{content_markdown}}"},
    "output_document": {"title": "Korean headline", "body": "Korean body"},
}
ARTICLE = '<!-- sec:intro -->\n## 1. Intro\n\nHe said "proxy".\n'


def test_stage_prompts_reads_every_stage_with_inputs_and_one_output_form():
    prompts = stage_prompts(make_expository_body().prompts)
    assert tuple(prompts) == STAGE_KEYS
    for key, prompt in prompts.items():
        assert prompt["inputs"]
        # 본문을 쓰는 단계는 문서로, 나머지는 JSON 으로 받는다
        assert ("output_document" in prompt) == (key in DOCUMENT_STAGE_KEYS)
        assert ("expected_output_schema" in prompt) == (key not in DOCUMENT_STAGE_KEYS)


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


def test_parse_prompt_takes_documents_and_exactly_one_output_form():
    assert parse_prompt(json.dumps(DOCUMENT_PROMPT)) == DOCUMENT_PROMPT

    with pytest.raises(ValueError, match="정확히 하나"):
        parse_prompt(json.dumps({**DOCUMENT_PROMPT, "expected_output_schema": {"type": "object"}}))
    with pytest.raises(ValueError, match="title·body"):
        parse_prompt(json.dumps({**DOCUMENT_PROMPT, "output_document": {"title": "t"}}))
    with pytest.raises(ValueError, match="겹치지 않는"):
        parse_prompt(json.dumps({**DOCUMENT_PROMPT, "documents": {"terms": "{{terms}}"}}))


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
    with pytest.raises(ValueError):  # documents 의 값도 빠지면 안 된다
        render_prompt(DOCUMENT_PROMPT, {"terms": []})


def test_render_prompt_fills_documents_in_place():
    rendered = json.loads(render_prompt(DOCUMENT_PROMPT, {"content_markdown": ARTICLE, "terms": []}))
    assert rendered["inputs"] == {"terms": []}
    assert rendered["documents"] == {"content_markdown": ARTICLE}
    assert list(rendered) == list(DOCUMENT_PROMPT)


@pytest.mark.parametrize("layout", ["json_blob", "documents_first"])
def test_render_messages_of_a_prompt_without_documents_is_the_same_in_every_layout(layout):
    system, user = render_messages(OLD_PROMPT, {"items": ["a"]}, layout)
    # 옛 프롬프트는 D-103 전과 글자까지 같은 메시지를 받는다
    assert system == SYSTEM_INSTRUCTION
    assert user == json.dumps({**OLD_PROMPT, "inputs": {"items": ["a"]}}, ensure_ascii=False, indent=2)


def test_render_messages_json_blob_keeps_documents_inside_the_json():
    system, user = render_messages(DOCUMENT_PROMPT, {"content_markdown": ARTICLE, "terms": []}, "json_blob")
    assert user == render_prompt(DOCUMENT_PROMPT, {"content_markdown": ARTICLE, "terms": []})
    assert '"documents" as data' in system and "markdown document" in system


def test_render_messages_documents_first_sends_documents_raw_above_the_instructions():
    system, user = render_messages(DOCUMENT_PROMPT, {"content_markdown": ARTICLE, "terms": ["t"]}, "documents_first")

    documents, instructions = user.split("\n\n<instructions>\n")
    # 본문은 이스케이프 없이 원문 그대로이고, 지시문 JSON 에는 본문 대신 위 블록을 가리키는 말이 있다
    assert documents == f"<documents>\n<content_markdown>\n{ARTICLE}\n</content_markdown>\n</documents>"
    filled = json.loads(instructions.removesuffix("\n</instructions>"))
    assert filled["documents"] == {"content_markdown": "provided above in <content_markdown>"}
    assert filled["inputs"] == {"terms": ["t"]}
    assert "<documents>" in system and "markdown document" in system


def test_render_messages_raises_for_an_unknown_layout_or_a_document_that_is_not_text():
    with pytest.raises(ValueError, match="모르는 배치"):
        render_messages(OLD_PROMPT, {"items": []}, "xml")
    with pytest.raises(ValueError, match="문자열"):
        render_messages(DOCUMENT_PROMPT, {"content_markdown": {"a": 1}, "terms": []}, "documents_first")


def test_parse_output_reads_json_inside_fences_with_trailing_text():
    text = "```json\n" + json.dumps({"a": [1, {"b": "}"}]}) + "\n```\nDone."
    assert parse_output(text) == {"a": [1, {"b": "}"}]}


def test_parse_output_raises_when_there_is_no_object():
    with pytest.raises(ValueError):
        parse_output("no json here")


def test_parse_document_reads_the_title_line_and_the_body():
    body = "<!-- sec:intro -->\n## 1. Intro\n\nText.\n\n```java\n// # not a title\n```"
    assert parse_document(f"# Why it happens\n\n{body}\n") == {"title": "Why it happens", "content_markdown": body}


def test_parse_document_drops_a_code_fence_around_the_whole_document():
    body = "## 1. Intro\n\n```java\nclass A {}\n```"
    assert parse_document(f"```markdown\n# Title\n\n{body}\n```") == {"title": "Title", "content_markdown": body}


def test_parse_document_raises_without_a_title_line_or_a_body():
    with pytest.raises(ValueError, match="title line"):
        parse_document("## 1. Intro\n\nText.")
    with pytest.raises(ValueError, match="title line"):
        parse_document('{"title": "t", "content_markdown": "b"}')
    with pytest.raises(ValueError, match="no body"):
        parse_document("# Title\n\n")
