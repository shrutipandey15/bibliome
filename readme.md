# 📖 Book DNA

**Your reading leaves fingerprints. This maps them.**

Book DNA is a reading personality engine. You log books with the emotions they made you feel — not star ratings, not "liked it" — and the system builds a psychological profile of who you are as a reader.

Are you a Grief Romantic? A Control-Seeking Intellectual? A Soft Masochist who keeps picking up books that destroy you? Book DNA will tell you.

🔗 **Live:** [bookdna.fdev31.space](https://bookdna.fdev31.space)  
🎨 **Frontend repo:** [bookDNA-frontend](https://github.com/shrutipandey15/bookDNA-frontend)

---

## How It Works

```
You read a book.
You felt something.
You tell Book DNA what.
      ↓
24 emotions × intensity scoring × recency weighting
      ↓
Personality type + emotional profile + shareable DNA card
```

The engine tracks emotion frequency, co-occurrence, intensity patterns, and what you *avoid* feeling. Three books and you get your first reading DNA. The more you add, the sharper the portrait.

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
| Auth | JWT (python-jose) + bcrypt |
| External | Google Books API, Open Library API (parallel via httpx) |
| Images | Pillow for OG card generation |
| Rate limiting | Redis (optional, falls back to in-memory) |

---

## API

Base: `/api`

| Group | Endpoints | Auth |
|-------|-----------|------|
| **Auth** | register, login, refresh, me | Public |
| **Entries** | CRUD with cursor pagination | JWT |
| **DNA** | profile, generate, heatmap, stats, history, recap, twin | JWT |
| **Books** | search (smart multi-source) | JWT |
| **User** | settings (get/patch) | JWT |
| **Public** | echoes, card data, OG images | None |
| **Admin** | dashboard, users, catalog, db-health, cleanup | Admin |

Full OpenAPI docs at `/docs` when running locally.

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
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set DATABASE_URL + SECRET_KEY
alembic upgrade head
uvicorn app.main:app --reload # → localhost:8000/docs
```

### Environment

| Variable | Required | Default |
|----------|----------|---------|
| `DATABASE_URL` | Yes | `postgresql+asyncpg://...localhost.../bookdna` |
| `SECRET_KEY` | Yes | — |
| `CORS_ORIGINS` | For frontend | — |
| `REDIS_URL` | No | Falls back to in-memory |
| `ENVIRONMENT` | No | `development` |

---

## Deploy

Production: nginx → uvicorn (127.0.0.1:8100) → PostgreSQL

```bash
git pull
alembic upgrade head
sudo systemctl restart bookdna
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
│                 book_search · background · og_image
└── utils/        emotions (24 emotions, colors, icons)
```

---

## What's Next

See [ROADMAP.md](./ROADMAP.md) for bugs, gaps, and planned features — from Goodreads import to AI-generated reading personality narratives.

---

## License

MIT

---

*"A reader lives a thousand lives before he dies. The man who never reads lives only one."* — George R.R. Martin

*Book DNA remembers all of them.*