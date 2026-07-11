"""Echo + safety tests (B3.14) — the Phase 3 GATE properties."""

import pytest

pytestmark = pytest.mark.asyncio


async def _user(client, name):
    await client.post("/api/auth/register", json={
        "email": f"{name}@example.com", "username": name, "password": "hunter2pass",
    })
    r = await client.post("/api/auth/login", json={"email": f"{name}@example.com", "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _post_echo(client, headers, **over):
    body = {"body": "This book undid me.", "book_title": "Piranesi", "primary_emotion": "awe", **over}
    return await client.post("/api/echoes", json=body, headers=headers)


# ── Echo core ──

async def test_post_book_anchored_echo_appears_in_feed(client):
    h = await _user(client, "alice")
    r = await _post_echo(client, h)
    assert r.status_code == 201, r.text
    assert r.json()["held_for_review"] is False

    r = await client.get("/api/echoes/feed", headers=h)
    assert r.status_code == 200
    feed = r.json()
    assert len(feed["echoes"]) == 1
    assert feed["caught_up"] is True  # feed ends


async def test_echo_requires_an_anchor(client):
    h = await _user(client, "bob")
    r = await client.post("/api/echoes", json={"body": "no anchor here"}, headers=h)
    assert r.status_code == 400


async def test_feed_payload_has_no_counts(client):
    h = await _user(client, "carol")
    await _post_echo(client, h)
    r = await client.get("/api/echoes/feed", headers=h)
    card = r.json()["echoes"][0]
    for key in card:
        assert not any(bad in key.lower() for bad in ("count", "like", "reaction", "replies", "score", "karma"))


async def test_reply_shows_in_thread(client):
    a = await _user(client, "dave")
    b = await _user(client, "erin")
    echo_id = (await _post_echo(client, a)).json()["echo"]["id"]
    r = await client.post(f"/api/echoes/{echo_id}/replies", json={"body": "me too"}, headers=b)
    assert r.status_code == 201
    r = await client.get(f"/api/echoes/{echo_id}", headers=a)
    assert len(r.json()["replies"]) == 1
    assert r.json()["replies"][0]["handle"] == "erin"


# ── Block / mute enforcement ──

async def test_block_hides_content_bidirectionally(client):
    a = await _user(client, "fay")
    b = await _user(client, "gus")
    await _post_echo(client, b, body="gus was here")

    # a blocks gus
    r = await client.post("/api/social/blocks", json={"handle": "gus"}, headers=a)
    assert r.status_code == 204

    # gus's echo is gone from a's feed
    r = await client.get("/api/echoes/feed", headers=a)
    assert all(e["handle"] != "gus" for e in r.json()["echoes"])

    # and a's echoes are gone from gus's feed (bidirectional)
    await _post_echo(client, a, body="fay was here")
    r = await client.get("/api/echoes/feed", headers=b)
    assert all(e["handle"] != "fay" for e in r.json()["echoes"])


async def test_mute_is_one_way(client):
    a = await _user(client, "hana")
    b = await _user(client, "ivan")
    await _post_echo(client, b, body="ivan speaks")
    await client.post("/api/social/mutes", json={"handle": "ivan"}, headers=a)

    r = await client.get("/api/echoes/feed", headers=a)
    assert all(e["handle"] != "ivan" for e in r.json()["echoes"])

    # ivan is unaffected — hana's echo still visible to ivan
    await _post_echo(client, a, body="hana speaks")
    r = await client.get("/api/echoes/feed", headers=b)
    assert any(e["handle"] == "hana" for e in r.json()["echoes"])


# ── Report auto-throttle ──

async def test_report_threshold_holds_echo(client):
    author = await _user(client, "jill")
    echo_id = (await _post_echo(client, author, body="something reported")).json()["echo"]["id"]

    for name in ("rep1", "rep2", "rep3"):
        reporter = await _user(client, name)
        r = await client.post(f"/api/echoes/{echo_id}/report", json={"category": "spam"}, headers=reporter)
        assert r.status_code == 202

    # After 3 weighted reports the echo is held → gone from the feed.
    viewer = await _user(client, "viewer")
    r = await client.get("/api/echoes/feed", headers=viewer)
    assert all(e["id"] != echo_id for e in r.json()["echoes"])


# ── Crisis path ──

async def test_self_harm_routes_to_support_not_punishment(client):
    h = await _user(client, "kate")
    r = await client.post("/api/echoes", json={
        "body": "I want to kill myself after reading this",
        "primary_emotion": "devastation",
    }, headers=h)
    assert r.status_code == 201
    body = r.json()
    assert body["held_for_review"] is True
    assert body["crisis"] is not None
    assert "988" in str(body["crisis"]["resources"])


# ── Private reactions ──

async def test_reactions_private_to_author(client):
    a = await _user(client, "liam")
    b = await _user(client, "mira")
    echo_id = (await _post_echo(client, a)).json()["echo"]["id"]

    r = await client.post(f"/api/echoes/{echo_id}/react", json={"kind": "felt_this", "on": True}, headers=b)
    assert r.status_code == 204

    # author can read the aggregate
    r = await client.get(f"/api/echoes/{echo_id}/reactions", headers=a)
    assert r.status_code == 200
    assert r.json().get("felt_this") == 1

    # a non-author cannot
    r = await client.get(f"/api/echoes/{echo_id}/reactions", headers=b)
    assert r.status_code == 403


# ── Handle change ──

async def test_moderation_queue_and_dismiss_restores(client, db):
    from sqlalchemy import update
    from app.models.user import User

    author = await _user(client, "opal")
    echo_id = (await _post_echo(client, author, body="held then restored")).json()["echo"]["id"]
    for name in ("mrep1", "mrep2", "mrep3"):
        reporter = await _user(client, name)
        await client.post(f"/api/echoes/{echo_id}/report", json={"category": "spam"}, headers=reporter)

    # promote an admin
    await _user(client, "modadmin")
    await db.execute(update(User).where(User.username == "modadmin").values(is_admin=True))
    await db.commit()
    admin = {"Authorization": (await client.post("/api/auth/login", json={
        "email": "modadmin@example.com", "password": "hunter2pass"})).json()["access_token"]}
    admin = {"Authorization": f"Bearer {admin['Authorization']}"}

    r = await client.get("/api/admin/moderation/queue", headers=admin)
    assert r.status_code == 200
    assert any(item["target_id"] == echo_id for item in r.json())

    r = await client.post("/api/admin/moderation/resolve",
                          json={"target_type": "echo", "target_id": echo_id, "action": "dismiss"},
                          headers=admin)
    assert r.status_code == 200

    # dismissed → echo restored to the feed
    r = await client.get("/api/echoes/feed", headers=author)
    assert any(e["id"] == echo_id for e in r.json()["echoes"])


async def test_handle_change_and_cooldown(client):
    h = await _user(client, "nora")
    r = await client.patch("/api/user/handle", json={"handle": "nova"}, headers=h)
    assert r.status_code == 200
    assert r.json()["handle"] == "nova"
    # Second change is rate-limited.
    r = await client.patch("/api/user/handle", json={"handle": "nyx"}, headers=h)
    assert r.status_code == 400
