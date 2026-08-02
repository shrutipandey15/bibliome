# Bibliome Backend — ROADMAP

*Working task document for the `bibliome` (FastAPI) repo. Commit this to the repo root as `ROADMAP.md`.*

Part of a three-document set:
- **`audit.md`** — the canonical problem list (what's broken today). Referenced here by ID, e.g. `[P0-1]`.
- **`blueprint.md`** — the product vision and reasoning (why we're building this way). Referenced by section, e.g. `[§2]`.
- **This file** — the canonical *task* list for the backend, sequenced so you can work in parallel with the frontend without blocking.

---

## The product, in one paragraph (so this file stands alone)
Bibliome is a **private mirror** for your reading life. Everything — library, journal, DNA, profile — is private by default, for you. The **one** public surface is **Echo**: a pseudonymous, book-anchored place to put the raw thing a book did to you, and to read and reply to others doing the same. No chat. No public profiles. No follower/karma/like counts anywhere. Twin (reader-matching) is parked until Echo proves itself. Pseudonymous, never anonymous; real identity is never collected `[§0.2, §2]`.

---

## How to work in parallel (read this once, then live by it)

**1. Contract-first.** Before either side writes a line of a feature, the request/response shape of its endpoints is agreed and written down (in this repo, as an OpenAPI stub or a markdown table in the relevant task). Marked `[CONTRACT]` below. This is the single most important habit — it's what lets the frontend build against a mock while you build the real thing.

**2. Backend implements the contract; frontend mocks it.** Once `[CONTRACT]` is locked, you build the endpoint for real with tests; the frontend builds its UI against a mock of the same shape. Neither waits on the other.

**3. Integration gate.** At the end of each phase, the frontend swaps its mocks for your real endpoints and runs the smoke suite. Marked `[GATE]`. A phase isn't done until its gate passes.

**4. Phase discipline — the rule that matters most for you.** Do **not** start Phase N+1 until Phase N's *Definition of Done* is checked off. Your track record is starting new phases before closing old ones; that discipline gap is how the emotion-engine bug `[P0-1]` shipped. One test suite, green, before moving on.

**Dependency markers:** `[depends: X]` = must be done first. `[FE-facing]` = the frontend consumes this; needs a `[CONTRACT]`. `[internal]` = no frontend impact, safe to do anytime in the phase.

---

## PHASE 1 — Foundation & Correctness
*Why first: every later feature reuses the auth layer, the emotion vectors, the pagination, and the session model. Fix them once here. Nothing public ships on plaintext tokens, dead rate limiting, and a broken engine. This whole phase is the audit's P0/P1 turned into backend work.*

### Correctness
- [ ] **B1.1** Fix the DNA engine emotion vocabulary `[P0-1]` `[internal]` — every `primary_emotions`/`anti_emotions` slug must be a canonical `VALID_SLUGS` value; run `canonicalize()` on read so legacy rows count. *This is the product's core output; it's wrong until this lands.*
- [ ] **B1.2** Fix unlock checks using dead slug `"2am"` → `"two_am"` in `entries.py`, `dna.py`, `user.py` `[P0-6]` `[internal]`.
- [ ] **B1.3** Unify the DNA/heatmap/stats caching regime — one source of truth, consistent invalidation `[P2-5]` `[internal]`.
- [ ] **B1.4** Correct keyset pagination (stable tie-break on `(created_at, id)`, reject invalid cursors, drop the two wasted COUNT queries) `[P2-2]` `[CONTRACT]` `[FE-facing]`.
- [ ] **B1.5** Remove GET-with-side-effects (`GET /user/room` writing on read) `[P2-7]` `[internal]`.
- [ ] **B1.6** One session/transaction pattern; schedule background work **post-commit** (use `BackgroundTasks`, not fire-and-forget `create_task` inside the handler) `[P2-1, P3-4]` `[internal]`.

### Security
- [ ] **B1.7** `await` every rate limiter; move lockout + limiter + cache state to shared Redis so it works across workers `[P0-2, P1-4]` `[internal]`.
- [ ] **B1.8** Validate `cover_url` on write (scheme + host allowlist), and kill SSRF on the server-side image fetch (allowlist, size cap, no redirects) `[P0-3]` `[internal]`.
- [ ] **B1.9** Add `aiosmtplib` to requirements; make email send failures **loud**, not silently "sent" `[P0-4]` `[internal]`.
- [ ] **B1.10** Hash refresh tokens at rest; rotate; **revoke on password change/reset**; move refresh token to httpOnly/Secure/SameSite cookie `[P1-1]` `[CONTRACT]` `[FE-facing]` — *coordinate cookie contract with frontend token handling (F1.1).*
- [ ] **B1.11** Fix error-handler CORS (`ACAO: *` + credentials is invalid and leaks) `[P1-2]` `[internal]`.
- [ ] **B1.12** Close enumeration + lockout-as-DoS: generic register/login errors, constant-time forgot-password, don't 404-vs-200 on usernames, rethink email-keyed lockout `[P1-3]` `[FE-facing]` (error copy changes).
- [ ] **B1.13** Trust `X-Forwarded-For` only from the known proxy `[P1-5]` `[internal]`.
- [ ] **B1.14** Migrate `python-jose` → `PyJWT` (CVEs) `[P1-7]` `[internal]`.
- [ ] **B1.15** Dockerfile: non-root user, `.dockerignore`, HEALTHCHECK, **install fonts** so image generation works `[P1-8, P0-5]` `[internal]`.

### Cleanup that unblocks later work
- [ ] **B1.16** Rename the glob-accident migration `c5d6e7f8a9b0_*.py`; verify the chain `[P6-2]` `[internal]`.
- [ ] **B1.17** Resolve config/env drift (token expiry, ports) `[P3-5]` `[internal]`.
- [ ] **B1.18** Park the duplicate/unused room APIs and dual `RoomResponse` schemas `[P3-2]` `[internal]` — the Room feature is deferred `[blueprint §0.5]`; don't delete data, just stop maintaining two contracts.

### Testing (the safety net for everything after)
- [ ] **B1.19** Stand up `pytest` + CI (GitHub Actions) `[P6-1]` `[internal]`.
- [ ] **B1.20** Write the test that would have caught `[P0-1]`: assert every personality emotion ∈ `VALID_SLUGS`. Plus engine output fixtures.
- [ ] **B1.21** Auth flow tests: register/login/refresh-rotation/lockout/reset-revokes-tokens.
- [ ] **B1.22** One end-to-end API smoke test (register → log book → get profile).

**`[GATE]` Phase 1 Definition of Done:** CI green; engine invariant + auth tests pass; no un-awaited limiter; refresh tokens hashed and cookie-based; no server-side fetch of unvalidated URLs; pagination contract published for the frontend.

---

## PHASE 2 — The Private Mirror (the real product)
*Why here: the mirror is the moat and the thing people return to. It's also what generates the rich per-user data any future public/social surface needs. Everything in this phase is private by default. Get the core loop excellent before opening anything up.*

### The identity/visibility spine (do first — everything reads from it)
- [ ] **B2.1** Implement the visibility model `[§2.3]`: `profile_visibility` enum defaulting to `private`; collapse the tangled `is_public`/strict-public/`share_token` logic into it; share tokens become revocable, optionally-expiring capability links `[CONTRACT]` `[FE-facing]`.

### Complete the core reading loop (endpoints mostly exist — wire, secure, test them)
- [ ] **B2.2** Finish Flow endpoint (`/entries/{id}/finish`, three-beat arc) — verify ownership authz, canonicalize emotions, don't clobber per-emotion strengths `[P2-4]` `[CONTRACT]` `[FE-facing]`.
- [ ] **B2.3** Currently-reading check-ins (`/entries/{id}/checkins`, `/status`) — verify authz + contract `[CONTRACT]` `[FE-facing]`.
- [ ] **B2.4** Full entry fields respected on create/update (notes, dates, status, finish thought) so Mirror/calendar have `finished_at` to key on `[P5-7]` `[CONTRACT]` `[FE-facing]`.
- [ ] **B2.5** Emotional signature (DNA) served from the now-correct engine `[depends: B1.1]` `[FE-facing]`.
- [ ] **B2.6** Surface the existing Mirror insights + resurfaced memories `[FE-facing]`.

### Cold-start (without this, every future feature starves)
- [ ] **B2.7** Import pipeline: Goodreads/StoryGraph CSV → entries (dedupe against catalog) `[CONTRACT]` `[FE-facing]`.
- [ ] **B2.8** Fix book search backing: caching in Redis, dedupe writes, add a Google Books API key, unique constraint on `(title_normalized, author_normalized)` `[P4-5, P4-6]` `[FE-facing]`.
- [ ] **B2.9** In-library search/filter (your own books by emotion/title/author) `[CONTRACT]` `[FE-facing]`.

### Shared vocabulary (kills the FE/BE drift permanently)
- [ ] **B2.10** Publish the canonical emotion vocabulary as a single served source (endpoint or generated file the frontend consumes) `[P2-9]` `[CONTRACT]` `[FE-facing]`.

### Testing
- [ ] **B2.11** Tests: finish/checkin/status flows; visibility enforcement (private data never leaks); import parser; in-library search; DNA correctness with fixtures.

**`[GATE]` Phase 2 DoD:** a new user can import a library, log/finish a book with full fields, and see a correct emotional signature + a resurfaced memory — all private. Visibility-leak tests pass. CI green.

---

## PHASE 3 — Echo (the single public surface) + scale
*Why now: only after the mirror is rich and the safety substrate can be built does opening a public surface make sense. Echo is the one place data fans out across users, so all the scalability/elasticity work concentrates here. The safety core (block, report, crisis path) is non-negotiable and ships **with** Echo, not after `[§Feature 1]`.*

### Identity for the public surface
- [ ] **B3.1** Pseudonymous handle model `[§2]`: the handle is the only public identifier on an Echo; rate-limited change with a "previously known as" grace window `[CONTRACT]` `[FE-facing]`.

### Echo core
- [ ] **B3.2** Echo schema + service: **book-anchored** (and/or emotion-anchored), minimal composer (text + anchor), visibility, status `[CONTRACT]` `[FE-facing]`.
- [ ] **B3.3** Feed: **chronological, keyset-paginated, no counts of any kind in the response**; book-page and emotion-page views; feed **ends** (caught-up state) `[§Feature 1]` `[CONTRACT]` `[FE-facing]`.
- [ ] **B3.4** Replies `[CONTRACT]` `[FE-facing]`.
- [ ] **B3.5** Reactions **private-aggregate only** (author sees count, public sees nothing) — or omit for v1 if you'd rather keep the surface even quieter `[FE-facing]`.

### Safety core (ships with Echo — not optional)
- [ ] **B3.6** Block + mute, enforced **server-side**, cross-surface, silent to the blocked party `[§Feature 1]` `[CONTRACT]` `[FE-facing]`.
- [ ] **B3.7** Report → categorized → **auto-throttle above threshold** → moderation queue; reporter-reputation weighting to resist report-bombing `[CONTRACT]` `[FE-facing]`.
- [ ] **B3.8** Crisis path: a lightweight classifier flags self-harm content → route the *author* to a supportive interstitial with resources rather than a punitive block; flag for review `[§Feature 1]` `[FE-facing]`.
- [ ] **B3.9** Pre-publish guards: length caps, new-account cool-down, PII/slur/threat classifier → hold-for-review `[internal]`.

### Scalability & elasticity (Echo is the fan-out surface)
- [ ] **B3.10** Stateless API workers (no in-memory feed/session state) so you can add instances elastically `[internal]`.
- [ ] **B3.11** Feed read path served from cache + read replicas; keyset cursors; author-frequency caps `[internal]`.
- [ ] **B3.12** Redis-backed rate limits/caches confirmed shared across the fleet `[depends: B1.7]` `[internal]`.

### Minimal notifications (Echo introduces the first real one)
- [ ] **B3.13** In-app "someone replied to your echo" (batched) `[CONTRACT]` `[FE-facing]` — the full notification system is Phase 4.

### Testing
- [ ] **B3.14** Tests: block/mute enforcement (both directions), report auto-throttle, report-bombing resistance, crisis-classifier routing, feed pagination correctness, "no counts leak in feed payload."

**`[GATE]` Phase 3 DoD:** a pseudonymous user can post a book-anchored Echo, read a chronological countless feed that ends, reply, block, and report; self-harm content triggers the supportive path; safety-enforcement and no-leak tests pass; feed serves from cache/replica under load test. CI green.

---

## PHASE 4 — Calm Notifications + polish
*Why here: notifications only make sense once there's something worth being told about (Echo replies). Built calm-first: batched, digest-default, no re-engagement guilt `[§Feature 5]`.*
- [ ] **B4.1** Notification service with tiers (0 security / 1 direct-batched / 2 weekly-digest) `[CONTRACT]` `[FE-facing]`.
- [ ] **B4.2** Weekly "your reading week" + resurfaced-memory digest job `[FE-facing]`.
- [ ] **B4.3** Preferences + quiet hours + timezone `[CONTRACT]` `[FE-facing]`.
- [ ] **B4.4** Tests: tier routing, quiet-hours precedence (security bypasses), batching, digest generation.

**`[GATE]` Phase 4 DoD:** users get a weekly digest and batched reply notifications, fully user-controllable; security notices always deliver; no per-event spam. CI green.

---

## PHASE 5 — Future (parked)
*Explicitly on hold until earlier phases retain. Do not start without reopening the blueprint.*
- Twin (reader-matching) — reuse DNA vectors, batch pipeline, explainable matches `[§Feature 4]`. Parked per your decision.
- Book pages as destinations (emotional consensus per book).
- Recommendation depth (emotional-adjacency).
- Reading Room, reconsidered without dopamine mechanics.

---

## Start here (backend, week 1)
1. **B1.19** CI + pytest scaffold (so every fix lands verified).
2. **B1.1 + B1.20** Fix the engine and lock it with the invariant test.
3. **B1.7** Await limiters + Redis state (unblocks all later abuse-prevention).

---

## Change log
- 2026-07-11 — Initial roadmap. Reflects locked decisions: private-by-default, Echo as sole public surface, no chat, Twin parked.
