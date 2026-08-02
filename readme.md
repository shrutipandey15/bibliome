# 📖 Bibliome

**Your reading leaves fingerprints. This maps them.**

Bibliome is a reading personality engine. You log books with the emotions they made you feel — not star ratings, not "liked it" — and the system builds a psychological profile of who you are as a reader.

Are you a Grief Romantic? A Control-Seeking Intellectual? A Soft Masochist who keeps picking up books that destroy you? Bibliome will tell you.

🔗 **Live:** [bibliome.app](https://bibliome.app)  
🎨 **Frontend repo:** [bibliome-frontend](https://github.com/shrutipandey15/bibliome-frontend)

---

## How It Works

```
You read a book.
You felt something.
You tell Bibliome what.
      ↓
18 emotions × intensity scoring × recency weighting
      ↓
Personality type + emotional profile + shareable DNA card
```

The engine tracks emotion frequency, co-occurrence, intensity patterns, and what you *avoid* feeling. Five books and you get your first reading DNA. The more you add, the sharper the portrait.

---

## What's Under the Hood

### Smart Book Search

Not your average title lookup. Three-layer architecture that learns:

```
Layer 1 → Local catalog    (< 5ms)   PostgreSQL trigram fuzzy matching
Layer 2 → Google Books + Open Library  (parallel, ~300ms)
Layer 3 → Merge, deduplicate, score, rank
```

Every search teaches the system. Every book a user adds makes the next search faster. Typo in "forth wing"? Trigram index still finds *Fourth Wing*. The catalog starts empty and grows organically — the more people use it, the smarter it gets.

### DNA Engine

Rule-based personality calculator. No ML, no black boxes — deterministic and explainable.

Inputs: emotion frequency, average intensity, recency weighting, co-occurrence patterns, anti-emotion penalties.

Output: one of 8 personality archetypes, score breakdown, top emotions, avoided emotions, co-occurrence map.

The algorithm is opinionated by design. It doesn't just count what you read — it weighs *how recently* you felt something, *how strongly*, and *what you consistently avoid*.

### Auth Hardening

- 5 failed logins → 15-minute lockout
- Registration rate limiting (3/hour per IP)
- Timing-safe responses (constant delay defeats enumeration)
- Disposable email blocking
- Password strength enforcement with real-time feedback

### Admin Dashboard

Stats, user management, book catalog browser (searchable, sortable by popularity/recency/title), database health monitoring, token cleanup. Everything you need to run a small-scale literary observatory.

---

## Stack

| Layer | Tech |
|-------|------|
| API | Python 3.11+, FastAPI, async everywhere |
| Database | PostgreSQL + asyncpg, pg_trgm for fuzzy search |
| Migrations | Alembic |
| Auth | JWT (PyJWT) + bcrypt |
| External | Google Books API, Open Library API (parallel via httpx) |
| Rate limiting | Redis (optional, falls back to in-memory) |

---

## API

Base: `/api`

| Group | Endpoints | Auth |
|-------|-----------|------|
| **Auth** | register, login, refresh, me | Public |
| **Entries** | CRUD with cursor pagination | JWT |
| **DNA** | profile, generate, heatmap, stats, patterns, evolution, history, recap, emotional-calendar, blind-spots | JWT |
| **Books** | search (smart multi-source), aggregate profile | JWT |
| **User** | settings (get/patch) | JWT |
| **Echo** | feed, thread, replies, reactions, reports | JWT |
| **Public** | shared DNA card by revocable token | None |
| **Admin** | dashboard, users, catalog, db-health, cleanup | Admin |

Full OpenAPI docs at `/docs` when running locally. Disabled in production
(`/docs`, `/redoc` and `/openapi.json` all 404) — they enumerate every route,
including `/api/admin/*`.

`GET /api/meta/version` reports the git SHA of the running build.

---

## Data Model

```
users ──────────┐
  cached_dna    │
  is_admin      │
  dna_dirty     │
                │
book_entries ───┤ (user_id FK)
  title, author │
  intensity 1-10│
  quote, echo   │
                │
entry_emotions ─┘ (entry_id FK)
  emotion_id
  strength 1-10

books ──────────── self-growing catalog
  title_normalized  (trigram GIN index)
  isbn_13, isbn_10  (unique indexes)
  popularity        (bumped on user add)
  source            (google / openlibrary / user)

dna_snapshots ──── historical profiles
refresh_tokens ─── JWT tracking
```

---

## Run Locally

```bash
./scripts/dev.sh              # → localhost:8000/docs
```

That's the whole thing. The script is idempotent: it builds the venv if missing
(or if the repo has been moved, which breaks the venv's absolute shebangs),
syncs dependencies only when `requirements-dev.txt` changes, checks Postgres is
up, runs pending migrations, frees the port if a previous run is still on it,
then starts uvicorn with autoreload. A warm start skips straight to the server.

| Flag | Effect |
|------|--------|
| `--port N` | listen on N instead of 8000 |
| `--sql` | echo every SQL statement (query debugging) |
| `--rebuild` | force a venv rebuild |
| `--no-reload` | disable autoreload |

Tests: `venv/bin/pytest`

### Environment

| Variable | Required | Default |
|----------|----------|---------|
| `DATABASE_URL` | Yes | `postgresql+asyncpg://...localhost.../bibliome` |
| `SECRET_KEY` | Yes | — |
| `CORS_ORIGINS` | For frontend | — |
| `REDIS_URL` | No | Falls back to per-worker in-memory |
| `ENVIRONMENT` | No | `development` |
| `TRUSTED_PROXY_COUNT` | Behind a proxy | `0` (trust the socket peer) |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | No | `5` / `5` per worker |
| `GIT_SHA` | No | `unknown` — stamped at deploy time |

Full list with prod defaults: [`.env.example`](./.env.example) and
[`deploy/env.production`](./deploy/env.production).

---

## Deploy

```
Cloudflare edge ──▶ cloudflared (loopback) ──▶ nginx :80 ──▶ uvicorn 127.0.0.1:8100 ──▶ PostgreSQL
                                                                        └──▶ Redis
```

TLS terminates at Cloudflare and the tunnel dials `http://localhost:80`, so
there is **no certbot** and no public `:443` — `.app` is HSTS-preloaded, and the
edge cert covers the browser side. nginx also serves the built SPA, which is why
the frontend talks to a same-origin `/api`.

`deploy/` holds the single source of truth for both machine configs:

| File | Installed to |
|------|--------------|
| `deploy/bibliome.nginx.conf` | `/etc/nginx/sites-available/bibliome` (via `envsubst`) |
| `deploy/bibliome.service` | `/etc/systemd/system/bibliome.service` |

```bash
sudo bash deploy/deploy.sh    # first run, or after changing either config above
sudo bash deploy/update.sh    # code-only: pull, migrate, rebuild, restart
```

Routine restart:

```bash
git pull
alembic upgrade head
sudo systemctl restart bibliome
```

After a deploy, confirm what's actually running and that nginx is passing the
real client IP through (not `127.0.0.1`, which would rate-limit everyone as one
client):

```bash
curl -s https://bibliome.app/api/meta/version
curl -s https://bibliome.app/health          # {"status":"ok","db":"up"}, 503 if Postgres is down
```

---

## Project Structure

```
app/
├── main.py, config.py, database.py
├── middleware/    auth · rate_limit · error_handlers
├── models/       user · book_entry · book · dna_snapshot · refresh_token
├── routers/      auth · entries · dna · user · public · books · admin
├── schemas/      auth · entry · dna · public · user
├── services/     auth_service · entry_service · dna_engine
│                 book_search · background · echo_service
└── utils/        emotions (18 emotions, colors, icons)
```

---

## What's Next

See [ROADMAP.md](./ROADMAP.md) for bugs, gaps, and planned features — from Goodreads import to AI-generated reading personality narratives.

---

## License

MIT

---

*"A reader lives a thousand lives before he dies. The man who never reads lives only one."* — George R.R. Martin

*Bibliome remembers all of them.*