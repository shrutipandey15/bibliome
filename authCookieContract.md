# Auth Token Contract (B1.10)

_The agreed contract between `bibliome` (backend) and `bibliome-frontend`. Both sides build to this. Closes audit `[P1-1]`._

**Status:** LOCKED — 2026-07-11

---

## The model

| Token       | Lives where                                                                    | Lifetime                      | Readable by JS?                    |
| ----------- | ------------------------------------------------------------------------------ | ----------------------------- | ---------------------------------- |
| **Access**  | Frontend **memory only** (React state / module var). **Never** `localStorage`. | 15 min                        | Yes (by design — it's short-lived) |
| **Refresh** | **httpOnly cookie**, set by the server. Frontend never sees or touches it.     | 30 days, rotated on every use | **No** — this is the whole point   |

**Why:** an XSS today can read `localStorage` and steal a 7-day refresh token = full account takeover. An httpOnly cookie is invisible to JavaScript, so the worst an XSS gets is a 15-minute access token.

---

## Prerequisite: same-origin

Frontend and API **must** be served from the same origin.

- **Prod:** nginx serves the SPA and proxies `/api/*` to the backend. Already true.
- **Dev:** use the Vite dev-server proxy so `/api/*` hits the backend. Already configured.

This is what lets us use `SameSite=Strict`. If the API ever moves to a different domain, this contract must be reopened (you'd need `SameSite=None` + a CSRF token).

---

## The cookie

```
Set-Cookie: bibliome_refresh=<token>
            HttpOnly
            Secure            ← prod only; omit on plain-http localhost
            SameSite=Strict
            Path=/api/auth
            Max-Age=2592000   ← 30 days
```

- `Path=/api/auth` — the cookie is only ever sent to auth endpoints. Every other API call carries no refresh token at all. Minimal exposure.
- `SameSite=Strict` — the browser will not send this cookie on any cross-site request, which kills CSRF against `/refresh` and `/logout`.
- The stored value is still **hashed at rest** in `refresh_tokens` (already done).

---

## Endpoints

### `POST /api/auth/login`

**Request:** `{ "email": "...", "password": "..." }`
**Response 200:**

```json
{
  "access_token": "<jwt>",
  "expires_in": 900,
  "user": { "id": "...", "handle": "...", "email": "..." }
}
```

**Plus:** `Set-Cookie: bibliome_refresh=...`
No refresh token in the body. Ever.

### `POST /api/auth/register`

Same shape as login (auto-login on success).

### `POST /api/auth/refresh`

**Request:** empty body. The cookie is the credential.
**Response 200:** `{ "access_token": "<jwt>", "expires_in": 900 }` + `Set-Cookie` with the **rotated** token.
**Response 401:** cookie missing/invalid/revoked → frontend clears memory and shows login.

### `POST /api/auth/logout`

Revokes the refresh token server-side **and** clears the cookie:
`Set-Cookie: bibliome_refresh=; Max-Age=0; Path=/api/auth`

### Password change / reset

Revokes **all** refresh tokens for the user and clears the cookie → all sessions die. (Already implemented server-side; just make sure the cookie is cleared too.)

---

## Frontend behaviour

1. **App boot:** access token is gone (memory). Call `POST /api/auth/refresh` once.
   - 200 → store access token in memory, render the app. _(This is the "silent login" — user stays logged in across reloads without any token in `localStorage`.)_
   - 401 → show login.
2. **Every API call:** `Authorization: Bearer <access_token>` from memory.
3. **On 401 from any call:** call `/refresh` **once**, through a **single-flight promise** (audit `[P2-11]` — parallel refreshes currently log users out), then retry the original request. If refresh fails → clear state, go to login.
4. **Fetch options:** `credentials: 'same-origin'` (the default; be explicit anyway). Never read, write, or send the refresh token manually — the browser does it.
5. **Delete** all `localStorage` token code (`bibliome_tokens`).

---

## Backend requirements to close this out

- [ ] Set/clear the cookie on login, register, refresh, logout, password change/reset.
- [ ] `/refresh` reads the cookie, not a body field or header.
- [ ] CORS: `allow_credentials=True`, explicit origin allowlist — and **fix the error handler** that hardcodes `Access-Control-Allow-Origin: *`, which is invalid with credentials and will break this (audit `[P1-2]`).
- [ ] Resolve the token-lifetime drift: `REFRESH_TOKEN_EXPIRE_DAYS` is 7 in code, 30 in `env.production` (audit `[P3-5]`). **Pick 30**, set it in both.

---

## Cutover

Existing users have a refresh token in `localStorage` that will no longer be accepted. On first load after deploy, the frontend clears `localStorage`, calls `/refresh`, gets a 401 (no cookie yet), and shows login. **Everyone is logged out once.** That's acceptable and clean — don't build a migration path for it.

---

## Edge cases

- **User blocks cookies** → login appears to succeed but `/refresh` always 401s. Detect and show a clear message rather than a silent loop.
- **Access token expires mid-flight on parallel requests** → single-flight refresh (point 3) is what prevents the current random-logout bug. Non-negotiable.
- **Two tabs** → both share the cookie. Rotation means one tab's refresh invalidates the other's in-flight one; single-flight + a retry-once-on-401 handles it.
- **Refresh rotation race** → if a rotated token is reused (replay), revoke the whole token family and force re-login. Standard, and it's your theft-detection signal.
