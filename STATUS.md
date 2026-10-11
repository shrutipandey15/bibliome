# Bibliome — Project Status (2026-10-11)

Checked against the code on `main` in both repos, not against the docs.
**Tests:** backend 626 passed (`pytest`, local Postgres) · frontend 478 passed, 4 skipped (`vitest`, 54 files).

Key: ✅ done · 🟡 partial · ⬜ not started · ⏸ blocked · 🅿 parked/removed on purpose

## The project
Bibliome is a private reading journal. You log books, say how they made you feel,
and it builds your "reading DNA": an emotional signature plus one of 12 archetypes.
Private by default. Echo is the only public surface.
- **Backend:** FastAPI, Postgres (40 Alembic migrations, latest is `038_dna_card`), Redis. Self-hosted behind Cloudflare Tunnel (`deploy/`).
- **Frontend:** React 18 + Vite + react-router. No UI library.
- **Live tracker:** `FEATURE_QUEUE.md`. `ROADMAP.md` is **stale**: none of its checkboxes are ticked, but most of the items are built (see below).

## Features

| Area | Backend | Frontend | Notes |
|---|---|---|---|
| Auth (register/login/reset, refresh cookie, lockout, rate limits) | ✅ | ✅ | PyJWT. Refresh token is an httpOnly cookie. |
| Library: log a book, 6 statuses, check-ins, finish flow | ✅ | ✅ | `EntryModal`, `CheckinPanel`, `FinishFlow` |
| Feelings v3 (21 feelings, 6 groups, "something else…", "How did it land?") | ✅ | ✅ | Migration 036 |
| TBR fast-add (#2) | ✅ | ✅ | |
| DNF + reason (#3), DNF insight (#4) | ✅ | ✅ | |
| Book search / catalog | ✅ | ✅ | |
| Goodreads/StoryGraph import, data export | ✅ | ✅ | `POST /entries/import`, `GET /user/export` |
| DNA engine (emotional signature, 12 archetypes, blind spots, stated-vs-revealed) | ✅ | ✅ | DNA1–4 are done. The archetype baseline is still a hand-made stand-in based on 8 readers. |
| DNA share card / OG images | ✅ | ✅ | Migration 038. Shared at `/s/:token`. |
| Mirror insights, calendar, resurfaced memories | ✅ | ✅ | |
| Journal (encrypted, per book) | ✅ | ✅ | See `journalCryptoContract.md` |
| Visibility model (`profile_visibility`, revocable share tokens) | ✅ | ✅ | The old public feed was removed, and a test keeps it removed. |
| Echo (book-anchored posts, chronological feed, replies, private reactions) | ✅ | ✅ | `/echoes` |
| Safety: block/mute, reporting, moderation, crisis routing | ✅ | ✅ | |
| Shareable collections (#5) + collection chat (#6) | ✅ | ✅ | Realtime over a websocket |
| Resonance (matching with readers who felt the same) + threads | ✅ | ✅ | `refresh_resonance.py` is run by hand |
| Notifications: tiers, quiet hours, Web Push | ✅ | ✅ | |
| Weekly digest (in-app + push) | ✅ | ✅ | Phase 1 shipped. Phase 2 (outside book data, weekly question) and Phase 3 (sealed letters, clicks page) not started. |
| Daily book fact (in-app + push) | ✅ | ✅ | 10 am local, on by default. 88 checked facts + Wikipedia "On this day". |
| Admin page | ✅ | ✅ | |
| Public content pages (`/archetypes`, `/vs/:slug`, landing, legal) | — | ✅ | Pre-rendered for crawlers |
| Landing page UX pass | — | ✅ | `plan.md` T1–T12 are all done. Sticky nav still hasn't been checked in a real browser. |

## Not built yet (queue order: 7 → 12)
| # | Feature | State | Blocker |
|---|---|---|---|
| 7 | Book emotion vectors (LLM, column on `books`) | ⬜ | None. **Next up.** Unblocks 8–12. |
| 8 | Prescription mode (pick a feeling → books, works logged out) | ⬜ | #7 |
| 9 | Blind-spot recs | ⬜ | #7. Blind spots are already computed; there are no recs yet. |
| 10 | Stated-vs-revealed recs | ⬜ | #7 |
| 11 | Resonance recs ("readers who felt this also loved…") | ⬜ | Resonance matching already exists. |
| 12 | Outcome prediction ("this will wreck you" + confirm/refute) | ⬜ | #7 + more data |

## Blocked / parked / removed
- ⏸ **#13 Marginalia:** blocked since 2026-08-16. No code was found for it, and there is no age-gating anywhere. It needs you to point at where it lives.
- 🅿 **Old global public feed:** removed on purpose (private-by-default).
- 🅿 **Twin, Room:** parked (`ROADMAP.md`). The old "no chat" rule was overridden by collection chat (#6).

## Housekeeping
- `ROADMAP.md` checkboxes need ticking or retiring. `FEATURE_QUEUE.md` is the real tracker.
- The untracked `*.patch` files in both repos are **already committed** (migrations 036–038 are in the tree), so they can be deleted. The frontend's `audit.md`, `decision.md`, `plan.md`, `ui-audit.md` and `vibecheck.md` are untracked too.
- Ops jobs are all run by hand: `refresh_archetype_baseline.py` (once the DB has ≥15 readers), `refresh_resonance.py` (meant to run nightly, but nothing schedules it).
