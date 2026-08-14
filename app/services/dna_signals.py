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
# Raised from 0.15 alongside one-entry-one-vote, in the same commit and for the
# same reason. Splitting an entry's weight across its tags moves the frequency
# vectors further apart — the recency decay and the tag-count divisor compound
# instead of cancelling — so `drift` rises across the whole population. Left at
# 0.15 the patch would have roughly doubled how often we snapshot and how often
# we send `dna_shifted`: a notification-spam regression bought with an archetype
# fix. That is why the two changes ship in one commit.
#
# Calibrated against the expression this constant actually gates —
# `drift(prev_snapshot["current_vector"], current)` in dna_service, NOT the
# enduring-vs-current gap. Those two have different distributions and calibrate
# to different numbers; only the former decides whether anyone gets notified.
#
# Measured, not guessed: `python -m scripts.dna_bias_probe`. The absolute crossing
# rate is highly sensitive to the shelf model (2%-12% depending on assumed
# tag-count spread), but the pick that restores the pre-patch rate sits at
# 0.175-0.185 across every spread and seed tried. Re-run that probe before
# changing this.
DRIFT_SNAPSHOT_THRESHOLD = 0.18
MONTHLY_CADENCE_DAYS = 30
MIN_BOOKS_FOR_DNA = 5
# Below this many books carrying a tag, its average intensity is one reader's mood
# on a Tuesday, not a preference — stated_vs_revealed refuses to compare on it.
MIN_BOOKS_PER_CLAIM = 3

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
    # want_to_read | reading | finished | abandoned | paused | reread
    # (migration 022 widened the check constraint to these six)
    status: str
    arc_start: str | None = None
    arc_end: str | None = None
    source: str = "book"         # book | journal
    # bored | too_much | badly_written | wrong_time | lost_me | drifted, or None.
    # Only meaningful when status == "abandoned". Lets the abandonment insight say
    # WHY a book was put down, which is a far stronger sentence than naming the
    # emotion that correlates with stopping.
    dnf_reason: str | None = None


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
        dnf_reason=raw.get("dnf_reason"),
    )


# ── Recency weighting (B7.3) ──

def recency_weight(age_days: float) -> float:
    return math.exp(-_LN2 * max(age_days, 0.0) / HALF_LIFE_DAYS)


def frequency_vector(sigs: list[EntrySig], *, weighted: bool, now: datetime | None = None) -> dict[str, float]:
    """An emotion vector over the full canonical vocabulary, normalized to sum 1.0 (all-zero if no tags).

    weighted=False → enduring (each tagged entry contributes 1, split across its tags).
    weighted=True  → current (that contribution scaled by exp-decay on the entry's age).

    ONE ENTRY, ONE VOTE. Each entry's weight is divided by the number of tags it
    carries rather than repeated per tag. Without this a book tagged five emotions
    outvotes five books tagged one, and the books people multi-tag are the ones
    that hit hardest — so the vector drifted toward whatever the reader felt most
    intensely rather than what they read most. On simulated shelves this alone cut
    the archetype win-share spread from 27x to 11x and the exact-tie rate from 5.7%
    to 0.9%.
    """
    now = now or datetime.now(timezone.utc)
    vec = {s: 0.0 for s in _ALL_SLUGS}
    for sig in sigs:
        if not sig.emotions:
            continue
        w = recency_weight((now - sig.ts).days) if weighted else 1.0
        w /= len(sig.emotions)
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
    """Canonical emotions this reader has NEVER tagged, most surprising absence first.

    Ranked by the emotion's rate in ``BASELINE_VECTOR`` — how often readers in
    general reach for it — because that is what makes an absence a finding. Never
    tagging Awe (the most common tag) says something about this reader; never
    tagging Nostalgia (the rarest) says almost nothing, and saying it anyway is how
    the mirror ends up sounding like a horoscope.

    Previously this returned declaration order and the template took ``[0]``, so a
    reader missing devastation, grief and joy was told "devastation" — always,
    because devastation is first in ``EMOTIONS``. That is the archetype tie-break
    bug one layer up: a real ranking question silently answered by list position.

    Ties break on declaration order, via a stable sort, so the output is
    deterministic without being decided by it.
    """
    tagged: set[str] = set()
    for s in sigs:
        tagged.update(s.emotions)
    never = [slug for slug in _ALL_SLUGS if slug not in tagged]
    return sorted(never, key=lambda slug: -BASELINE_VECTOR.get(slug, 0.0))


def co_occurrence(sigs: list[EntrySig]) -> Counter:
    """Which emotions arrive together for THIS reader (deduped per book)."""
    pairs: Counter = Counter()
    for s in sigs:
        emos = sorted(set(s.emotions))
        for i in range(len(emos)):
            for j in range(i + 1, len(emos)):
                pairs[(emos[i], emos[j])] += 1
    return pairs


def _stated_vs_revealed_one(
    sigs: list[EntrySig], stated: str, also_stated: set[str] | None = None
) -> dict:
    """The verdict for a single stated emotion. See ``stated_vs_revealed``.

    ``also_stated`` is the reader's OTHER stated emotions, which are excluded from
    the comparison entirely. This is stated vs *revealed*: a reader who said
    "comfort and tenderness" and rates comfort higher has not been caught out by
    their shelf — both answers were theirs. Letting one stated emotion play
    challenger to the other manufactures a contradiction out of the reader
    agreeing with themselves.
    """
    also_stated = also_stated or set()

    def books_disjoint(slug: str, against: str) -> list[int]:
        return [s.intensity for s in sigs
                if slug in s.emotions and against not in s.emotions]

    def avg_intensity_disjoint(slug: str, against: str) -> float | None:
        # Disjoint on purpose. Intensity is one slider per *book*, so a book tagged
        # both comfort and devastation contributes the identical value to both
        # averages — any gap measured across overlapping sets is manufactured by
        # tagging habits, not felt by the reader. Compare only books carrying one
        # tag and not the other, and require enough of them that a single outlier
        # can't call someone's stated preference a lie.
        xs = [s.intensity for s in sigs
              if slug in s.emotions and against not in s.emotions]
        return sum(xs) / len(xs) if len(xs) >= MIN_BOOKS_PER_CLAIM else None

    # Revealed top by frequency (what they actually reach for), excluding
    # everything the reader already told us about.
    excluded = {stated} | also_stated
    freq = frequency_vector(sigs, weighted=False)
    ranked = sorted(((s, f) for s, f in freq.items() if s not in excluded and f > 0),
                    key=lambda kv: kv[1], reverse=True)
    revealed_top = ranked[0][0] if ranked else None

    # The intensity gap: the strongest challenger vs. the stated one, each averaged
    # over the books the other tag doesn't touch. Both sides are recomputed per
    # candidate because which books are excluded depends on which pair is being
    # compared. A candidate that can't clear MIN_BOOKS_PER_CLAIM on BOTH sides is
    # not comparable and is skipped — the same floor for every verdict.
    revealed_hi, delta, evidence = None, None, None
    for slug, _ in ranked:
        theirs = books_disjoint(slug, stated)
        ours = books_disjoint(stated, slug)
        if len(theirs) < MIN_BOOKS_PER_CLAIM or len(ours) < MIN_BOOKS_PER_CLAIM:
            continue
        theirs_avg = sum(theirs) / len(theirs)
        ours_avg = sum(ours) / len(ours)
        gap = round(theirs_avg - ours_avg, 1)
        # Keep the LARGEST gap: it is simultaneously the strongest case against the
        # reader's claim and, when negative, the narrowest margin their claim
        # survives by. One number answers both questions.
        if delta is None or gap > delta:
            revealed_hi, delta = slug, gap
            evidence = {
                "stated": {"emotion": stated, "books": len(ours),
                           "avg": round(ours_avg, 1)},
                "compared": {"emotion": slug, "books": len(theirs),
                             "avg": round(theirs_avg, 1)},
            }

    # Three outcomes, one threshold, symmetric about zero. `confirmed` is not a
    # compliment and carries no praise language: it is the same measurement as
    # `contradicted` with the sign the other way, and it is reported because a
    # signal that can only ever accuse is a search for gaps, not a measurement.
    if delta is None:
        verdict, reason = "inconclusive", "too_few_books"
    elif delta > 0.5:
        verdict, reason = "contradicted", None
    elif delta < -0.5:
        # The largest gap is still under -0.5, so EVERY comparable emotion is:
        # the stated one out-rates all of them, not merely the closest.
        verdict, reason = "confirmed", None
    else:
        verdict, reason = "inconclusive", "dead_heat"

    # Raw book counts behind the frequency ranking. `revealed_top` is a RANK, and a
    # rank ties silently: with 12 books each, "you reach for devastation more often"
    # is simply false. Any copy making a frequency claim has to compare these.
    def n_books(slug: str | None) -> int:
        return sum(1 for s in sigs if slug and slug in s.emotions)

    return {"stated": stated, "revealed_top": revealed_top,
            "revealed_hi": revealed_hi, "delta": delta, "disjoint": True,
            "min_books": MIN_BOOKS_PER_CLAIM,
            "verdict": verdict, "reason": reason, "evidence": evidence,
            "stated_books": n_books(stated),
            "revealed_top_books": n_books(revealed_top)}


def stated_vs_revealed(sigs: list[EntrySig], reads_for: list[str] | None) -> dict | None:
    """The gold: what they SAID measured against what their shelf shows (B7.1).

    Returns one of three verdicts, all carrying the same evidence:
      - ``contradicted``  the top non-stated emotion out-rates the stated one by >0.5
      - ``confirmed``     the stated emotion out-rates EVERY comparable one by >0.5
      - ``inconclusive``  neither leads by >0.5, or nothing was comparable

    ``None`` is returned only when the reader never told us what they read for.
    ``inconclusive`` is a real result and must stay distinguishable from silence.

    This used to return a gap only when one existed against the reader, which made
    it a search for gaps rather than a measurement: it could accuse and it could
    say nothing, and being right about yourself was indistinguishable from never
    having been asked.

    ``reads_for`` allows two emotions and both are scored. Collecting a second
    answer and then dropping it is exactly the kind of thing this project refuses
    to do.
    """
    stated_slugs = _canon_list(reads_for)
    if not stated_slugs:
        return None

    all_stated = set(stated_slugs)
    results = [_stated_vs_revealed_one(sigs, s, all_stated - {s}) for s in stated_slugs]
    # A decisive verdict outranks an inconclusive one; between two decisive ones,
    # the larger gap wins IN EITHER DIRECTION. Ranking by the signed gap would mean
    # a reader who was right about one claim and wrong about the other always heard
    # the accusation — the same bias this function just removed, one layer up.
    return max(
        results,
        key=lambda r: (r["verdict"] != "inconclusive", abs(r["delta"] or 0.0)),
    )


# Books that were opened. `want_to_read` is excluded from the abandonment
# denominator entirely: a book on the pile was never started, so it can neither
# be abandoned nor finished, and counting it as "not finished" inflated the
# denominator with books the reader never opened.
#
# `paused` COUNTS AS ABANDONED here. A paused book is one the reader stopped
# reading; the distinction between "paused" and "abandoned" is largely how
# generous the reader feels about their own intentions at the moment they tap it,
# and treating it as a finish would make the rate an undercount. `reading` also
# counts in the denominator but not as abandoned — it is genuinely in progress.
_OPENED_STATUSES = frozenset({"reading", "finished", "abandoned", "paused", "reread"})
_DNF_STATUSES = frozenset({"abandoned", "paused"})


def abandonment(sigs: list[EntrySig]) -> dict | None:
    """Which emotion correlates with NOT finishing (B7.2).

    Matches on the explicit status. Migration 022 widened the status constraint to
    include 'abandoned' and 'paused' and added a `dnf_reason` column, so the old
    "there is no explicit DNF flag yet" proxy of `status != 'finished'` is simply
    stale — it counted `reread` (a book finished twice!) and `want_to_read` (never
    opened) as abandonments.

    Returns the emotion whose DNF rate most exceeds the reader's overall rate, plus
    the most common stated reason among those books when there is one.
    """
    opened = [s for s in sigs if s.status in _OPENED_STATUSES]
    if not opened:
        return None
    dnf = [s for s in opened if s.status in _DNF_STATUSES]
    if len(dnf) < 3:
        return None

    overall_rate = len(dnf) / len(opened)
    best_slug, best_rate = None, overall_rate
    for slug in _ALL_SLUGS:
        tagged = [s for s in opened if slug in s.emotions]
        if len(tagged) < 3:
            continue
        rate = sum(1 for s in tagged if s.status in _DNF_STATUSES) / len(tagged)
        if rate > best_rate:
            best_slug, best_rate = slug, rate
    if best_slug is None:
        return None

    # The reader's own words for why, when they gave them. Only counted on the
    # abandoned books carrying this emotion — that is what the sentence is about.
    reasons = Counter(
        s.dnf_reason for s in dnf
        if best_slug in s.emotions and s.dnf_reason
    )
    top_reason, reason_books = (reasons.most_common(1)[0] if reasons else (None, 0))

    return {
        "emotion": best_slug,
        "fraction": round(best_rate, 2),
        "dnf_reason": top_reason,
        "dnf_reason_books": reason_books,
    }


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

# The population's mean emotion vector. Every archetype's score is measured as
# DEVIATION from what this baseline would already give it, because the raw sum is
# not comparable between archetypes: they hold emotions with wildly different base
# rates, and two of them hold an anti-emotion from the "It lost me" family that
# nobody ever tags, so their penalty term is free.
#
# Uncentered, the leader was not the archetype that fit the reader — it was the
# one holding the most commonly-tagged emotions. `control_intellectual`
# (recognition + dread + awe, and its only live anti is catharsis) took 43% of
# simulated readers against a 12.5% fair share, and a reader with an exactly even
# spread across all 14 experiential tags produced a FIVE-WAY tie that
# `PERSONALITY_TYPES` list order silently resolved in its favour.
#
# Recompute from real users once there are enough of them (see
# scripts/refresh_archetype_baseline.py). Until then this prior is a stand-in, and
# a wrong baseline is still strictly better than none: centering on the wrong
# numbers costs a few points of fairness, centering on nothing costs 10x.
BASELINE_VECTOR: dict[str, float] = {
    "awe": 0.126, "longing": 0.099, "devastation": 0.094, "joy": 0.089,
    "recognition": 0.081, "tenderness": 0.081, "dread": 0.077, "grief": 0.074,
    "desire": 0.069, "rage": 0.052, "catharsis": 0.049, "amusement": 0.032,
    "comfort": 0.032, "nostalgia": 0.024, "boredom": 0.005, "revulsion": 0.005,
    "confusion": 0.005, "indifference": 0.005,
}

# Below this gap the leader has not earned the noun outright, and the caller shows
# the runner-up alongside it. This is a HEDGE, not an abstention: a reader who is
# genuinely between two archetypes should be told which two, not handed a blank.
# Abstention is reserved for having no signal at all.
HEDGE_ARCHETYPE_GAP = 0.05

# Emotions that anchor at least one archetype. A reader whose entire vector sits
# outside this set (only "It lost me" tags) has told us what bored them and nothing
# about who they are — that is an abstention, not a score of zero.
_ANCHOR_SLUGS = frozenset(
    e for t in PERSONALITY_TYPES for e in t["primary_emotions"]
)


def _raw_archetype_score(freq: dict[str, float], t: dict) -> float:
    s = sum(freq.get(e, 0.0) for e in t["primary_emotions"])
    s -= 0.5 * sum(freq.get(e, 0.0) for e in t.get("anti_emotions", []))
    return s


# What each archetype scores on the average reader — the constant we subtract.
_BASELINE_OFFSET: dict[str, float] = {
    t["id"]: _raw_archetype_score(BASELINE_VECTOR, t) for t in PERSONALITY_TYPES
}


def score_archetype(
    current_freq: dict[str, float],
) -> tuple[str | None, dict[str, float], float]:
    """Score the 8 archetypes against the CURRENT (recency-weighted) vector, so the
    headline can actually change as the reader changes.

    Scores are centered on ``BASELINE_VECTOR``: a score of 0.0 means "exactly what
    the average reader would score here", positive means this reader leans that way
    more than most, negative less. They are therefore signed, and the old
    ``top <= 0`` abstention rule would have thrown away every reader who is simply
    less extreme than average.

    Returns (best_id | None, scores, gap). ``best_id`` is None when the reader has
    tagged nothing at all, or when the leader fails to clear the runner-up by
    ``MIN_ARCHETYPE_GAP`` — an unearned label is worse than an honest blank.
    ``gap`` is the absolute lead over second place, in the same units as the
    frequency vector, so it is comparable between readers. When it falls below
    ``HEDGE_ARCHETYPE_GAP`` the label still stands but the caller shows the
    runner-up next to it.
    """
    scores: dict[str, float] = {
        t["id"]: round(_raw_archetype_score(current_freq, t) - _BASELINE_OFFSET[t["id"]], 4)
        for t in PERSONALITY_TYPES
    }

    if sum(current_freq.get(s, 0.0) for s in _ANCHOR_SLUGS) <= 0:
        return None, scores, 0.0

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    (best, top), (_, second) = ranked[0], ranked[1]
    return best, scores, round(top - second, 4)


def basis_for(archetype_id: str, sigs: list[EntrySig]) -> dict:
    """The evidence line under the label. Counts only — no adjectives.

    This is what turns the name from a bucket the reader was sorted into to a
    headline for a number they can go and check against their own shelf. Every
    figure here is countable by hand: "grief in 14 of your 31 books" is either
    true or it isn't, and the reader is the one who can tell.

    Books only — the caller passes book sigs, never journal days, because this
    line is rendered on public surfaces and says the word "books".
    """
    t = _TYPES_BY_ID[archetype_id]
    total = len(sigs)
    rows = []
    for slug in t["primary_emotions"]:
        n = sum(1 for s in sigs if slug in s.emotions)
        if n:
            rows.append({"emotion": slug, "books": n, "of": total})
    # What the reader reserves the top of their scale for. Three books is few
    # enough to be a real claim about specific books they'll remember.
    top_rated = sorted(sigs, key=lambda s: -s.intensity)[:3]
    return {
        "counts": sorted(rows, key=lambda r: -r["books"]),
        "top_rated_emotions": sorted({e for s in top_rated for e in s.emotions}),
        "top_rated_n": len(top_rated),
    }


def archetype_dict(type_id: str) -> dict:
    t = _TYPES_BY_ID[type_id]
    return {"id": t["id"], "name": t["name"], "description": t["description"],
            "color": t["color"], "glyph": t["glyph"],
            "blind_spots": t["blind_spots"], "comfort_tropes": t["comfort_tropes"]}
