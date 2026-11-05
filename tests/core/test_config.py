"""Settings 테스트. CODE_GRAPH_REPOS 가 깨져도 앱은 떠야 한다 (code_graph_design/README.md G-24)."""

import logging

import pytest

from app.core.config import Settings


def test_code_graph_repos_parses_json_object(monkeypatch):
    monkeypatch.setenv("CODE_GRAPH_REPOS", '{"demo": {"owner": "x"}}')

    assert Settings(_env_file=None).code_graph_repos == {"demo": {"owner": "x"}}


@pytest.mark.parametrize("raw", ['{"demo": {broken secret-looking', "[1, 2]"])
def test_code_graph_repos_turns_feature_off_instead_of_failing_startup(monkeypatch, caplog, raw):
    monkeypatch.setenv("CODE_GRAPH_REPOS", raw)

    with caplog.at_level(logging.ERROR):
        settings = Settings(_env_file=None)

    assert settings.code_graph_repos == {}
    assert "코드 그래프를 끈다" in caplog.text
    assert "secret-looking" not in caplog.text


def test_code_graph_repos_empty_value_is_off(monkeypatch):
    monkeypatch.setenv("CODE_GRAPH_REPOS", "")

    assert Settings(_env_file=None).code_graph_repos == {}
