"""The encrypted journal — key bundle lifecycle, ciphertext CRUD, DNA feed.

The tests that matter most here are the negative ones: that the server refuses to
overwrite a key bundle, that it never gained a search endpoint, that a password
reset tells the truth about what was just lost, and that a private journal never
leaks into the public profile signature.
"""

import base64

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


def _b64(n: int, fill: int = 7) -> str:
    return base64.b64encode(bytes([fill] * n)).decode()


def _bundle(**over) -> dict:
    """A structurally valid bundle. The bytes are nonsense — which is the point:
    the server cannot tell, and nothing in the API depends on it being able to."""
    data = {
        "cipher": "AES-GCM",
        "kdf": "argon2id",
        "kdf_params": {"m": 65536, "t": 3, "p": 1},
        "password_salt": _b64(16, 1),
        "wrapped_dek": _b64(48, 2),
        "wrapped_dek_nonce": _b64(12, 3),
        "recovery_salt": _b64(16, 4),
        "wrapped_dek_recovery": _b64(48, 5),
        "wrapped_dek_recovery_nonce": _b64(12, 6),
    }
    data.update(over)
    return data


async def _user(client, name, password="hunter2pass"):
    await client.post("/api/auth/register", json={
        "email": f"{name}@example.com", "username": name, "password": password,
    })
    r = await client.post("/api/auth/login", json={
        "email": f"{name}@example.com", "password": password,
    })
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _journaller(client, name, password="hunter2pass"):
    """A user with a journal key already set up."""
    h = await _user(client, name, password)
    r = await client.post("/api/journal/key", json=_bundle(), headers=h)
    assert r.status_code == 201, r.text
    return h


async def _write(client, headers, day, text="ciphertext", emotions=None):
    return await client.post("/api/journal/entries", json={
        "entry_date": day,
        "ciphertext": base64.b64encode(text.encode()).decode(),
        "nonce": _b64(12),
        "emotions": [{"emotion_id": e, "strength": 8} for e in (emotions or [])],
    }, headers=headers)


# ── Key bundle lifecycle ──

async def test_key_bundle_setup_and_fetch(client):
    h = await _journaller(client, "jkey")
    r = await client.get("/api/journal/key", headers=h)
    assert r.status_code == 200
    body = r.json()
    # Served back byte-identically — the client is the only party that can use it.
    assert body["wrapped_dek"] == _bundle()["wrapped_dek"]
    assert body["wrapped_dek_recovery"] == _bundle()["wrapped_dek_recovery"]
    assert body["password_wrap_stale"] is False
    assert body["key_version"] == 1


async def test_key_bundle_missing_is_404_not_empty(client):
    h = await _user(client, "jnokey")
    assert (await client.get("/api/journal/key", headers=h)).status_code == 404


async def test_second_setup_refused_because_it_would_destroy_the_journal(client):
    h = await _journaller(client, "jdup")
    r = await client.post("/api/journal/key", json=_bundle(password_salt=_b64(16, 9)), headers=h)
    assert r.status_code == 409
    assert "unreadable" in r.json()["detail"]


async def test_rewrap_requires_the_account_password(client):
    h = await _journaller(client, "jrewrap")
    new = _bundle(password_salt=_b64(16, 8), wrapped_dek=_b64(48, 8))

    r = await client.put("/api/journal/key", json={**new, "current_password": "wrongpass"}, headers=h)
    assert r.status_code == 400

    r = await client.put("/api/journal/key", json={**new, "current_password": "hunter2pass"}, headers=h)
    assert r.status_code == 200
    assert r.json()["wrapped_dek"] == new["wrapped_dek"]


async def test_rewrap_without_a_bundle_is_404(client):
    h = await _user(client, "jrewrapnone")
    r = await client.put("/api/journal/key",
                         json={**_bundle(), "current_password": "hunter2pass"}, headers=h)
    assert r.status_code == 404


async def test_bundle_structure_is_validated_but_never_content(client):
    h = await _user(client, "jstruct")
    # Rejected on shape alone: not base64, unknown cipher, one salt reused.
    for bad in (
        _bundle(wrapped_dek="not base64 at all!"),
        _bundle(cipher="ROT13"),
        _bundle(recovery_salt=_bundle()["password_salt"]),
        _bundle(wrapped_dek=_b64(8)),          # far too short to be a wrapped key
    ):
        assert (await client.post("/api/journal/key", json=bad, headers=h)).status_code == 422
    # Accepted: valid shape, meaningless bytes. The server cannot tell the
    # difference, and that is the whole design.
    assert (await client.post("/api/journal/key", json=_bundle(), headers=h)).status_code == 201


# ── Entries ──

async def test_entry_requires_a_key_bundle_first(client):
    h = await _user(client, "jnobundle")
    r = await _write(client, h, "2026-07-30")
    assert r.status_code == 409
    assert "journal key" in r.json()["detail"]


async def test_ciphertext_round_trips_verbatim(client, db):
    from app.models.journal import JournalEntry

    h = await _journaller(client, "jround")
    blob = base64.b64encode(b"\x00\x01\x02 sealed prose \xff").decode()
    r = await client.post("/api/journal/entries", json={
        "entry_date": "2026-07-30", "ciphertext": blob, "nonce": _b64(12),
    }, headers=h)
    assert r.status_code == 201
    assert r.json()["ciphertext"] == blob

    # And the column holds exactly that — opaque, unparsed, unmodified.
    stored = (await db.execute(
        select(JournalEntry.ciphertext).where(JournalEntry.id == r.json()["id"])
    )).scalar_one()
    assert stored == blob


async def test_malformed_ciphertext_rejected(client):
    h = await _journaller(client, "jbadct")
    for bad in ({"ciphertext": "%%%", "nonce": _b64(12)},
                {"ciphertext": "", "nonce": _b64(12)},
                {"ciphertext": _b64(10), "nonce": _b64(2)}):
        r = await client.post("/api/journal/entries",
                              json={"entry_date": "2026-07-30", **bad}, headers=h)
        assert r.status_code == 422


async def test_list_is_newest_day_first_and_pages_by_cursor(client):
    h = await _journaller(client, "jpage")
    for day in ("2026-07-01", "2026-07-02", "2026-07-03"):
        assert (await _write(client, h, day)).status_code == 201

    r = await client.get("/api/journal/entries?limit=2", headers=h)
    body = r.json()
    assert body["total"] == 3 and body["has_more"] is True
    assert [e["entry_date"] for e in body["entries"]] == ["2026-07-03", "2026-07-02"]

    r2 = await client.get(f"/api/journal/entries?limit=2&cursor={body['next_cursor']}", headers=h)
    body2 = r2.json()
    assert [e["entry_date"] for e in body2["entries"]] == ["2026-07-01"]
    assert body2["has_more"] is False


async def test_several_entries_on_one_day_all_page_out(client):
    """A day is not a unique key — the journal is a continuous book, not one card
    per date. The (date, id) cursor must not drop or repeat same-day entries."""
    h = await _journaller(client, "jsameday")
    for i in range(3):
        await _write(client, h, "2026-07-30", text=f"pass {i}")

    seen, cursor = [], None
    while True:
        url = "/api/journal/entries?limit=1" + (f"&cursor={cursor}" if cursor else "")
        body = (await client.get(url, headers=h)).json()
        seen += [e["id"] for e in body["entries"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 3 and len(set(seen)) == 3


async def test_bad_cursor_is_400_not_a_silent_restart(client):
    h = await _journaller(client, "jbadcursor")
    r = await client.get("/api/journal/entries?cursor=garbage", headers=h)
    assert r.status_code == 400


async def test_filters_are_metadata_only(client):
    h = await _journaller(client, "jfilter")
    await _write(client, h, "2026-06-01", emotions=["grief"])
    await _write(client, h, "2026-07-01", emotions=["joy"])
    await _write(client, h, "2026-07-02")                       # unnamed

    assert (await client.get("/api/journal/entries?emotion=grief", headers=h)).json()["total"] == 1
    assert (await client.get("/api/journal/entries?untagged=true", headers=h)).json()["total"] == 1
    r = await client.get("/api/journal/entries?date_from=2026-07-01", headers=h)
    assert r.json()["total"] == 2


async def test_there_is_no_journal_search(client):
    """Server-side search over ciphertext is impossible, so the parameter does not
    exist — asserted against the schema, not just against behaviour."""
    from app.main import app

    params = set()
    for route in app.routes:
        if getattr(route, "path", "") == "/api/journal/entries" and "GET" in getattr(route, "methods", ()):
            params = {p.name for p in route.dependant.query_params}
    assert params, "journal list route not found"
    assert "q" not in params and "search" not in params
    # By contrast the book shelf, whose titles are plaintext, does have one.
    shelf = set()
    for route in app.routes:
        if getattr(route, "path", "") == "/api/entries" and "GET" in getattr(route, "methods", ()):
            shelf = {p.name for p in route.dependant.query_params}
    assert "q" in shelf


async def test_update_replaces_ciphertext_and_tags(client):
    h = await _journaller(client, "jupdate")
    entry_id = (await _write(client, h, "2026-07-30", emotions=["grief"])).json()["id"]

    new_blob = base64.b64encode(b"rewritten").decode()
    r = await client.put(f"/api/journal/entries/{entry_id}", json={
        "ciphertext": new_blob, "nonce": _b64(12, 9),
        "emotions": [{"emotion_id": "catharsis", "strength": 4}],
    }, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["ciphertext"] == new_blob
    assert [e["emotion_id"] for e in body["emotions"]] == ["catharsis"]


async def test_ciphertext_and_nonce_must_move_together(client):
    """Accepting one without the other is how a client walks into nonce reuse."""
    h = await _journaller(client, "jnoncepair")
    entry_id = (await _write(client, h, "2026-07-30")).json()["id"]
    r = await client.put(f"/api/journal/entries/{entry_id}",
                         json={"ciphertext": _b64(20)}, headers=h)
    assert r.status_code == 422


async def test_tags_only_write_leaves_ciphertext_untouched(client):
    """Batch-tag-later: naming a day must not require decrypting and re-sending it."""
    h = await _journaller(client, "jtagslater")
    created = (await _write(client, h, "2026-07-30", text="unnamed day")).json()
    assert created["emotions"] == []

    r = await client.put(f"/api/journal/entries/{created['id']}/tags", json={
        "emotions": [{"emotion_id": "longing", "strength": 6},
                     {"emotion_id": "comfort", "strength": 3}],
    }, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["ciphertext"] == created["ciphertext"]
    assert {e["emotion_id"] for e in body["emotions"]} == {"longing", "comfort"}


async def test_tag_vocabulary_is_exactly_the_book_vocabulary(client):
    """One vocabulary, no journal dialect: the journal accepts and refuses precisely
    what a book entry does, because both go through `EmotionIn`."""
    h = await _journaller(client, "jlegacy")
    entry_id = (await _write(client, h, "2026-07-30")).json()["id"]

    for slug in ("chaos", "made_up_feeling"):   # `chaos` is a retired pre-cutover slug
        r = await client.put(f"/api/journal/entries/{entry_id}/tags",
                             json={"emotions": [{"emotion_id": slug, "strength": 5}]}, headers=h)
        assert r.status_code == 422, slug
        book = await _add_book(client, h, f"Book {slug}", [slug])
        assert book.status_code == 422, slug

    # Strength shares the book model too: 1–10, nothing outside it.
    r = await client.put(f"/api/journal/entries/{entry_id}/tags",
                         json={"emotions": [{"emotion_id": "grief", "strength": 11}]}, headers=h)
    assert r.status_code == 422


async def test_delete_removes_the_entry(client):
    h = await _journaller(client, "jdelete")
    entry_id = (await _write(client, h, "2026-07-30")).json()["id"]
    assert (await client.delete(f"/api/journal/entries/{entry_id}", headers=h)).status_code == 204
    assert (await client.get(f"/api/journal/entries/{entry_id}", headers=h)).status_code == 404


async def test_a_journal_has_no_reader_but_its_author(client):
    h_owner = await _journaller(client, "jowner")
    h_other = await _journaller(client, "jstranger")
    entry_id = (await _write(client, h_owner, "2026-07-30")).json()["id"]

    assert (await client.get(f"/api/journal/entries/{entry_id}", headers=h_other)).status_code == 404
    assert (await client.put(f"/api/journal/entries/{entry_id}",
                             json={"ciphertext": _b64(10), "nonce": _b64(12)},
                             headers=h_other)).status_code == 404
    assert (await client.delete(f"/api/journal/entries/{entry_id}", headers=h_other)).status_code == 404
    # And the stranger's own list is empty, not "everything they can't decrypt".
    assert (await client.get("/api/journal/entries", headers=h_other)).json()["total"] == 0


async def test_entries_require_authentication(client):
    assert (await client.get("/api/journal/entries")).status_code in (401, 403)
    assert (await client.get("/api/journal/key")).status_code in (401, 403)


# ── Password change / reset honesty ──

async def test_password_change_with_rewrapped_bundle_is_atomic(client):
    h = await _journaller(client, "jpwchange")
    new = _bundle(password_salt=_b64(16, 11), wrapped_dek=_b64(48, 11))

    r = await client.post("/api/user/change-password", json={
        "current_password": "hunter2pass", "new_password": "brandnewpass1",
        "journal_key_bundle": new,
    }, headers=h)
    assert r.status_code == 200
    assert r.json()["journal"] == {"rewrapped": True}

    # New session; the stored bundle is the re-wrapped one and is not stale.
    h2 = {"Authorization": "Bearer " + (await client.post("/api/auth/login", json={
        "email": "jpwchange@example.com", "password": "brandnewpass1",
    })).json()["access_token"]}
    body = (await client.get("/api/journal/key", headers=h2)).json()
    assert body["wrapped_dek"] == new["wrapped_dek"]
    assert body["password_wrap_stale"] is False


async def test_password_change_without_rewrap_says_the_journal_is_now_recovery_only(client):
    h = await _journaller(client, "jpwstale")
    r = await client.post("/api/user/change-password", json={
        "current_password": "hunter2pass", "new_password": "brandnewpass1",
    }, headers=h)
    assert r.status_code == 200
    journal = r.json()["journal"]
    assert journal["rewrapped"] is False and journal["locked"] is True
    assert journal["recoverable_with_recovery_code"] is True

    h2 = {"Authorization": "Bearer " + (await client.post("/api/auth/login", json={
        "email": "jpwstale@example.com", "password": "brandnewpass1",
    })).json()["access_token"]}
    assert (await client.get("/api/journal/key", headers=h2)).json()["password_wrap_stale"] is True


async def test_password_change_says_nothing_about_a_journal_that_doesnt_exist(client):
    h = await _user(client, "jpwnojournal")
    r = await client.post("/api/user/change-password", json={
        "current_password": "hunter2pass", "new_password": "brandnewpass1",
    }, headers=h)
    assert r.status_code == 200 and "journal" not in r.json()


async def test_reset_password_is_honest_that_the_journal_is_now_recovery_only(client, db):
    from app.models.journal import JournalKeyBundle
    from app.models.user import User
    from app.services.auth_service import hash_token

    h = await _journaller(client, "jreset")
    # Mint a reset token the way forgot-password does (only the hash is stored).
    await client.post("/api/auth/forgot-password", json={"email": "jreset@example.com"})
    token = "known-plaintext-reset-token"
    user = (await db.execute(select(User).where(User.email == "jreset@example.com"))).scalar_one()
    user.reset_token = hash_token(token)
    await db.commit()

    r = await client.post("/api/auth/reset-password",
                          json={"token": token, "new_password": "afterresetpass1"})
    assert r.status_code == 200
    journal = r.json()["journal"]
    assert journal["locked"] is True
    assert journal["recoverable_with_recovery_code"] is True
    assert "permanently unreadable" in journal["message"]

    # The bundle survives: the recovery wrapping never depended on the password, so
    # deleting it here would destroy the user's only remaining way in.
    bundle = (await db.execute(
        select(JournalKeyBundle).where(JournalKeyBundle.user_id == user.id)
    )).scalar_one()
    await db.refresh(bundle)
    assert bundle.password_wrap_stale is True
    assert bundle.wrapped_dek_recovery == _bundle()["wrapped_dek_recovery"]

    # And the recovery path works: unlock with the code client-side, re-wrap here.
    h2 = {"Authorization": "Bearer " + (await client.post("/api/auth/login", json={
        "email": "jreset@example.com", "password": "afterresetpass1",
    })).json()["access_token"]}
    rewrapped = _bundle(password_salt=_b64(16, 12), wrapped_dek=_b64(48, 12))
    r = await client.put("/api/journal/key",
                         json={**rewrapped, "current_password": "afterresetpass1"}, headers=h2)
    assert r.status_code == 200 and r.json()["password_wrap_stale"] is False


async def test_reset_password_says_nothing_when_there_is_no_journal(client, db):
    from app.models.user import User
    from app.services.auth_service import hash_token

    await _user(client, "jresetnone")
    user = (await db.execute(select(User).where(User.email == "jresetnone@example.com"))).scalar_one()
    user.reset_token = hash_token("another-reset-token")
    await db.commit()

    r = await client.post("/api/auth/reset-password",
                          json={"token": "another-reset-token", "new_password": "afterresetpass1"})
    assert r.status_code == 200 and "journal" not in r.json()


# ── DNA integration: journal emotions are just another emotion source ──

async def _add_book(client, headers, title, emotions):
    return await client.post("/api/entries", json={
        "title": title, "intensity": 6, "status": "finished",
        "emotions": [{"emotion_id": e, "strength": 7} for e in emotions],
    }, headers=headers)


async def test_journal_tags_move_the_dna_but_never_the_book_count(client):
    h = await _journaller(client, "jdna")
    for i in range(6):
        await _add_book(client, h, f"Book {i}", ["comfort"])

    before = (await client.get("/api/dna/profile", headers=h)).json()
    assert before["book_count"] == 6
    assert before["profiles"]["current"]["comfort"] == pytest.approx(1.0)
    assert before["journal_entry_count"] == 0

    for i in range(6):
        assert (await _write(client, h, f"2026-07-0{i + 1}", emotions=["rage"])).status_code == 201

    after = (await client.get("/api/dna/profile", headers=h)).json()
    # Life is in the mirror now…
    assert after["profiles"]["current"]["rage"] > 0
    assert after["profiles"]["current"]["comfort"] < 1.0
    assert after["journal_entry_count"] == 6
    # …but "you've logged N books" is still about books.
    assert after["book_count"] == 6


async def test_untagged_days_carry_no_signal(client):
    """Silence is not indifference — an unnamed day must not enter the math."""
    h = await _journaller(client, "jdnauntagged")
    for i in range(6):
        await _add_book(client, h, f"Book {i}", ["comfort"])
    for i in range(5):
        await _write(client, h, f"2026-07-0{i + 1}")

    body = (await client.get("/api/dna/profile", headers=h)).json()
    assert body["journal_entry_count"] == 0
    assert body["profiles"]["current"]["comfort"] == pytest.approx(1.0)


async def test_journal_never_reaches_the_public_profile_signature(client, db):
    """The private mirror spans reading and life. The public signature is books
    only — a stranger must not be able to read a private journal's emotions out of
    someone's public profile, even in aggregate."""
    from app.models.user import User

    h = await _journaller(client, "jdnapublic")
    for i in range(6):
        await _add_book(client, h, f"Book {i}", ["comfort"])
    for i in range(6):
        await _write(client, h, f"2026-07-0{i + 1}", emotions=["rage"])
    await client.get("/api/dna/profile", headers=h)   # force a recompute

    user = (await db.execute(select(User).where(User.email == "jdnapublic@example.com"))).scalar_one()
    await db.refresh(user)
    freq = (user.cached_dna_profile or {}).get("emotion_frequency", {})
    assert freq.get("comfort") == 6
    assert "rage" not in freq
    # The private payload, by contrast, has it.
    assert user.cached_dna_v2["profiles"]["current"]["rage"] > 0


async def test_deleting_a_journal_entry_removes_it_from_the_dna(client):
    h = await _journaller(client, "jdnadelete")
    for i in range(6):
        await _add_book(client, h, f"Book {i}", ["comfort"])
    entry_id = (await _write(client, h, "2026-07-30", emotions=["rage"])).json()["id"]

    assert (await client.get("/api/dna/profile", headers=h)).json()["profiles"]["current"]["rage"] > 0
    await client.delete(f"/api/journal/entries/{entry_id}", headers=h)
    after = (await client.get("/api/dna/profile", headers=h)).json()
    assert after["profiles"]["current"]["rage"] == 0
    assert after["journal_entry_count"] == 0


# ── Ciphertext must never reach the logs ──

async def test_sql_parameters_are_redacted_before_logging():
    """A failed statement stringifies its bound parameters, and for a journal write
    those parameters *are* the ciphertext. Nothing may log them (contract §3)."""
    from app.middleware.error_handlers import redact_sql_parameters

    blob = "c2VhbGVkIHByb3NlIHRoZSBzZXJ2ZXIgY2Fubm90IHJlYWQ="
    raw = (
        "(asyncpg.exceptions.DataError) invalid input\n"
        "[SQL: INSERT INTO journal_entries (id, ciphertext, nonce) VALUES ($1, $2, $3)]\n"
        f"[parameters: ('abc', '{blob}', 'bm9uY2U=')]\n"
        "(Background on this error at: https://sqlalche.me/e/20/9h9h)"
    )
    out = redact_sql_parameters(raw)
    assert blob not in out
    assert "[parameters: REDACTED]" in out
    assert "INSERT INTO journal_entries" in out          # the SQL is still useful
    assert "Background on this error" in out

    # Unparseable layouts truncate rather than gamble.
    weird = f"boom [parameters: {blob} and then some ] trailing"
    assert blob not in redact_sql_parameters(weird)
    # And untouched text passes through unchanged.
    assert redact_sql_parameters("plain error") == "plain error"


async def test_a_failed_journal_write_does_not_log_ciphertext(client, caplog):
    """End to end: force a DB error on a journal insert and read the log."""
    import logging
    from unittest.mock import patch

    from sqlalchemy.exc import DataError

    h = await _journaller(client, "jnolog")
    blob = base64.b64encode(b"the actual secret prose").decode()

    boom = DataError(
        "INSERT INTO journal_entries (id, ciphertext) VALUES ($1, $2)",
        {"ciphertext": blob},
        Exception("invalid input"),
    )
    # Starlette re-raises server exceptions under ASGITransport, but only *after*
    # our handler has logged — which is the thing under test here.
    with caplog.at_level(logging.ERROR, logger="bibliome"), \
            patch("app.routers.journal.create_entry", side_effect=boom), \
            pytest.raises(DataError):
        await client.post("/api/journal/entries", json={
            "entry_date": "2026-07-30", "ciphertext": blob, "nonce": _b64(12),
        }, headers=h)

    logged = "\n".join(
        r.getMessage() for r in caplog.records if r.name.startswith("bibliome")
    )
    assert "Unhandled exception" in logged, "expected the failure to be logged at all"
    assert blob not in logged
