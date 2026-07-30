"""DNA signals — the math behind the mirror (Phase 7, B7.2/B7.3).

Pure functions over a normalized list of ``EntrySig``. No DB access, no I/O.

The one rule everything here serves: a signal is only worth computing if it can
say something that could NOT be true of a different reader. Every function is
gated by a minimum book count below which its output would be noise dressed as
truth (see ``GATES``) — the caller enforces the gates, these functions just
compute.

Two profiles always exist side by side (B7.3):
  - enduring: unweighted, all-time — who you've been.
  - current:  exponentially recency-weighted — who you've been lately.
The gap between them (drift) is the product.
"""

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.dna_engine import PERSONALITY_TYPES
from app.utils.emotions import EMOTIONS, VALID_SLUGS, canonicalize

# ── Tunable constants (Part 4 / §11) ──
HALF_LIFE_DAYS = 120          # a book's emotional weight halves every ~4 months
DRIFT_SNAPSHOT_THRESHOLD = 0.15
MONTHLY_CADENCE_DAYS = 30
MIN_BOOKS_FOR_DNA = 5

_LN2 = math.log(2)
_ALL_SLUGS = [e["slug"] for e in EMOTIONS]              # canonical declaration order
_MAX_ENTROPY = math.log2(len(_ALL_SLUGS))               # log2(vocabulary size)

# Minimum book count below which each insight is NOT computed and NOT shown.
# This is statistical validity, not artificial scarcity (B7.6).
GATES: dict[str, int] = {
    "frequency": 5,
    "intensity": 5,
    "intensity_signature": 8,
    "range": 8,
    "blind_spot": 10,
    "pairing": 15,
    "contradiction": 10,
    "drift": 15,
    "abandonment": 10,
    "arc": 5,
    "seasonality": 25,
}


@dataclass
class EntrySig:
    """The minimal slice of an emotion source the signal math needs.

    Usually a book entry. Since the journal landed it can also be a named day:
    journal emotions are just another emotion source, so they arrive in this same
    shape rather than through a parallel pipeline. ``source`` is what lets the
    caller keep book-specific claims ("you've logged N books", rating style,
    abandonment) about books alone while the emotion vectors span both.
    """
    emotions: list[str]          # canonical, deduped
    intensity: int
    ts: datetime                 # finished_at (fallback created_at), tz-aware
    status: str                  # finished | reading | want_to_read
    arc_start: str | None = None
    arc_end: str | None = None
    source: str = "book"         # book | journal


def _canon_list(raw) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for e in raw or []:
        c = canonicalize(e)
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def entry_sig(raw: dict) -> EntrySig:
    """Build an EntrySig from a loader dict, canonicalizing on the way in
    (Part 4 discipline — legacy slugs still count)."""
    ts = raw.get("ts")
    if ts is None:
        fin = raw.get("finished_at")
        created = raw.get("created_at")
        if fin is not None:
            ts = datetime.combine(fin, datetime.min.time(), tzinfo=timezone.utc)
        else:
            ts = created or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return EntrySig(
        emotions=_canon_list(raw.get("emotions")),
        intensity=int(raw.get("intensity", 5) or 5),
        ts=ts,
        status=raw.get("status", "finished") or "finished",
        arc_start=canonicalize(raw["arc_start"]) if raw.get("arc_start") else None,
        arc_end=canonicalize(raw["arc_end"]) if raw.get("arc_end") else None,
        source=raw.get("source") or "book",
    )


# ── Recency weighting (B7.3) ──

def recency_weight(age_days: float) -> float:
    return math.exp(-_LN2 * max(age_days, 0.0) / HALF_LIFE_DAYS)


def frequency_vector(sigs: list[EntrySig], *, weighted: bool, now: datetime | None = None) -> dict[str, float]:
    """An emotion vector over the full canonical vocabulary, normalized to sum 1.0 (all-zero if no tags).

    weighted=False → enduring (each tagged book contributes 1 per distinct emotion).
    weighted=True  → current (contribution scaled by exp-decay on the book's age).
    """
    now = now or datetime.now(timezone.utc)
    vec = {s: 0.0 for s in _ALL_SLUGS}
    for sig in sigs:
        w = recency_weight((now - sig.ts).days) if weighted else 1.0
        for e in sig.emotions:
            vec[e] += w
    total = sum(vec.values())
    if total > 0:
        for s in vec:
            vec[s] /= total
    return vec


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    dot = sum(a[s] * b[s] for s in _ALL_SLUGS)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def drift(enduring: dict[str, float], current: dict[str, float]) -> float:
    """1 - cosine. 0 = unchanged, →1 = the reader has moved (B7.3)."""
    return round(1.0 - cosine(enduring, current), 4)


# ── Signals ──

def intensity_signature(sigs: list[EntrySig]) -> dict:
    """Are they an 8-or-nothing reader or a careful 6–7 rater? (B7.2)"""
    xs = [s.intensity for s in sigs]
    n = len(xs)
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / n
    std = math.sqrt(var)
    skew = (sum((x - mean) ** 3 for x in xs) / n) / (std ** 3) if std > 0 else 0.0
    share_high = sum(1 for x in xs if x >= 8) / n
    # The modal 2-point band, for the "careful rater" variant.
    band_counts = Counter((x // 2) * 2 for x in xs)
    band_lo = band_counts.most_common(1)[0][0]
    return {
        "mean": round(mean, 2),
        "variance": round(var, 2),
        "skew": round(skew, 2),
        "share_high": round(share_high, 3),
        "band_lo": band_lo,
        "band_hi": band_lo + 1,
    }


def range_entropy(sigs: list[EntrySig]) -> dict:
    """How wide do they reach? Normalized Shannon entropy 0..1 + distinct count."""
    freq = frequency_vector(sigs, weighted=False)
    ent = 0.0
    for p in freq.values():
        if p > 0:
            ent -= p * math.log2(p)
    distinct = sum(1 for p in freq.values() if p > 0)
    return {"entropy": round(ent / _MAX_ENTROPY, 3) if _MAX_ENTROPY else 0.0,
            "distinct": distinct}


def blind_spots(sigs: list[EntrySig]) -> list[str]:
    """Canonical emotions this reader has NEVER tagged, in declaration order."""
    tagged: set[str] = set()
    for s in sigs:
        tagged.update(s.emotions)
    return [slug for slug in _ALL_SLUGS if slug not in tagged]


def co_occurrence(sigs: list[EntrySig]) -> Counter:
    """Which emotions arrive together for THIS reader (deduped per book)."""
    pairs: Counter = Counter()
    for s in sigs:
        emos = sorted(set(s.emotions))
        for i in range(len(emos)):
            for j in range(i + 1, len(emos)):
                pairs[(emos[i], emos[j])] += 1
    return pairs


def stated_vs_revealed(sigs: list[EntrySig], reads_for: list[str] | None) -> dict | None:
    """The gold: gap between what they SAID and what their shelf shows (B7.1).

    Returns None when no stated preference exists (so the insight can't fire).
    """
    stated_slugs = _canon_list(reads_for)
    if not stated_slugs:
        return None
    stated = stated_slugs[0]

    def avg_intensity_for(slug: str) -> float | None:
        xs = [s.intensity for s in sigs if slug in s.emotions]
        return sum(xs) / len(xs) if xs else None

    stated_avg = avg_intensity_for(stated)
    # Revealed top by frequency (what they actually reach for), excluding the stated.
    freq = frequency_vector(sigs, weighted=False)
    ranked = sorted(((s, f) for s, f in freq.items() if s != stated and f > 0),
                    key=lambda kv: kv[1], reverse=True)
    revealed_top = ranked[0][0] if ranked else None

    # The intensity gap: the non-stated emotion they rate highest vs. the stated one.
    revealed_hi, revealed_hi_avg = None, None
    for slug, _ in ranked:
        a = avg_intensity_for(slug)
        if a is not None and (revealed_hi_avg is None or a > revealed_hi_avg):
            revealed_hi, revealed_hi_avg = slug, a
    delta = None
    if stated_avg is not None and revealed_hi_avg is not None:
        delta = round(revealed_hi_avg - stated_avg, 1)

    return {"stated": stated, "revealed_top": revealed_top,
            "revealed_hi": revealed_hi, "delta": delta}


def abandonment(sigs: list[EntrySig]) -> dict | None:
    """Which emotion correlates with NOT finishing (B7.2).

    DNF is proxied by status != 'finished' — there is no explicit DNF flag yet;
    a real "why did you stop?" capture is a future input-surface win (Part 1).
    """
    unfinished = [s for s in sigs if s.status != "finished"]
    if len(unfinished) < 3:
        return None
    overall_rate = len(unfinished) / len(sigs)
    best_slug, best_rate = None, overall_rate
    for slug in _ALL_SLUGS:
        tagged = [s for s in sigs if slug in s.emotions]
        if len(tagged) < 3:
            continue
        rate = sum(1 for s in tagged if s.status != "finished") / len(tagged)
        if rate > best_rate:
            best_slug, best_rate = slug, rate
    if best_slug is None:
        return None
    return {"emotion": best_slug, "fraction": round(best_rate, 2)}


def arc_shape(sigs: list[EntrySig]) -> dict | None:
    """Do they start in dread and end in catharsis? From Finish-Flow arc columns."""
    with_arc = [s for s in sigs if s.arc_start and s.arc_end]
    if len(with_arc) < 5:
        return None
    pairs = Counter((s.arc_start, s.arc_end) for s in with_arc)
    (start, end), count = pairs.most_common(1)[0]
    if start == end:
        return None  # not a shape, just a mood
    return {"start": start, "end": end,
            "fraction": round(count / len(with_arc), 2), "n_arc": len(with_arc)}


def seasonality(sigs: list[EntrySig]) -> dict | None:
    """Emotion by month, year over year. Locked this pass: honestly needs 25 books
    AND a 12-month span before it can be anything but noise. Returns None until
    then (and even the computed form is out of scope for this pass)."""
    return None


# ── Archetype from the recency-weighted vector (B7.5) ──

_TYPES_BY_ID = {t["id"]: t for t in PERSONALITY_TYPES}


def score_archetype(current_freq: dict[str, float]) -> tuple[str, dict[str, float]]:
    """Score the 8 archetypes against the CURRENT (recency-weighted) vector, so the
    headline can actually change as the reader changes. Returns (best_id, scores)."""
    scores: dict[str, float] = {}
    for t in PERSONALITY_TYPES:
        s = sum(current_freq.get(e, 0.0) for e in t["primary_emotions"])
        s -= 0.5 * sum(current_freq.get(e, 0.0) for e in t.get("anti_emotions", []))
        scores[t["id"]] = round(s, 4)
    best = max(scores, key=scores.get)
    return best, scores


def archetype_dict(type_id: str) -> dict:
    t = _TYPES_BY_ID[type_id]
    return {"id": t["id"], "name": t["name"], "description": t["description"],
            "color": t["color"], "glyph": t["glyph"],
            "blind_spots": t["blind_spots"], "comfort_tropes": t["comfort_tropes"]}
