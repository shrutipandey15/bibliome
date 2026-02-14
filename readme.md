# Book DNA API

Backend API for logging book entries with emotion tags and generating a reading "DNA" profile.

This README reflects the current implementation and operational constraints.

## What It Does

- User accounts with JWT auth (access + refresh tokens)
- Book entry CRUD per user
- Emotion tagging per entry (`entry_emotions`)
- Rule-based personality calculation from emotion history
- Cached DNA profile on `users.cached_dna_profile`
- DNA snapshots stored in `dna_snapshots`
- Public profile endpoints (echoes + card data + OG images)
- Book search endpoint backed by Google Books data, with request/response contract exposed via OpenAPI docs

## Stack

- Python / FastAPI
- SQLAlchemy async + PostgreSQL (`asyncpg`)
- Alembic migrations
- JWT via `python-jose`
- Password hashing via `bcrypt`
- Pillow for OG image generation
- Optional Redis-backed rate limiting (falls back to in-memory)

## API Surface

Base prefix: `/api` (configurable via `API_V1_PREFIX`).

Health:
- `GET /health`

Auth:
- `POST /api/auth/register`
- `POST /api/auth/login`
- `POST /api/auth/refresh`
- `GET /api/auth/me`

Entries:
- `GET /api/entries`
- `POST /api/entries`
- `GET /api/entries/{entry_id}`
- `PUT /api/entries/{entry_id}`
- `DELETE /api/entries/{entry_id}`

DNA:
- `GET /api/dna/profile`
- `POST /api/dna/generate`
- `GET /api/dna/heatmap`
- `GET /api/dna/stats`
- `GET /api/dna/history`
- `GET /api/dna/recap?month=YYYY-MM`
- `GET /api/dna/twin`

User:
- `GET /api/user/settings`
- `PATCH /api/user/settings`

Public (no auth):
- `GET /api/public/echoes/{username}`
- `GET /api/public/card/{username}`
- `GET /api/public/card/{username}/og`
- `GET /api/public/echo/{entry_id}/og`

Books:
- `GET /api/books/search?q=...` (auth required)

## Data Model

Tables:
- `users`
- `book_entries`
- `entry_emotions`
- `dna_snapshots`
- `refresh_tokens`

Important fields:
- `users.dna_dirty` + `users.cached_dna_profile` for profile caching
- `book_entries.intensity` constrained to `1..10`
- `entry_emotions.strength` constrained to `1..10`
- unique constraint on `(entry_id, emotion_id)`

## DNA Engine (Current Behavior)

The personality engine in `app/services/dna_engine.py` is rule-based.

It uses:
- Emotion frequency
- Average emotion intensity
- Recency weighting
- Emotion co-occurrence
- Penalties for anti-emotions

Returns:
- winning personality type (8 predefined types)
- score breakdown
- top emotions
- avoided emotions
- co-occurrence summary

`POST /api/dna/generate` and `GET /api/dna/twin` require at least 3 entries.

## Rate Limiting

Defined in `app/middleware/rate_limit.py`:
- Auth limiter: 10 requests / 60s per IP
- DNA generate limiter: 5 requests / 300s per IP

Behavior:
- Uses Redis sorted sets if `REDIS_URL` is configured and reachable
- Falls back to in-memory limiter if Redis is unavailable

## Project Layout

```text
app/
  main.py
  config.py
  database.py
  middleware/
    auth.py
    error_handlers.py
    rate_limit.py
  models/
    user.py
    book_entry.py
    dna_snapshot.py
    refresh_token.py
  routers/
    auth.py
    entries.py
    dna.py
    user.py
    public.py
    books.py
  schemas/
    auth.py
    entry.py
    dna.py
    public.py
    user.py
  services/
    auth_service.py
    entry_service.py
    dna_engine.py
    background.py
    book_search.py
    og_image.py
  utils/
    emotions.py
alembic/
  versions/
```

## Local Setup

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Set at minimum:
- `DATABASE_URL`
- `SECRET_KEY`

### 3. Run migrations

```bash
alembic upgrade head
```

### 4. Start API

```bash
uvicorn app.main:app --reload
```

Docs:
- Swagger UI: `http://localhost:8000/docs`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

## Docker

```bash
docker build -t book-dna-api .
docker run --rm -p 8000:8000 --env-file .env book-dna-api
```

The container command runs:
- `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4`

## Environment Variables

From `app/config.py`:

- `DATABASE_URL` (default: `postgresql+asyncpg://postgres:postgres@localhost:5432/bookdna`)
- `SECRET_KEY` (must be changed for production)
- `ALGORITHM` (default: `HS256`)
- `ACCESS_TOKEN_EXPIRE_MINUTES` (default: `15`)
- `REFRESH_TOKEN_EXPIRE_DAYS` (default: `7`)
- `CORS_ORIGINS` (comma-separated)
- `REDIS_URL` (optional)
- `ENVIRONMENT` (`development` / `production`)
- `APP_NAME` (default: `Book DNA`)
- `API_V1_PREFIX` (default: `/api`)

## Current Limitations (Honest Snapshot)

- Automated tests are not yet included in this repository.
- Background DNA recalculation currently runs as in-process FastAPI background work rather than a separate job queue.
- Book search relies on external API availability (Google Books) and network access; endpoint behavior is documented via OpenAPI.
- The personality algorithm is currently deterministic and rule-based, rather than trained on user outcome data.
- Redis rate limiting is optional; if Redis is absent, limiting falls back to per-process memory.

## License

Public repository. No explicit license file is currently included in this repo.

*Because the books that change you deserve more than a star rating.*