"""Rate-limiter and lockout tracker tests (B1.7).

These exercise the in-memory fallback path (no REDIS_URL configured), which is
what CI runs. The Redis path shares the same public behaviour.
"""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.middleware.rate_limit import RateLimiter, FailedAttemptTracker


def _request(ip="1.2.3.4", xff=None):
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (ip, 12345),
    }
    return Request(scope)


def test_xff_is_ignored_when_no_trusted_proxy(monkeypatch):
    # Default TRUSTED_PROXY_COUNT=0: a client-supplied X-Forwarded-For must NOT
    # override the socket peer, else rate limits are trivially spoofable (P1-5).
    limiter = RateLimiter(max_requests=5, window_seconds=60, prefix="xff")
    ip = limiter._get_ip(_request(ip="9.9.9.9", xff="1.1.1.1, 2.2.2.2"))
    assert ip == "9.9.9.9"


def test_xff_uses_outermost_trusted_hop(monkeypatch):
    from app import config
    limiter = RateLimiter(max_requests=5, window_seconds=60, prefix="xff2")
    # Pretend we run behind exactly one trusted proxy that appends the real peer.
    monkeypatch.setattr(config.get_settings(), "TRUSTED_PROXY_COUNT", 1)
    # Client spoofs "6.6.6.6"; our proxy appends the true peer "3.3.3.3".
    ip = limiter._get_ip(_request(ip="10.0.0.1", xff="6.6.6.6, 3.3.3.3"))
    assert ip == "3.3.3.3"


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
