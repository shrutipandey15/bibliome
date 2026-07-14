"""
TTL Cache — lightweight in-memory cache with expiration.
Thread-safe for async use. No external dependencies.

Usage:
    cache = TTLCache(ttl_seconds=300, max_size=500)
    cache.set("key", value)
    result = cache.get("key")  # None if expired or missing
    cache.invalidate("key")
    cache.invalidate_prefix("user:abc:")  # wipe all keys starting with prefix

RedisDNACache — async Redis-backed cache with TTLCache fallback.
Uses Redis when REDIS_URL is configured; falls back to TTLCache transparently.
"""

import json
import logging
import time
from typing import Any

import redis.asyncio as redis
from fastapi.encoders import jsonable_encoder

logger = logging.getLogger("bookdna.cache")


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

class RedisDNACache:
    """
    Async DNA cache: Redis when available, TTLCache fallback otherwise.
    Mirrors the RateLimiter pattern — requires no config changes to work.
    """

    def __init__(self, ttl_seconds: int = 600, namespace: str = "bookdna:dna"):
        self._ttl = ttl_seconds
        self._ns = namespace
        self._fallback = TTLCache(ttl_seconds=ttl_seconds, max_size=100)
        self._client: redis.Redis | None = None
        self._failed: bool = False

    async def _get_redis(self) -> "redis.Redis | None":
        if self._failed:
            return None
        if self._client is not None:
            try:
                await self._client.ping()
                return self._client
            except Exception:
                self._client = None

        from app.config import get_settings
        redis_url = getattr(get_settings(), "REDIS_URL", None)
        if not redis_url:
            self._failed = True
            return None

        try:
            self._client = redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            await self._client.ping()
            return self._client
        except Exception as e:
            self._failed = True
            self._client = None
            logger.warning("Redis unavailable for DNA cache (%s) — using in-memory fallback", e)
            return None

    def _redis_key(self, key: str) -> str:
        return f"{self._ns}:{key}"

    async def get(self, key: str) -> Any | None:
        r = await self._get_redis()
        if r is not None:
            try:
                val = await r.get(self._redis_key(key))
                return json.loads(val) if val else None
            except Exception:
                pass
        return self._fallback.get(key)

    async def set(self, key: str, value: Any) -> None:
        r = await self._get_redis()
        if r is not None:
            try:
                await r.setex(
                    self._redis_key(key),
                    self._ttl,
                    json.dumps(jsonable_encoder(value)),
                )
                return
            except Exception:
                pass
        self._fallback.set(key, value)

    async def invalidate(self, key: str) -> None:
        r = await self._get_redis()
        if r is not None:
            try:
                await r.delete(self._redis_key(key))
            except Exception:
                pass
        self._fallback.invalidate(key)

    async def invalidate_prefix(self, prefix: str) -> None:
        r = await self._get_redis()
        if r is not None:
            try:
                pattern = f"{self._ns}:{prefix}*"
                async for k in r.scan_iter(pattern):
                    await r.delete(k)
            except Exception:
                pass
        self._fallback.invalidate_prefix(prefix)


dna_cache = RedisDNACache(ttl_seconds=600)
# Book-search results, shared across workers so a query hits external APIs once
# for the whole fleet, not once per worker (P4-5).
book_search_cache = RedisDNACache(ttl_seconds=300, namespace="bookdna:search")


async def invalidate_dna(user_id) -> None:
    """Single invalidation point for a user's out-of-band DNA caches.

    The DB is the source of truth. The `/profile` cache lives on the user row and
    is invalidated inside the writing transaction via the ``dna_dirty`` flag; the
    heatmap and stats caches live in ``dna_cache`` and are cleared here. Every DNA
    write path (entry create/update/delete/finish, /dna/generate, and the
    post-commit recalc) routes through this one helper so invalidation stays
    consistent across the three regimes (P2-5 / B1.3).
    """
    await dna_cache.invalidate_prefix(f"heatmap:{user_id}")
    await dna_cache.invalidate_prefix(f"stats:{user_id}")