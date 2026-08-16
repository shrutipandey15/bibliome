"""Web Push (add-on to #6).

Push is a courtesy layer on top of notifications that already exist. Most of
this file is about it staying that way: never breaking the write that caused it,
never re-deciding who may be notified, and never leaking content to a lock screen.
"""

import uuid

import pytest

pytestmark = pytest.mark.asyncio

SUB = {
    "endpoint": "https://push.example.com/abc123",
    "keys": {"p256dh": "BPk3S9m" + "x" * 20, "auth": "c2VjcmV0" + "y" * 8},
}


async def _auth(client, email="p@example.com", username="pusher"):
    await client.post("/api/auth/register", json={
        "email": email, "username": username, "password": "hunter2pass",
    })
    r = await client.post("/api/auth/login", json={"email": email, "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _enable(monkeypatch):
    """Turn push on without real keys — nothing here actually sends."""
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "VAPID_PUBLIC_KEY", "test-public", raising=False)
    monkeypatch.setattr(s, "VAPID_PRIVATE_KEY", "test-private", raising=False)
    return s


# ── The subscription itself ──

async def test_key_endpoint_says_disabled_rather_than_erroring(client, monkeypatch):
    """The client hides the toggle instead of showing one that 500s.

    The keys are cleared explicitly rather than assumed absent: `.env` is real on
    a developer machine, and this once failed for the sole reason that the person
    running it had configured push.
    """
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "VAPID_PUBLIC_KEY", None, raising=False)
    monkeypatch.setattr(s, "VAPID_PRIVATE_KEY", None, raising=False)

    headers = await _auth(client)
    r = await client.get("/api/push/key", headers=headers)
    assert r.status_code == 200
    assert r.json()["enabled"] is False


async def test_subscribing_twice_from_one_browser_is_one_row(client, db, monkeypatch):
    """The endpoint is the device. Inserting blindly would ring one phone twice
    for every reinstall."""
    from sqlalchemy import select
    from app.models.push import PushSubscription
    from app.database import async_session

    _enable(monkeypatch)
    headers = await _auth(client)

    assert (await client.post("/api/push/subscribe", json=SUB, headers=headers)).status_code == 204
    assert (await client.post("/api/push/subscribe", json=SUB, headers=headers)).status_code == 204

    async with async_session() as s:
        rows = (await s.execute(select(PushSubscription))).scalars().all()
    assert len(rows) == 1


async def test_a_shared_device_repoints_to_whoever_subscribed_last(client, db, monkeypatch):
    """Two people, one browser. The endpoint can only belong to one account, and
    it must be the one that just asked — otherwise the first user keeps getting
    the second user's pushes."""
    from sqlalchemy import select
    from app.models.push import PushSubscription
    from app.database import async_session

    _enable(monkeypatch)
    first = await _auth(client, "a@example.com", "areader")
    second = await _auth(client, "b@example.com", "breader")

    await client.post("/api/push/subscribe", json=SUB, headers=first)
    await client.post("/api/push/subscribe", json=SUB, headers=second)

    me = (await client.get("/api/auth/me", headers=second)).json()["id"]
    async with async_session() as s:
        rows = (await s.execute(select(PushSubscription))).scalars().all()
    assert len(rows) == 1
    assert str(rows[0].user_id) == me


async def test_unsubscribe_removes_the_device(client, monkeypatch):
    from sqlalchemy import select
    from app.models.push import PushSubscription
    from app.database import async_session

    _enable(monkeypatch)
    headers = await _auth(client)
    await client.post("/api/push/subscribe", json=SUB, headers=headers)

    r = await client.post("/api/push/unsubscribe",
                          json={"endpoint": SUB["endpoint"]}, headers=headers)
    assert r.status_code == 204

    async with async_session() as s:
        assert (await s.execute(select(PushSubscription))).scalars().all() == []


# ── What a push may say ──

def test_payload_never_carries_the_content_itself():
    """A push is read on a lock screen by whoever is holding the phone. It is a
    knock, not the message."""
    from app.services.push_service import _payload

    body = _payload("collection_message", {
        "collection_id": "c1", "book_id": "b1",
    })
    blob = str(body).lower()
    for leak in ("piranesi", "@shruti", "wrecked me"):
        assert leak not in blob
    assert body["title"] == "Bibliome"
    assert "collection" in body["body"].lower()


def test_payload_deep_links_to_the_right_room():
    from app.services.push_service import _payload

    body = _payload("collection_message", {"collection_id": "c1", "book_id": "b1"})
    assert body["url"] == "/collections/c1/discussion/b1"

    # Without a book it still opens the collection's book list.
    body = _payload("collection_message", {"collection_id": "c1"})
    assert body["url"] == "/collections/c1/discussion"


def test_an_unknown_kind_still_produces_a_safe_payload():
    """A new notification kind must not crash the sender or leak its payload."""
    from app.services.push_service import _payload

    body = _payload("something_new", {"secret": "do not show"})
    assert "do not show" not in str(body)
    assert body["title"] == "Bibliome"
    assert body["url"] == "/"


# ── Push is a courtesy, never a dependency ──

async def test_a_failing_push_does_not_fail_the_action_that_caused_it(client, monkeypatch):
    """If the push service is down, the message still sends and the in-app
    notification is still there. Anything else trades a working feature for a
    broken courtesy."""
    import app.services.notification_service as ns

    _enable(monkeypatch)

    async def _boom(*a, **k):
        raise RuntimeError("push service on fire")
    monkeypatch.setattr(ns, "push_to_user", _boom)

    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")

    cid = (await client.post("/api/collections", json={"title": "Group"},
                             headers=owner)).json()["id"]
    book = (await client.post("/api/entries", json={"title": "Piranesi", "intensity": 7,
                                                    "emotions": []},
                              headers=owner)).json()["book_id"]
    await client.post(f"/api/collections/{cid}/books", json={"book_id": book}, headers=owner)
    token = (await client.post(f"/api/collections/{cid}/invites", json={},
                               headers=owner)).json()["token"]
    await client.post(f"/api/collections/invites/{token}/join", headers=friend)

    r = await client.post(f"/api/collections/{cid}/messages",
                          json={"body": "still works"}, headers=owner)
    assert r.status_code == 201, r.text


async def test_push_is_skipped_when_not_configured(client, db, monkeypatch):
    """No keys, no attempt — and no error either."""
    from app.config import get_settings
    from app.services.push_service import push_to_user
    from app.database import async_session

    cfg = get_settings()
    monkeypatch.setattr(cfg, "VAPID_PUBLIC_KEY", None, raising=False)
    monkeypatch.setattr(cfg, "VAPID_PRIVATE_KEY", None, raising=False)

    async with async_session() as s:
        assert await push_to_user(s, uuid.uuid4(), "collection_message", {}) == 0


async def test_a_batched_message_does_not_push_again(client, monkeypatch):
    """Five messages about one book coalesce into ONE unread notification, so
    they must be one buzz. Pushing per message is how a conversation becomes a
    reason to turn notifications off."""
    import app.services.notification_service as ns

    _enable(monkeypatch)
    calls = []

    async def _count(db, user_id, kind, payload):
        calls.append(kind)
        return 1
    monkeypatch.setattr(ns, "push_to_user", _count)

    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid = (await client.post("/api/collections", json={"title": "Group"},
                             headers=owner)).json()["id"]
    book = (await client.post("/api/entries", json={"title": "Piranesi", "intensity": 7,
                                                    "emotions": []},
                              headers=owner)).json()["book_id"]
    await client.post(f"/api/collections/{cid}/books", json={"book_id": book}, headers=owner)
    token = (await client.post(f"/api/collections/{cid}/invites", json={},
                               headers=owner)).json()["token"]
    await client.post(f"/api/collections/invites/{token}/join", headers=friend)

    for i in range(4):
        await client.post(f"/api/collections/{cid}/messages",
                          json={"body": f"message {i}"}, headers=owner)

    assert calls.count("collection_message") == 1
