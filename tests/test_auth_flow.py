"""Auth-flow integration tests (B1.21 + B1.10 cookie contract).

Refresh token lives in an httpOnly cookie (authCookieContract.md): it's never in
the response body, /refresh reads the cookie, and rotation/logout/password-change
kill it. httpx's cookie jar carries the cookie across calls like a browser.
"""

import pytest

pytestmark = pytest.mark.asyncio

COOKIE = "bibliome_refresh"
REG = {"email": "a@example.com", "username": "alice", "password": "hunter2pass", "display_name": "Alice"}


async def _register(client, **over):
    return await client.post("/api/auth/register", json={**REG, **over})


async def _login(client, email=REG["email"], password=REG["password"]):
    return await client.post("/api/auth/login", json={"email": email, "password": password})


async def test_register_auto_logs_in_with_cookie(client):
    r = await _register(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["access_token"] and body["expires_in"] == 900
    assert body["user"]["email"] == REG["email"]
    assert "refresh_token" not in body        # never in the body
    assert COOKIE in r.cookies                 # refresh cookie set


async def test_login_returns_access_token_and_cookie(client):
    await _register(client)
    r = await _login(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"]
    assert "refresh_token" not in body
    assert COOKIE in r.cookies


async def test_duplicate_register_is_generic_409(client):
    await _register(client)
    r = await _register(client, username="alice2")  # same email, different username
    assert r.status_code == 409
    assert "not available" in r.json()["detail"].lower()


async def test_wrong_password_is_generic_401(client):
    await _register(client)
    r = await _login(client, password="wrongpassword")
    assert r.status_code == 401
    assert "remaining" not in r.json()["detail"].lower()


async def test_refresh_uses_cookie_and_rotates(client):
    await _register(client)
    await _login(client)
    old = client.cookies.get(COOKIE)
    assert old

    # Empty body — the cookie is the credential.
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]
    assert "refresh_token" not in r.json()

    # Replaying the rotated-out token must fail.
    client.cookies.clear()
    client.cookies.set(COOKIE, old, domain="test", path="/api/auth")
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 401


async def test_refresh_without_cookie_is_401(client):
    client.cookies.clear()
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 401


async def test_logout_revokes_and_clears_cookie(client):
    await _register(client)
    await _login(client)
    r = await client.post("/api/auth/logout")
    assert r.status_code == 200
    # After logout the (revoked) token no longer refreshes.
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 401


async def test_lockout_after_repeated_failures(client):
    await _register(client)
    for _ in range(5):
        assert (await _login(client, password="wrongpassword")).status_code == 401
    assert (await _login(client, password="wrongpassword")).status_code == 429


async def test_change_password_revokes_existing_sessions(client):
    await _register(client)
    login = await _login(client)
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    r = await client.post(
        "/api/user/change-password",
        json={"current_password": REG["password"], "new_password": "brandnewpass1"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    r = await client.post("/api/auth/refresh")
    assert r.status_code == 401


async def test_forgot_password_is_uniform_for_unknown_email(client):
    r = await client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert r.status_code == 200
    assert "if that email exists" in r.json()["message"].lower()


async def test_reset_password_is_rate_limited(client):
    """POST /auth/reset-password sits behind auth_limiter like the rest of /auth.

    Without this the endpoint was an unthrottled guessing oracle: the reset
    token is the only thing between a caller and an account, and there is no
    lockout keyed to it the way login_lockout is keyed to an email. [#3]

    conftest relaxes auth_limiter to 100000 because every test shares one
    synthetic IP, so this test restores the production cap for its own duration
    — otherwise it would pass whether or not the route is wired to the limiter.
    """
    from app.middleware import rate_limit

    original = rate_limit.auth_limiter.max_requests
    rate_limit.auth_limiter.max_requests = 10
    rate_limit.auth_limiter._hits.clear()
    try:
        await _assert_reset_password_limited(client)
    finally:
        rate_limit.auth_limiter.max_requests = original
        rate_limit.auth_limiter._hits.clear()


async def _assert_reset_password_limited(client):
    body = {"token": "not-a-real-token", "new_password": "hunter2pass"}

    for _ in range(10):
        r = await client.post("/api/auth/reset-password", json=body)
        assert r.status_code == 400, r.text  # rejected on the token, not the limit

    r = await client.post("/api/auth/reset-password", json=body)
    assert r.status_code == 429, r.text
