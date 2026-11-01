"""프롬프트 템플릿 로더 테스트."""

import pytest

from app.agents.prompts.registry import PROMPT_VERSION, TEMPLATE_NAMES, load_template


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_every_v1_template_exists_and_asks_for_json_only(name):
    template = load_template(name, PROMPT_VERSION)

    assert "JSON" in template
    assert "$" not in template.replace("$display_name", "").replace("$writer_bias", "")


def test_unknown_template_name_raises():
    with pytest.raises(ValueError):
        load_template("nope")


def test_unknown_version_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_template("arbitrate", "v0")
