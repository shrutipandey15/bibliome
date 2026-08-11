# Bibliome DNA — implementation plan

Ordered by impact. P0 items are correctness; ship them together. P1 is credibility.
P2 is the schema change that unblocks the rest.

Repos: `bibliome` (API), `bibliome-frontend`.

---

## P0-1 · One engine, every surface

**Problem.** The in-app DNA page uses `dna_signals.score_archetype`. The share card
(`/api/public/shared/{token}`) uses the legacy `dna_engine.calculate_personality`,
recomputed live. On 3,000 simulated readers the two disagreed **42.7%** of the time.
Legacy also gates at 3 books vs. the new engine's 5, so a 3-book reader sees
"not enough yet" in-app while their share link renders an archetype.

`profile_service.py:340` returns `signature: owner.cached_dna_profile` (legacy)
alongside `personality_type` (new) — two engines contradicting each other inside
one response.

### `app/services/dna_service.py` — add

```python
def card_payload(user: User) -> dict | None:
    """The one shape every public surface renders. Reads cache only — a public
    path must never recompute, and must never see a different engine than the
    owner's own DNA tab."""
    v2 = user.cached_dna_v2
    if not v2 or not v2.get("enough") or not v2.get("archetype"):
        return None
    return {
        "archetype": v2["archetype"],
        "archetype_scores": v2["archetype_scores"],
        "margin": v2.get("margin"),
        "basis": v2.get("basis"),
        "book_count": v2["book_count"],
        "top_emotions": [
            {"emotion_id": s, "weight": round(w, 4)}
            for s, w in sorted(
                v2["profiles"]["current"].items(), key=lambda kv: -kv[1]
            )[:5]
            if w > 0
        ],
    }
```

### `app/routers/public.py` — replace the body of `get_shared_card`

Drop the `BookEntry` query and the `calculate_personality` call entirely.

```python
user = await resolve_share_token(db, token)
if not user:
    raise HTTPException(404, "Link invalid or expired")

card = card_payload(user)
if card is None:
    raise HTTPException(404, "This reader's DNA isn't ready yet")

return {"handle": user.handle, "share_token": token, **card}
```

Also note: the old response returned `dna.get("stats", {})`, but
`calculate_personality` has no `stats` key — that field was always `{}`. It goes
away with this change.

### `app/services/profile_service.py:340`

```python
"signature": card_payload(owner),   # was: owner.cached_dna_profile
```

### `dna_engine.calculate_personality`

Do not delete yet — `generate_recap` calls it for shift detection. Instead mark it
internal and remove its two public callers (above). Add to its docstring:

> INTERNAL ONLY. Not the archetype source. `dna_signals.score_archetype` is the
> single headline authority; this exists for recap shift detection. Do not wire
> this to any user-visible surface.

**Also fix the false contract in its comments.** The `# Frequency match (0-40 points)`
etc. comments imply bounded ranges summing to 100. Nothing is bounded — with 50
books the frequency term alone exceeds 400. Either bound them or correct the comments.

---

## P0-2 · Let the engine abstain

**Problem.** `max(scores, key=scores.get)` breaks ties by dict insertion order, and
`grief_romantic` is first in `PERSONALITY_TYPES`. Since `EntryCreate.emotions`
defaults to an empty list and `build_dna` gates on `len(sigs)` rather than tagged
entries, five untagged imported books produce:

```
enough: True | archetype: The Grief Romantic | scores: all 0.0
```

A reader who has said nothing gets a confident label about their inner life.

### `app/services/dna_signals.py`

```python
def score_archetype(current_freq: dict[str, float]) -> tuple[str | None, dict[str, float], float]:
    """Score the 8 archetypes against the recency-weighted vector.

    Returns (best_id | None, scores, margin). best_id is None when the reader has
    given us nothing to go on — an empty tally must not fall through to whichever
    archetype happens to be first in the list.
    """
    scores = {}
    for t in PERSONALITY_TYPES:
        s = sum(current_freq.get(e, 0.0) for e in t["primary_emotions"])
        s -= 0.5 * sum(current_freq.get(e, 0.0) for e in t.get("anti_emotions", []))
        scores[t["id"]] = round(s, 4)

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    (best, top), (_, second) = ranked[0], ranked[1]
    if top <= 0:
        return None, scores, 0.0
    return best, scores, round((top - second) / top, 4)
```

### `app/services/dna_insights.py` — `build_dna`

Gate on tagged books, and handle abstention:

```python
tagged = [s for s in sigs if s.emotions]
if len(tagged) < MIN_BOOKS_FOR_DNA:
    return {
        "enough": False,
        "book_count": len(sigs),
        "tagged_count": len(tagged),
        "needed": MIN_BOOKS_FOR_DNA,
        "message": f"{len(tagged)} books with a feeling logged. "
                   f"At {MIN_BOOKS_FOR_DNA}, the mirror starts to see you.",
        "snapshot_count": snapshot_count,
        "has_two_snapshots": snapshot_count >= 2,
        "journal_entry_count": journal_count,
    }
```

At the assembly step:

```python
archetype_id, scores, margin = sig.score_archetype(current)

return {
    ...
    "archetype": sig.archetype_dict(archetype_id) if archetype_id else None,
    "archetype_scores": scores,
    "margin": margin,
    "runner_up": (
        sig.archetype_dict(sorted(scores, key=scores.get, reverse=True)[1])["name"]
        if archetype_id and margin < 0.10 else None
    ),
    "basis": basis_for(archetype_id, sigs) if archetype_id else None,
    ...
}
```

### Callers to update for the new 3-tuple

- `dna_insights.build_dna` (above)
- `dna_service.maybe_snapshot_and_notify:134` — `archetype_id, _, _ = sig.score_archetype(current)`, then bail out with `return None` if `archetype_id is None`
- `dna_service.compute_and_cache:110` — `user.personality_type = v2["archetype"]["name"] if v2.get("archetype") else None`

---

## P0-3 · Fix `stated_vs_revealed`

This is your strongest function — half its input is a sentence the reader typed, so
it can't be dismissed as a horoscope. Three bugs currently undermine it.

**a) No minimum count.** `avg_intensity_for` averages however many books it finds,
including one. A single 10/10 devastation book beats thirty comfort books at 7, and
the app confidently calls your stated preference a lie.

```python
MIN_BOOKS_PER_CLAIM = 3

def avg_intensity_for(slug: str) -> float | None:
    xs = [s.intensity for s in sigs if slug in s.emotions]
    return sum(xs) / len(xs) if len(xs) >= MIN_BOOKS_PER_CLAIM else None
```

**b) Co-tagged books manufacture the gap.** Intensity is one slider per _book_, so a
book tagged both comfort and devastation contributes the identical value to both
averages. The measured gap therefore comes only from books carrying one tag and not
the other — partly an artifact of tagging habits. Until P2 lands, compare on
disjoint sets and say so:

```python
def avg_intensity_disjoint(slug: str, against: str) -> float | None:
    xs = [s.intensity for s in sigs
          if slug in s.emotions and against not in s.emotions]
    return sum(xs) / len(xs) if len(xs) >= MIN_BOOKS_PER_CLAIM else None
```

**c) The second stated emotion is discarded.** `UserUpdate.reads_for` allows two
(`max_length=2`); the code reads `stated_slugs[0]` only. Either compute the gap
against both and report the larger, or drop the schema to one — silently collecting
input you never use is the kind of thing this project otherwise refuses to do.

---

## P1-4 · Hysteresis on the headline

32% of simulated readers changed archetype after adding a _single_ book. If the
"your DNA shifted" notification fires that often it becomes noise, and noise is what
makes people stop believing the whole thing.

In `maybe_snapshot_and_notify`, require the challenger to clear the incumbent:

```python
ARCHETYPE_SWITCH_MARGIN = 0.05

# Keep the incumbent unless the new leader beats it by a real margin.
if ctx.last_archetype and archetype_name != ctx.last_archetype:
    incumbent_id = next(
        (t["id"] for t in PERSONALITY_TYPES
         if t["name"] == ctx.last_archetype), None
    )
    if incumbent_id and scores[archetype_id] - scores[incumbent_id] < ARCHETYPE_SWITCH_MARGIN:
        archetype_id, archetype_name = incumbent_id, ctx.last_archetype
```

---

## P1-5 · Separate the twins, unbias Comfort Architect

`grief_romantic` and `soft_masochist` currently share two of three primaries
(grief, devastation) **and** identical anti-emotions — a reader tagging only
grief+devastation scores an exact 1.0 vs 1.0 tie, resolved by list order.

Separately, `comfort_architect` won only **5.4%** of 5,000 random readers vs. ~12.5%
expected: it is the only type carrying three anti-emotions.

In `PERSONALITY_TYPES`:

| id                  | primary_emotions              | anti_emotions                  |
| ------------------- | ----------------------------- | ------------------------------ |
| `grief_romantic`    | grief, catharsis, devastation | comfort, amusement             |
| `soft_masochist`    | **rage, dread, devastation**  | comfort, **joy**               |
| `comfort_architect` | comfort, longing, tenderness  | rage, dread _(drop revulsion)_ |

Now they share one primary instead of two, every type carries exactly two
anti-emotions, and all 14 experiential emotions remain in use — so
`test_every_experiential_emotion_is_used_somewhere` still passes. Update
`soft_masochist`'s description to match its new anchors (hurt on purpose: rage and
dread, not sorrow).

---

## P1-6 · Give the noun a receipt

The card needs a name — keep all eight. What changes is that the name stops being a
bucket you were sorted into and becomes a headline for a number the reader can check.

```python
def basis_for(archetype_id: str, sigs: list[EntrySig]) -> dict:
    """The evidence line under the label. Counts only — no adjectives."""
    t = _TYPES_BY_ID[archetype_id]
    total = len(sigs)
    rows = []
    for slug in t["primary_emotions"]:
        n = sum(1 for s in sigs if slug in s.emotions)
        if n:
            rows.append({"emotion": slug, "books": n, "of": total})
    top_rated = sorted(sigs, key=lambda s: -s.intensity)[:3]
    return {
        "counts": sorted(rows, key=lambda r: -r["books"]),
        "top_rated_emotions": sorted({e for s in top_rated for e in s.emotions}),
    }
```

Renders as:

> **The Grief Romantic**
> grief in 14 of your 31 books · your 3 highest-rated were all devastation

---

## P1-7 · Self-scoring (the honest confidence number)

Right now any "confidence" you could show is a property of your own arithmetic —
the score margin. That's circular. Backtested accuracy is a property of reality, and
it works at n=1: every reader validates the engine against themselves, with no user
base required.

```python
BACKTEST_MIN_BOOKS = 20

def backtest(sigs: list[EntrySig], k: int = 3) -> dict | None:
    """Build the profile on the first 70% of a reader's history, predict the
    emotional register of the rest, and score it. The engine puts money down
    before the cards turn over."""
    tagged = sorted([s for s in sigs if s.emotions], key=lambda s: s.ts)
    if len(tagged) < BACKTEST_MIN_BOOKS:
        return None
    cut = int(len(tagged) * 0.7)
    train, test = tagged[:cut], tagged[cut:]
    vec = frequency_vector(train, weighted=True)
    predicted = {s for s, _ in sorted(vec.items(), key=lambda kv: -kv[1])[:k] if _ > 0}
    hits = sum(1 for s in test if set(s.emotions) & predicted)
    return {"n": len(test), "hits": hits, "rate": round(hits / len(test), 3),
            "predicted": sorted(predicted)}
```

Surfaces as: _"From your first 40 books I predicted the emotional register of your
next 17. I got 12 right."_ A claim the reader can check against a shelf they
remember.

If the rate comes back near chance, that is the answer to "are eight buckets the
right eight" — arrived at from data rather than from asking the user.

---

## P2-8 · Frontend

`archetype_scores` appears **zero times** in the frontend today. The backend computes
the margin and nothing renders it.

**`src/components/dna/DNAView.jsx`**

- Line 120: drop `|| !!profile?.archetype` from the `enough` fallback — with P0-2,
  `archetype` can now legitimately be `null` on an `enough: true` payload.
- Section V: when `profile.archetype` is null, render "Not enough tagged books to
  name a shorthand yet" instead of the `DNACard`.
- Pass `margin`, `runner_up`, and `basis` through to `cardProfile`.

**`src/components/DNACard.jsx`**

- Prefix the name with "Closest to" when `margin < 0.10`, and render the runner-up:
  _"closest to The Grief Romantic, shading toward The Soft Masochist."_
- Render the `basis` line under the name — this is the single highest-value addition
  on the card.
- **Remove `no two alike`** above the fingerprint bars. Those bars come from
  `stats.emotion_counts`; two eight-book readers who both tag grief and comfort draw
  the same silhouette. It's the one line on the card making a claim the rest of the
  project refuses to make.
- Consider letting card weight/ink track `margin`, so a card visibly firms up as
  books accrue.

**`src/services/api.js:307`** — update the documented payload shape for the new
`margin` / `runner_up` / `basis` / `tagged_count` fields.

---

## P2-9 · Intensity per emotion (schema, do last)

`BookEntry.intensity` is one column per book, shared by every emotion on it. Two
consequences:

1. Intensity contributes **nothing** to the archetype — `score_archetype` reads only
   the frequency vector. I verified an all-1s reader and an all-10s reader produce
   byte-identical `archetype_scores`.
2. It confounds `stated_vs_revealed` (see P0-3b).

Your readme says you "pick from 18 emotions and give each one a strength from 1 to
10." The schema doesn't do that. Either move intensity onto `EntryEmotion` (additive
migration: nullable column, backfill from the parent entry, keep the book-level value
as the default the UI pre-fills), or correct the readme. The first is the better
product — but it's a migration plus an input-surface change, so it lands after the
correctness work above.

---

## Test coverage to add

```
test_score_archetype_abstains_on_empty_tally
test_score_archetype_abstains_when_only_lost_me_tags
test_untagged_import_does_not_produce_an_archetype
test_public_card_matches_in_app_archetype        # the 42.7% regression guard
test_stated_vs_revealed_requires_three_books
test_archetype_survives_one_book_within_margin   # hysteresis
test_grief_romantic_and_soft_masochist_do_not_tie_on_grief_devastation
test_every_experiential_emotion_is_used_somewhere  # existing — confirm still green
```

---

## Sequencing

1. **P0 together** (one engine, abstention, `stated_vs_revealed` min-n) — this is the
   set that takes the feature from _dishonest_ to merely _rough_.
2. **P1-5 + P1-6** — twins and the receipt line. Small, and the receipt is the biggest
   single credibility win per line of code.
3. **P1-4, P2-8** — hysteresis and the frontend surfacing.
4. **P1-7** backtest, then **P2-9** the migration.

The rule you already wrote in your own readme, applied to the one place it wasn't:
_could this sentence be true of a different reader?_
