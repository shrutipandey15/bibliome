"""
Rate limiter for Book DNA API.

Primary: Redis-backed sliding window (production-grade, works across instances).
Fallback: In-memory if Redis is unavailable (dev/testing).

Usage:
    limiter = RateLimiter(max_requests=10, window_seconds=60, prefix="auth")

    @router.post("/login")
    async def login(request: Request):
        await limiter.check(request)  # raises HTTPException(429) if over limit
"""

import logging
import time
from collections import defaultdict

import redis.asyncio as redis
from fastapi import HTTPException, Request, status

from app.config import get_settings

logger = logging.getLogger("bookdna.ratelimit")

# Shared Redis connection — initialized lazily
_redis_client: redis.Redis | None = None
_redis_failed: bool = False


async def get_redis() -> redis.Redis | None:
    """Get or create Redis connection. Returns None if unavailable."""
    global _redis_client, _redis_failed

    if _redis_failed:
        return None

    if _redis_client is not None:
        try:
            await _redis_client.ping()
            return _redis_client
        except Exception:
            _redis_client = None

    settings = get_settings()
    redis_url = getattr(settings, "REDIS_URL", None)

    if not redis_url:
        _redis_failed = True
        logger.info("No REDIS_URL configured — using in-memory rate limiting")
        return None

    try:
        _redis_client = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await _redis_client.ping()
        logger.info("Redis connected for rate limiting")
        return _redis_client
    except Exception as e:
        _redis_failed = True
        _redis_client = None
        logger.warning("Redis unavailable (%s) — falling back to in-memory", e)
        return None


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int, prefix: str = "rl"):
        self.max_requests = max_requests
        self.window = window_seconds
        self.prefix = prefix
        # In-memory fallback
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.monotonic()

    def _get_ip(self, request: Request) -> str:
        """Extract client IP, respecting proxy headers."""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _redis_key(self, ip: str) -> str:
        return f"bookdna:{self.prefix}:{ip}"

    async def _check_redis(self, r: redis.Redis, request: Request) -> None:
        """Rate limit check using Redis sorted set sliding window."""
        ip = self._get_ip(request)
        key = self._redis_key(ip)
        now = time.time()
        cutoff = now - self.window

        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, cutoff)
        pipe.zcard(key)
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, self.window + 1)
        results = await pipe.execute()

        request_count = results[1]

        if request_count >= self.max_requests:
            # Remove the optimistic add
            await r.zrem(key, str(now))

            oldest = await r.zrange(key, 0, 0, withscores=True)
            retry_after = int(oldest[0][1] + self.window - now) + 1 if oldest else self.window

            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many requests. Try again in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )

    def _check_memory(self, request: Request) -> None:
        """Fallback in-memory rate limiter."""
        now = time.monotonic()

        # Periodic cleanup
        if now - self._last_cleanup > 60:
            self._last_cleanup = now
            cutoff = now - self.window
            expired = [k for k, v in self._hits.items() if all(t < cutoff for t in v)]
            for k in expired:
                del self._hits[k]

        ip = self._get_ip(request)
        cutoff = now - self.window
        self._hits[ip] = [t for t in self._hits[ip] if t > cutoff]

        if len(self._hits[ip]) >= self.max_requests:
            retry_after = int(self._hits[ip][0] + self.window - now) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many requests. Try again in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )

        self._hits[ip].append(now)

    async def check(self, request: Request) -> None:
        """Check rate limit. Uses Redis if available, falls back to memory."""
        r = await get_redis()
        if r is not None:
            await self._check_redis(r, request)
        else:
            self._check_memory(request)


# ── Pre-configured limiters ──
auth_limiter = RateLimiter(max_requests=10, window_seconds=60, prefix="auth")
generate_limiter = RateLimiter(max_requests=5, window_seconds=300, prefix="dna_gen")