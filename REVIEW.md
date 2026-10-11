# Bibliome Codebase Review Rules

Stack: FastAPI · PostgreSQL · Redis · React/Vite · self-hosted (Pico PC → Cloudflare Tunnel) · backups to Backblaze B2
Scope: both repos (backend + frontend), plus the contract between them.

You can follow this yourself, or drop it into the repo root and tell Claude Code: *"Review the codebase following REVIEW.md. Do not fix anything yet — produce the report."*

---

## Part 0 — Rules for the reviewer

These govern *how* the review is done. They matter more than any checklist item.

1. **Review, don't fix.** Findings first, fixes later. Mixing the two hides what was actually wrong and makes regressions harder to trace.
2. **No finding without evidence.** Every finding cites `file:line` and says *why* it's wrong — a failing test, a repro, a query plan, or a clear reasoning trace. "This looks off" is not a finding.
3. **Label certainty.** Mark each finding `VERIFIED` (reproduced / proven) or `SUSPECTED` (needs confirmation). Never present a suspicion as fact.
4. **Severity is about impact, not effort.**
   - **P0** — security hole, data leak, data loss, prod-down risk
   - **P1** — bug or regression users will hit
   - **P2** — correctness risk, missing test on critical path, perf problem at expected load
   - **P3** — maintainability, style, nits
5. **Every finding has a proposed test.** If you can't describe the test that would catch it, you don't understand the bug yet.
6. **Check both sides of every boundary.** Backend ↔ frontend, API ↔ DB, engine ↔ UI. Most Bibliome bugs so far lived *between* layers (D1–D8 cross-surface bugs, dual-engine mismatch).
7. **Establish a baseline before judging anything** (Part 1). Record which tests/linters already fail so new failures aren't confused with old ones.
8. **Private data is sacred.** Anything touching the per-book journal, emotions on private profiles, or email addresses gets reviewed at P0 scrutiny regardless of how small the change looks.

---

## Part 1 — Baseline (run first, record results)

Backend:
- [ ] `ruff check .` and `ruff format --check .`
- [ ] `mypy` or `pyright` (note how much is typed at all)
- [ ] `pytest --cov` — record pass/fail count and coverage of `dna/`, `auth/`, `routers/`
- [ ] `bandit -r .` (security lint)
- [ ] `pip-audit` (known-vulnerable dependencies)
- [ ] `alembic check` / confirm models and migrations are in sync; `alembic upgrade head` on a fresh DB works
- [ ] `python dna_contract_check.py` against both repos

Frontend:
- [ ] `npm run lint`, `tsc --noEmit` (if TS)
- [ ] `npm run build` — note warnings and bundle size
- [ ] `npm audit --omit=dev`

Repo-wide:
- [ ] `gitleaks detect` **over full git history**, both repos (the VAPID key taught us history matters, not just HEAD)
- [ ] Confirm `.env*` is gitignored and a `.env.example` exists with no real values
- [ ] Fresh clone → README setup steps → app runs. If not, that's a finding.

---

## Part 2 — Security (P0 pass, do this before anything else)

**Secrets**
- [ ] No keys, tokens, passwords, DSNs anywhere in code or history; anything ever committed is rotated, not just deleted
- [ ] Secrets load from env via a settings object (`pydantic-settings`) that fails fast at startup if missing

**Authentication**
- [ ] Passwords hashed with argon2 or bcrypt; hashing runs off the event loop
- [ ] Password-reset tokens are random, single-use, short-lived, stored hashed, and invalidated on password change
- [ ] Rate limiting (Redis-backed) on login, signup, password reset, email resend, and any endpoint that sends mail
- [ ] Rate limiter keys on the *real* client IP — behind Cloudflare that's `CF-Connecting-IP`, not the tunnel's IP (otherwise everyone shares one bucket, or it's trivially bypassed)
- [ ] Session/JWT expiry, refresh, and logout actually revoke access
- [ ] Disposable-email and email-format checks are enforced server-side, normalized (case, `+tags`, dots for Gmail) before checking

**Authorization — the most common real-world bug**
- [ ] Every endpoint that takes an ID (`book_id`, `entry_id`, `user_id`, `journal_id`) checks the requester owns it or is allowed to see it. Test: user A requests user B's journal entry → must 403/404
- [ ] Private profiles/entries never appear in Echoes, Resonance, Marginalia, recommendations, public DNA cards, or OG images
- [ ] Admin/debug endpoints are absent in prod or properly gated

**Input & mass assignment**
- [ ] PATCH/PUT use explicit Pydantic update schemas — no `setattr` loops over request dicts (a user must not be able to set `is_admin`, `email_verified`, `id`, etc.)
- [ ] No f-string / string-concat SQL; raw SQL uses bound parameters
- [ ] String lengths capped (journal text, bios, tags) at schema and DB level
- [ ] Any URL the server fetches (cover images, avatars) is validated → no SSRF to local network

**Web surface**
- [ ] CORS: explicit origin list (`https://bibliome.app`), no `*` with credentials
- [ ] OG-tag middleware HTML-escapes every user-supplied value it injects (display names, archetype text) — this is a server-side XSS vector React can't protect you from
- [ ] No `dangerouslySetInnerHTML` with user content in React
- [ ] Security headers set (CSP, `X-Content-Type-Options`, `Referrer-Policy`, HSTS)
- [ ] Errors return generic messages in prod; no stack traces, SQL, or file paths leak
- [ ] Auth token storage: if in `localStorage`, note the XSS trade-off; httpOnly cookies are safer
- [ ] Web push: VAPID keys from env; subscription endpoints are per-user and can't be registered for someone else

---

## Part 3 — Backend correctness

**Async & concurrency**
- [ ] No blocking calls inside `async def` (sync DB driver, `requests`, `time.sleep`, bcrypt, SMTP, heavy DNA math). These freeze the whole server for every user
- [ ] Check-then-insert patterns are backed by DB unique constraints (double-tap "add book" must not create duplicates)
- [ ] Multi-step writes (e.g., add entry + update DNA + invalidate cache) are in one transaction, or explicitly designed to tolerate partial failure
- [ ] Background tasks log failures; nothing fails silently

**Data layer**
- [ ] No N+1 queries on list endpoints (shelf, feed, Echoes) — check with SQL logging on
- [ ] Indexes on every FK and every column used in `WHERE`/`ORDER BY`
- [ ] Timestamps are `timestamptz`, stored in UTC, converted only at display
- [ ] Pagination has a stable, unique sort order (e.g., `created_at, id`)
- [ ] Migrations: reversible where possible, no destructive change without a data migration, safe to run on the live DB
- [ ] DB connection pool size fits the Pico's resources and worker count

**Cache (Redis)**
- [ ] Every write that affects cached data invalidates or updates the cache (stale DNA after adding a book = bug)
- [ ] App degrades gracefully if Redis is down (rate limiting fails closed for auth, cache fails open for reads)
- [ ] Keys have TTLs; nothing grows unbounded

**Error handling**
- [ ] No bare `except:` or `except Exception: pass`
- [ ] Every external call (SMTP, push, B2, any API) has a timeout and a failure path; SMTP failing must not break signup
- [ ] Every route declares a `response_model` so the response shape is enforced, not accidental

---

## Part 4 — DNA / archetype engine (Bibliome's core — treat as P1 minimum)

**Single source of truth**
- [ ] Emotion slugs (19), families (6), and archetypes (10) are defined in *one* place and imported everywhere — frontend, backend, OG cards, recommendations
- [ ] Only one scoring implementation exists (the dual-engine 42.7% mismatch must not come back through a second code path)
- [ ] Taxonomy is versioned; stored tags from older taxonomies are migrated or mapped, never silently dropped

**Invariants — each should be an automated test**
- [ ] Deterministic: same shelf → same archetype, every time
- [ ] Ties broken deterministically and documented
- [ ] Ranking happens on full-precision values; rounding only at display
- [ ] Scores are centered against the baseline vector; per-entry votes normalized
- [ ] No archetype overlaps the structural audit flagged as illegal
- [ ] No NaN/inf, no division by zero

**Edge cases**
- [ ] 0 books, 1 book, 2 books, exactly the threshold (3), large shelves
- [ ] A book tagged with one emotion only; a book tagged with all 19
- [ ] User deletes books until they drop below threshold
- [ ] User changes a tag on an old entry

**Distribution checks (simulated readers)**
- [ ] Run the reader simulation: no archetype wins a wildly disproportionate share (the old `control_intellectual` 43% problem)
- [ ] Confidence/stability labels are distributed sensibly across shelf sizes (the old "91% uncertain" problem)
- [ ] `PUBLIC_ARCHETYPE_MIN_STABILITY = 0.0` is a named constant with a comment explaining the Option B decision, and confidence data is still in the payload

**Contract**
- [ ] `dna_contract_check.py` passes; it runs in CI, not just manually
- [ ] Frontend handles every archetype and slug the backend can emit (including World-Diver and Adrenaline Seeker) — no missing labels, colours, or icons

---

## Part 5 — Regression hunting

- [ ] List every past fix (D1–D8, default-archetype-for-zero-tags, twin-archetype ties, scoring bias, confidence calibration, security fixes). For each: **is there a test that would fail if it came back?** Missing test = P2 finding
- [ ] `git log` since the last known-good deploy; review every change to `dna/`, auth, and models with extra care
- [ ] Look for fixes that were partially reverted or re-implemented differently in a later commit
- [ ] Snapshot the OpenAPI schema (`/openapi.json`) and diff against the last release — any removed/renamed field is a potential frontend break
- [ ] Check every frontend API call against the current backend response shape (field names, nullability, enums)
- [ ] Re-run the Sprint 1–3 DNA API checks: add 3+ books → generate DNA → load `/api/public/card/<user>/og`

---

## Part 6 — Frontend (React/Vite)

**Bugs**
- [ ] `useEffect` dependency arrays correct; no stale closures
- [ ] Fetches cancelled on unmount / rapid navigation (AbortController) — no "old response overwrites new" races
- [ ] Every data view has loading, error, *and* empty states (empty shelf, no Echoes, no Resonance matches)
- [ ] Stable, unique `key`s in lists (not array index for reorderable lists)
- [ ] Intervals, listeners, and subscriptions cleaned up
- [ ] Error boundary around major routes so one crash doesn't white-screen the app
- [ ] Double-submit protection on buttons that write data

**Config & build**
- [ ] API base URL and public keys from `import.meta.env`, nothing hardcoded to localhost or prod
- [ ] No secrets in the frontend bundle (anything in `VITE_*` is public)
- [ ] Routes lazy-loaded; images sized and compressed; check bundle size

**Mobile & accessibility (relevant to the redesign)**
- [ ] Touch targets ≥ 44px; safe-area insets respected; no horizontal scroll at 360px width
- [ ] Muted/secondary text colours pass WCAG AA contrast on the dark background (the brass accent itself is fine; greys often aren't)
- [ ] Keyboard navigation and focus states work; images have alt text; icon buttons have labels
- [ ] Emotion selection works without relying on colour alone

**Push notifications**
- [ ] Expired subscriptions (HTTP 404/410 from push service) are deleted from the DB
- [ ] Permission is requested after a user action, not on page load

---

## Part 7 — Performance & capacity

Target: comfortable at ~150–300 concurrent users on the Pico.
- [ ] `EXPLAIN ANALYZE` the five heaviest queries (shelf, DNA generation, Echoes feed, Resonance matching, recommendations)
- [ ] DNA computation cached and recomputed only when the shelf changes
- [ ] OG card images cached, not regenerated per crawler hit
- [ ] Every list endpoint paginated with a max page size
- [ ] Request body size limits set
- [ ] Quick load test (e.g., `locust` or `k6`) on the main flows to confirm the capacity estimate

---

## Part 8 — Reliability & operations

- [ ] **Restore a backup from B2 into a scratch DB.** A backup that's never been restored is a hope, not a backup
- [ ] Backups include everything needed (DB dump, uploaded files, config minus secrets) and run on a schedule with failure alerts
- [ ] Services run under systemd (or Docker restart policy) with `Restart=always`; the machine is set to power on after power loss (relevant to the spontaneous shutdowns)
- [ ] Cloudflare Tunnel runs as a service and reconnects on its own
- [ ] `/health` endpoint checks DB and Redis; an external uptime monitor pings it
- [ ] Logs are structured, rotated, and contain no passwords, tokens, journal text, or full emails
- [ ] Disk usage monitored (Postgres + logs + backups can fill a small SSD)
- [ ] Deploy process is written down and repeatable, including migrations and rollback

---

## Part 9 — Privacy & user data

- [ ] Journal content never appears in: logs, error reports, analytics, public endpoints, OG tags, recommendation inputs shared with others
- [ ] Account deletion removes user rows *and* dependent data (cascades), cached Redis keys, and push subscriptions
- [ ] Users can export their data
- [ ] Emails are only visible to their owner
- [ ] Privacy policy matches what the code actually does

---

## Part 10 — Code quality & standards (P3 unless it causes bugs)

- [ ] Clear layering: routers thin → services hold logic → models/repositories hold data access
- [ ] Type hints on public functions; Pydantic models for all request/response shapes
- [ ] Magic numbers → named constants with a comment on *why* that value (thresholds, decay rates, weights)
- [ ] Non-obvious math in the DNA engine has docstrings explaining the intent, not just the operation
- [ ] No dead code, commented-out blocks, or unused dependencies
- [ ] Duplicate logic consolidated (especially anything duplicated across the two repos)
- [ ] Dependencies pinned with a lockfile (`uv.lock` / `poetry.lock` / `requirements.txt` with versions, `package-lock.json`)
- [ ] Consistent naming between backend and frontend for the same concept (Echoes, Resonance, Marginalia, archetype slugs)

---

## Part 11 — Testing & CI

- [ ] Tests exist for: auth flows, authorization (cross-user access denied), DNA invariants, every past bug, migrations up/down
- [ ] Tests use a separate DB and never touch prod
- [ ] CI (e.g., GitHub Actions) runs lint, types, tests, `gitleaks`, and the contract check on every push
- [ ] Coverage judged by *critical paths covered*, not by a percentage

---

## Report format

Produce one report per repo plus one for the cross-repo contract.

```
## Summary
- Baseline: tests X/Y passing, lint N issues, audit N vulns
- Findings: P0 n · P1 n · P2 n · P3 n

## Findings (sorted by severity)

### [P0] Short title
- Location: path/to/file.py:123
- Status: VERIFIED | SUSPECTED
- What: one-sentence description
- Why it matters: impact on users / data / security
- Evidence: repro steps, test output, or reasoning
- Proposed fix: brief
- Test that proves the fix: brief

## Regression coverage
| Past fix | Test exists? | Test name |

## Things that are good (keep doing these)
```

Fix order afterwards: all P0 → P1 in DNA engine and auth → remaining P1 → add missing regression tests → P2 → P3 opportunistically.
