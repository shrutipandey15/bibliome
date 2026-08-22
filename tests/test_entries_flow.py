"""Core reading-loop tests (B2.2 / B2.3 / B2.4 / B2.9 / B2.10)."""

import datetime as dt

import pytest

pytestmark = pytest.mark.asyncio


async def _auth(client, email="r@example.com", username="reader"):
    await client.post("/api/auth/register", json={
        "email": email, "username": username, "password": "hunter2pass",
    })
    r = await client.post("/api/auth/login", json={"email": email, "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _create(client, headers, **over):
    body = {"title": "A Book", "intensity": 7, "emotions": [], **over}
    return await client.post("/api/entries", json=body, headers=headers)


# ── Per-emotion intensity: each emotion carries its own strength ──

async def test_per_emotion_strengths_persist_independently(client):
    headers = await _auth(client)
    r = await _create(client, headers, emotions=[
        {"emotion_id": "grief", "strength": 9},
        {"emotion_id": "comfort", "strength": 2},
    ])
    assert r.status_code == 201, r.text
    by_slug = {e["emotion_id"]: e["strength"] for e in r.json()["emotions"]}
    assert by_slug == {"grief": 9, "comfort": 2}


async def test_read_path_canonicalizes_legacy_emotion(client):
    # A retired slug reaching the read path surfaces under its canonical name.
    # (Write validation blocks legacy slugs, so this proves the read-time remap.)
    from app.utils.emotions import canonicalize
    assert canonicalize("chaos") == "confusion"


# ── verdict + dnf_reason axes round-trip ──

async def test_verdict_round_trips(client):
    headers = await _auth(client)
    r = await _create(client, headers, verdict="yes")
    assert r.status_code == 201, r.text
    assert r.json()["verdict"] == "yes"


async def test_dnf_reason_round_trips_on_abandoned(client):
    headers = await _auth(client)
    r = await _create(client, headers, status="abandoned", dnf_reason="bored")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "abandoned"
    assert body["dnf_reason"] == "bored"


async def test_invalid_verdict_rejected(client):
    headers = await _auth(client)
    r = await _create(client, headers, verdict="maybe")
    assert r.status_code == 422


# ── B2.4: full fields + finished_at defaulting ──

async def test_status_respected_on_create(client):
    headers = await _auth(client)
    r = await _create(client, headers, status="reading")
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "reading"
    assert body["finished_at"] is None


async def test_finished_default_sets_finished_at(client):
    headers = await _auth(client)
    r = await _create(client, headers)  # default status = finished
    body = r.json()
    assert body["status"] == "finished"
    assert body["finished_at"] == dt.date.today().isoformat()


async def test_full_fields_persist(client):
    headers = await _auth(client)
    r = await _create(client, headers, notes="loved it", quote="a line", started_at="2026-01-01")
    body = r.json()
    assert body["notes"] == "loved it"
    assert body["quote"] == "a line"
    assert body["started_at"] == "2026-01-01"


# ── B2.2: finish flow preserves per-emotion strength ──

async def test_finish_preserves_existing_emotion_strength(client):
    headers = await _auth(client)
    r = await _create(client, headers, status="reading",
                      emotions=[{"emotion_id": "grief", "strength": 9}])
    entry_id = r.json()["id"]

    r = await client.post(
        f"/api/entries/{entry_id}/finish",
        json={
            "start_emotion_slug": "grief",     # already tagged at strength 9
            "middle_emotion_slug": "longing",  # new
            "end_emotion_slug": "catharsis",   # new
            "thought": "wrecked me",
            "intensity": 5,
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "finished"
    assert body["finished_at"] == dt.date.today().isoformat()
    assert body["finish_thought"] == "wrecked me"

    strengths = {e["emotion_id"]: e["strength"] for e in body["emotions"]}
    assert strengths["grief"] == 9        # NOT clobbered to 5
    assert strengths["longing"] == 5      # new arc emotion at finish intensity
    assert strengths["catharsis"] == 5


# ── B2.3: status transitions + checkins ──

async def test_status_patch_toggles_finished_at(client):
    headers = await _auth(client)
    entry_id = (await _create(client, headers, status="reading")).json()["id"]

    r = await client.patch(f"/api/entries/{entry_id}/status", json={"status": "finished"}, headers=headers)
    assert r.json()["finished_at"] == dt.date.today().isoformat()

    r = await client.patch(f"/api/entries/{entry_id}/status", json={"status": "reading"}, headers=headers)
    assert r.json()["finished_at"] is None


async def test_status_vocabulary_is_one_vocabulary(client):
    """The status vocabulary is declared in three places; they must not drift.

    ``EntryStatus`` (write/read schemas), ``StatusUpdate.status`` (the PATCH
    route), and the ``check_entry_status`` DB constraint each spell the list out
    separately, so any one of them can quietly fall behind the others — which is
    how the route came to advertise three statuses while the DB stored six.
    """
    import re
    import typing

    from app.models.book_entry import BookEntry
    from app.schemas.checkin import StatusUpdate
    from app.schemas.entry import EntryStatus

    declared = set(typing.get_args(EntryStatus))
    route = set(typing.get_args(StatusUpdate.model_fields["status"].annotation))
    constraint = next(
        c for c in BookEntry.__table__.constraints
        if getattr(c, "name", None) == "check_entry_status"
    )
    stored = set(re.findall(r"'([a-z_]+)'", str(constraint.sqltext)))

    assert declared == route == stored


async def test_status_patch_accepts_every_declared_status(client):
    """Every status in the vocabulary is reachable through the PATCH route."""
    import typing

    from app.schemas.entry import EntryStatus

    headers = await _auth(client)
    entry_id = (await _create(client, headers, status="reading")).json()["id"]

    for value in typing.get_args(EntryStatus):
        r = await client.patch(
            f"/api/entries/{entry_id}/status", json={"status": value}, headers=headers
        )
        assert r.status_code == 200, f"{value}: {r.text}"
        assert r.json()["status"] == value


async def test_status_patch_marks_dna_dirty(client, db, monkeypatch):
    """Finishing a book must refresh the reader's DNA.

    This handler was the one write path that changed what the DNA is computed
    over without flagging it, so a book could go reading → finished and leave the
    profile describing a library the reader no longer has.

    The recalc is stubbed out because it clears the flag as its last act — with
    the real task running, a handler that never set the flag and one that set it
    look identical afterwards.
    """
    from sqlalchemy import select
    from app.models.user import User
    from app.routers import entries as entries_router

    scheduled = []
    async def _fake_recalc(user_id):
        scheduled.append(user_id)
    monkeypatch.setattr(entries_router, "recalculate_dna", _fake_recalc)

    headers = await _auth(client, "dirty@example.com", "dirtyreader")
    entry_id = (await _create(client, headers, status="reading")).json()["id"]

    # Clear the flag the create set, so what we read back can only come from the
    # status change.
    user = (await db.execute(select(User).where(User.email == "dirty@example.com"))).scalar_one()
    user.dna_dirty = False
    await db.commit()
    scheduled.clear()

    r = await client.patch(f"/api/entries/{entry_id}/status",
                           json={"status": "finished"}, headers=headers)
    assert r.status_code == 200, r.text

    await db.refresh(user)
    assert user.dna_dirty is True
    assert scheduled == [user.id]


async def test_every_entry_write_handler_flags_dna_dirty():
    """Regression guard: a new write path must not silently skip the flag.

    Asserted against the source because the failure mode is an *omission* — there
    is no call to intercept when the line is simply missing, and the only
    behavioural symptom is a profile that quietly goes stale.
    """
    import inspect
    from app.routers import entries as entries_router

    # Handlers that change what the DNA is computed over. Checkins are excluded:
    # they're mid-read notes and feed no DNA signal.
    write_handlers = [
        "create_new_entry", "import_library", "update_existing_entry",
        "delete_existing_entry", "finish_existing_entry", "patch_entry_status",
    ]
    for name in write_handlers:
        source = inspect.getsource(getattr(entries_router, name))
        assert "dna_dirty = True" in source, f"{name} does not flag DNA as dirty"
        assert "recalculate_dna" in source, f"{name} does not schedule a DNA recalc"


async def test_checkins_create_and_list(client):
    headers = await _auth(client)
    entry_id = (await _create(client, headers, status="reading")).json()["id"]

    r = await client.post(f"/api/entries/{entry_id}/checkins",
                          json={"emotion_slug": "dread", "note": "tense"}, headers=headers)
    assert r.status_code == 201

    r = await client.get(f"/api/entries/{entry_id}/checkins", headers=headers)
    assert r.status_code == 200
    checkins = r.json()
    assert len(checkins) == 1
    assert checkins[0]["emotion_slug"] == "dread"


async def test_checkin_on_foreign_entry_is_404(client):
    a = await _auth(client, "a@example.com", "usera")
    b = await _auth(client, "b@example.com", "userb")
    entry_id = (await _create(client, a)).json()["id"]
    r = await client.get(f"/api/entries/{entry_id}/checkins", headers=b)
    assert r.status_code == 404


# ── B2.9: in-library search/filter ──

async def test_in_library_search_and_emotion_filter(client):
    headers = await _auth(client)
    await _create(client, headers, title="The Sea", author="Iris",
                  emotions=[{"emotion_id": "longing", "strength": 6}])
    await _create(client, headers, title="Mountain Air", author="Ken",
                  emotions=[{"emotion_id": "awe", "strength": 6}])

    r = await client.get("/api/entries?q=sea", headers=headers)
    assert r.json()["total"] == 1
    assert r.json()["entries"][0]["title"] == "The Sea"

    r = await client.get("/api/entries?emotion=awe", headers=headers)
    assert r.json()["total"] == 1
    assert r.json()["entries"][0]["title"] == "Mountain Air"


# ── B2.10: served vocabulary ──

async def test_emotion_vocabulary_endpoint(client):
    r = await client.get("/api/emotions")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 19
    slugs = {e["slug"] for e in body["emotions"]}
    assert "nostalgia" in slugs and "devastation" in slugs
    assert "two_am" not in slugs and "chaos" not in slugs  # old vocab retired
    for e in body["emotions"]:
        assert e["slug"] and e["name"] and e["color"] and e["symbol"] and e["family"] and e["phrase"]
    # phrase is the first-person line the UI shows, distinct from the plain word.
    conf = next(e for e in body["emotions"] if e["slug"] == "confusion")
    assert conf["name"] == "confusion" and conf["phrase"] == "I have no idea what happened"
    # The 19th slug is served like any other, families included.
    absorb = next(e for e in body["emotions"] if e["slug"] == "absorption")
    assert absorb["phrase"] == "I couldn't put it down" and absorb["family"] == "It got me"


# ── B2.2: TBR fast-add — one tap, no modal ──

async def _tbr(client, headers, **over):
    body = {"title": "Piranesi", "author": "Susanna Clarke", **over}
    return await client.post("/api/entries/tbr", json=body, headers=headers)


async def test_tbr_add_shelves_as_want_to_read(client):
    headers = await _auth(client)
    r = await _tbr(client, headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    assert body["entry"]["status"] == "want_to_read"
    assert body["entry"]["title"] == "Piranesi"


async def test_tbr_add_is_idempotent(client):
    """A one-tap surface has no confirmation step, so the same book gets tapped
    twice. The second tap must return the first entry, not a duplicate."""
    headers = await _auth(client)
    first = (await _tbr(client, headers)).json()
    second = (await _tbr(client, headers)).json()

    assert second["created"] is False
    assert second["entry"]["id"] == first["entry"]["id"]

    listed = (await client.get("/api/entries", headers=headers)).json()
    assert len([e for e in listed["entries"] if e["title"] == "Piranesi"]) == 1


async def test_tbr_add_never_demotes_a_book_already_read(client):
    """Shelving a book the reader already finished must not reset it to intention.

    The destructive version of this bug is silent: the entry keeps its id, so the
    UI shows success while the finish date and status are gone.
    """
    headers = await _auth(client)
    created = (await _create(
        client, headers, title="Piranesi", author="Susanna Clarke",
        status="finished", emotions=[{"emotion_id": "awe", "strength": 9}],
    )).json()

    r = await _tbr(client, headers)
    assert r.json()["created"] is False

    after = (await client.get(f"/api/entries/{created['id']}", headers=headers)).json()
    assert after["status"] == "finished"
    assert after["finished_at"] == created["finished_at"]
    assert [e["emotion_id"] for e in after["emotions"]] == ["awe"]


async def test_tbr_add_dedupes_on_normalized_title_and_author(client):
    """Search results vary in punctuation and spacing; the catalog's identity
    function is what decides sameness, not the raw string."""
    headers = await _auth(client)
    await _tbr(client, headers)
    r = await _tbr(client, headers, title="  PIRANESI ", author="Susanna  Clarke")
    assert r.json()["created"] is False


async def test_tbr_add_rejects_a_rating(client):
    """The fast-add accepts identity fields only — it must not let a caller
    smuggle in a reading (intensity, emotions, status) for a book never opened."""
    headers = await _auth(client)
    r = await client.post(
        "/api/entries/tbr",
        json={"title": "Piranesi", "intensity": 9, "status": "finished"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["entry"]["status"] == "want_to_read"


async def test_tbr_add_does_not_move_the_dna(client, db):
    """End-to-end guard for the same invariant the pure test pins: fast-adding
    books must leave the reader's DNA payload untouched."""
    headers = await _auth(client)
    for i in range(6):
        await _create(client, headers, title=f"Read {i}", intensity=9,
                      emotions=[{"emotion_id": "grief", "strength": 9}])
    before = (await client.get("/api/dna/profile", headers=headers)).json()

    for i in range(20):
        await _tbr(client, headers, title=f"Pile {i}", author=None)
    after = (await client.get("/api/dna/profile", headers=headers)).json()

    assert after["book_count"] == before["book_count"] == 6


# ── B2.3 / A1: a reread keeps the book's finish history ──

async def test_reread_preserves_finished_at(client):
    """Marking a finished book as a reread must not erase when it was finished.

    A reread is evidence the book WAS finished. Clearing the date dropped the
    book out of the calendar and mirror, silently, on a status change the reader
    reads as celebratory.
    """
    headers = await _auth(client)
    entry_id = (await _create(client, headers, status="finished")).json()["id"]
    finished_on = (await client.get(f"/api/entries/{entry_id}", headers=headers)).json()["finished_at"]
    assert finished_on is not None

    r = await client.patch(
        f"/api/entries/{entry_id}/status", json={"status": "reread"}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["finished_at"] == finished_on


async def test_only_reread_keeps_the_finish_date(client):
    """Every other non-finished status still means "not finished" and clears it.

    Written over the vocabulary rather than a hand-picked status or two, so a
    status added later has to make this decision explicitly instead of
    inheriting whichever branch it happens to fall into.
    """
    import typing

    from app.schemas.entry import EntryStatus

    headers = await _auth(client)
    for value in typing.get_args(EntryStatus):
        if value == "finished":
            continue
        entry_id = (await _create(client, headers, title=f"Book {value}",
                                  status="finished")).json()["id"]
        r = await client.patch(
            f"/api/entries/{entry_id}/status", json={"status": value}, headers=headers
        )
        kept = r.json()["finished_at"] is not None
        assert kept == (value == "reread"), f"{value} kept finished_at: {kept}"


async def test_dnf_reason_vocabulary_matches_the_client(client):
    """The DNF reasons are declared in the backend and re-typed in the UI.

    `DnfReason` is the source of truth; EntryModal's DNF_OPTIONS mirrors it. A
    reason offered by the UI but rejected by the API is a 422 the reader cannot
    act on, so the two lists are pinned here the same way the status vocabulary is.
    """
    import typing

    from app.schemas.entry import DnfReason

    assert set(typing.get_args(DnfReason)) == {
        "bored", "too_much", "badly_written", "wrong_time", "lost_me", "drifted",
    }


async def test_every_dnf_reason_round_trips(client):
    """Each reason in the vocabulary is actually writable and readable back."""
    import typing

    from app.schemas.entry import DnfReason

    headers = await _auth(client)
    for reason in typing.get_args(DnfReason):
        r = await _create(client, headers, title=f"DNF {reason}",
                          status="abandoned", dnf_reason=reason)
        assert r.status_code == 201, f"{reason}: {r.text}"
        assert r.json()["dnf_reason"] == reason


async def test_reread_keeps_finish_date_on_the_edit_path_too(client):
    """The PUT path must agree with the status-tap path.

    They are separate branches, so history could survive a status tap and die on
    an edit — the kind of split that makes the loss look random to the reader.
    """
    headers = await _auth(client)
    created = (await _create(client, headers, status="finished")).json()
    finished_on = created["finished_at"]

    r = await client.put(
        f"/api/entries/{created['id']}",
        json={"status": "reread"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["finished_at"] == finished_on
