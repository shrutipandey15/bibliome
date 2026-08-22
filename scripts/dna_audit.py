"""End-to-end audit of what the DNA engine actually outputs.

    python -m scripts.dna_audit                 # full report
    python -m scripts.dna_audit --strict        # exit 1 on WARN as well as ERROR
    python -m scripts.dna_audit --json          # machine-readable findings
    python -m scripts.dna_audit --only 3,5      # just those sections
    python -m scripts.dna_audit --readers 20000 --seed 11

HOW THIS DIFFERS FROM scripts/dna_bias_probe.py
-----------------------------------------------
The probe answers ONE question — is the win-share distribution fair — and exists
to calibrate HEDGE_ARCHETYPE_GAP and DRIFT_SNAPSHOT_THRESHOLD against it. It is a
threshold-setting instrument.

This is a *diagnostic*. It asks what the engine is saying and whether the sentence
is true: which emotion leads to which archetype, at what percentage, with what
margin, and whether the label a reader gets actually describes the shelf they
have. Run it during development after touching PERSONALITY_TYPES, EMOTIONS,
BASELINE_VECTOR, or the scoring math, and read the numbers — not just the exit
code.

It shares BOOK_BUNDLES with the probe on purpose (one shelf model, imported, not
copied) so the two tools cannot drift apart in what they think a book is.

DESIGN NOTE — THIS IS DELIBERATELY NOT AN OPTIMISTIC SCRIPT
------------------------------------------------------------
Every check here reports the measured number whether or not it passes, and
several are expected to emit WARNs on a healthy tree. A WARN is "look at this
before you ship", not "the build is broken". The failure mode this is built
against is a checker that goes green while the product says something false, so
where a check could be written either as a tight assertion or as a printed
number, it is written as both. Findings carry a severity and a stable code so you
can grep for one across runs.

  ERROR — an invariant is broken. The engine can emit a wrong or arbitrary label.
  WARN  — a real deviation. Might be a deliberate trade-off; must be a known one.
  INFO  — context you need to interpret the WARNs. Never affects exit code.

No DB, no I/O, no network. Pure math over app.services.*.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.services import dna_signals as sig
from app.services.dna_engine import PERSONALITY_TYPES
from app.services.dna_insights import build_dna
from app.utils.emotions import (
    BLIND_SPOT_HINTS,
    EMOTIONS,
    LEGACY_EMOTION_MAP,
    LOST_ME_SLUGS,
    VALID_SLUGS,
)
from scripts.dna_bias_probe import BOOK_BUNDLES, KNOWN_SHORTFALL, _labelled_gaps

IDS = [t["id"] for t in PERSONALITY_TYPES]
BY_ID = {t["id"]: t for t in PERSONALITY_TYPES}
FAIR = 1.0 / len(IDS)
NOW = datetime.now(timezone.utc)
ALL = sig._ALL_SLUGS
EXPERIENTIAL = [s for s in ALL if s not in LOST_ME_SLUGS]

# A baseline rate at or below this is the "nobody tags this" floor. An archetype
# anchored on such a slug gets a free ride; an archetype whose ANTI sits there
# pays a penalty that never fires. Both are silent, and both have shipped before.
FLOOR_RATE = 0.010


# ── findings ────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    severity: str          # ERROR | WARN | INFO
    code: str              # stable, greppable
    message: str
    detail: dict = field(default_factory=dict)


FINDINGS: list[Finding] = []


def err(code, message, **detail):
    FINDINGS.append(Finding("ERROR", code, message, detail))


def warn(code, message, **detail):
    FINDINGS.append(Finding("WARN", code, message, detail))


def info(code, message, **detail):
    FINDINGS.append(Finding("INFO", code, message, detail))


def head(n: int, title: str) -> None:
    print(f"\n\n{'=' * 78}\n{n}. {title}\n{'=' * 78}")


def sub(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def _norm(counts: Counter) -> dict[str, float]:
    total = sum(counts.values())
    if not total:
        return {s: 0.0 for s in ALL}
    return {s: counts.get(s, 0) / total for s in ALL}


def _only(*slugs: str) -> dict[str, float]:
    """The vector of a reader who tagged exactly these, evenly."""
    return _norm(Counter(dict.fromkeys(slugs, 1)))


def _sig(emotions, days_ago, intensity=7, status="finished"):
    return sig.EntrySig(emotions=list(emotions), intensity=intensity,
                        ts=NOW - timedelta(days=days_ago), status=status)


# ── 1. vocabulary ───────────────────────────────────────────────────────────

def section_vocabulary() -> None:
    head(1, "VOCABULARY INTEGRITY")

    families: dict[str, list[str]] = defaultdict(list)
    for e in EMOTIONS:
        families[e["family"]].append(e["slug"])

    sub(f"{len(EMOTIONS)} slugs in {len(families)} families")
    for fam, slugs in families.items():
        anchored = sum(1 for s in slugs
                       if any(s in t["primary_emotions"] for t in PERSONALITY_TYPES))
        tag = "disengagement" if set(slugs) <= LOST_ME_SLUGS else f"{anchored}/{len(slugs)} anchor a type"
        print(f"  {fam:22} {len(slugs)}  {', '.join(slugs):52} ({tag})")

    # Coverage of every side table. A slug missing from one of these does not
    # raise — it silently drops a feature for that emotion.
    for name, table in (("BLIND_SPOT_HINTS", set(BLIND_SPOT_HINTS)),
                        ("BASELINE_VECTOR", set(sig.BASELINE_VECTOR))):
        missing = VALID_SLUGS - table
        extra = table - VALID_SLUGS
        if missing:
            err("VOCAB_TABLE_MISSING", f"{name} is missing {sorted(missing)}",
                table=name, slugs=sorted(missing))
        if extra:
            err("VOCAB_TABLE_STALE", f"{name} has slugs not in the vocabulary: {sorted(extra)}",
                table=name, slugs=sorted(extra))
    total = sum(sig.BASELINE_VECTOR.values())
    print(f"\n  BASELINE_VECTOR sums to {total:.4f} over {len(sig.BASELINE_VECTOR)} slugs")
    if abs(total - 1.0) > 0.001:
        err("BASELINE_NOT_NORMALIZED",
            f"BASELINE_VECTOR sums to {total:.4f}, not 1.0 — every centered score is skewed",
            total=total)

    # Legacy slugs must land somewhere real, or historical rows are dropped on read.
    for old, new in LEGACY_EMOTION_MAP.items():
        if new not in VALID_SLUGS:
            err("LEGACY_TARGET_DEAD",
                f"LEGACY_EMOTION_MAP[{old!r}] -> {new!r}, which is not a canonical slug",
                legacy=old, target=new)

    # Duplicate reader-facing strings would make two chips indistinguishable.
    for field_name in ("phrase", "name", "color", "symbol"):
        seen: dict[str, str] = {}
        for e in EMOTIONS:
            v = e[field_name]
            if v in seen:
                warn("VOCAB_DUPLICATE",
                     f"duplicate {field_name} {v!r}: {seen[v]} and {e['slug']}",
                     field=field_name, value=v, slugs=[seen[v], e["slug"]])
            seen[v] = e["slug"]

    # THE REVULSION CHECK. An experiential slug sitting at the disengagement floor
    # is almost always a slug that changed meaning and kept its old prior — which
    # hands every archetype anchored on it a free offset.
    sub("base rates of anchoring emotions (floor check)")
    anchored = {s for t in PERSONALITY_TYPES for s in t["primary_emotions"]}
    for slug in sorted(anchored, key=lambda s: sig.BASELINE_VECTOR.get(s, 0.0)):
        rate = sig.BASELINE_VECTOR.get(slug, 0.0)
        flag = ""
        if rate <= FLOOR_RATE:
            flag = "  <-- AT THE FLOOR"
            warn("ANCHOR_AT_FLOOR",
                 f"{slug!r} anchors an archetype but its baseline is {rate:.4f} — "
                 "a slug that changed meaning and kept its old prior gives every "
                 "type holding it a free offset",
                 slug=slug, rate=rate,
                 types=[t["id"] for t in PERSONALITY_TYPES if slug in t["primary_emotions"]])
        print(f"  {slug:14} {rate:.4f}{flag}")


# ── 2. archetype table ──────────────────────────────────────────────────────

def section_archetypes() -> None:
    head(2, "ARCHETYPE TABLE — primaries, antis, and what each one costs")

    shared = Counter()
    for t in PERSONALITY_TYPES:
        for s in t["primary_emotions"]:
            shared[s] += 1

    print(f"\n  {'archetype':26} {'offset':>8}  primaries (baseline rate, #types sharing)")
    for t in PERSONALITY_TYPES:
        prim = "  ".join(
            f"{s}({sig.BASELINE_VECTOR.get(s, 0):.3f}×{shared[s]})" for s in t["primary_emotions"]
        )
        print(f"  {t['id']:26} {sig._BASELINE_OFFSET[t['id']]:8.4f}  {prim}")
        antis = []
        for s in t.get("anti_emotions", []):
            rate = sig.BASELINE_VECTOR.get(s, 0.0)
            mark = " FREE" if rate <= FLOOR_RATE else ""
            antis.append(f"{s}({rate:.3f}){mark}")
            if rate <= FLOOR_RATE:
                warn("FREE_ANTI",
                     f"{t['id']}: anti-emotion {s!r} has baseline {rate:.4f} — its "
                     "penalty effectively never fires, so this type carries one real "
                     "anti, not two",
                     type=t["id"], slug=s, rate=rate)
        print(f"  {'':26} {'':8}  anti: {'  '.join(antis)}")

        if len(t.get("anti_emotions", [])) != 2:
            err("ANTI_COUNT", f"{t['id']} carries {len(t['anti_emotions'])} anti_emotions, not 2",
                type=t["id"], antis=t["anti_emotions"])
        for s in t["primary_emotions"] + t["anti_emotions"]:
            if s not in VALID_SLUGS:
                err("NONCANONICAL_SLUG", f"{t['id']} references non-canonical {s!r}",
                    type=t["id"], slug=s)
        for s in t["primary_emotions"]:
            if s in LOST_ME_SLUGS:
                err("DISENGAGEMENT_PRIMARY",
                    f"{t['id']} is anchored on {s!r}, a disengagement register — "
                    "being bored a lot is not a reading identity",
                    type=t["id"], slug=s)

    # Every pair, printed, not just the offenders — so a near-miss is visible too.
    sub(f"pairwise primary overlap ({len(IDS) * (len(IDS) - 1) // 2} pairs)")
    worst = []
    for i, a in enumerate(PERSONALITY_TYPES):
        for b in PERSONALITY_TYPES[i + 1:]:
            common = set(a["primary_emotions"]) & set(b["primary_emotions"])
            worst.append((len(common), a["id"], b["id"], sorted(common)))
            if len(common) > 1:
                err("SHARED_PAIR",
                    f"{a['id']} and {b['id']} share {sorted(common)} — a reader tagging "
                    "exactly those two is an exact tie broken by declaration order",
                    types=[a["id"], b["id"]], shared=sorted(common))
    worst.sort(reverse=True)
    for n, a, b, common in worst[:6]:
        print(f"  {n}  {a:26} {b:26} {common}")
    print(f"  ... {sum(1 for w in worst if w[0] == 0)} pairs share nothing")

    # Structural advantage: a type whose primaries are simply the popular ones.
    sub("primary base-rate mass (structural advantage before any reader is seen)")
    mass = {t["id"]: sum(sig.BASELINE_VECTOR.get(s, 0) for s in t["primary_emotions"])
            for t in PERSONALITY_TYPES}
    med = statistics.median(mass.values())
    for tid, m in sorted(mass.items(), key=lambda kv: -kv[1]):
        ratio = m / med if med else 0
        flag = ""
        if ratio > 1.5 or ratio < 0.67:
            flag = "  <-- OUTLIER"
            warn("PRIMARY_MASS_OUTLIER",
                 f"{tid} primaries carry {m:.3f} of baseline mass, {ratio:.2f}x the "
                 f"median {med:.3f} — centering removes the constant but not the "
                 "variance, so this type still has more room to move",
                 type=tid, mass=m, ratio=ratio)
        print(f"  {tid:26} {m:.3f}  ({ratio:.2f}x median){flag}")


# ── 3. emotion -> archetype ─────────────────────────────────────────────────

def section_emotion_map() -> None:
    head(3, "EMOTION -> ARCHETYPE — what each single tag actually buys")
    print("\n  A reader who tagged this and nothing else gets this label. `margin` is")
    print("  the lead over second place; 0.0000 means the tag decides nothing on its")
    print("  own and the winner came from declaration order.\n")
    print(f"  {'emotion':14}{'rate':>7}{'#prim':>6}{'#anti':>6}  {'-> label':28}{'margin':>9}  runner-up")

    for slug in ALL:
        n_prim = sum(1 for t in PERSONALITY_TYPES if slug in t["primary_emotions"])
        n_anti = sum(1 for t in PERSONALITY_TYPES if slug in t.get("anti_emotions", []))
        best, scores, margin = sig.score_archetype(_only(slug))
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        second = ranked[1][0] if best else "-"
        rate = sig.BASELINE_VECTOR.get(slug, 0.0)
        flag = ""
        if best is None:
            flag = "  (abstains — correct for a disengagement tag)"
            if slug not in LOST_ME_SLUGS:
                err("EXPERIENTIAL_ABSTAINS",
                    f"{slug!r} is experiential but a reader tagging only it gets no label",
                    slug=slug)
        elif margin == 0.0:
            flag = "  <-- DECIDES NOTHING"
            warn("ZERO_LEVERAGE",
                 f"{slug!r} alone produces an exact tie between {best} and {second}; "
                 "the winner is declaration order, not the reader",
                 slug=slug, tied=[best, second])
        elif margin < 0.005:
            flag = "  <-- NEARLY DECIDES NOTHING"
            warn("NEAR_ZERO_LEVERAGE",
                 f"{slug!r} alone separates {best} from {second} by only {margin:.4f}. "
                 f"It is a primary for {n_prim} types, so it barely discriminates — a "
                 "rounding-scale nudge anywhere else flips the label",
                 slug=slug, margin=margin, n_primary=n_prim, tied=[best, second])
        print(f"  {slug:14}{rate:7.3f}{n_prim:6}{n_anti:6}  {str(best):28}{margin:9.4f}  {second}{flag}")

        if slug not in LOST_ME_SLUGS and n_prim == 0:
            err("EXPERIENTIAL_UNANCHORED",
                f"{slug!r} is experiential but anchors no archetype — it can be tagged "
                "and can never contribute to a label",
                slug=slug)


# ── 4. reachability ─────────────────────────────────────────────────────────

def section_reachability() -> None:
    head(4, "REACHABILITY — can each archetype win on its own three primaries?")
    print("\n  If a type cannot win when the reader tags exactly its own anchors, it is")
    print("  unreachable in practice and the name is decoration.\n")
    print(f"  {'archetype':26}{'wins?':>7}{'margin':>10}  actual winner / runner-up")

    for t in PERSONALITY_TYPES:
        best, scores, margin = sig.score_archetype(_only(*t["primary_emotions"]))
        ok = best == t["id"]
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        second = ranked[1][0]
        print(f"  {t['id']:26}{'yes' if ok else 'NO':>7}{margin:10.4f}  {best} / {second}")
        if not ok:
            err("UNREACHABLE",
                f"{t['id']} loses on its own primaries — a reader tagging exactly "
                f"{t['primary_emotions']} is labelled {best}",
                type=t["id"], winner=best, primaries=t["primary_emotions"])
        elif margin < sig.HEDGE_ARCHETYPE_GAP:
            warn("REACHABLE_BUT_HEDGED",
                 f"{t['id']} wins its own primaries by only {margin:.4f}, below "
                 f"HEDGE_ARCHETYPE_GAP ({sig.HEDGE_ARCHETYPE_GAP}) — its clearest "
                 "possible reader still gets a hedged card",
                 type=t["id"], margin=margin)


# ── 5. realistic population ─────────────────────────────────────────────────

def _shelf(rng: random.Random) -> list[sig.EntrySig]:
    """One reader's shelf, built from whole books rather than loose tags."""
    keys = list(BOOK_BUNDLES)
    taste = [rng.gammavariate(2, 1) for _ in keys]
    lost = sorted(LOST_ME_SLUGS)
    sigs = []
    for i in range(rng.randint(10, 50)):
        tags = [s for s in BOOK_BUNDLES[rng.choices(keys, weights=taste)[0]]
                if rng.random() < 0.75]
        if rng.random() < 0.07:
            tags.append(rng.choice(lost))
        sigs.append(_sig(tags, rng.uniform(0, 900), intensity=rng.randint(4, 10)))
    return sigs


def section_population(n_readers: int, seed: int) -> None:
    head(5, f"REALISTIC POPULATION — {n_readers} readers on the correlated shelf model")

    rng = random.Random(seed)
    wins: Counter = Counter()
    margins: dict[str, list[float]] = defaultdict(list)
    top_tags: dict[str, Counter] = defaultdict(Counter)
    hedged: Counter = Counter()

    for _ in range(n_readers):
        shelf = _shelf(rng)
        vec = sig.frequency_vector(shelf, weighted=True, now=NOW)
        best, _scores, margin = sig.score_archetype(vec)
        wins[best] += 1
        if best is None:
            continue
        margins[best].append(margin)
        if margin < sig.HEDGE_ARCHETYPE_GAP:
            hedged[best] += 1
        for slug, v in sorted(vec.items(), key=lambda kv: -kv[1])[:3]:
            if v > 0:
                top_tags[best][slug] += 1

    labelled = sum(v for k, v in wins.items() if k is not None)
    print(f"\n  labelled {labelled}/{n_readers}   abstained {wins.get(None, 0)} "
          f"({wins.get(None, 0) / n_readers:.1%})")
    print(f"  fair share {FAIR:.1%}\n")
    print(f"  {'archetype':26}{'win%':>7}{'vs fair':>9}{'med margin':>12}{'hedged':>8}  "
          "hit  what its readers actually tag most")
    print(f"  {'':26}{'':7}{'':9}{'':12}{'':8}  "
          "^ how many of its 3 primaries are in that top-3")

    for tid in IDS:
        share = wins[tid] / n_readers
        ratio = share / FAIR
        med = statistics.median(margins[tid]) if margins[tid] else 0.0
        hrate = hedged[tid] / wins[tid] if wins[tid] else 0.0
        tops = [s for s, _ in top_tags[tid].most_common(3)]
        flag = ""
        if not 0.048 <= share <= 0.16:
            if tid in KNOWN_SHORTFALL:
                flag = "  (known shortfall)"
                info("KNOWN_SHORTFALL_CONFIRMED",
                     f"{tid} at {share:.1%}, outside the band but on the accepted list",
                     type=tid, share=share)
            else:
                flag = "  <-- OUT OF BAND"
                err("WIN_SHARE_OUT_OF_BAND",
                    f"{tid} takes {share:.1%} of readers against a {FAIR:.1%} fair "
                    f"share ({ratio:.2f}x)",
                    type=tid, share=share, ratio=ratio)
        overlap = len(set(tops) & set(BY_ID[tid]["primary_emotions"]))
        print(f"  {tid:26}{share:7.1%}{ratio:8.2f}x{med:12.4f}{hrate:8.0%}  "
              f"{overlap}/3  {', '.join(tops)}{flag}")

        # THE REALISM CHECK the win-share number cannot make. If the emotions this
        # type's own readers tag most are not the emotions it is anchored on, the
        # label is winning by arithmetic and describing somebody else.
        if wins[tid] and not set(tops) & set(BY_ID[tid]["primary_emotions"]):
            warn("LABEL_DOES_NOT_DESCRIBE_ITS_READERS",
                 f"{tid} is anchored on {BY_ID[tid]['primary_emotions']} but the "
                 f"readers who get it tag {tops} most — the name is winning on "
                 "arithmetic, not on what these people read",
                 type=tid, primaries=BY_ID[tid]["primary_emotions"], actual=tops)

    if wins.get(None, 0) / n_readers > 0.02:
        warn("HIGH_ABSTENTION",
             f"{wins[None] / n_readers:.1%} of readers get no label at all",
             rate=wins[None] / n_readers)


# ── 6. the gate ─────────────────────────────────────────────────────────────

def section_gate() -> None:
    head(6, "THE GATE — raw count and effective sample size")
    print(f"\n  MIN_BOOKS_FOR_DNA = {sig.MIN_BOOKS_FOR_DNA} tagged books")
    print(f"  MIN_EFFECTIVE_BOOKS_FOR_DNA = {sig.MIN_EFFECTIVE_BOOKS_FOR_DNA} (Kish ESS)")
    print("\n  ESS is scale-invariant: age alone does not shrink it. It falls when the")
    print("  recency weight is unevenly spread — one live book beside dead ones.\n")
    print(f"  {'shelf':44}{'tagged':>8}{'ESS':>8}{'passes':>9}")

    rng = random.Random(7)
    cases = [
        ("5 tagged, all within 3 months", [rng.uniform(0, 90) for _ in range(5)]),
        ("5 tagged, spread over 1 year", [rng.uniform(0, 365) for _ in range(5)]),
        ("5 tagged, evenly over 2 years", [5, 90, 200, 400, 700]),
        ("5 tagged, evenly over 4 years", [10, 350, 700, 1100, 1450]),
        ("5 tagged, all read 2 years ago", [730] * 5),
        ("1 read last week + 4 long dead", [2, 900, 950, 1000, 1100]),
        ("12 tagged over 2 years", [i * 60 for i in range(12)]),
        ("4 tagged (under the raw gate)", [10, 20, 30, 40]),
    ]
    for label, ages in cases:
        shelf = [_sig(["joy"], a) for a in ages]
        ess = sig.effective_sample_size(shelf, now=NOW)
        out = build_dna(shelf, ["escape"], insight_limit=4)
        passes = out["enough"]
        why = ""
        if not passes:
            why = " (raw count)" if len(shelf) < sig.MIN_BOOKS_FOR_DNA else " (ESS)"
        verdict = ("yes" if passes else "no") + why
        print(f"  {label:44}{len(shelf):8}{ess:8.2f}   {verdict}")

    # An ordinary slow reader must not be silently withheld from. This is the
    # assertion that pins the ESS threshold down.
    slow = [_sig(["joy"], d) for d in (5, 90, 200, 400, 700)]
    if not build_dna(slow, ["escape"], insight_limit=4)["enough"]:
        err("GATE_BLOCKS_ORDINARY_READER",
            "5 tagged books evenly across 2 years is refused — that is a normal "
            "shelf, and it sits exactly at the five-book minimum where withholding "
            "hurts most",
            ess=sig.effective_sample_size(slow, now=NOW))

    skewed = [_sig(["joy"], 2)] + [_sig(["grief"], d) for d in (900, 950, 1000, 1100)]
    if build_dna(skewed, ["escape"], insight_limit=4)["enough"]:
        err("GATE_MISSES_SKEWED_SHELF",
            "1 recent book beside 4 dead ones clears the gate — the label would be "
            "one book's opinion wearing the authority of a shelf",
            ess=sig.effective_sample_size(skewed, now=NOW))


# ── 7. thresholds and tie-breaking ──────────────────────────────────────────

def section_thresholds(n_readers: int, seed: int) -> None:
    head(7, "THRESHOLDS — hedging, ties, and runner-up consistency")

    # The gap distribution is measured with the PROBE's reader construction, not
    # this file's, on purpose: HEDGE_ARCHETYPE_GAP was pinned to a percentile of
    # that distribution, so measuring it against a different shelf model would
    # produce a number that disagrees with its own calibration authority and
    # generate a WARN every run. One model, one threshold.
    gaps = _labelled_gaps(n_readers, seed, keep=0.75)

    def pct(p):
        return gaps[min(int(len(gaps) * p / 100), len(gaps) - 1)] if gaps else 0.0

    hedge_rate = sum(1 for g in gaps if g < sig.HEDGE_ARCHETYPE_GAP) / len(gaps)
    sub(f"gap distribution over {len(gaps)} labelled readers (probe shelf model)")
    for p in (5, 10, 20, 25, 50, 75, 90):
        print(f"  p{p:<3} {pct(p):.4f}")
    print(f"\n  HEDGE_ARCHETYPE_GAP = {sig.HEDGE_ARCHETYPE_GAP} -> hedges {hedge_rate:.1%} "
          f"of labelled readers")
    if not 0.15 <= hedge_rate <= 0.30:
        warn("HEDGE_RATE_DRIFTED",
             f"the hedge fires on {hedge_rate:.1%} of labelled readers; it is "
             "documented as ~22% (roughly the closest fifth). Below ~15% close calls "
             "are asserted flatly, above ~30% the hedge is the default state. "
             "Re-pin it to the percentile with `python -m scripts.dna_bias_probe`",
             rate=hedge_rate, threshold=sig.HEDGE_ARCHETYPE_GAP)

    # Ties and rounded-rank disagreement are properties of the scorer, not of the
    # threshold, so these use this file's shelves — a second, independent model is
    # a feature here rather than an inconsistency.
    rng = random.Random(seed + 1)
    ties = runner_mismatch = labelled = 0
    for _ in range(n_readers):
        vec = sig.frequency_vector(_shelf(rng), weighted=True, now=NOW)
        best, scores, margin = sig.score_archetype(vec)
        if best is None:
            continue
        labelled += 1
        if margin == 0.0:
            ties += 1
        # build_dna picks `runner_up` by re-sorting the ROUNDED scores dict, while
        # score_archetype ranks the exact floats. When two rounded values collide
        # the card can name a runner-up that is not really second.
        if sorted(scores, key=scores.get, reverse=True)[0] != best:
            runner_mismatch += 1

    print(f"\n  exact ties (winner from declaration order): {ties}/{labelled} = "
          f"{ties / labelled:.2%}")
    if ties / labelled > 0.01:
        err("TIE_RATE_HIGH",
            f"{ties / labelled:.2%} of labelled readers are exact ties resolved by "
            "PERSONALITY_TYPES order",
            rate=ties / labelled)

    print(f"  rounded-vs-exact leader disagreements: {runner_mismatch}/{labelled}")
    if runner_mismatch:
        warn("ROUNDED_RANK_DISAGREES",
             f"on {runner_mismatch} readers the rounded `archetype_scores` dict ranks "
             "differently from the exact floats score_archetype ranked. build_dna "
             "picks `runner_up` from the rounded dict, so the card can name a "
             "runner-up that is not actually second",
             count=runner_mismatch)


# ── 8. sample dossiers ──────────────────────────────────────────────────────

SAMPLES: dict[str, list[tuple[list[str], float]]] = {
    "a horror reader": [(["dread", "revulsion", "absorption"], d) for d in (5, 20, 40, 70, 110, 150)]
                       + [(["dread", "rage", "awe"], d) for d in (200, 260)],
    "a cosy reader": [(["comfort", "tenderness", "joy"], d) for d in (10, 25, 45, 80, 120, 170)],
    "a grief reader": [(["grief", "devastation", "catharsis"], d) for d in (8, 30, 60, 95, 140)]
                      + [(["recognition", "grief", "catharsis", "nostalgia"], 180)],
    "a page-turner reader": [(["absorption", "awe", "joy"], d) for d in (5, 15, 35, 60, 90, 130)],
    "a reader who tags only what bored them": [(["boredom"], 10), (["confusion"], 30),
                                               (["indifference"], 50), (["boredom"], 70),
                                               (["confusion"], 90)],
}


def section_dossiers() -> None:
    head(8, "SAMPLE DOSSIERS — the whole output for a handful of legible readers")
    print("\n  Read these as sentences. The question is not whether the number is")
    print("  computable, it is whether the label is a true thing to say to this person.")

    for label, books in SAMPLES.items():
        shelf = [_sig(tags, days) for tags, days in books]
        out = build_dna(shelf, ["escape"], insight_limit=6)
        sub(label)
        if not out["enough"]:
            print(f"  GATED: {out['message']}")
            continue

        arc = out["archetype"]
        print(f"  label      : {arc['name'] if arc else 'NONE (abstained)'}")
        print(f"  margin     : {out['margin']:.4f}"
              f"{'   HEDGED, runner-up: ' + out['runner_up'] if out['runner_up'] else ''}")
        print(f"  books      : {out['book_count']} ({out['tagged_count']} tagged)   "
              f"drift {out['drift']:.3f}")

        vec = out["profiles"]["current"]
        tops = [(s, v) for s, v in sorted(vec.items(), key=lambda kv: -kv[1]) if v > 0][:5]
        print("  vector     : " + "  ".join(f"{s} {v:.1%}" for s, v in tops))

        print("  scores     :")
        for tid, score in sorted(out["archetype_scores"].items(), key=lambda kv: -kv[1])[:4]:
            mark = " <-- label" if arc and tid == arc["id"] else ""
            print(f"                {tid:26} {score:+.4f}{mark}")

        if out["basis"]:
            print(f"  basis      : {out['basis']}")

        # Does the label's own anchor actually appear in this reader's top tags?
        if arc:
            prim = set(BY_ID[arc["id"]]["primary_emotions"])
            hit = prim & {s for s, _ in tops[:3]}
            if not hit:
                warn("DOSSIER_LABEL_MISMATCH",
                     f"{label}: labelled {arc['name']} (anchored on {sorted(prim)}) but "
                     f"their top tags are {[s for s, _ in tops[:3]]}",
                     reader=label, type=arc["id"], top=[s for s, _ in tops[:3]])


# ── report ──────────────────────────────────────────────────────────────────

SECTIONS = {
    1: ("vocabulary", lambda a: section_vocabulary()),
    2: ("archetypes", lambda a: section_archetypes()),
    3: ("emotion-map", lambda a: section_emotion_map()),
    4: ("reachability", lambda a: section_reachability()),
    5: ("population", lambda a: section_population(a.readers, a.seed)),
    6: ("gate", lambda a: section_gate()),
    7: ("thresholds", lambda a: section_thresholds(a.readers, a.seed)),
    8: ("dossiers", lambda a: section_dossiers()),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--readers", type=int, default=6000,
                    help="simulated readers per population pass (default 6000)")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--strict", action="store_true", help="exit 1 on WARN as well as ERROR")
    ap.add_argument("--json", action="store_true", help="emit findings as JSON on stdout")
    ap.add_argument("--only", default="", help="comma-separated section numbers, e.g. 3,5")
    args = ap.parse_args()

    wanted = ([int(x) for x in args.only.split(",")] if args.only else list(SECTIONS))

    if args.json:
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for n in wanted:
                SECTIONS[n][1](args)
    else:
        for n in wanted:
            SECTIONS[n][1](args)

    errors = [f for f in FINDINGS if f.severity == "ERROR"]
    warns = [f for f in FINDINGS if f.severity == "WARN"]
    infos = [f for f in FINDINGS if f.severity == "INFO"]

    if args.json:
        print(json.dumps({
            "errors": len(errors), "warnings": len(warns), "info": len(infos),
            "findings": [{"severity": f.severity, "code": f.code,
                          "message": f.message, "detail": f.detail} for f in FINDINGS],
        }, indent=2, default=str))
    else:
        head(9, "FINDINGS")
        for group, items in (("ERROR", errors), ("WARN", warns), ("INFO", infos)):
            if not items:
                continue
            sub(f"{group} ({len(items)})")
            for f in items:
                print(f"  [{f.code}] {f.message}")
        if not FINDINGS:
            print("\n  Nothing flagged. That is a claim about the checks written here,")
            print("  not a guarantee about the engine — read the numbers above too.")
        print(f"\n  {len(errors)} error(s), {len(warns)} warning(s), {len(infos)} info.")
        if warns and not errors:
            print("  Warnings are deviations, not breakage. Each one should be a")
            print("  decision you have made on purpose, not a surprise.")

    if errors:
        return 1
    if warns and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
