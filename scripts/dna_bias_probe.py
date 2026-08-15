"""Archetype distribution probe for score_archetype().

Why this exists: the P1-5 fix was validated against *independent uniform* readers,
a model under which the scorer looks fair (~12.5% each). Real tagging is
correlated — a book makes you feel several things at once, and the picker groups
by family — and under correlated tagging the scorer collapses onto two labels.

Run:  python -m scripts.dna_bias_probe
Exit code 1 if any archetype's win share falls outside [6%, 20%] on the
correlated model. Wire it into CI so the next re-anchor can't regress silently.

No DB, no I/O. Pure math over app.services.dna_signals.
"""

import itertools
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from app.services.dna_engine import PERSONALITY_TYPES
from app.services.dna_signals import (
    DRIFT_SNAPSHOT_THRESHOLD,
    HEDGE_ARCHETYPE_GAP,
    GATES,
    EntrySig,
    _ALL_SLUGS,
    drift,
    frequency_vector,
    recency_weight,
    score_archetype,
)
from app.utils.emotions import EMOTIONS

FAMILY = {e["slug"]: e["family"] for e in EMOTIONS}
IDS = [t["id"] for t in PERSONALITY_TYPES]
FAIR = 1.0 / len(IDS)
LOWER, UPPER = 0.06, 0.20

# What a real book actually makes you feel, together. Tags inside one of these
# bundles are correlated by construction — which is the whole point, because the
# scorer is a linear sum over marginal frequencies and therefore silently rewards
# archetypes whose three primaries co-occur.
BOOK_BUNDLES: dict[str, list[str]] = {
    "romantasy_dark":   ["desire", "dread", "devastation", "rage", "awe"],
    "romantasy_soft":   ["desire", "longing", "joy", "awe"],
    "grief_litfic":     ["grief", "devastation", "catharsis", "tenderness"],
    "cozy":             ["comfort", "tenderness", "joy"],
    "thriller":         ["dread", "rage", "awe"],
    "quiet_litfic":     ["recognition", "tenderness", "longing", "awe"],
    "memoir":           ["recognition", "grief", "catharsis", "nostalgia"],
    "comic_novel":      ["amusement", "joy", "recognition"],
    "epic_fantasy":     ["awe", "dread", "devastation", "longing"],
    "sad_romance":      ["longing", "grief", "desire", "devastation"],
}
LOST_ME = [s for s in _ALL_SLUGS if FAMILY[s] == "It lost me"]


def _norm(counts: Counter) -> dict[str, float]:
    total = sum(counts.values())
    if not total:
        return {s: 0.0 for s in _ALL_SLUGS}
    return {s: counts.get(s, 0) / total for s in _ALL_SLUGS}


def _bar(frac: float, width: int = 34) -> str:
    return "█" * round(frac * width * len(IDS))


def _report(title: str, wins: Counter, n: int) -> dict[str, float]:
    print(f"\n{title}")
    print("-" * len(title))
    shares = {}
    for i in IDS:
        share = wins[i] / n
        shares[i] = share
        flag = "  <-- OUT OF BAND" if not LOWER <= share <= UPPER else ""
        print(f"  {i:26} {share:6.1%}  {_bar(share)}{flag}")
    if wins.get(None):
        print(f"  {'(abstained)':26} {wins[None] / n:6.1%}")
    return shares


def tie_rate() -> None:
    """Exact ties are decided by position in PERSONALITY_TYPES. Measure how often."""
    order = {t["id"]: i for i, t in enumerate(PERSONALITY_TYPES)}
    print("\nEXACT-TIE RATE (winner decided by list order, not by the reader)")
    print("-" * 62)
    for k in (2, 3, 4):
        combos = list(itertools.combinations(_ALL_SLUGS, k))
        ties = 0
        by_pos: Counter = Counter()
        for combo in combos:
            best, scores, margin = score_archetype(_norm(Counter(dict.fromkeys(combo, 1))))
            if margin == 0.0 and best is not None:
                ties += 1
                by_pos[order[best]] += 1
        print(f"  readers using exactly {k} distinct emotions: "
              f"{ties}/{len(combos)} = {ties / len(combos):.0%} tie")
        if by_pos:
            print(f"     ties awarded to list index: {dict(sorted(by_pos.items()))}")


def uniform_reader() -> None:
    """The most average reader possible. Should not be a 5-way tie."""
    exp = [s for s in _ALL_SLUGS if FAMILY[s] != "It lost me"]
    best, scores, margin = score_archetype(_norm(Counter(dict.fromkeys(exp, 1))))
    print("\nTHE PERFECTLY BALANCED READER (equal share of all 14 experiential tags)")
    print("-" * 70)
    for k, v in sorted(scores.items(), key=lambda kv: -kv[1]):
        print(f"  {k:26} {v}")
    top = max(scores.values())
    tied = [k for k, v in scores.items() if v == top]
    print(f"  -> labelled {best}, margin {margin}, {len(tied)}-way tie: {tied}")


def leverage() -> None:
    """Per-tag discriminating power. spread == 0 means the tag decides nothing."""
    print("\nPER-TAG LEVERAGE (spread between the best and 2nd-best archetype)")
    print("-" * 66)
    rows = []
    for slug in _ALL_SLUGS:
        gains = {
            t["id"]: (1.0 if slug in t["primary_emotions"] else 0.0)
            - (0.5 if slug in t["anti_emotions"] else 0.0)
            for t in PERSONALITY_TYPES
        }
        ranked = sorted(gains.items(), key=lambda kv: -kv[1])
        rows.append((slug, ranked[0][0], ranked[0][1] - ranked[1][1]))
    for slug, winner, spread in sorted(rows, key=lambda r: -r[2]):
        note = "" if spread > 0 else "   <-- decides nothing on its own"
        print(f"  {slug:13} -> {winner:26} spread {spread:.2f}{note}")


def independent_population(n_readers: int = 20_000, seed: int = 21) -> dict[str, float]:
    """The model the P1-5 fix was validated against: tags drawn independently."""
    random.seed(seed)
    exp = [s for s in _ALL_SLUGS if FAMILY[s] != "It lost me"]
    wins: Counter = Counter()
    for _ in range(n_readers):
        weights = [random.gammavariate(4, 1) for _ in exp]
        counts: Counter = Counter()
        for _book in range(random.randint(10, 50)):
            tags = [random.choices(exp, weights=weights)[0]
                    for _ in range(random.randint(1, 3))]
            for slug in tags:
                counts[slug] += 1.0 / len(tags)
        wins[score_archetype(_norm(counts))[0]] += 1
    return _report("INDEPENDENT TAGGING (the old validation model)", wins, n_readers)


def correlated_population(n_readers: int = 20_000, seed: int = 5) -> dict[str, float]:
    """Readers whose shelves are built from whole books, not loose tags."""
    random.seed(seed)
    keys = list(BOOK_BUNDLES)
    wins: Counter = Counter()
    for _ in range(n_readers):
        taste = [random.gammavariate(2, 1) for _ in keys]
        counts: Counter = Counter()
        for _book in range(random.randint(10, 50)):
            tags = [s for s in BOOK_BUNDLES[random.choices(keys, weights=taste)[0]]
                    if random.random() < 0.75]
            if random.random() < 0.07:
                tags.append(random.choice(LOST_ME))
            if not tags:
                continue
            for slug in tags:            # one entry, one vote — matches frequency_vector
                counts[slug] += 1.0 / len(tags)
        wins[score_archetype(_norm(counts))[0]] += 1
    return _report("CORRELATED TAGGING (how books actually work)", wins, n_readers)


def family_ownership() -> None:
    by_family = defaultdict(list)
    for slug, fam in FAMILY.items():
        by_family[fam].append(slug)
    print("\nWHO OWNS EACH UI FAMILY (reader tags evenly inside one family)")
    print("-" * 62)
    for fam, slugs in by_family.items():
        best, _, margin = score_archetype(_norm(Counter(dict.fromkeys(slugs, 1))))
        print(f"  {fam:22} -> {str(best):26} margin {margin}")


# ── Drift threshold (the other half of the calibration commit) ──
#
# One-entry-one-vote changes the *shape* of both frequency vectors, and it does
# not change them identically: `weighted=True` divides an already-decayed weight
# by the tag count, so a heavily-tagged recent book no longer dominates the
# current vector the way it dominated the enduring one. The two vectors therefore
# sit further apart than they used to, and `drift` — 1 - cosine between them —
# rises across the board. DRIFT_SNAPSHOT_THRESHOLD is an absolute cut on that
# number, so leaving it at 0.15 silently raises how often we snapshot and how
# often we tell someone their DNA shifted.
#
# This probe measures the crossing rate under BOTH vector implementations on the
# same shelves and picks the threshold that restores the old rate.
#
# TWO SHAPES, and only one of them is the one that matters. `drift()` is a plain
# 1-cosine and the codebase calls it on two different pairs of vectors:
#
#   enduring-vs-current   drift(frequency_vector(weighted=False),
#                               frequency_vector(weighted=True))
#                         — the product concept, "who you've been vs lately".
#
#   snapshot-vs-today     drift(prev_snapshot["current_vector"], current)
#                         — what dna_service.maybe_snapshot_and_notify actually
#                           computes, and the ONLY thing DRIFT_SNAPSHOT_THRESHOLD
#                           is ever compared against.
#
# These have different distributions, so they do not calibrate to the same
# number. The threshold is calibrated on the second one, because that is the one
# that decides whether a row is written and a `dna_shifted` notification is sent.
# The first is reported alongside it because it is the more intuitive quantity
# and a reader of this output will otherwise assume that is what was measured.

SHELF_SPAN_DAYS = 730
# MONTHLY_CADENCE_DAYS: snapshots are at most this far apart, so this is the
# window over which a reader accumulates books between two drift evaluations.
SNAPSHOT_GAP_DAYS = 30
DRIFT_CURVE = [0.10, 0.125, 0.15, 0.175, 0.20, 0.225, 0.25, 0.275, 0.30, 0.35, 0.40]

# The threshold the OLD vector shipped with. Pinned as a literal on purpose: the
# recalibration targets "the rate the old engine produced at its own threshold",
# so if this read DRIFT_SNAPSHOT_THRESHOLD it would re-target itself against the
# value we just changed and ratchet the threshold up on every subsequent run.
PRE_CALIBRATION_THRESHOLD = 0.15


def _old_frequency_vector(sigs: list[EntrySig], *, weighted: bool, now: datetime) -> dict[str, float]:
    """The PRE-calibration vector: one vote per TAG, not per entry.

    Duplicated here rather than imported because the version in dna_signals.py no
    longer exists. Without it, "the crossing rate doubled" is an unfalsifiable
    claim and the new threshold would be a guess dressed as a measurement.
    """
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


def _drift_shelf(now: datetime, keep: float = 0.75) -> list[EntrySig]:
    """One reader's shelf, with tag counts that VARY from book to book.

    Two things here are load-bearing:

    - Varying tag counts. If every entry carried the same number of tags, the
      one-entry-one-vote divisor would be the same constant on every entry and
      would cancel in the normalisation — the new vector would be numerically
      identical to the old one and this probe would measure exactly nothing. The
      `keep` rate on each bundle is what makes the divisor differ per book, and
      lowering it widens the spread of tag counts across the shelf.

    - Taste that rotates over the shelf's span. `drift` is the gap between the
      all-time and the recency-weighted vector, so if a reader's taste were
      stationary the two would differ only by sampling noise and every candidate
      threshold would look equally good. Oldest books are drawn from `early`
      taste, newest from `late`.

    Returns (shelf_at_last_snapshot, books_added_since). The second list is what
    makes the snapshot-vs-today shape measurable: without new books the reader's
    current vector would only move by recency decay.
    """
    keys = list(BOOK_BUNDLES)
    early = [random.gammavariate(2, 1) for _ in keys]
    late = [random.gammavariate(2, 1) for _ in keys]
    sigs: list[EntrySig] = []
    for _ in range(random.randint(15, 60)):
        age = random.uniform(0, SHELF_SPAN_DAYS)
        f = age / SHELF_SPAN_DAYS                     # 1.0 = oldest book
        taste = [f * e + (1.0 - f) * l for e, l in zip(early, late)]
        tags = [s for s in BOOK_BUNDLES[random.choices(keys, weights=taste)[0]]
                if random.random() < keep]
        if random.random() < 0.07:
            tags.append(random.choice(LOST_ME))
        if not tags:
            continue
        sigs.append(EntrySig(
            emotions=sorted(set(tags)), intensity=7,
            ts=now - timedelta(days=age), status="finished",
        ))

    # Books logged in the month between the last snapshot and today.
    added: list[EntrySig] = []
    for _ in range(random.randint(0, 6)):
        tags = [s for s in BOOK_BUNDLES[random.choices(keys, weights=late)[0]]
                if random.random() < keep]
        if not tags:
            continue
        added.append(EntrySig(
            emotions=sorted(set(tags)), intensity=7,
            ts=now + timedelta(days=random.uniform(0, SNAPSHOT_GAP_DAYS)),
            status="finished",
        ))
    return sigs, added


def _rate(values: list[float], threshold: float) -> float:
    return sum(1 for v in values if v >= threshold) / len(values)


_PICK_GRID = [round(0.10 + 0.005 * i, 3) for i in range(61)]


def _drift_samples(n_readers: int, seed: int, keep: float, *, shape: str
                   ) -> tuple[list[float], list[float]]:
    """Drift values under the new and the old vector, on identical shelves.

    shape="snapshot" reproduces what dna_service actually gates on; shape="enduring"
    is the product-concept quantity. See the module comment above.
    """
    random.seed(seed)
    t1 = datetime.now(timezone.utc)
    t2 = t1 + timedelta(days=SNAPSHOT_GAP_DAYS)
    new_d: list[float] = []
    old_d: list[float] = []
    for _ in range(n_readers):
        shelf, added = _drift_shelf(t1, keep=keep)
        if len(shelf) < GATES["drift"]:
            continue                                   # drift isn't computed below the gate
        for fv, acc in ((frequency_vector, new_d), (_old_frequency_vector, old_d)):
            if shape == "snapshot":
                acc.append(drift(fv(shelf, weighted=True, now=t1),
                                 fv(shelf + added, weighted=True, now=t2)))
            else:
                acc.append(drift(fv(shelf, weighted=False, now=t1),
                                 fv(shelf, weighted=True, now=t1)))
    return new_d, old_d


def _pick(new_d: list[float], target: float) -> float:
    """The threshold at which the new vector crosses as often as the old one did."""
    return min(_PICK_GRID, key=lambda t: abs(_rate(new_d, t) - target))


def drift_threshold(n_readers: int = 20_000, seed: int = 13) -> float:
    new_d, old_d = _drift_samples(n_readers, seed, keep=0.75, shape="snapshot")
    target = _rate(old_d, PRE_CALIBRATION_THRESHOLD)

    print("\nDRIFT SNAPSHOT / NOTIFICATION CROSSING RATE")
    print("-" * 62)
    print("  shape: drift(last snapshot's current vector, today's current vector)")
    print("         — the only expression DRIFT_SNAPSHOT_THRESHOLD gates.")
    print(f"  shelves measured: {len(new_d)}  (>= {GATES['drift']} books, the drift gate)")
    print(f"  old vector @ {PRE_CALIBRATION_THRESHOLD}: {target:.1%}   <-- the rate to restore")
    print(f"  new vector @ {PRE_CALIBRATION_THRESHOLD}: {_rate(new_d, PRE_CALIBRATION_THRESHOLD):.1%}   "
          f"<-- what shipping the patch alone would do")
    print(f"\n  {'threshold':>10}  {'new vector':>11}  {'old vector':>11}")
    for t in DRIFT_CURVE:
        mark = "  <-- current" if t == DRIFT_SNAPSHOT_THRESHOLD else ""
        print(f"  {t:>10.3f}  {_rate(new_d, t):>10.1%}  {_rate(old_d, t):>10.1%}{mark}")

    best = _pick(new_d, target)
    print(f"\n  -> {best} on the new vector gives {_rate(new_d, best):.1%}, "
          f"restoring the {target:.1%} the old vector produced at {PRE_CALIBRATION_THRESHOLD}.")

    # The ABSOLUTE crossing rate is a property of this generator, not of the
    # engine: it swings from ~0% to ~20% depending on how much tag-count spread
    # and taste-rotation you assume, and no shelf model is the real population.
    # So don't trust any single row of it. What survives the sweep is the PICK —
    # the ratio between the two vectors is stable even when the level is not.
    # Re-run this before trusting a new threshold; if the pick column stops being
    # flat, the recalibration logic itself needs revisiting.
    print("\n  sensitivity — is the pick an artifact of the shelf model?")
    print(f"  {'shape':<11} {'variant':<18} {'old@base':>9} {'new@base':>9} {'pick':>7}")
    picks = []
    for shape in ("snapshot", "enduring"):
        for label, keep, seed_ in (
            ("keep=0.50 (wide)",   0.50, 13),
            ("keep=0.60",          0.60, 13),
            ("keep=0.75",          0.75, 13),
            ("keep=0.90 (narrow)", 0.90, 13),
            ("keep=0.75 seed=77",  0.75, 77),
            ("keep=0.75 seed=404", 0.75, 404),
        ):
            nd, od = _drift_samples(6000, seed_, keep, shape=shape)
            tgt = _rate(od, PRE_CALIBRATION_THRESHOLD)
            p = _pick(nd, tgt)
            if shape == "snapshot":
                picks.append(p)
            print(f"  {shape:<11} {label:<18} {tgt:>8.1%} "
                  f"{_rate(nd, PRE_CALIBRATION_THRESHOLD):>9.1%} {p:>7.3f}")
    print(f"\n  snapshot-shape picks span {min(picks):.3f}-{max(picks):.3f}. The absolute")
    print("  crossing rate swings with the shelf model; the pick does not. The")
    print("  'enduring' rows are the quantity SESSION.md named — reported so the")
    print("  difference is visible, NOT used to set the threshold.")
    return best


# ── Hedge rate ──
#
# HEDGE_ARCHETYPE_GAP is an absolute cut on `gap`, and centering moved `gap` onto a
# much smaller scale than the old fraction-of-leader margin lived on. A constant
# picked by eye on the old scale is meaningless on the new one: 0.05 sat above the
# median (0.031), so it hedged 69% of readers and the hedge became the default
# state rather than an exception. A hedge that fires on most readers tells nobody
# anything.
#
# Set this at a PERCENTILE of the gap distribution, not at a round number.

HEDGE_CANDIDATES = [0.008, 0.010, 0.011, 0.012, 0.013, 0.015, 0.020, 0.025, 0.031, 0.05]


def _labelled_gaps(n_readers: int, seed: int, keep: float) -> list[float]:
    """Sorted `gap` for every reader the scorer actually names.

    Abstentions are excluded: they are handed a blank, not a hedge, so including
    them would understate the rate among readers who see a label at all.
    """
    random.seed(seed)
    keys = list(BOOK_BUNDLES)
    out: list[float] = []
    for _ in range(n_readers):
        taste = [random.gammavariate(2, 1) for _ in keys]
        counts: Counter = Counter()
        for _book in range(random.randint(10, 50)):
            tags = [s for s in BOOK_BUNDLES[random.choices(keys, weights=taste)[0]]
                    if random.random() < keep]
            if random.random() < 0.07:
                tags.append(random.choice(LOST_ME))
            if not tags:
                continue
            for slug in tags:
                counts[slug] += 1.0 / len(tags)
        best, _, gap = score_archetype(_norm(counts))
        if best is not None:
            out.append(gap)
    return sorted(out)


def _pctile(xs: list[float], p: float) -> float:
    return xs[min(len(xs) - 1, int(p / 100.0 * len(xs)))]


def _hedge_rate(xs: list[float], t: float) -> float:
    return sum(1 for g in xs if g < t) / len(xs)


def hedge_rate(n_readers: int = 20_000, seed: int = 5) -> None:
    xs = _labelled_gaps(n_readers, seed, keep=0.75)
    print("\nHEDGE RATE (how often the card shows a runner-up)")
    print("-" * 62)
    print(f"  labelled readers: {len(xs)}   median gap {_pctile(xs, 50):.4f}")
    print(f"  current HEDGE_ARCHETYPE_GAP = {HEDGE_ARCHETYPE_GAP} -> hedges "
          f"{_hedge_rate(xs, HEDGE_ARCHETYPE_GAP):.1%}")
    print(f"\n  {'percentile':>10}  {'gap':>8}")
    for p in (5, 10, 15, 20, 25, 30, 40, 50, 75, 90):
        print(f"  {'p' + str(p):>10}  {_pctile(xs, p):>8.4f}")
    print(f"\n  {'candidate':>10}  {'hedge rate':>11}")
    for t in HEDGE_CANDIDATES:
        mark = "  <-- current" if t == HEDGE_ARCHETYPE_GAP else ""
        print(f"  {t:>10.3f}  {_hedge_rate(xs, t):>10.1%}{mark}")

    print("\n  stability of the current value across seeds and tag-count spread")
    print(f"  {'variant':<20} {'median':>8} {'p20':>8} {'p25':>8} {'rate':>8}")
    for label, seed_, keep in (("seed=5  keep=0.75", 5, 0.75),
                               ("seed=11 keep=0.75", 11, 0.75),
                               ("seed=77 keep=0.75", 77, 0.75),
                               ("seed=5  keep=0.50", 5, 0.50),
                               ("seed=5  keep=0.90", 5, 0.90)):
        ys = _labelled_gaps(8000, seed_, keep)
        print(f"  {label:<20} {_pctile(ys,50):>8.4f} {_pctile(ys,20):>8.4f} "
              f"{_pctile(ys,25):>8.4f} {_hedge_rate(ys, HEDGE_ARCHETYPE_GAP):>7.1%}")


def main() -> int:
    uniform_reader()
    tie_rate()
    leverage()
    family_ownership()
    independent_population()
    shares = correlated_population()
    hedge_rate()
    drift_threshold()

    worst = sorted(shares.items(), key=lambda kv: -abs(kv[1] - FAIR))
    print(f"\nfair share = {FAIR:.1%};  accepted band = {LOWER:.0%}–{UPPER:.0%}")
    failures = [(k, v) for k, v in shares.items() if not LOWER <= v <= UPPER]
    if failures:
        print("FAIL — archetypes outside the band on the correlated model:")
        for k, v in sorted(failures, key=lambda kv: -kv[1]):
            print(f"   {k:26} {v:.1%}")
        print(f"  (largest deviation: {worst[0][0]} at {worst[0][1]:.1%})")
        return 1
    print("PASS — every archetype within band.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
