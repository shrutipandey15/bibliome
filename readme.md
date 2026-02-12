# ◈ Book DNA — API

**The emotional fingerprint of your reading life.**

Book DNA doesn't care what you rated a book. It cares what the book did to you — the 2AM spiral, the grief that crept in sideways, the comfort that felt like coming home. This is the backend that powers that experience.

---

## What This Is

A RESTful API that tracks the emotional aftermath of books and distills it into a reading personality. Built with FastAPI and PostgreSQL, designed for speed, designed for feelings.

Every book entry captures not a rating but an emotional signature — which of ten core emotions it triggered, how intensely, and the one line that hit hardest. From that data, the DNA Engine calculates your reading personality type, tracks how it shifts over time, and generates shareable visual cards.

---

## The Emotional Vocabulary

Book DNA recognizes ten emotions that books leave behind:

| Emotion | What It Means |
|---------|--------------|
| 🔥 **Rage** | The book made you angry — at a character, the world, or yourself |
| 🧣 **Comfort** | A warm, safe feeling. Like a book-shaped hug |
| 🌀 **Dread** | A creeping unease that followed you off the page |
| 🌿 **Healing** | Something shifted. Something got lighter |
| 💜 **Obsession** | You couldn't stop thinking about it. Still can't |
| 🌊 **Grief** | Loss — real, fictional, anticipated |
| 👁 **Seen** | The book knew you. Uncomfortably well |
| ⚡ **Chaos** | Rules broken, expectations shattered |
| ◻️ **Nothing** | Emotional numbness. Which is its own kind of feeling |
| 🌙 **2AM** | You stayed up too late. No regrets |

---

## Reading Personality Types

The DNA Engine maps your emotional patterns to one of eight archetypes:

- **◈ The Grief Romantic** — seeks books that break the heart because feeling deeply is how they know they're alive
- **◇ The Control-Seeking Intellectual** — reads to master the chaos, understanding as armor
- **◆ The Soft Masochist** — chooses pain on purpose, trusts books that hurt
- **○ The Comfort Architect** — builds emotional safety through stories
- **△ The Midnight Arsonist** — reads like setting fire to their own beliefs
- **□ The Quiet Witness** — absorbs everything, processes in silence
- **♡ The Obsessive Romantic** — doesn't read books, falls into them
- **◎ The Emotional Archaeologist** — digs into stories looking for buried parts of themselves

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI (async Python) |
| Database | PostgreSQL |
| ORM | SQLAlchemy (async) |
| Migrations | Alembic |
| Auth | JWT access + refresh tokens with rotation |
| Images | Pillow (OG card generation) |
| Deployment | Docker-ready |

---

## API Endpoints

### Auth
```
POST   /api/auth/register     Create account
POST   /api/auth/login        Get tokens
POST   /api/auth/refresh      Rotate tokens
GET    /api/auth/me           Current user
```

### Book Entries
```
GET    /api/entries            List entries (paginated)
POST   /api/entries            Log a new book
GET    /api/entries/{id}       Get single entry
PUT    /api/entries/{id}       Update entry
DELETE /api/entries/{id}       Delete entry
```

### DNA & Analytics
```
GET    /api/dna/profile        Live personality calculation
POST   /api/dna/generate       Save a DNA snapshot (min 3 books)
GET    /api/dna/heatmap        Emotion × book matrix
GET    /api/dna/stats          Reading statistics
GET    /api/dna/history        Past DNA snapshots
```

### User Settings
```
GET    /api/user/settings      Get profile settings
PATCH  /api/user/settings      Update (display name, public/private)
```

### Public (no auth)
```
GET    /api/public/echoes/{username}       Public echoes
GET    /api/public/card/{username}         DNA card data
GET    /api/public/card/{username}/og      DNA card as PNG image
GET    /api/public/echo/{entry_id}/og      Echo card as PNG image
```

---

## Setup

```bash
# Clone
git clone <repo-url>
cd book-dna-api

# Environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env — set your DATABASE_URL and SECRET_KEY

# Database
createdb bookdna
alembic upgrade head

# Run
uvicorn app.main:app --reload
```

API docs at [http://localhost:8000/docs](http://localhost:8000/docs)

---

## Project Structure

```
app/
├── main.py                 Application entry
├── config.py               Environment configuration
├── database.py             Async engine + session
├── models/                 SQLAlchemy models
│   ├── user.py
│   ├── book_entry.py
│   ├── entry_emotion.py
│   ├── dna_snapshot.py
│   └── refresh_token.py
├── schemas/                Pydantic request/response models
│   ├── auth.py
│   ├── entries.py
│   ├── dna.py
│   ├── public.py
│   └── user.py
├── services/               Business logic
│   ├── auth_service.py     JWT + password hashing
│   ├── entry_service.py    CRUD operations
│   ├── dna_engine.py       Personality algorithm
│   └── og_image.py         Card image generation
├── routers/                API route handlers
│   ├── auth.py
│   ├── entries.py
│   ├── dna.py
│   ├── public.py
│   └── user.py
├── middleware/
│   └── auth.py             JWT dependency
└── utils/
    └── emotions.py         Emotion definitions
```

---

## The DNA Engine

The personality algorithm scores users across four dimensions:

1. **Emotion frequency** — which emotions appear most across all entries
2. **Intensity weighting** — high-intensity emotions count more
3. **Recency bias** — recent books influence your profile more than older ones
4. **Co-occurrence patterns** — grief + seen appearing together means something different than grief alone

Each personality type has primary emotions, anti-emotions (which reduce its score), blind spots, and comfort tropes. The engine also detects emotional blind spots — emotions you never tag — and tracks co-occurrence patterns for deeper analysis.

Phase 1 (current): Rule-based scoring
Phase 2 (planned): Pattern detection from aggregate user data
Phase 3 (planned): AI-powered with Claude API

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost/bookdna` | Async Postgres connection |
| `SECRET_KEY` | `change-me-in-production` | JWT signing key |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime |

---

## License

Private. Not open source.

---

*Because the books that change you deserve more than a star rating.*