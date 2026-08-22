"""Can the engine recover a personality it was never told about?

    python -m scripts.dna_recovery
    python -m scripts.dna_recovery --readers 400 --seed 11
    python -m scripts.dna_recovery --only A,D,E

WHY THIS EXISTS, AND WHY THE OTHER TWO SCRIPTS CANNOT ANSWER IT
----------------------------------------------------------------
`dna_bias_probe` and `dna_audit` both build simulated readers out of
`BOOK_BUNDLES` — that is, a reader IS a genre mix and nothing else. Every
question they can ask is therefore circular with respect to the one question that
decides whether this product is honest: **is the label about the reader, or about
what the reader happened to pick up?** A shelf of horror produces dread and
revulsion tags whether the person is a fear-seeker or was handed a Stephen King
at an airport, and no amount of fairness testing can tell those two apart.

This script separates the two variables. Every synthetic reader here has:

  * a LATENT TYPE — one of the ten archetypes, assigned up front. Ground truth.
    The engine is never shown it.
  * a GENRE DIET — which bundles they read, sampled EITHER correlated with the
    latent type (realistic: taste shapes what you pick up) OR independently of it
    (the hard case: the right person reading the wrong shelf).

Tagging is then a product of both: a reader can only tag what the book affords,
but within what it affords, their personality decides what they actually notice.
That is the real causal structure, and it is what makes the confound measurable
rather than assumed.

WHAT THIS PROVES AND WHAT IT DOES NOT
--------------------------------------
It proves IDENTIFIABILITY, not truth. If the engine cannot recover a personality
that was deliberately planted, that is decisive evidence of a defect — there is
no reading of that result where the engine is fine. If it can, that is necessary
but not sufficient: it means the engine is capable of reading a person when a
person is there to read, which is the strongest claim any simulation can make.

The generative model below is a hypothesis about how reading works, written by
hand. It is not data. Its parameters (`boost`, `damp`) are the honest weak point,
which is why section F sweeps them instead of picking one and hoping.

THE DISTINCTION THAT MATTERS MOST
----------------------------------
A Grief Romantic who only ever reads cosy books cannot be recovered by anything,
because their tags genuinely contain no grief — the information is not there to
find. That is a limit of the data, not a bug in the engine, and section B
separates the two by conditioning accuracy on how many of the reader's true
anchors their shelf ever gave them a chance to express.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from app.services import dna_signals as sig
from app.services.dna_engine import PERSONALITY_TYPES
from scripts.dna_bias_probe import BOOK_BUNDLES

IDS = [t["id"] for t in PERSONALITY_TYPES]
BY_ID = {t["id"]: t for t in PERSONALITY_TYPES}
SHORT = {tid: "".join(w[0] for w in tid.split("_")).upper() for tid in IDS}
NOW = datetime.now(timezone.utc)
# How much a reader's personality bends what they notice in a book that affords
# it. boost=3.0 means an emotion among your anchors is 3x as likely to get tagged
# as a neutral one; damp=3.0 means one of your anti-emotions is 3x less likely.
# These are the model's load-bearing guesses. Section F sweeps them.
BOOST = 3.0
DAMP = 3.0
P_BASE = 0.30            # chance of tagging a neutral afforded emotion
BOOKS_MIN, BOOKS_MAX = 12, 45

FINDINGS: list[tuple[str, str]] = []


def err(msg): FINDINGS.append(("ERROR", msg))
def warn(msg): FINDINGS.append(("WARN", msg))
def info(msg): FINDINGS.append(("INFO", msg))


def head(letter, title):
    print(f"\n\n{'=' * 78}\n{letter}. {title}\n{'=' * 78}")


def _sig(emotions, days):
    return sig.EntrySig(emotions=list(emotions), intensity=7,
                        ts=NOW - timedelta(days=days), status="finished")


# ── the generative model ────────────────────────────────────────────────────

def tag_book(rng, afforded, t, boost=BOOST, damp=DAMP, p_base=P_BASE):
    """What this reader notices in a book that affords these emotions."""
    prim, anti = set(t["primary_emotions"]), set(t["anti_emotions"])
    out = []
    for e in afforded:
        p = p_base
        if e in prim:
            p *= boost
        if e in anti:
            p /= damp
        if rng.random() < min(p, 0.95):
            out.append(e)
    return out


def diet_weights(rng, t, correlated):
    """How much of each bundle this reader reads."""
    keys = list(BOOK_BUNDLES)
    if not correlated:
        # Taste in genre entirely unrelated to who they are — the hard case.
        return keys, [rng.gammavariate(2, 1) for _ in keys]
    prim = set(t["primary_emotions"])
    # Taste shaped by personality: bundles that afford your anchors get picked
    # more. This is the realistic case, and it is also where the confound lives.
    return keys, [1.0 + 3.0 * len(prim & set(BOOK_BUNDLES[k])) + rng.gammavariate(1, 0.5)
                  for k in keys]


def make_reader(rng, t, correlated=True, boost=BOOST, damp=DAMP, fixed_bundle=None):
    """Returns (shelf, how many of t's anchors the shelf ever afforded)."""
    if fixed_bundle:
        keys, weights = [fixed_bundle], [1.0]
    else:
        keys, weights = diet_weights(rng, t, correlated)
    shelf, afforded_total = [], set()
    for _ in range(rng.randint(BOOKS_MIN, BOOKS_MAX)):
        bundle = rng.choices(keys, weights=weights)[0]
        afforded = BOOK_BUNDLES[bundle]
        afforded_total |= set(afforded)
        tags = tag_book(rng, afforded, t, boost, damp)
        if tags:
            shelf.append(_sig(tags, rng.uniform(0, 180)))
    chance = len(set(t["primary_emotions"]) & afforded_total)
    return shelf, chance


def label(shelf):
    if not shelf:
        return None, None, 0.0
    vec = sig.frequency_vector(shelf, weighted=True, now=NOW)
    best, scores, gap = sig.score_archetype(vec)
    runner = list(scores)[1] if best else None
    return best, runner, gap


# ── A. upper bound ──────────────────────────────────────────────────────────

def section_A(n, seed):
    head("A", "IDENTIFIABILITY CEILING — an unconstrained reader")
    print("\n  Every book affords every experiential emotion, so personality is the")
    print("  only thing shaping the tags. This is the best the engine could ever do.")
    print("  If it is not near-perfect, nothing further matters: the engine cannot")
    print("  read a personality even when the personality is all there is.\n")
    rng = random.Random(seed)
    allowed = sorted({e for t in PERSONALITY_TYPES for e in t["primary_emotions"]} |
                     {e for t in PERSONALITY_TYPES for e in t["anti_emotions"]})
    hits = top2 = total = 0
    per = Counter(); per_n = Counter()
    for tid in IDS:
        t = BY_ID[tid]
        for _ in range(n):
            shelf = []
            for _b in range(rng.randint(BOOKS_MIN, BOOKS_MAX)):
                tags = tag_book(rng, allowed, t)
                if tags:
                    shelf.append(_sig(tags, rng.uniform(0, 180)))
            best, runner, _ = label(shelf)
            total += 1; per_n[tid] += 1
            if best == tid:
                hits += 1; top2 += 1; per[tid] += 1
            elif runner == tid:
                top2 += 1
    acc = hits / total
    print(f"  exact recovery  {acc:.1%}   top-2 {top2 / total:.1%}   (chance = {1/len(IDS):.0%})")
    worst = sorted(((per[i] / per_n[i], i) for i in IDS))[:3]
    for a, i in worst:
        print(f"    weakest: {i:26} {a:.1%}")
    if acc < 0.85:
        err(f"identifiability ceiling is only {acc:.1%} — the engine cannot reliably "
            "recover a personality even when books impose no constraint at all. "
            "This is an engine defect, not a data limit.")
    else:
        info(f"ceiling {acc:.1%}: the scorer can read a planted personality.")
    return acc


# ── B. realistic recovery ───────────────────────────────────────────────────

def section_B(n, seed, correlated, title):
    head("B" if correlated else "B2", title)
    rng = random.Random(seed)
    by_chance = defaultdict(lambda: [0, 0])
    hits = top2 = total = abstain = 0
    confusion = defaultdict(Counter)
    for tid in IDS:
        t = BY_ID[tid]
        for _ in range(n):
            shelf, chance = make_reader(rng, t, correlated=correlated)
            best, runner, _ = label(shelf)
            total += 1
            by_chance[chance][1] += 1
            if best is None:
                abstain += 1
                continue
            confusion[tid][best] += 1
            if best == tid:
                hits += 1; top2 += 1; by_chance[chance][0] += 1
            elif runner == tid:
                top2 += 1
    print(f"\n  exact recovery  {hits / total:.1%}   top-2 {top2 / total:.1%}   "
          f"abstained {abstain / total:.1%}   (chance = {1/len(IDS):.0%})")
    print("\n  conditioned on how many of the reader's 3 true anchors their shelf")
    print("  ever afforded them a chance to express:")
    print(f"    {'anchors afforded':>18}{'readers':>10}{'recovery':>11}")
    for c in sorted(by_chance):
        got, tot = by_chance[c]
        note = "   <- unrecoverable in principle" if c == 0 else ""
        print(f"    {c:>18}{tot:>10}{got / tot:>10.1%}{note}")
    return hits / total, confusion


def print_confusion(confusion):
    head("C", "CONFUSION MATRIX — what gets mistaken for what")
    print("\n  Row = true latent type, column = what the engine said. Percentages of")
    print("  that row. A dense diagonal is the engine reading people; a dense column")
    print("  is one archetype swallowing everyone.\n")
    print(f"  {'true \\\\ said':26}" + "".join(f"{SHORT[i]:>6}" for i in IDS))
    col_total = Counter()
    for tid in IDS:
        row = confusion[tid]
        tot = sum(row.values()) or 1
        cells = ""
        for i in IDS:
            v = row[i] / tot
            col_total[i] += row[i]
            cells += f"{v * 100:>6.0f}" if v >= 0.005 else f"{'·':>6}"
        print(f"  {tid:26}{cells}")
    print(f"\n  {'(column share)':26}" +
          "".join(f"{col_total[i] / max(sum(col_total.values()), 1) * 100:>6.0f}" for i in IDS))
    print("\n  key: " + "  ".join(f"{SHORT[i]}={i}" for i in IDS[:5]))
    print("       " + "  ".join(f"{SHORT[i]}={i}" for i in IDS[5:]))
    for tid in IDS:
        row = confusion[tid]
        tot = sum(row.values()) or 1
        if row[tid] / tot < 0.25:
            top = row.most_common(1)[0]
            warn(f"{tid} is recovered only {row[tid]/tot:.0%} of the time; it is most "
                 f"often called {top[0]} ({top[1]/tot:.0%})")


# ── D. the confound, measured ───────────────────────────────────────────────

def section_D(n, seed):
    head("D", "GENRE CONFOUND — same person, different shelf")
    print("\n  The same latent type is generated twice, once with a personality-driven")
    print("  genre diet and once with a random one. If the label follows the shelf")
    print("  rather than the person, the two disagree. This is the confound, as a")
    print("  number rather than an argument.\n")
    rng = random.Random(seed)
    agree = total = both_named = 0
    per = {}
    for tid in IDS:
        t = BY_ID[tid]
        a_ok = 0; n_pairs = 0
        for _ in range(n):
            s1, _ = make_reader(rng, t, correlated=True)
            s2, _ = make_reader(rng, t, correlated=False)
            l1, _, _ = label(s1); l2, _, _ = label(s2)
            total += 1
            if l1 is None or l2 is None:
                continue
            both_named += 1; n_pairs += 1
            if l1 == l2:
                agree += 1; a_ok += 1
        per[tid] = a_ok / max(n_pairs, 1)
    rate = agree / max(both_named, 1)
    print(f"  label survives a change of genre diet: {rate:.1%} of readers")
    for tid, v in sorted(per.items(), key=lambda kv: kv[1])[:4]:
        print(f"    most shelf-dependent: {tid:26} {v:.1%}")
    if rate < 0.5:
        warn(f"only {rate:.1%} of readers keep their label when their genre diet "
             "changes — the label is substantially a description of the shelf, "
             "not the reader. This is the confound that book-level baselines exist "
             "to close.")
    else:
        info(f"{rate:.1%} label stability across genre diets.")
    return rate


# ── E. the decisive one ─────────────────────────────────────────────────────

def section_E(n, seed):
    head("E", "DISCRIMINATION — ten different people, one identical shelf")
    print("\n  Every reader here reads ONLY the same genre. Any difference in their")
    print("  tags is personality and nothing else. If the engine returns the same")
    print("  label for all ten latent types, it is reading the shelf. If it spreads")
    print("  them out, there is real personality signal surviving inside a fixed")
    print("  genre — which is exactly what the product claims.\n")
    rng = random.Random(seed)
    print("  `reachable` is how many archetypes have at least one anchor inside that")
    print("  bundle at all. It is the ceiling the data allows: a cosy shelf affords")
    print("  comfort, tenderness and joy and nothing else, so ten different people")
    print("  reading only cosy books genuinely have almost nothing to differ on. Read")
    print("  `distinct` against `reachable`, not against 10.\n")
    print(f"  {'bundle':22}{'affords':>9}{'reachable':>11}{'distinct':>10}"
          f"{'recovery':>10}{'top share':>11}")
    spreads = []
    for bundle in BOOK_BUNDLES:
        got = Counter(); hits = 0; tot = 0
        for tid in IDS:
            t = BY_ID[tid]
            for _ in range(n):
                shelf, _c = make_reader(rng, t, fixed_bundle=bundle)
                best, _r, _g = label(shelf)
                if best is None:
                    continue
                tot += 1; got[best] += 1
                if best == tid:
                    hits += 1
        if not tot:
            continue
        top_share = got.most_common(1)[0][1] / tot
        afford = set(BOOK_BUNDLES[bundle])
        reachable = sum(1 for t in PERSONALITY_TYPES
                        if afford & set(t["primary_emotions"]))
        spreads.append((len(got), top_share, reachable))
        print(f"  {bundle:22}{len(afford):>9}{reachable:>11}{len(got):>10}"
              f"{hits / tot:>9.1%}{top_share:>10.0%}")
    avg_distinct = sum(s[0] for s in spreads) / len(spreads)
    avg_top = sum(s[1] for s in spreads) / len(spreads)
    avg_reach = sum(s[2] for s in spreads) / len(spreads)
    fill = sum(s[0] for s in spreads) / max(sum(s[2] for s in spreads), 1)
    print(f"\n  average distinct labels per fixed genre: {avg_distinct:.1f}")
    print(f"  average reachable given what the shelf affords: {avg_reach:.1f}")
    print(f"  the engine uses {fill:.0%} of the room the data actually leaves it")
    print(f"  average share taken by the single most common label: {avg_top:.0%}")
    if fill < 0.5:
        warn(f"the engine produces only {fill:.0%} of the distinct labels the shelves "
             "could support — personality signal is being lost that the data contains")
    else:
        info(f"the engine uses {fill:.0%} of the discrimination the data allows within "
             "a fixed genre; the rest of the collapse is an information limit, not "
             "a scoring one")
    if avg_distinct < 2.0:
        err(f"within a fixed genre the engine produces {avg_distinct:.1f} distinct "
            "labels on average — it is reading the shelf, not the reader. The "
            "archetype is a genre detector wearing a personality's name.")
    elif avg_top > 0.6:
        warn(f"within a fixed genre one label takes {avg_top:.0%} of all readers — "
             "personality signal survives but genre dominates it.")
    else:
        info(f"within a fixed genre the engine still separates readers "
             f"({avg_distinct:.1f} distinct labels, top label {avg_top:.0%}).")
    return avg_distinct, avg_top


# ── F. how strong must a personality be? ────────────────────────────────────

def section_F(n, seed):
    head("F", "SIGNAL STRENGTH — how loud must a personality be to be heard?")
    print("\n  `boost` is how much more likely you are to notice an emotion that is one")
    print("  of your anchors. 1.0 means personality does nothing and tagging is pure")
    print("  genre; the recovery rate there is the engine's false-signal floor and")
    print("  should sit at chance. The number that matters is where the curve leaves")
    print("  chance — that is the weakest real person this engine can see.\n")
    print(f"  {'boost':>7}{'damp':>7}{'recovery':>11}{'vs chance':>12}")
    chance = 1 / len(IDS)
    for boost, damp in ((1.0, 1.0), (1.5, 1.5), (2.0, 2.0), (3.0, 3.0), (5.0, 5.0), (8.0, 8.0)):
        rng = random.Random(seed)
        hits = tot = 0
        for tid in IDS:
            t = BY_ID[tid]
            for _ in range(n):
                shelf, _c = make_reader(rng, t, correlated=False, boost=boost, damp=damp)
                best, _r, _g = label(shelf)
                if best is None:
                    continue
                tot += 1
                if best == tid:
                    hits += 1
        acc = hits / max(tot, 1)
        flag = ""
        if boost == 1.0:
            flag = "   <- no personality present"
            if acc > chance * 1.6:
                err(f"with personality switched off entirely the engine still 'recovers' "
                    f"the latent type {acc:.1%} of the time against {chance:.0%} chance — "
                    "it is finding structure that was never planted")
        print(f"  {boost:>7.1f}{damp:>7.1f}{acc:>10.1%}{acc / chance:>11.2f}x{flag}")


SECTIONS = "ABCDEF"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--readers", type=int, default=200,
                    help="synthetic readers PER latent type (default 200 = 2000 total)")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--only", default="", help="comma-separated section letters, e.g. A,E")
    args = ap.parse_args()
    want = [s.strip().upper() for s in args.only.split(",")] if args.only else list(SECTIONS)

    confusion = None
    if "A" in want:
        section_A(args.readers, args.seed)
    if "B" in want:
        _, confusion = section_B(args.readers, args.seed, True,
                                 "RECOVERY — realistic reader, taste-driven genre diet")
        section_B(args.readers, args.seed + 1, False,
                  "RECOVERY — the hard case, genre diet unrelated to personality")
    if "C" in want:
        if confusion is None:
            _, confusion = section_B(args.readers, args.seed, True,
                                     "RECOVERY (for the confusion matrix)")
        print_confusion(confusion)
    if "D" in want:
        section_D(args.readers, args.seed + 2)
    if "E" in want:
        section_E(max(args.readers // 4, 25), args.seed + 3)
    if "F" in want:
        section_F(max(args.readers // 2, 50), args.seed + 4)

    head("V", "VERDICT")
    errors = [m for s, m in FINDINGS if s == "ERROR"]
    warns = [m for s, m in FINDINGS if s == "WARN"]
    for s, m in FINDINGS:
        print(f"\n  [{s}] {m}")
    print(f"\n  {len(errors)} error(s), {len(warns)} warning(s).")
    print("\n  Remember what this can and cannot say. A pass here means the engine is")
    print("  CAPABLE of reading a person — it does not mean the labels it gives real")
    print("  readers are true, because the reader model above is a hypothesis, not")
    print("  data. A failure here, though, is decisive.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
