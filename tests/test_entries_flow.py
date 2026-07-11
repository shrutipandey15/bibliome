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
    assert body["count"] == 13
    slugs = {e["slug"] for e in body["emotions"]}
    assert "two_am" in slugs and "devastation" in slugs
    for e in body["emotions"]:
        assert e["slug"] and e["name"] and e["color"] and e["symbol"]
