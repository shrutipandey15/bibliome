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


async def test_milestones_carry_dates_and_the_ones_not_yet_reached(client):
    h = await _user(client, "dater")
    await _log(client, h, "Only Book")
    ms = {m["kind"]: m for m in (await client.get("/api/me/profile", headers=h)).json()["milestones"]}

    assert ms["first_book"]["achieved"] is True
    assert ms["first_book"]["achieved_at"]  # dated by the entry that earned it
    # The unreached ones come back too, so the study can show what is still ahead.
    assert ms["full_spectrum"]["achieved"] is False
    assert ms["full_spectrum"]["achieved_at"] is None


# ── Figures: countable by hand, or absent ──

async def test_figures_count_registers_intensity_and_books_set_down(client):
    h = await _user(client, "counter")
    await _log(client, h, "One", emotion="grief")
    await _log(client, h, "Two", emotion="awe")
    await client.post("/api/entries", json={
        "title": "Put Down", "intensity": 3, "status": "abandoned",
        "emotions": [{"emotion_id": "grief", "strength": 4}],
    }, headers=h)

    p = (await client.get("/api/me/profile", headers=h)).json()
    assert p["registers_felt"] == 2          # grief + awe, counted once each
    assert p["set_down"] == 1
    assert p["avg_intensity"] == round((7 + 7 + 3) / 3, 1)
    assert p["member_since"]


async def test_avg_intensity_is_null_on_an_empty_shelf(client):
    h = await _user(client, "emptyshelf")
    p = (await client.get("/api/me/profile", headers=h)).json()
    assert p["avg_intensity"] is None  # never a fabricated zero
    assert p["registers_felt"] == 0


# ── Margins: the lines you kept ──

async def _log_quoted(client, headers, title, quote):
    return await client.post("/api/entries", json={
        "title": title, "intensity": 6, "quote": quote,
        "emotions": [{"emotion_id": "awe", "strength": 7}],
    }, headers=headers)


async def test_margins_keep_the_first_quote_per_book(client):
    h = await _user(client, "quoter")
    await _log_quoted(client, h, "Gilead", "the line I kept first")
    await _log_quoted(client, h, "gilead ", "the reread line")  # same book, read again
    await _log_quoted(client, h, "Middlemarch", "another book entirely")
    await _log(client, h, "No Quote Here")

    margins = (await client.get("/api/me/profile", headers=h)).json()["margins"]
    assert [m["title"] for m in margins] == ["Middlemarch", "Gilead"]  # newest kept first
    gilead = next(m for m in margins if m["title"] == "Gilead")
    assert gilead["quote"] == "the line I kept first"  # the reread does not displace it
    assert all(m["quote"] for m in margins)  # a book with no quote is simply absent


async def test_margins_never_reach_another_reader(client):
    owner = await _user(client, "privatequotes")
    await client.patch("/api/me/profile", json={"profile_visibility": "public"}, headers=owner)
    await _log_quoted(client, owner, "Gilead", "something I only wrote for me")

    viewer = await _user(client, "reader2")
    them = (await client.get("/api/profile/privatequotes", headers=viewer)).json()
    assert them["margins"] == []
    assert "something I only wrote for me" not in str(them)


async def test_emotion_counts_are_this_readers_own_tally(client):
    h = await _user(client, "fingerprint")
    await _log(client, h, "One", emotion="grief")
    await _log(client, h, "Two", emotion="grief")
    await _log(client, h, "Three", emotion="awe")

    counts = (await client.get("/api/me/profile", headers=h)).json()["emotion_counts"]
    assert counts["grief"] == 2 and counts["awe"] == 1
    # Registers never reached are simply absent — the client draws them as blanks.
    assert "rage" not in counts


async def test_archetype_share_is_withheld_until_it_would_mean_something(client):
    h = await _user(client, "loner")
    await _log(client, h, "A Book")
    # A handful of readers cannot support a percentage; the card omits the line.
    assert (await client.get("/api/me/profile", headers=h)).json()["archetype_share"] is None


# ── Progress: how far in, only when the reader said ──

async def test_progress_is_null_until_stated_and_clears_when_the_book_closes(client):
    h = await _user(client, "progresso")
    entry = (await client.post("/api/entries", json={
        "title": "Open Book", "intensity": 5, "status": "reading",
        "emotions": [{"emotion_id": "awe", "strength": 6}],
    }, headers=h)).json()
    assert entry["progress"] is None  # "hasn't said" is not 0%

    entry_id = entry["id"]
    r = await client.put(f"/api/entries/{entry_id}", json={"progress": 41}, headers=h)
    assert r.json()["progress"] == 41
    assert (await client.get("/api/me/profile", headers=h)).json()["now_reading"][0]["progress"] == 41

    # Finishing it drops the figure: the status is the answer now.
    r = await client.put(f"/api/entries/{entry_id}", json={"status": "finished"}, headers=h)
    assert r.json()["progress"] is None


async def test_updating_only_a_scalar_field_actually_saves_it(client):
    """Regression: `expire()` before `flush()` threw away any update that didn't
    also touch title/author/isbn or emotions — the two paths that happened to
    trip an autoflush. A lone {"notes": ...} returned 200 and saved nothing."""
    h = await _user(client, "scalaronly")
    entry_id = (await _log(client, h, "Quiet Edit")).json()["id"]

    await client.put(f"/api/entries/{entry_id}", json={"notes": "kept this"}, headers=h)
    r = await client.get(f"/api/entries/{entry_id}", headers=h)
    assert r.json()["notes"] == "kept this"


async def test_progress_outside_0_100_is_rejected(client):
    h = await _user(client, "overshoot")
    r = await client.post("/api/entries", json={
        "title": "Too Far", "intensity": 5, "status": "reading", "progress": 140,
        "emotions": [{"emotion_id": "awe", "strength": 6}],
    }, headers=h)
    assert r.status_code == 422


async def test_progress_is_not_kept_on_a_book_that_was_never_open(client):
    h = await _user(client, "finisher")
    r = await client.post("/api/entries", json={
        "title": "Already Done", "intensity": 5, "status": "finished", "progress": 80,
        "emotions": [{"emotion_id": "awe", "strength": 6}],
    }, headers=h)
    assert r.json()["progress"] is None


async def test_checkin_notes_ride_only_on_your_own_now_reading(client):
    owner = await _user(client, "checker")
    await client.patch("/api/me/profile", json={"profile_visibility": "public"}, headers=owner)
    entry_id = (await client.post("/api/entries", json={
        "title": "In Progress", "intensity": 5, "status": "reading",
        "emotions": [{"emotion_id": "awe", "strength": 6}],
    }, headers=owner)).json()["id"]
    await client.post(f"/api/entries/{entry_id}/checkins", json={
        "emotion_slug": "dread", "note": "nineteen nights in",
    }, headers=owner)

    mine = (await client.get("/api/me/profile", headers=owner)).json()
    assert mine["now_reading"][0]["last_checkin"]["note"] == "nineteen nights in"

    viewer = await _user(client, "reader3")
    theirs = (await client.get("/api/profile/checker", headers=viewer)).json()
    assert "last_checkin" not in theirs["now_reading"][0]
    assert "nineteen nights" not in str(theirs)
