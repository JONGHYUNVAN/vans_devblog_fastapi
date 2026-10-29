"""TTLStore 테스트."""

import time

from app.core.cache import TTLStore


def test_get_returns_value_until_ttl_expires():
    store = TTLStore()
    store.set("key", "value", ttl=0.05)

    assert store.get("key") == "value"
    time.sleep(0.06)
    assert store.get("key") is None
    assert store.get("key", "default") == "default"


def test_set_evicts_soonest_expiring_quarter_when_full():
    store = TTLStore(max_entries=4)
    for i in range(4):
        store.set(f"key{i}", i, ttl=10 + i)

    store.set("new", "value", ttl=100)

    assert store.get("key0") is None
    assert [store.get(f"key{i}") for i in range(1, 4)] == [1, 2, 3]
    assert store.get("new") == "value"


def test_set_evicts_expired_entries_before_live_ones():
    store = TTLStore(max_entries=2)
    store.set("expired", 1, ttl=0.01)
    store.set("live", 2, ttl=100)
    time.sleep(0.02)

    store.set("new", 3, ttl=100)

    assert (store.get("expired"), store.get("live"), store.get("new")) == (None, 2, 3)


def test_delete_and_clear():
    store = TTLStore()
    store.set("a", 1, ttl=100)
    store.set("b", 2, ttl=100)

    store.delete("a")
    assert (store.get("a"), store.get("b")) == (None, 2)
    store.clear()
    assert store.get("b") is None
