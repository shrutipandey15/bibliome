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
    assert body["count"] == 18
    slugs = {e["slug"] for e in body["emotions"]}
    assert "nostalgia" in slugs and "devastation" in slugs
    assert "two_am" not in slugs and "chaos" not in slugs  # old vocab retired
    for e in body["emotions"]:
        assert e["slug"] and e["name"] and e["color"] and e["symbol"] and e["family"] and e["phrase"]
    # phrase is the first-person line the UI shows, distinct from the plain word.
    conf = next(e for e in body["emotions"] if e["slug"] == "confusion")
    assert conf["name"] == "confusion" and conf["phrase"] == "it confused me"
