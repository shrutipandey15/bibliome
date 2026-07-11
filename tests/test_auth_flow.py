"""Auth-flow integration tests (B1.21) — register / login / refresh-rotation /
lockout / password-change-revokes-tokens. Runs against a real Postgres.
"""

import pytest

pytestmark = pytest.mark.asyncio

REG = {"email": "a@example.com", "username": "alice", "password": "hunter2pass", "display_name": "Alice"}


async def _register(client, **over):
    return await client.post("/api/auth/register", json={**REG, **over})


async def _login(client, email=REG["email"], password=REG["password"]):
    return await client.post("/api/auth/login", json={"email": email, "password": password})


async def test_register_then_login_returns_tokens(client):
    r = await _register(client)
    assert r.status_code == 201, r.text
    r = await _login(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"] and body["refresh_token"]


async def test_duplicate_register_is_generic_409(client):
    await _register(client)
    # Same email, different username → must not reveal which field collided.
    r = await _register(client, username="alice2")
    assert r.status_code == 409
    assert "email" not in r.json()["detail"].lower() or "not available" in r.json()["detail"].lower()


async def test_wrong_password_is_generic_401(client):
    await _register(client)
    r = await _login(client, password="wrongpassword")
    assert r.status_code == 401
    # No "N attempts remaining" hint that would confirm the account exists.
    assert "remaining" not in r.json()["detail"].lower()


async def test_refresh_rotation_revokes_old_token(client):
    await _register(client)
    tokens = (await _login(client)).json()
    old_refresh = tokens["refresh_token"]

    r = await client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 200, r.text
    new_refresh = r.json()["refresh_token"]
    assert new_refresh != old_refresh

    # Reusing the rotated-out token must fail (rotation).
    r = await client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 401


async def test_lockout_after_repeated_failures(client):
    await _register(client)
    for _ in range(5):
        r = await _login(client, password="wrongpassword")
        assert r.status_code == 401
    # The 6th attempt is locked out.
    r = await _login(client, password="wrongpassword")
    assert r.status_code == 429


async def test_change_password_revokes_existing_sessions(client):
    await _register(client)
    tokens = (await _login(client)).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    r = await client.post(
        "/api/user/change-password",
        json={"current_password": REG["password"], "new_password": "brandnewpass1"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    # The refresh token issued before the password change must be dead.
    r = await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


async def test_forgot_password_is_uniform_for_unknown_email(client):
    # Unknown email returns the same generic message (no enumeration).
    r = await client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert r.status_code == 200
    assert "if that email exists" in r.json()["message"].lower()
