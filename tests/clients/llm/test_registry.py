"""LLMClientRegistry 테스트."""

import pytest

from app.clients.llm.registry import LLMClientRegistry
from tests.fakes import FakeLLMClient


def test_get_returns_client_for_provider():
    client = FakeLLMClient([])

    registry = LLMClientRegistry({"fake": client}, concurrency_per_provider=2)

    assert registry.get("fake") is client
    assert registry.providers == ["fake"]


def test_get_raises_key_error_for_unknown_provider():
    with pytest.raises(KeyError):
        LLMClientRegistry({"fake": FakeLLMClient([])}, concurrency_per_provider=2).get("unknown")


def test_semaphore_is_one_per_provider():
    registry = LLMClientRegistry({"a": FakeLLMClient([]), "b": FakeLLMClient([])}, concurrency_per_provider=1)

    assert registry.semaphore("a") is registry.semaphore("a")
    assert registry.semaphore("a") is not registry.semaphore("b")


def test_rejects_non_positive_concurrency():
    with pytest.raises(ValueError):
        LLMClientRegistry({}, concurrency_per_provider=0)
