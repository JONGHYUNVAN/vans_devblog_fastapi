import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.cache import cache
from app.main import app

GOLDEN_DIR = Path(__file__).parent / "golden"


def load_golden(name: str):
    return json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def client():
    return TestClient(app)
