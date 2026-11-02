"""LLMClientRegistry·ProviderGate 테스트: 벤더별 게이트, 429 뒤 cooldown 동안 1건씩."""

import asyncio

import pytest

from app.clients.llm.registry import LLMClientRegistry, ProviderGate
from tests.fakes import FakeLLMClient


def test_get_returns_client_for_provider():
    client = FakeLLMClient([])

    registry = LLMClientRegistry({"fake": client}, concurrency_per_provider=2)

    assert registry.get("fake") is client
    assert registry.providers == ["fake"]


def test_get_raises_key_error_for_unknown_provider():
    with pytest.raises(KeyError):
        LLMClientRegistry({"fake": FakeLLMClient([])}, concurrency_per_provider=2).get("unknown")


def test_gate_is_one_per_provider():
    registry = LLMClientRegistry({"a": FakeLLMClient([]), "b": FakeLLMClient([])}, concurrency_per_provider=1)

    assert registry.gate("a") is registry.gate("a")
    assert registry.gate("a") is not registry.gate("b")


def test_rejects_non_positive_concurrency():
    with pytest.raises(ValueError):
        LLMClientRegistry({}, concurrency_per_provider=0)


async def _max_active(gate: ProviderGate, calls: int) -> int:
    active = {"now": 0, "max": 0}

    async def one() -> None:
        async with gate.slot():
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
            await asyncio.sleep(0.01)
            active["now"] -= 1

    await asyncio.gather(*(one() for _ in range(calls)))
    return active["max"]


@pytest.mark.anyio
async def test_gate_serializes_calls_while_throttled_then_recovers():
    gate = ProviderGate(concurrency=3)
    assert await _max_active(gate, 3) == 3

    gate.throttle(0.05)

    assert gate.throttled
    assert await _max_active(gate, 3) == 1
    await asyncio.sleep(0.06)
    assert not gate.throttled
    assert await _max_active(gate, 3) == 3
