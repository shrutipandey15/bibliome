# Bibliome

Live at **[bibliome.app](https://bibliome.app)**. This repo is the API. The frontend is
[bibliome-frontend](https://github.com/shrutipandey15/bibliome-frontend).

Bibliome started as a question I couldn't answer with any reading app I'd used: _not what I read,
but what reading did to me._ Star ratings can't hold that. "4/5, would recommend" is a review of a
product. It says nothing about the fact that the book made you cry on a train.

So the unit here isn't a rating. It's an econviniencemotion, plus how hard it hit. You log a book, you pick
from 18 emotions — _it wrecked me_, _it saw me_, _it left me cold_ — and give each one a strength
from 1 to 10. Do that five times and the system will tell you something about yourself. Do it fifty
times and it gets uncomfortably specific.

---

## The vocabulary is the whole product

Everything downstream — the personality engine, the reader matching, the journal, the feed — runs on
the same 18 slugs defined in [app/utils/emotions.py](app/utils/emotions.py). One vocabulary, no
second taxonomy anywhere. That constraint is load-bearing; the one time it drifted (the frontend
grew its own copy) it silently broke the engine's scoring, and that bug is why the file now says
"single source of truth" at the top in slightly aggressive language.

They're grouped into five families, which are a UI grouping only — the DB stores the flat slug:

| Family              | Emotions                                    |
| ------------------- | ------------------------------------------- |
| It hurt             | devastation, grief, dread, rage             |
| It held me          | comfort, tenderness, joy, amusement         |
| It wanted something | longing, desire, nostalgia                  |
| It moved me         | awe, recognition, catharsis                 |
| It lost me          | boredom, revulsion, confusion, indifference |

"It lost me" is deliberately not an insult category. Those are registers of _disengagement_ — the
book failing you — so they're valid to tag and they count against you as anti-emotions in scoring,
but no personality archetype is anchored on them. Being bored a lot isn't an identity.

Old slugs still resolve. `chaos → confusion`, `wit → amusement`, `obsession → desire`. Historical
rows get remapped forward on read rather than migrated, so nothing anyone logged in 2024 stops
counting.

---

## What's actually in here

### The DNA engine

Rule-based, deterministic, ~500 lines, no ML. Given your entries it computes emotion frequency,
average intensity, recency-weighted frequency, co-occurrence, and anti-emotion penalties, then
lands you on one of eight archetypes: The Grief Romantic, The Control-Seeking Intellectual, The Soft
Masochist, The Comfort Architect, The Midnight Arsonist, The Quiet Witness, The Obsessive Romantic,
The Emotional Archaeologist.

I get asked why there's no model behind this. Because I want to be able to answer "why did it say
that about me" with an actual number, and because a hallucinated sentence about someone's inner life
is worse than no sentence. The scoring breakdown ships with the result.

On top of the engine, [dna_signals.py](app/services/dna_signals.py) computes two profiles side by
side — **enduring** (all-time, unweighted) and **current** (exponentially recency-weighted, 120-day
half-life). The gap between them is the interesting part. That's drift: who you've been vs. who
you've been lately.

Every signal is gated behind a minimum book count. Below the gate it isn't computed and isn't
shown — instead you get "needs 8 books to read your rating style." Noise dressed as truth is the
failure mode I care most about avoiding, and an empty state that explains itself beats a confident
wrong answer.

The written insights in [dna_insights.py](app/services/dna_insights.py) are hand-written templates
with your numbers in the slots, ranked by how surprising the number is. Every template has to pass
one test: _could this sentence be true of a different reader?_ If yes, it doesn't ship.

### The journal, which I can't read

Separate from your book shelf: a private, end-to-end encrypted journal. The server stores ciphertext
and wrapped key material and holds nothing it can decrypt. Not a promise — an architecture.

A random 256-bit data key is generated client-side at setup and wrapped twice, independently: once
under a key derived from your password, once under a key derived from a recovery code shown exactly
once. Entries are AEAD-encrypted under the DEK with a fresh nonce per version. The server sees the
bcrypt hash of your password and nothing else — not the recovery code, not the DEK, not a word of
prose.

The split that makes it work: **prose is encrypted, emotion tags are not.** Tags are what DNA needs
and they're the least incriminating thing in the entry, so they stay readable. Entry dates stay
readable too, because you can't paginate "one continuous book" without them.

There is no journal search endpoint and there never will be one. Searching blobs the server can't
read isn't a missing feature, it's arithmetic. Search happens client-side after decryption.

The cost is real and stated at the moment it applies: reset your password without your recovery
code and the journal is **gone**. Not degraded, not recoverable by support, gone. The reset response
says so in plain words. That cost is also the proof the encryption is real. Full write-up in
[journalCryptoContract.md](journalCryptoContract.md), including a section on what this does _not_
protect against — mainly that E2E in a web client trusts the JavaScript you were served.

### Echo — the one public surface

Everything else in Bibliome is private by default. Echo is the exception: a pseudonymous,
book-anchored place to post the raw thing a book did to you.

You can't post freeform. Every echo must be anchored to a book, an emotion, or both. That single
constraint does more moderation work than any filter I could write, because "what did this book do
to you" is a much harder prompt to be a jerk inside of than an empty box.

The feed is chronological, keyset-paginated, and carries **no counts of any kind**. No likes, no
follower numbers, no karma, no reply totals. Replies are _shown_ (two inline per echo), never
counted. Feeds end — you get a "you're caught up," not an infinite scroll. This isn't an aesthetic
choice; it's in the design constitution in [blueprint.md](blueprint.md) and I've turned down my own
feature ideas over it.

Identity is pseudonymous, never anonymous. One durable handle per account, rate-limited changes
(30-day cooldown), with the old handle held in a grace window so links still resolve. Anonymity
gives you 4chan; pseudonymity gives you something blockable and accountable. Real names are never
collected, so they can never leak.

Moderation ([moderation.py](app/services/moderation.py)) ships _with_ Echo, not after it — a
pre-publish classifier, reputation-weighted report pressure, auto-throttle above a threshold, and a
review queue. Self-harm language routes to a supportive crisis interstitial with real resources,
never a punitive block. The classifier is deliberately keyword/regex so the request path stays fast
and dependency-free; there's a clean seam behind `classify_text` for something smarter later.

### Resonance

Find the reader who felt what you felt about a book. Deliberately narrow: two readers match when
they both have an engaged entry for the same canonical book _and_ their emotion sets overlap.
Overlap alone is a `light` match; the same emotion at a similar intensity (within 2 points) is
`strong`. Nothing else feeds it — not reading volume, not a follower graph, not engagement history.

Three suggestions at a time. Three is the whole point. Raise the number and it becomes a feed.

No identity is emitted before both sides connect — no id, handle, name, or email crosses the wire
until there's mutual consent. And nothing is counted; there is no "N readers also felt this" query
in that file and there must not be one. Matching runs as a background job after writes plus a
nightly sweep ([scripts/refresh_resonance.py](scripts/refresh_resonance.py)); the read path only
reads pre-computed rows.

Threads that come out of a match are text-only, rate-limited, with block and report as first-class
actions. Open DMs are not on the roadmap.

### Book search

Three layers, and the local one gets better on its own:

```
1. local catalog      ~5ms     Postgres pg_trgm fuzzy match
2. Google Books + Open Library   in parallel, ~300ms
3. merge → dedupe → score → rank
```

Every book anyone adds lands in the shared catalog with a normalized title and a popularity counter.
The catalog started empty and fills itself. Trigram matching means "forth wing" still finds _Fourth
Wing_.

### The smaller things that make it feel like a real app

- **Check-ins** — log how a book is making you feel _while_ you're reading it, not just at the end.
  Those become the beats of an **arc card**: the emotional shape of a single read, start to finish.
- **Mirror** — `/mirror/right-now` (what you're in the middle of) and `/mirror/landscape` (your
  shelf as emotional terrain).
- **Statuses** that admit reality: `want_to_read`, `reading`, `finished`, `abandoned`, `paused`,
  `reread`. Abandoning a book records _why_.
- **Collections**, owner-scoped, manually orderable.
- **Profiles** with milestones that are substance-based only — "read across nine emotional
  registers" — never volume, never streaks. Volume milestones are a slot machine.
- **Weekly digest** — one calm reason to come back, idempotent per ISO week, and _silent when
  there's nothing worth saying_. No "we miss you!" guilt pings.
- **Import** from Goodreads/StoryGraph CSV, deduped, with no fabricated emotions or invented
  intensities on imported rows. An imported book is honestly blank until you say something about it.
- **Export** — everything you wrote, as one JSON document. The journal comes out as ciphertext _plus
  its key bundle_, so you can decrypt it offline with a key we never had. Private thread transcripts
  are listed but not included, because a DM has two authors and one of them can't unilaterally
  publish it.
- **Share tokens** — revocable, optionally-expiring capability links for your DNA card. One place
  ([visibility.py](app/services/visibility.py)) answers "can this viewer see this profile," instead
  of the boolean tangle it replaced.

---

## Stack

Python 3.11+, FastAPI, SQLAlchemy 2.0 async, asyncpg, Postgres (with `pg_trgm`), Alembic, PyJWT +
bcrypt, httpx for the book APIs, Redis for rate limiting and lockout state (optional in dev — falls
back to per-process in-memory), aiosmtplib for transactional mail.

~13k lines of app code across 94 modules, 29 migrations, 226 tests.

FastAPI is pinned at 0.115.0 on purpose. Moving to 0.128.x drags starlette from 0.38 to 0.52, which
clears eight advisories but is a framework upgrade — it deserves its own change, not a ride-along on
a security pass. It's tracked, not forgotten. The note is in
[requirements.txt](requirements.txt) so nobody "helpfully" bumps it.

---

## Running it

```bash
./scripts/dev.sh        # → localhost:8000/docs
```

That's it. The script is idempotent and repairs whatever's out of date: builds the venv if it's
missing or if you moved the repo (which breaks the venv's absolute shebangs), syncs deps only when
`requirements-dev.txt` changed, checks Postgres is up, runs pending migrations, frees the port if a
previous run is still sitting on it, then starts uvicorn with reload. Warm start is about a second.

| Flag          |                          |
| ------------- | ------------------------ |
| `--port N`    | listen somewhere else    |
| `--sql`       | echo every SQL statement |
| `--rebuild`   | force a venv rebuild     |
| `--no-reload` | no autoreload            |

Tests: `venv/bin/pytest`

You need Postgres and a `.env` — copy [.env.example](.env.example), which has notes on every
variable. The two that actually matter to start are `DATABASE_URL` and `SECRET_KEY`. `REDIS_URL`,
`GOOGLE_BOOKS_API_KEY` and the SMTP block are all fine left empty in dev (email gets logged instead
of sent).

One that bites in production: `TRUSTED_PROXY_COUNT`. Leave it at 0 and the app trusts the socket
peer. Set it wrong behind a proxy and every request looks like it came from `127.0.0.1`, which means
your rate limiter treats the entire internet as one client.

---

## API shape

Everything is under `/api`. Interactive docs at `/docs` locally — **404 in production**, along with
`/redoc` and `/openapi.json`, because they're a complete route inventory including `/api/admin/*`.

| Group                                      | What                                                                                                    |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------- |
| `auth`                                     | register, login, refresh (rotating, hashed at rest, httpOnly cookie), reset                             |
| `entries`                                  | shelf CRUD, check-ins, arc cards, collections, cursor-paginated                                         |
| `dna`                                      | profile, generate, heatmap, stats, patterns, evolution, history, recap, emotional calendar, blind spots |
| `journal`                                  | key bundle lifecycle + encrypted entries. No `q` param, by design                                       |
| `echo`                                     | feed, threads, replies, reactions, reports                                                              |
| `resonance`                                | matches, reach out, respond, message threads                                                            |
| `mirror`                                   | right-now, landscape                                                                                    |
| `books`                                    | multi-source search, aggregate book profiles                                                            |
| `profile` / `social`                       | handles, profiles, blocks, mutes                                                                        |
| `notifications`, `prompts`, `user`, `meta` | the rest                                                                                                |
| `public`                                   | shared DNA card by revocable token — the only unauthenticated read                                      |
| `admin`                                    | dashboard, users, catalog, moderation queue, db health, token cleanup                                   |

`GET /api/meta/version` returns the git SHA of the running build. `GET /health` checks the database,
not just the process — a health check that goes green while Postgres is down is checking the wrong
thing, and that's the outage that matters.

---

## Deploy

```
Cloudflare edge → cloudflared (loopback) → nginx :80 → uvicorn 127.0.0.1:8100 → Postgres
                                                                             ↘ Redis
```

TLS terminates at Cloudflare and the tunnel dials `http://localhost:80`, so there's no certbot and
no public `:443` at all. `.app` is HSTS-preloaded and the edge cert covers the browser side. nginx
also serves the built SPA, which is why the frontend talks to a same-origin `/api` and CORS is
mostly a dev-only concern.

[deploy/](deploy/) is the single source of truth for both machine configs — the nginx site (rendered
through `envsubst`) and the systemd unit.

```bash
sudo bash deploy/deploy.sh    # first run, or after changing either config
sudo bash deploy/update.sh    # code only: pull, migrate, rebuild, restart
```

Then check what's actually running:

```bash
curl -s https://bibliome.app/api/meta/version
curl -s https://bibliome.app/health          # 503 if Postgres is down
```

---

## The rules I've written down so I can't quietly break them

Three documents outlive any individual commit:

- **[blueprint.md](blueprint.md)** — the product reasoning, including the section where I argue with
  my own brief. The core tension: the requested features (feed, matching, chat, notifications) are
  the standard apparatus of engagement-maximizing apps, and the stated soul is calm and
  anti-dopamine. The rule that resolves it: _compete on depth, never on volume; no metric that ranks
  humans against each other is ever shown._
- **[journalCryptoContract.md](journalCryptoContract.md)** — the irreversible decision, written down
  before the code.
- **[authCookieContract.md](authCookieContract.md)** — the token/cookie contract, locked so the two
  repos can't drift.
- **[ROADMAP.md](ROADMAP.md)** — what's next, what's broken, and the sequencing discipline I need
  because my track record is starting phase N+1 before closing phase N.

---

## Things I'd tell you if you were reviewing this

The classifier is regex. It's honest about that in its own docstring and it's the right call for
now, but it's the first thing I'd replace with real money.

The E2E journal protects you from database dumps, backups, logs, subpoenas, and me. It does not
protect you from a compromised server shipping malicious frontend JavaScript. That's an unavoidable
property of browser E2E and I'd rather say it here than let someone discover it.

The DNA engine is opinionated. Eight archetypes is a small number to sort human beings into, and the
thresholds are numbers I chose. It's explainable, which is different from being right.

Resonance has never been load-tested with a real user graph. The batch job has a hard 5,000-row
candidate ceiling per reader specifically because I could see the shape of that problem coming, but
"I bounded it" isn't "I measured it."

---

MIT.

_"A reader lives a thousand lives before he dies."_ Bibliome is an attempt to keep a record of which
ones.
