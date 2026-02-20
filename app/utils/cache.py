"""
TTL Cache — lightweight in-memory cache with expiration.
Thread-safe for async use. No external dependencies.

Usage:
    cache = TTLCache(ttl_seconds=300, max_size=500)
    cache.set("key", value)
    result = cache.get("key")  # None if expired or missing
    cache.invalidate("key")
    cache.invalidate_prefix("user:abc:")  # wipe all keys starting with prefix
"""

import time
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: int = 300, max_size: int = 500):
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        if len(self._store) >= self._max_size:
            oldest_key = min(self._store, key=lambda k: self._store[k][0])
            del self._store[oldest_key]
        self._store[key] = (time.monotonic() + (ttl or self._ttl), value)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> None:
        """Remove all keys starting with prefix."""
        keys = [k for k in self._store if k.startswith(prefix)]
        for k in keys:
            del self._store[k]

    def clear(self) -> None:
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)

search_cache = TTLCache(ttl_seconds=300, max_size=200)
dna_cache = TTLCache(ttl_seconds=120, max_size=100)