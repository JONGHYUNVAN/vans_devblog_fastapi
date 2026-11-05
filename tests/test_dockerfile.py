"""Dockerfile 이 분석기가 쓰는 문법을 이미지에 넣는지 확인한다. 어긋나면 운영에서 실행 중 다운로드가 일어난다 (G-24)."""

import re
from pathlib import Path

from app.services.code_graph import GRAMMARS

DOCKERFILE = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")


def test_dockerfile_prefetches_every_grammar_the_analyzer_uses():
    match = re.search(r"prefetch\(\[([^\]]*)\]\)", DOCKERFILE)

    assert match is not None
    assert sorted(re.findall(r"'([^']+)'", match.group(1))) == sorted(GRAMMARS)


def test_dockerfile_prefetches_as_app_user_into_the_configured_cache():
    assert DOCKERFILE.index("USER appuser") < DOCKERFILE.index("prefetch(")
    assert "ENV TREE_SITTER_LANGUAGE_PACK_CACHE_DIR=" in DOCKERFILE
