"""인프로세스 TTL 캐시. Django LocMemCache 대체.

uvicorn 워커 1개로 운영하므로 프로세스 메모리 캐시로 충분하다. 키마다 TTL이 달라서
cachetools.TTLCache 하나 대신 (만료시각, 값) 쌍을 직접 관리한다.
"""

import threading
import time
from typing import Any

# Django 서비스의 base.py 에 하드코딩돼 있던 값 (환경변수는 읽지 않았다)
SEARCH_TTL = 300
AUTOCOMPLETE_TTL = 600
POPULAR_TTL = 60
CATEGORIES_TTL = 60
HEALTH_TTL = 1800

_MAX_ENTRIES = 1000


class TTLStore:
    def __init__(self, max_entries: int = _MAX_ENTRIES):
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._max = max_entries

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return default
            expires_at, value = item
            if expires_at < time.monotonic():
                del self._data[key]
                return default
            return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            if len(self._data) >= self._max and key not in self._data:
                self._evict()
            self._data[key] = (time.monotonic() + ttl, value)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def _evict(self) -> None:
        now = time.monotonic()
        expired = [k for k, (exp, _) in self._data.items() if exp < now]
        for k in expired:
            del self._data[k]
        if len(self._data) >= self._max:
            # 가장 먼저 만료될 항목부터 1/4 제거
            for k, _ in sorted(self._data.items(), key=lambda kv: kv[1][0])[: self._max // 4]:
                del self._data[k]


cache = TTLStore()
