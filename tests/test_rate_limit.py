"""Rate-limiter and lockout tracker tests (B1.7).

These exercise the in-memory fallback path (no REDIS_URL configured), which is
what CI runs. The Redis path shares the same public behaviour.
"""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.middleware.rate_limit import RateLimiter, FailedAttemptTracker


def _request(ip="1.2.3.4"):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "client": (ip, 12345),
    }
    return Request(scope)


async def test_rate_limiter_blocks_after_max_requests():
    limiter = RateLimiter(max_requests=3, window_seconds=60, prefix="test")
    req = _request()
    for _ in range(3):
        await limiter.check(req)  # allowed
    with pytest.raises(HTTPException) as exc:
        await limiter.check(req)
    assert exc.value.status_code == 429


async def test_rate_limiter_isolates_by_ip():
    limiter = RateLimiter(max_requests=1, window_seconds=60, prefix="test2")
    await limiter.check(_request("10.0.0.1"))
    # Different IP still has budget.
    await limiter.check(_request("10.0.0.2"))


async def test_lockout_locks_after_threshold():
    tracker = FailedAttemptTracker(threshold=3, window_seconds=900, prefix="test_lock")
    ident = "user@example.com"
    await tracker.check_locked(ident)  # not locked yet
    for i in range(3):
        count = await tracker.record(ident)
        assert count == i + 1
    with pytest.raises(HTTPException) as exc:
        await tracker.check_locked(ident)
    assert exc.value.status_code == 429


async def test_lockout_clear_resets():
    tracker = FailedAttemptTracker(threshold=2, window_seconds=900, prefix="test_lock2")
    ident = "b@example.com"
    await tracker.record(ident)
    await tracker.record(ident)
    with pytest.raises(HTTPException):
        await tracker.check_locked(ident)
    await tracker.clear(ident)
    await tracker.check_locked(ident)  # no longer locked


async def test_lockout_isolates_by_identifier():
    tracker = FailedAttemptTracker(threshold=1, window_seconds=900, prefix="test_lock3")
    await tracker.record("a@example.com")
    with pytest.raises(HTTPException):
        await tracker.check_locked("a@example.com")
    # A different account is unaffected by another's failures.
    await tracker.check_locked("c@example.com")
