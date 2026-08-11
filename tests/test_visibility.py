"""Visibility spine tests (B2.1 / B2.11).

The critical property: private/community profiles never leak through the public
crawler endpoints, and share links are revocable + expiring.
"""

import pytest

pytestmark = pytest.mark.asyncio


async def _register_login(client, email, username):
    await client.post("/api/auth/register", json={
        "email": email, "username": username, "password": "hunter2pass",
    })
    r = await client.post("/api/auth/login", json={"email": email, "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_new_account_defaults_to_private(client):
    headers = await _register_login(client, "p@example.com", "priv")
    r = await client.get("/api/user/settings", headers=headers)
    assert r.status_code == 200
    assert r.json()["profile_visibility"] == "private"
    assert r.json()["is_public"] is False


async def test_old_username_card_endpoint_is_gone(client):
    # The username-based public card was removed in Phase 5 (B5.1). Public
    # profiles are now viewed via /profile/{handle}; the old surface is dead.
    await _register_login(client, "p2@example.com", "privcard")
    r = await client.get("/api/public/card/privcard")
    assert r.status_code == 404


async def test_setting_public_updates_visibility(client):
    headers = await _register_login(client, "pub@example.com", "pubcard")
    r = await client.patch("/api/user/settings", json={"profile_visibility": "public"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["profile_visibility"] == "public"
    assert r.json()["is_public"] is True
    # The old username-based public card no longer exists.
    assert (await client.get("/api/public/card/pubcard")).status_code == 404


async def test_invalid_visibility_rejected(client):
    headers = await _register_login(client, "bad@example.com", "badvis")
    r = await client.patch("/api/user/settings", json={"profile_visibility": "everyone"}, headers=headers)
    assert r.status_code == 422


async def _seed_dna(client, headers, n=5):
    """Enough tagged books for a card to exist. The share endpoint serves the
    owner's cached DNA now, so a reader with nothing logged has nothing to share."""
    for i in range(n):
        await client.post("/api/entries", json={
            "title": f"Book {i}", "intensity": 7,
            "emotions": [{"emotion_id": "comfort", "strength": 7}],
        }, headers=headers)
    await client.get("/api/dna/profile", headers=headers)   # warm the cache


async def test_share_link_grants_access_then_revoke_kills_it(client):
    headers = await _register_login(client, "s@example.com", "sharer")
    await _seed_dna(client, headers)
    # Even though the profile is private, a share link works...
    r = await client.post("/api/user/share-token", headers=headers)
    assert r.status_code == 200
    token = r.json()["share_token"]

    r = await client.get(f"/api/public/shared/{token}")
    assert r.status_code == 200
    assert r.json()["handle"] == "sharer"

    # ...until revoked.
    r = await client.delete("/api/user/share-token", headers=headers)
    assert r.status_code == 204
    r = await client.get(f"/api/public/shared/{token}")
    assert r.status_code == 404


async def test_bogus_share_token_is_404(client):
    r = await client.get("/api/public/shared/not-a-real-token")
    assert r.status_code == 404
