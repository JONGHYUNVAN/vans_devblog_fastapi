"""공용 픽스처: 테스트마다 캐시·의존성 덮어쓰기 초기화, lifespan 없는 TestClient, golden 로더."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.cache import cache
from app.main import app

GOLDEN_DIR = Path(__file__).parent / "golden"


def load_golden(name: str):
    return json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _isolate():
    cache.clear()
    app.dependency_overrides.clear()
    yield
    cache.clear()
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    # with 블록 없이 만들어 lifespan(실제 ES·Mongo 클라이언트 생성)을 돌리지 않는다. 의존성은 각 테스트가 덮어쓴다.
    return TestClient(app)
