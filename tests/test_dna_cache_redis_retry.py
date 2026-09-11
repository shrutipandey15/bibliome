"""RedisDNACache must retry after a transient connection failure, not go
permanently in-memory — the exact bug already fixed once in the rate limiter
(app/middleware/rate_limit.py) and ported here."""

import time
from unittest.mock import AsyncMock, patch

import pytest

from app.utils.cache import RedisDNACache, _REDIS_RETRY_SECONDS


@pytest.mark.asyncio
async def test_transient_failure_retries_after_window():
    cache = RedisDNACache()

    with patch("app.config.get_settings") as get_settings:
        get_settings.return_value.REDIS_URL = "redis://localhost:6379/0"

        with patch("redis.asyncio.from_url", side_effect=ConnectionError("boom")):
            assert await cache._get_redis() is None
        assert cache._failed_at is not None
        assert cache._disabled is False

        # Still inside the retry window: stays down without a fresh connect attempt.
        with patch("redis.asyncio.from_url") as from_url:
            assert await cache._get_redis() is None
            from_url.assert_not_called()

        # Window elapsed: a fresh connection attempt is made and can succeed.
        cache._failed_at = time.monotonic() - _REDIS_RETRY_SECONDS - 1
        ok_client = AsyncMock()
        ok_client.ping = AsyncMock(return_value=True)
        with patch("redis.asyncio.from_url", return_value=ok_client):
            result = await cache._get_redis()
        assert result is ok_client
        assert cache._failed_at is None


@pytest.mark.asyncio
async def test_no_redis_url_is_permanently_disabled_not_retried():
    cache = RedisDNACache()
    with patch("app.config.get_settings") as get_settings:
        get_settings.return_value.REDIS_URL = None
        assert await cache._get_redis() is None
    assert cache._disabled is True

    with patch("redis.asyncio.from_url") as from_url:
        assert await cache._get_redis() is None
        from_url.assert_not_called()
