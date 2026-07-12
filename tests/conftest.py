"""Shared pytest fixtures.

DB-backed tests (auth flow, API smoke) point the whole app at a throwaway
`bookdna_test` Postgres database. Pure-unit tests (engine, tokens, cursor codec,
rate-limit) don't touch the DB and are unaffected.

Requires a reachable Postgres. Locally that's the default localhost:5432; in CI a
`postgres` service provides it. Set TEST_DATABASE_URL to override.
"""

import asyncio
import os

import pytest

TEST_DB = "bookdna_test"
_DEFAULT_URL = f"postgresql+asyncpg://postgres:postgres@localhost:5432/{TEST_DB}"

# Must be set before anything imports app.database (which binds the engine).
os.environ.setdefault("DATABASE_URL", os.environ.get("TEST_DATABASE_URL", _DEFAULT_URL))
os.environ.setdefault("ENVIRONMENT", "development")

# Parse admin connection params from the configured URL (connect to the default
# `postgres` database to create/drop the test DB).
from urllib.parse import urlparse  # noqa: E402

_p = urlparse(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
_ADMIN = dict(host=_p.hostname or "localhost", port=_p.port or 5432,
              user=_p.username or "postgres", password=_p.password or "postgres")


def _run(coro):
    return asyncio.run(coro)


async def _create_db():
    import asyncpg
    conn = await asyncpg.connect(database="postgres", **_ADMIN)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE {TEST_DB}')
    finally:
        await conn.close()


async def _drop_db():
    import asyncpg
    conn = await asyncpg.connect(database="postgres", **_ADMIN)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)')
    finally:
        await conn.close()


async def _create_tables():
    # Use a throwaway engine so the app's singleton engine stays unbound until a
    # test's own event loop uses it (avoids asyncpg "different loop" errors).
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.database import Base
    import app.models  # noqa: F401 — registers all tables on Base.metadata

    eng = create_async_engine(os.environ["DATABASE_URL"])
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    await eng.dispose()


def _pg_available() -> bool:
    try:
        _run(_create_db())
        return True
    except Exception as e:  # pragma: no cover
        print(f"\n[conftest] Postgres unavailable, skipping DB tests: {e}")
        return False


_PG_OK = None


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Make skipped DB coverage LOUD (audit-v2 P2).

    The DB-backed suite silently skips when Postgres is unreachable, so `pytest`
    can print green while most of the suite never ran — the exact false-confidence
    that let P0-1 ship. Shout about it at the end, where people actually look."""
    if _PG_OK is False:
        skipped = len(terminalreporter.stats.get("skipped", []))
        terminalreporter.write_sep("!", "PARTIAL TEST RUN", red=True, bold=True)
        terminalreporter.write_line(
            f"Postgres was UNAVAILABLE — {skipped} DB-backed tests were SKIPPED, not run.",
            red=True,
        )
        terminalreporter.write_line(
            "Green here does NOT mean the DB-backed suite passed. Run against a real "
            "Postgres (set TEST_DATABASE_URL) before trusting this result.",
            red=True,
        )
        terminalreporter.write_sep("!", red=True, bold=True)


@pytest.fixture(scope="session", autouse=True)
def _database():
    global _PG_OK
    _PG_OK = _pg_available()
    if not _PG_OK:
        yield
        return
    _run(_create_tables())
    yield
    _run(_drop_db())


def _reset_limiters():
    """Clear in-memory limiter/lockout state and relax per-IP caps for tests.

    All test requests share one synthetic IP, so the production per-IP caps
    (e.g. register_limiter = 3/hour) would bite when a single test creates
    several users. We raise those caps but leave login_lockout untouched so the
    lockout tests still assert the real threshold."""
    from app.middleware import rate_limit
    from app.routers import auth as auth_router
    from app.routers import entries as entries_router
    from app.routers import echo as echo_router
    relaxed = (
        rate_limit.auth_limiter, rate_limit.generate_limiter,
        auth_router.register_limiter, entries_router.entries_limiter,
        echo_router.echo_write_limiter,
    )
    for lim in relaxed:
        lim._hits.clear()
        lim.max_requests = 100000
    rate_limit.login_lockout._hits.clear()


@pytest.fixture
async def db_ready():
    """Skip a DB-backed test if Postgres wasn't reachable."""
    if not _PG_OK:
        pytest.skip("Postgres not available")
    _reset_limiters()
    from app.database import engine, Base
    from sqlalchemy import text
    # Bind the pool to this test's event loop and start from clean tables.
    await engine.dispose()
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as c:
        await c.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
async def client(db_ready):
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def db(db_ready):
    """A raw async session against the test DB (for service-level tests)."""
    from app.database import async_session
    async with async_session() as session:
        yield session
