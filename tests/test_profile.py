"""Profile + collections tests (Feature 2). Focus: server-side visibility/block
enforcement (this is where leaks happen) and milestone honesty."""

import pytest

pytestmark = pytest.mark.asyncio


async def _user(client, name):
    await client.post("/api/auth/register", json={
        "email": f"{name}@example.com", "username": name, "password": "hunter2pass",
    })
    r = await client.post("/api/auth/login", json={"email": f"{name}@example.com", "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _log(client, headers, title="A Book", emotion="grief"):
    return await client.post("/api/entries", json={
        "title": title, "intensity": 7, "emotions": [{"emotion_id": emotion, "strength": 8}],
    }, headers=headers)


# ── Self profile ──

async def test_me_profile_composes(client):
    h = await _user(client, "selfie")
    await _log(client, h, "First Book")
    r = await client.get("/api/me/profile", headers=h)
    assert r.status_code == 200
    p = r.json()
    assert p["is_self"] is True
    assert p["handle"] == "selfie"
    assert p["book_count"] == 1
    assert "email" not in p  # never leak real data


async def test_patch_profile_bio_and_visibility(client):
    h = await _user(client, "editme")
    r = await client.patch("/api/me/profile", json={
        "bio": "  just a reader  ", "profile_visibility": "public",
    }, headers=h)
    assert r.status_code == 200
    assert r.json()["bio"] == "just a reader"  # sanitized/trimmed
    assert r.json()["profile_visibility"] == "public"


# ── Other-profile visibility enforcement ──

async def test_private_profile_shows_minimal_card_to_stranger(client):
    owner = await _user(client, "privo")
    await _log(client, owner, "Secret Read")
    stranger = await _user(client, "nosy")

    r = await client.get("/api/profile/privo", headers=stranger)
    assert r.status_code == 200
    p = r.json()
    assert p["restricted"] is True
    assert "book_count" not in p and "recent" not in p  # no data leak


async def test_public_profile_visible_to_members(client):
    owner = await _user(client, "openo")
    await _log(client, owner, "Public Read")
    await client.patch("/api/me/profile", json={"profile_visibility": "public"}, headers=owner)
    viewer = await _user(client, "watcher")

    r = await client.get("/api/profile/openo", headers=viewer)
    assert r.status_code == 200
    assert r.json()["restricted"] is False
    assert r.json()["book_count"] == 1


async def test_blocked_viewer_gets_404(client):
    owner = await _user(client, "blocko")
    await client.patch("/api/me/profile", json={"profile_visibility": "public"}, headers=owner)
    baddie = await _user(client, "baddie")
    # owner blocks baddie
    await client.post("/api/social/blocks", json={"handle": "baddie"}, headers=owner)

    r = await client.get("/api/profile/blocko", headers=baddie)
    assert r.status_code == 404  # appears not to exist


# ── Collections ──

async def test_collections_crud_and_visibility_filtering(client):
    owner = await _user(client, "curator")
    await client.patch("/api/me/profile", json={"profile_visibility": "public"}, headers=owner)
    entry_id = (await _log(client, owner, "Collected Book")).json()["id"]

    # public + private collection
    pub = (await client.post("/api/collections", json={"title": "Faves", "visibility": "public"}, headers=owner)).json()
    priv = (await client.post("/api/collections", json={"title": "Secret", "visibility": "private"}, headers=owner)).json()
    await client.post(f"/api/collections/{pub['id']}/items", json={"entry_id": entry_id}, headers=owner)
    await client.post(f"/api/collections/{priv['id']}/items", json={"entry_id": entry_id}, headers=owner)

    # owner sees both
    me = (await client.get("/api/me/profile", headers=owner)).json()
    assert {c["title"] for c in me["collections"]} == {"Faves", "Secret"}

    # a stranger sees only the public one, with the book card
    viewer = await _user(client, "peeker")
    them = (await client.get("/api/profile/curator", headers=viewer)).json()
    titles = {c["title"] for c in them["collections"]}
    assert titles == {"Faves"}
    faves = next(c for c in them["collections"] if c["title"] == "Faves")
    assert faves["books"][0]["title"] == "Collected Book"
    # private notes never surface on a collection card
    assert "notes" not in faves["books"][0]


async def test_cannot_add_foreign_book_to_collection(client):
    owner = await _user(client, "ownr")
    other = await _user(client, "othr")
    foreign_entry = (await _log(client, other, "Not Yours")).json()["id"]
    coll = (await client.post("/api/collections", json={"title": "Mine"}, headers=owner)).json()
    r = await client.post(f"/api/collections/{coll['id']}/items", json={"entry_id": foreign_entry}, headers=owner)
    assert r.status_code == 400


# ── Milestones honesty ──

async def test_milestones_are_substance_based_not_volume(client):
    h = await _user(client, "milestoner")
    for i in range(5):
        await _log(client, h, f"Book {i}", emotion="grief")
    p = (await client.get("/api/me/profile", headers=h)).json()
    kinds = {m["kind"] for m in p["milestones"]}
    assert "first_book" in kinds
    # No volume/streak milestone exists at all.
    labels = " ".join(m["label"].lower() for m in p["milestones"])
    assert "streak" not in labels
    assert not any(str(n) in labels for n in ("100", "50", "streak"))
