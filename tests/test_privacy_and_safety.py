"""Regression tests for the pre-launch security pass.

Each test here pins one specific gap the audit found. They are grouped by the
gap rather than by endpoint, because the point is "this exact hole stays shut",
not "this endpoint works".
"""

import pytest

from app.config import Settings
from app.schemas.user import ACCOUNT_DELETE_CONFIRMATION
from app.services.moderation import classify_text

# No module-level asyncio mark: pytest.ini runs asyncio_mode=auto, and half the
# tests here are synchronous (config validation, classifier, redaction).

COOKIE = "bibliome_refresh"
REG = {"email": "owner@example.com", "username": "owner", "password": "hunter2pass"}


async def _register(client, **over):
    r = await client.post("/api/auth/register", json={**REG, **over})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ── A: the refresh 401 must actually clear the cookie ──────────────────────────

def _put_cookie(client, value):
    """Replace whatever is in the jar with exactly this refresh cookie."""
    client.cookies.clear()
    client.cookies.set(COOKIE, value)


async def test_failed_refresh_clears_the_cookie(client):
    """Raising HTTPException dropped the Set-Cookie, so a dead cookie stayed in
    the browser and re-failed every subsequent refresh."""
    await _register(client)
    _put_cookie(client, "not-a-real-token")

    r = await client.post("/api/auth/refresh")

    assert r.status_code == 401
    set_cookie = r.headers.get("set-cookie", "")
    assert COOKIE in set_cookie, "401 must carry the cookie deletion"
    assert "max-age=0" in set_cookie.lower() or "expires=" in set_cookie.lower()


async def test_replaying_a_rotated_refresh_token_kills_the_family(client):
    """Reuse detection: a correctly-signed but already-rotated token means either
    theft or a self-race. Assume theft and revoke everything."""
    reg = await client.post("/api/auth/register", json=REG)
    stolen = reg.cookies[COOKIE]

    rotated = await client.post("/api/auth/refresh")
    assert rotated.status_code == 200
    live = rotated.cookies[COOKIE]
    assert live != stolen

    _put_cookie(client, stolen)
    assert (await client.post("/api/auth/refresh")).status_code == 401

    # The token that was still legitimate a moment ago is now dead too.
    _put_cookie(client, live)
    assert (await client.post("/api/auth/refresh")).status_code == 401


# ── A6: login must not skip bcrypt for an unknown account ─────────────────────

async def test_login_hashes_even_when_the_account_does_not_exist(monkeypatch):
    """The enumeration oracle was structural: `if not user or not verify(...)`
    never called bcrypt for a missing email. Count the calls rather than the
    clock — timing assertions are flaky, this is exact."""
    from app.services import auth_service

    calls = []
    real_verify = auth_service.verify_password

    async def counting_verify(plain, hashed):
        calls.append(hashed)
        return await real_verify(plain, hashed)

    monkeypatch.setattr(auth_service, "verify_password", counting_verify)

    class _NoUser:
        def scalar_one_or_none(self):
            return None

    class _DB:
        async def execute(self, *_a, **_k):
            return _NoUser()

    result = await auth_service.authenticate_user(_DB(), "ghost@example.com", "whatever")

    assert result is None
    assert len(calls) == 1, "bcrypt must run even with no matching account"
    assert calls[0].startswith("$2b$"), "must compare against a real bcrypt hash"


# ── D3: export and erasure ────────────────────────────────────────────────────

async def test_export_returns_the_users_own_data_as_a_download(client):
    token = await _register(client)
    await client.post(
        "/api/entries",
        json={"title": "Piranesi", "author": "Susanna Clarke", "intensity": 8},
        headers=_auth(token),
    )

    r = await client.get("/api/user/export", headers=_auth(token))

    assert r.status_code == 200, r.text
    assert "attachment" in r.headers.get("content-disposition", "")
    assert r.headers.get("cache-control") == "no-store"
    body = r.json()
    assert body["account"]["email"] == REG["email"]
    assert [e["title"] for e in body["entries"]] == ["Piranesi"]
    # A two-party transcript is not one party's to export.
    assert "note" in body["social"]["resonance_threads"]


async def test_export_requires_auth(client):
    assert (await client.get("/api/user/export")).status_code == 403


async def test_delete_account_needs_the_password_and_the_phrase(client):
    token = await _register(client)

    wrong_pw = await client.post(
        "/api/user/delete",
        json={"password": "wrong", "confirm": ACCOUNT_DELETE_CONFIRMATION},
        headers=_auth(token),
    )
    assert wrong_pw.status_code == 400

    wrong_phrase = await client.post(
        "/api/user/delete",
        json={"password": REG["password"], "confirm": "yes"},
        headers=_auth(token),
    )
    assert wrong_phrase.status_code == 400

    # Still there.
    assert (await client.get("/api/auth/me", headers=_auth(token))).status_code == 200


async def test_delete_account_erases_the_user_and_their_rows(client, db):
    from sqlalchemy import func, select
    from app.models.book_entry import BookEntry
    from app.models.user import User

    token = await _register(client)
    await client.post(
        "/api/entries",
        json={"title": "Piranesi", "author": "Susanna Clarke", "intensity": 8},
        headers=_auth(token),
    )

    r = await client.post(
        "/api/user/delete",
        json={"password": REG["password"], "confirm": ACCOUNT_DELETE_CONFIRMATION},
        headers=_auth(token),
    )
    assert r.status_code == 204

    users = (await db.execute(
        select(func.count(User.id)).where(User.email == REG["email"])
    )).scalar_one()
    entries = (await db.execute(select(func.count(BookEntry.id)))).scalar_one()
    assert users == 0
    assert entries == 0, "entries must cascade, not orphan"

    # The access token outlives the row; the user lookup must still 401.
    assert (await client.get("/api/auth/me", headers=_auth(token))).status_code == 401


# ── E2/E2b: moderation actually reaches private threads ───────────────────────

async def test_thread_reports_are_resolvable_by_an_admin(client, db):
    """A reported thread used to be looked up in the replies table, so it could
    never be resolved and sat open in the queue forever."""
    from sqlalchemy import select
    from app.models.social import Report
    from app.models.user import User
    from app.services.moderation import resolve_target, submit_report

    token = await _register(client)
    me = (await db.execute(select(User).where(User.email == REG["email"]))).scalar_one()

    # A report whose target isn't an echo or a reply.
    fake_thread_id = me.id  # any uuid; resolve must 'not found', never mis-resolve
    await submit_report(db, me.id, "thread", fake_thread_id, "harassment")
    await db.commit()

    queued = [r for r in (await db.execute(
        select(Report).where(Report.status == "open")
    )).scalars().all() if r.target_type == "thread"]
    assert len(queued) == 1, "thread reports must reach the queue"

    # Unknown thread id → honest False, not a silent mis-resolve against replies.
    assert await resolve_target(db, me.id, "thread", fake_thread_id, "remove") is False
    # And an unrecognised type is rejected rather than defaulting to EchoReply.
    assert await resolve_target(db, me.id, "nonsense", fake_thread_id, "remove") is False

    # 'clear' is the one way out for a report whose target is already gone.
    # Without it the row above sits open forever — the same dead end the thread
    # bug caused, reached by the target being deleted instead of mis-looked-up.
    assert await resolve_target(db, me.id, "thread", fake_thread_id, "clear") is True
    await db.commit()
    still_open = [r for r in (await db.execute(
        select(Report).where(Report.status == "open")
    )).scalars().all() if r.target_type == "thread"]
    assert still_open == [], "a cleared orphan report must leave the queue"


def test_threats_are_classified_and_self_harm_routes_to_care():
    """post_message now runs this; before, DMs were the one unclassified surface."""
    from app.services.moderation import VERDICT_CRISIS, VERDICT_HOLD, VERDICT_OK

    assert classify_text("i'll kill you") == (VERDICT_HOLD, "threat")
    assert classify_text("i want to die")[0] == VERDICT_CRISIS
    assert classify_text("this book wrecked me") == (VERDICT_OK, None)


# ── F3: the production validator can't be skipped by omission ─────────────────

def test_unknown_environment_is_rejected_not_silently_development():
    with pytest.raises(ValueError, match="ENVIRONMENT must be one of"):
        Settings(ENVIRONMENT="Prod", _env_file=None)


def test_production_still_rejects_the_placeholder_secret():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(ENVIRONMENT="production", SECRET_KEY="CHANGE_THIS_TO_RANDOM_64_CHARS", _env_file=None)


def test_a_prod_looking_box_left_on_development_refuses_to_boot():
    """The actual bypass: ENVIRONMENT unset defaults to 'development', which is a
    *valid* value, so the whole production block never runs. FRONTEND_URL is the
    independent tell."""
    with pytest.raises(ValueError, match="looks like a production deployment"):
        Settings(FRONTEND_URL="https://bibliome.app", _env_file=None)


def test_local_development_is_unaffected():
    s = Settings(FRONTEND_URL="http://localhost:3000", _env_file=None)
    assert s.ENVIRONMENT == "development"
    assert s.cookie_secure is False


# ── C3: oversized free-text fields are rejected at the edge ───────────────────

async def test_a_five_megabyte_quote_is_rejected_before_the_db(client):
    token = await _register(client)
    r = await client.post(
        "/api/entries",
        json={"title": "Piranesi", "intensity": 5, "quote": "x" * 5_000_001},
        headers=_auth(token),
    )
    assert r.status_code == 422


async def test_update_bounds_match_create_bounds(client):
    token = await _register(client)
    created = await client.post(
        "/api/entries", json={"title": "Piranesi", "intensity": 5}, headers=_auth(token)
    )
    entry_id = created.json()["id"]

    # Over the author column width: a 422, not a 500 from Postgres.
    r = await client.put(
        f"/api/entries/{entry_id}",
        json={"author": "a" * 500},
        headers=_auth(token),
    )
    assert r.status_code == 422


# ── D4: no plaintext addresses in the logs ────────────────────────────────────

def test_redacted_email_is_stable_and_not_the_address():
    from app.utils.redact import redact_email

    tag = redact_email("Owner@Example.com ")
    assert tag == redact_email("owner@example.com"), "must survive case/whitespace"
    assert "owner" not in tag and "@" not in tag
    assert redact_email(None) == "email:none"
