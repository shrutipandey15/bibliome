"""Notification tests (B4.4): tier routing, quiet-hours precedence, batching, digest."""

from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.asyncio


def _handle(name):
    return f"usr_{name}"  # ensure >= 3 chars for the username/handle rules


async def _user(client, name):
    await client.post("/api/auth/register", json={
        "email": f"{name}@example.com", "username": _handle(name), "password": "hunter2pass",
    })
    r = await client.post("/api/auth/login", json={"email": f"{name}@example.com", "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _echo(client, headers):
    r = await client.post("/api/echoes", json={
        "body": "a real reflection", "book_title": "Piranesi", "primary_emotion": "awe",
    }, headers=headers)
    return r.json()["echo"]["id"]


async def _notifs(client, headers):
    return (await client.get("/api/notifications", headers=headers)).json()


# ── Tier 1: reply notification + batching ──

async def test_reply_creates_tier1_notification(client):
    a = await _user(client, "aa")
    b = await _user(client, "bb")
    echo_id = await _echo(client, a)
    await client.post(f"/api/echoes/{echo_id}/replies", json={"body": "same"}, headers=b)

    n = await _notifs(client, a)
    assert n["unread_count"] == 1
    item = n["notifications"][0]
    assert item["tier"] == 1 and item["kind"] == "echo_reply"
    assert item["payload"]["count"] == 1


async def test_replies_batch_into_one_notification(client):
    a = await _user(client, "cc")
    b = await _user(client, "dd")
    c = await _user(client, "ee")
    echo_id = await _echo(client, a)
    await client.post(f"/api/echoes/{echo_id}/replies", json={"body": "b says"}, headers=b)
    await client.post(f"/api/echoes/{echo_id}/replies", json={"body": "c says"}, headers=c)

    n = await _notifs(client, a)
    assert n["unread_count"] == 1  # collapsed, not two pings
    item = n["notifications"][0]
    assert item["payload"]["count"] == 2
    assert set(item["payload"]["actors"]) == {_handle("dd"), _handle("ee")}


async def test_self_reply_does_not_notify(client):
    a = await _user(client, "ff")
    echo_id = await _echo(client, a)
    await client.post(f"/api/echoes/{echo_id}/replies", json={"body": "talking to myself"}, headers=a)
    assert (await _notifs(client, a))["unread_count"] == 0


async def test_direct_notices_have_no_off_switch(client):
    """The old reply_enabled switch silenced every tier-1 notice; it's gone.
    Sending it now changes nothing, and a reply still arrives."""
    a = await _user(client, "gg")
    b = await _user(client, "hh")
    r = await client.patch("/api/notifications/preferences", json={"reply_enabled": False}, headers=a)
    assert r.status_code == 200 and "reply_enabled" not in r.json()
    echo_id = await _echo(client, a)
    await client.post(f"/api/echoes/{echo_id}/replies", json={"body": "hi"}, headers=b)
    assert (await _notifs(client, a))["unread_count"] == 1


# ── Quiet hours + tier-0 precedence ──

async def test_quiet_hours_defer_tier1_but_security_bypasses(client):
    a = await _user(client, "ii")
    b = await _user(client, "jj")
    # Quiet hours covering "now" (UTC).
    now_hour = datetime.now(timezone.utc).hour
    await client.patch("/api/notifications/preferences", json={
        "timezone": "UTC",
        "quiet_hours_start": now_hour,
        "quiet_hours_end": (now_hour + 2) % 24,
    }, headers=a)

    echo_id = await _echo(client, a)
    await client.post(f"/api/echoes/{echo_id}/replies", json={"body": "hush"}, headers=b)
    # Tier-1 is deferred → not surfaced during quiet hours.
    assert (await _notifs(client, a))["unread_count"] == 0

    # Tier-0 security notice bypasses quiet hours entirely.
    tokens = (await client.post("/api/auth/login", json={"email": "ii@example.com", "password": "hunter2pass"})).json()
    hdr = {"Authorization": f"Bearer {tokens['access_token']}"}
    await client.post("/api/user/change-password", json={
        "current_password": "hunter2pass", "new_password": "brandnewpass1"}, headers=hdr)

    n = await _notifs(client, a)
    kinds = [x["kind"] for x in n["notifications"]]
    assert "password_changed" in kinds
    assert n["unread_count"] >= 1


# ── Read + preferences ──

async def test_mark_read_clears_unread(client):
    a = await _user(client, "kk")
    b = await _user(client, "ll")
    echo_id = await _echo(client, a)
    await client.post(f"/api/echoes/{echo_id}/replies", json={"body": "x"}, headers=b)
    assert (await _notifs(client, a))["unread_count"] == 1
    await client.post("/api/notifications/read", json={}, headers=a)
    assert (await _notifs(client, a))["unread_count"] == 0


async def test_invalid_timezone_rejected(client):
    a = await _user(client, "mm")
    r = await client.patch("/api/notifications/preferences", json={"timezone": "Mars/Phobos"}, headers=a)
    assert r.status_code == 400


