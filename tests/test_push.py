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


async def test_a_still_live_conversation_knocks_again_after_the_cooldown(client, monkeypatch):
    """The other half of batching. Collapsing a burst is right; collapsing
    forever is how a closed app goes silent on a thread that is still going —
    every later message folded into one unread notification and pushed nothing.
    Once the cooldown has passed, the next message knocks again."""
    import app.services.notification_service as ns
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    from app.database import async_session
    from app.models.notification import Notification

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

    await client.post(f"/api/collections/{cid}/messages",
                      json={"body": "first"}, headers=owner)
    assert calls.count("collection_message") == 1

    # Rewind the knock past the cooldown — the thread has been quiet a while and
    # the reader still has not opened it.
    stale = (datetime.now(timezone.utc) - ns.REPUSH_AFTER - timedelta(minutes=1)).isoformat()
    async with async_session() as s:
        async with s.begin():
            n = (await s.execute(
                select(Notification).where(Notification.kind == "collection_message")
            )).scalars().first()
            n.payload = {**n.payload, "_pushed_at": stale}

    await client.post(f"/api/collections/{cid}/messages",
                      json={"body": "second"}, headers=owner)
    assert calls.count("collection_message") == 2

    # Still one notification in the app: the shade collapses, the buzz repeats.
    items = (await client.get("/api/notifications", headers=friend)).json()["notifications"]
    assert sum(i["kind"] == "collection_message" for i in items) == 1


async def test_quiet_hours_knock_once_they_end(client, monkeypatch):
    """Quiet hours must mean "not yet", not "never". The notification is written
    silently, and the sweep rings it once the window closes — otherwise the only
    way to learn about it is to open the app, which is the bug."""
    import app.services.notification_service as ns
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    from app.database import async_session
    from app.models.notification import Notification

    _enable(monkeypatch)
    calls = []

    async def _count(db, user_id, kind, payload):
        calls.append(kind)
        return 1
    monkeypatch.setattr(ns, "push_to_user", _count)

    friend = await _auth(client, "q@example.com", "quiet")
    from app.models.user import User
    async with async_session() as s:
        me_id = (await s.execute(
            select(User).where(User.username == "quiet")
        )).scalar_one().id

    # Quiet right now, in every timezone: a window covering the whole day.
    await client.patch("/api/notifications/preferences",
                       json={"quiet_hours_start": 0, "quiet_hours_end": 23,
                             "timezone": "UTC"}, headers=friend)

    now = datetime.now(timezone.utc)
    async with async_session() as s:
        async with s.begin():
            n = await ns.notify(s, me_id, 1, "echo_reply",
                                {"echo_id": str(uuid.uuid4())})
            assert n is not None
            assert n.deliver_after > now, "should have been deferred"
    assert calls == [], "a deferred notification must not knock yet"

    # The window closes.
    async with async_session() as s:
        async with s.begin():
            row = (await s.execute(
                select(Notification).where(Notification.id == n.id)
            )).scalar_one()
            # Written ten minutes ago, due a minute ago — a deferral that has
            # just matured, which is exactly what the sweep looks for.
            row.created_at = now - timedelta(minutes=10)
            row.deliver_after = now - timedelta(minutes=1)

    async with async_session() as s:
        async with s.begin():
            assert await ns.sweep_deferred_pushes(s) == 1
    assert calls == ["echo_reply"]

    # And exactly once — a second sweep finds it already knocked.
    async with async_session() as s:
        async with s.begin():
            assert await ns.sweep_deferred_pushes(s) == 0
    assert calls == ["echo_reply"]


def test_every_notification_kind_lands_where_the_in_app_click_lands():
    """The push url mirrors notificationTarget() in the frontend. Kinds this
    did not know fell through to "/", so a tapped notification dropped you on
    the home page and left you to find the thing yourself.

    Pairs with src/components/notifications/target.test.js in the frontend — if
    a route moves, both have to move.
    """
    from app.services.push_service import _payload

    def url(kind, payload=None):
        return _payload(kind, payload or {})["url"]

    assert url("password_reset") == "/settings?section=security"
    assert url("password_changed") == "/settings?section=security"
    assert url("echo_reply", {"echo_id": "e1"}) == "/echoes?echo=e1"
    assert url("echo_reply") == "/echoes"
    assert url("resonance_reach", {"match_id": "m1"}) == "/resonance"
    assert url("resonance_connected", {"thread_id": "t1"}) == "/resonance"
    assert url("resonance_message", {"thread_id": "t1"}) == "/resonance"
    assert url("chat_reaction", {"thread_id": "t1"}) == "/resonance"
    assert url("chat_reaction", {"collection_id": "c1"}) == "/collections/c1/discussion"
    assert url("chat_mention", {"collection_id": "c1"}) == "/collections/c1/discussion"
    assert url("collection_joined", {"collection_id": "c1"}) == "/collections/c1/discussion"
    assert url("dna_shifted", {"old": 1, "new": 2}) == "/?view=dna"

    # Genuinely nowhere to go: the room is gone, and the digest is about the
    # shelf itself.
    assert url("collection_deleted", {"title": "Group"}) == "/"
    assert url("weekly_digest", {"period": "w"}) == "/"

    # A kind with its id missing must not build "/collections/None/discussion".
    assert url("chat_mention") == "/"
    assert url("collection_message") == "/"


def test_a_push_is_held_for_a_day_rather_than_dropped(monkeypatch):
    """pywebpush defaults to ttl=0, which means "deliver only if the device is
    connected this second, else discard". A sleeping phone got nothing — the
    exact symptom of "the app was closed so I never heard". Ask for a day."""
    from app.services import push_service

    _enable(monkeypatch)
    sent = {}

    def _fake_webpush(**kwargs):
        sent.update(kwargs)

    import sys, types
    stub = types.ModuleType("pywebpush")
    stub.webpush = _fake_webpush
    stub.WebPushException = Exception
    monkeypatch.setitem(sys.modules, "pywebpush", stub)

    status = push_service._send_one_sync(
        {"endpoint": "https://push.example.com/x", "p256dh": "k", "auth": "a"},
        '{"title": "Bibliome"}',
    )
    assert status is None, "a clean send reports no status to act on"
    assert sent["ttl"] == push_service.PUSH_TTL == 86400


async def test_a_push_that_failed_is_retried_rather_than_lost(client, monkeypatch):
    """A push service having a bad minute used to cost the reader the whole
    notification: one inline attempt, error swallowed, never revisited. The
    sweep picks up anything due that was never successfully sent."""
    import app.services.notification_service as ns
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.database import async_session
    from app.models.user import User

    _enable(monkeypatch)
    attempts = []

    async def _on_fire(db, user_id, kind, payload):
        attempts.append(kind)
        raise RuntimeError("push service on fire")
    monkeypatch.setattr(ns, "push_to_user", _on_fire)

    await _auth(client, "r@example.com", "retry")
    async with async_session() as s:
        uid = (await s.execute(select(User).where(User.username == "retry"))).scalar_one().id

    async with async_session() as s:
        async with s.begin():
            n = await ns.notify(s, uid, 1, "echo_reply", {"echo_id": str(uuid.uuid4())})
    assert attempts == ["echo_reply"], "the inline attempt still happens first"
    assert n.deliver_after <= datetime.now(timezone.utc), "not deferred — it simply failed"

    # The service comes back.
    async def _works(db, user_id, kind, payload):
        attempts.append(kind)
        return 1
    monkeypatch.setattr(ns, "push_to_user", _works)

    async with async_session() as s:
        async with s.begin():
            assert await ns.sweep_deferred_pushes(s) == 1
    assert attempts == ["echo_reply", "echo_reply"]

    # And it is not sent a third time.
    async with async_session() as s:
        async with s.begin():
            assert await ns.sweep_deferred_pushes(s) == 0


# ── The test button ──

async def test_test_push_rings_only_the_caller(client, monkeypatch):
    """There is no user parameter, and there must never be one: this endpoint
    exists to let a reader prove their OWN phone works, not to ring anyone."""
    import app.routers.push as push_router

    _enable(monkeypatch)
    rung = []

    async def _capture(db, user_id, kind, payload):
        rung.append((user_id, kind))
        return 2
    monkeypatch.setattr(push_router, "push_to_user", _capture)

    me = await _auth(client, "t@example.com", "tester")
    r = await client.post("/api/push/test", headers=me)

    assert r.status_code == 200, r.text
    assert r.json() == {"sent": 2}
    assert len(rung) == 1 and rung[0][1] == "push_test"


async def test_test_push_reports_zero_rather_than_pretending(client, monkeypatch):
    """No devices registered is the single most likely reason notifications are
    silent. Saying "sent!" there would hide the actual answer."""
    import app.routers.push as push_router

    _enable(monkeypatch)

    async def _none(db, user_id, kind, payload):
        return 0
    monkeypatch.setattr(push_router, "push_to_user", _none)

    me = await _auth(client, "z@example.com", "zero")
    r = await client.post("/api/push/test", headers=me)
    assert r.json() == {"sent": 0}


async def test_test_push_needs_a_login(client):
    r = await client.post("/api/push/test")
    assert r.status_code in (401, 403)


async def test_test_push_says_so_when_push_is_not_configured(client, monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "VAPID_PUBLIC_KEY", None, raising=False)
    monkeypatch.setattr(s, "VAPID_PRIVATE_KEY", None, raising=False)

    me = await _auth(client, "n@example.com", "nokeys")
    r = await client.post("/api/push/test", headers=me)
    assert r.status_code == 503


def test_test_push_copy_names_itself_and_lands_on_settings():
    from app.services.push_service import _payload

    body = _payload("push_test", {})
    assert body["body"] == "Notifications are working."
    assert body["url"] == "/settings?section=notifications"
