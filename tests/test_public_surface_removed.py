"""Regression: the old public surface must stay dead (audit-v2 P0-NEW-1).

A private-by-default user's writing must never reach a global unauthenticated feed.
"""

import pytest

pytestmark = pytest.mark.asyncio


async def _user(client, name):
    await client.post("/api/auth/register", json={
        "email": f"{name}@example.com", "username": name, "password": "hunter2pass",
    })
    r = await client.post("/api/auth/login", json={"email": f"{name}@example.com", "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_public_stream_is_gone(client):
    r = await client.get("/api/public/stream")
    assert r.status_code == 404


async def test_public_user_echoes_is_gone(client):
    r = await client.get("/api/public/echoes/someone")
    assert r.status_code == 404


async def test_entry_echo_image_endpoints_are_gone(client):
    import uuid
    fake = uuid.uuid4()
    assert (await client.get(f"/api/public/echo/{fake}/og")).status_code == 404
    assert (await client.get(f"/api/public/echo/{fake}/story")).status_code == 404


async def test_public_echo_is_not_writable_or_returned(client):
    h = await _user(client, "quiet")
    # Sending public_echo is silently ignored (field retired) — entry still created,
    # and the field is not echoed back anywhere.
    r = await client.post("/api/entries", json={
        "title": "Private Thought", "intensity": 5, "emotions": [],
        "public_echo": "this should never be public",
    }, headers=h)
    assert r.status_code == 201
    assert "public_echo" not in r.json()

    r = await client.get("/api/entries", headers=h)
    assert "public_echo" not in r.json()["entries"][0]


async def test_dna_twin_is_unmounted(client):
    h = await _user(client, "lonely")
    r = await client.get("/api/dna/twin", headers=h)
    assert r.status_code == 404
