"""Insight templates — hand-written sentences, hard data in the slots (Phase 7, B7.7).

NOT LLM-generated. Generated prose about someone's emotional life is
nondeterministic, occasionally hallucinatory, and reads as *more* artificial, not
less. The voice is Shruti's; the facts are the reader's. That's what makes it feel
like a reader wrote it — because one did.

Every template obeys the falsifiability rule: could this sentence be true of a
different reader? If yes, it doesn't ship. Each fills its slots from the reader's
own numbers. Ranking is by internal `surprise` magnitude — NOT population-relative
(rarity is deferred; a rarity claim would need a population baseline we don't have).
"""

from dataclasses import dataclass
from typing import Callable

from app.services import dna_signals as sig
from app.services.dna_signals import GATES, MIN_BOOKS_FOR_DNA
from app.utils.emotions import EMOTIONS_BY_SLUG

# Human-readable "what unlocks this" strings for the locked list (B7.6).
UNLOCK_REASONS: dict[str, str] = {
    "intensity_signature": "needs 8 books to read your rating style",
    "range": "needs 8 books to measure how wide you reach",
    "blind_spot": "needs 10 books before a gap means anything",
    "contradiction": "needs 10 books — and telling me what you read for",
    "abandonment": "needs 10 books, a few of them unfinished",
    "pairing": "needs 15 books to see which feelings travel together",
    "drift": "needs 15 books and two snapshots to see movement",
    "seasonality": "needs 25 books across a full year",
}


def _name(slug: str | None) -> str:
    if not slug:
        return ""
    meta = EMOTIONS_BY_SLUG.get(slug)
    return meta["name"] if meta else slug.title()


@dataclass
class InsightTemplate:
    category: str
    variant: str
    min_n: int
    signed_off: bool                       # a human (Shruti) approved this sentence
    applicable: Callable[[dict], bool]     # is the data present to fill it?
    render: Callable[[dict], str]
    surprise: Callable[[dict], float]      # ranking magnitude, 0..1 (not population)


# ── 1. Contradiction — the gold (gate 10, needs reads_for) ──
def _contra_ok(c):
    s = c.get("stated")
    return bool(s and s.get("delta") is not None and s.get("delta") > 0.5
                and s.get("revealed_hi") and s["revealed_hi"] != s["stated"])

CONTRADICTION = [
    InsightTemplate(
        "contradiction", "intensity_gap", GATES["contradiction"], True, _contra_ok,
        lambda c: (f"You said you read for {_name(c['stated']['stated'])}. "
                   f"The books you rate highest are the ones that gut you — "
                   f"you give them {c['stated']['delta']} more points."),
        lambda c: min(1.0, c["stated"]["delta"] / 4.0),
    ),
    InsightTemplate(
        "contradiction", "center_of_gravity", GATES["contradiction"], True,
        lambda c: bool(c.get("stated") and c["stated"].get("revealed_top")
                       and c["stated"]["revealed_top"] != c["stated"]["stated"]),
        lambda c: (f"You told me {_name(c['stated']['stated'])}. "
                   f"Your shelf's center of gravity is {_name(c['stated']['revealed_top'])}."),
        lambda c: 0.7,
    ),
]

# ── 2. Blind spot (gate 10) ──
BLIND_SPOT = [
    InsightTemplate(
        "blind_spot", "never", GATES["blind_spot"], True,
        lambda c: bool(c.get("blind_spots")),
        lambda c: (f"You've logged {c['book_count']} books. "
                   f"You have never once reached for {_name(c['blind_spots'][0])}."),
        lambda c: 0.9,
    ),
    InsightTemplate(
        "blind_spot", "rare", GATES["blind_spot"], True,
        lambda c: bool(c.get("rare")),
        lambda c: (f"{_name(c['rare'][0][0])} shows up in under "
                   f"{max(1, round(c['rare'][0][1] * 100))}% of what you read. "
                   f"Not never. Close."),
        lambda c: 0.6,
    ),
]

# ── 3. Drift (gate 15 + two snapshots) ──
DRIFT = [
    InsightTemplate(
        "drift", "gave_way", GATES["drift"], True,
        lambda c: bool(c.get("has_two_snapshots") and c.get("drift", 0) >= 0.15
                       and c.get("old_top") and c.get("new_top") and c["old_top"] != c["new_top"]),
        lambda c: (f"Your reading moved. {_name(c['old_top'])} gave way to "
                   f"{_name(c['new_top'])} across your recent books."),
        lambda c: min(1.0, c.get("drift", 0) * 2),
    ),
]

# ── 4. Intensity signature (gate 8) ──
INTENSITY = [
    InsightTemplate(
        "intensity_signature", "eight_or_nothing", GATES["intensity_signature"], True,
        lambda c: c.get("intensity_signature", {}).get("share_high", 0) >= 0.5,
        lambda c: (f"You don't have mild opinions. "
                   f"{round(c['intensity_signature']['share_high'] * 100)}% of your books "
                   f"land at 8 or above."),
        lambda c: c["intensity_signature"]["share_high"],
    ),
    InsightTemplate(
        "intensity_signature", "careful", GATES["intensity_signature"], True,
        lambda c: (c.get("intensity_signature", {}).get("share_high", 1) < 0.2
                   and c.get("intensity_signature", {}).get("variance", 99) < 2.0),
        lambda c: (f"You're a careful rater. Most of your books sit at "
                   f"{c['intensity_signature']['band_lo']}–{c['intensity_signature']['band_hi']}; "
                   f"you save the top of the scale."),
        lambda c: 0.5,
    ),
]

# ── 5. Pairing (gate 15) ──
def _pair_ok(c):
    top = c.get("top_pair")
    return bool(top and top[1] >= 3)

PAIRING = [
    InsightTemplate(
        "pairing", "side_by_side", GATES["pairing"], True, _pair_ok,
        lambda c: (f"{_name(c['top_pair'][0][0])} and {_name(c['top_pair'][0][1])} arrive "
                   f"together for you — tagged side by side in {c['top_pair'][1]} of your books."),
        lambda c: min(1.0, c["top_pair"][1] / 10.0),
    ),
]

# ── 6. Abandonment (gate 10, ≥3 unfinished) ──
ABANDONMENT = [
    InsightTemplate(
        "abandonment", "dnf_emotion", GATES["abandonment"], True,
        lambda c: bool(c.get("abandonment")),
        lambda c: (f"The books you don't finish are the ones you tag "
                   f"{_name(c['abandonment']['emotion'])}."),
        lambda c: c["abandonment"]["fraction"],
    ),
]

# ── 7. Range (gate 8; narrowing variant needs a prior snapshot) ──
RANGE = [
    InsightTemplate(
        "range", "narrowing", GATES["range"], True,
        lambda c: (c.get("range_prev_distinct") is not None
                   and c["range"]["distinct"] < c["range_prev_distinct"]),
        lambda c: (f"Your range narrowed — you used to reach for "
                   f"{c['range_prev_distinct']} feelings; lately {c['range']['distinct']}."),
        lambda c: 0.75,
    ),
    InsightTemplate(
        "range", "breadth", GATES["range"], True,
        lambda c: bool(c.get("range")),
        lambda c: (f"You reach across {c['range']['distinct']} of {len(EMOTIONS_BY_SLUG)} feelings."
                   + (" That's a wide emotional range." if c["range"]["entropy"] >= 0.7
                      else " You stay in a tight band.")),
        lambda c: abs(c["range"]["entropy"] - 0.5),
    ),
]

# ── 8. Arc (gate 5, arc data) ──
ARC = [
    InsightTemplate(
        "arc", "start_to_end", GATES["arc"], True,
        lambda c: bool(c.get("arc")),
        lambda c: (f"You start in {_name(c['arc']['start'])} and end in "
                   f"{_name(c['arc']['end'])} — {round(c['arc']['fraction'] * 100)}% "
                   f"of the books you finish."),
        lambda c: c["arc"]["fraction"],
    ),
]

# Registry, ordered by category power (B7.7). Seasonality is intentionally absent
# from the *renderable* registry — it is only ever a locked entry this pass.
REGISTRY: list[InsightTemplate] = (
    CONTRADICTION + BLIND_SPOT + DRIFT + INTENSITY + PAIRING + ABANDONMENT + RANGE + ARC
)

# Category order for stable ranking ties + the locked list.
CATEGORY_ORDER = [
    "contradiction", "blind_spot", "drift", "intensity_signature",
    "pairing", "abandonment", "range", "arc", "seasonality",
]


def generate_insights(ctx: dict, *, limit: int = 4) -> tuple[list[dict], list[dict]]:
    """From a computed signal context, return (unlocked_insights, locked).

    - An insight is emitted only if book_count ≥ its gate AND its data is present.
    - At most one variant per category (rotates by book_count so return visits vary).
    - Ranked by surprise; the strongest `limit` are returned — never a dump.
    - Locked: every category whose gate the reader hasn't reached, with an honest
      reason (B7.6). This is the curiosity gap, at zero integrity cost.
    """
    book_count = ctx["book_count"]

    # Group applicable, signed-off, gated candidates by category.
    by_cat: dict[str, list[InsightTemplate]] = {}
    for t in REGISTRY:
        if not t.signed_off or book_count < t.min_n or not t.applicable(ctx):
            continue
        by_cat.setdefault(t.category, []).append(t)

    chosen: list[dict] = []
    for cat, variants in by_cat.items():
        t = variants[book_count % len(variants)]   # deterministic rotation
        chosen.append({
            "category": cat,
            "variant": t.variant,
            "text": t.render(ctx),
            "n": book_count,
            "surprise": round(float(t.surprise(ctx)), 3),
        })

    chosen.sort(key=lambda i: (-i["surprise"], CATEGORY_ORDER.index(i["category"])))
    unlocked = chosen[:limit]

    locked: list[dict] = []
    shown = {i["category"] for i in chosen}
    for cat in CATEGORY_ORDER:
        gate = GATES.get(cat)
        if gate is None:
            continue
        if book_count < gate and cat not in shown:
            locked.append({
                "category": cat,
                "unlocks_at": f"{gate} books" if cat != "seasonality" else "25 books + 12 months",
                "reason": UNLOCK_REASONS.get(cat, f"needs {gate} books"),
            })
    # Seasonality is always locked this pass, even past 25 books (needs the 12 months).
    if "seasonality" not in {l["category"] for l in locked}:
        locked.append({"category": "seasonality", "unlocks_at": "25 books + 12 months",
                       "reason": UNLOCK_REASONS["seasonality"]})

    return unlocked, locked


def _top_slug(vec: dict[str, float]) -> str | None:
    ranked = sorted(vec.items(), key=lambda kv: kv[1], reverse=True)
    return ranked[0][0] if ranked and ranked[0][1] > 0 else None


def build_dna(
    sigs: list["sig.EntrySig"],
    reads_for: list[str] | None = None,
    *,
    journal_sigs: list["sig.EntrySig"] | None = None,
    prev_snapshot: dict | None = None,
    snapshot_count: int = 0,
    insight_limit: int = 4,
) -> dict:
    """The one entry point: turn a reader's entries into the DNA payload (B7.5/B7.6).

    Below 5 books it returns an honest "not enough yet" — not a failure state, but
    anticipation. Above it: recency-weighted profiles, a demoted archetype, the
    strongest few falsifiable insights, and the honestly-locked rest.

    ``sigs`` is books. ``journal_sigs`` is named journal days, and it feeds exactly
    the signals that are about *who you are* — the emotion vectors, their drift,
    the archetype, and the never-named blind spots — so DNA spans reading and life
    (VISION §6). It deliberately does NOT feed anything that makes a claim about
    books: ``book_count``, rating style, abandonment, arcs, pairing, and
    stated-vs-revealed all stay book-only, because "you've logged 12 books" and
    "the books you rate highest" have to remain true sentences.
    """
    book_count = len(sigs)
    # Every signal below reads one of these two lists, and which one it reads is
    # the whole editorial decision above.
    vector_sigs = sigs + (journal_sigs or [])
    journal_count = len(journal_sigs or [])
    # The gate counts books that carry a feeling, not books. Five untagged imports
    # are five titles we know nothing about — computing a profile from them would
    # be reading tea leaves in an empty cup.
    tagged = [s for s in sigs if s.emotions]
    if len(tagged) < MIN_BOOKS_FOR_DNA:
        return {
            "enough": False,
            "book_count": book_count,
            "tagged_count": len(tagged),
            "needed": MIN_BOOKS_FOR_DNA,
            "message": f"{len(tagged)} books with a feeling logged. "
                       f"At {MIN_BOOKS_FOR_DNA}, the mirror starts to see you.",
            # Present on both branches so the client never has to check `enough`
            # before reading it.
            "snapshot_count": snapshot_count,
            "has_two_snapshots": snapshot_count >= 2,
            "journal_entry_count": journal_count,
        }

    enduring = sig.frequency_vector(vector_sigs, weighted=False)
    current = sig.frequency_vector(vector_sigs, weighted=True)
    drift_val = sig.drift(enduring, current)

    # Book-share (distinct books tagged / total) — for the "rare" blind-spot variant.
    book_share: dict[str, float] = {}
    for slug in sig._ALL_SLUGS:
        n = sum(1 for s in sigs if slug in s.emotions)
        book_share[slug] = n / book_count
    rare = sorted(((s, v) for s, v in book_share.items() if 0 < v < 0.05), key=lambda kv: kv[1])

    pairs = sig.co_occurrence(sigs)
    top_pair = pairs.most_common(1)[0] if pairs else None

    prev_current = (prev_snapshot or {}).get("current_vector")
    prev_enduring = (prev_snapshot or {}).get("enduring_vector")
    range_prev_distinct = (
        sum(1 for v in prev_enduring.values() if v > 0) if prev_enduring else None
    )

    ctx = {
        "book_count": book_count,
        "intensity_signature": sig.intensity_signature(sigs),
        "range": sig.range_entropy(sigs),
        "range_prev_distinct": range_prev_distinct,
        # An emotion is only a blind spot if it's absent from the journal too —
        # "you have never named this" is a stronger and more honest claim when it
        # covers everywhere the reader names feelings, not just the shelf.
        "blind_spots": sig.blind_spots(vector_sigs),
        "rare": rare,
        "top_pair": top_pair,
        "stated": sig.stated_vs_revealed(sigs, reads_for),
        "abandonment": sig.abandonment(sigs),
        "arc": sig.arc_shape(sigs),
        "drift": drift_val,
        "has_two_snapshots": snapshot_count >= 2,
        "old_top": _top_slug(prev_current) if prev_current else None,
        "new_top": _top_slug(current),
    }

    unlocked, locked = generate_insights(ctx, limit=insight_limit)
    archetype_id, scores, margin = sig.score_archetype(current)

    return {
        "enough": True,
        "book_count": book_count,
        "tagged_count": len(tagged),
        "insights": unlocked,
        "locked": locked,
        # None is a legitimate answer here: the reader can be past the gate and
        # still have a tally that names nobody. The client must handle it.
        "archetype": sig.archetype_dict(archetype_id) if archetype_id else None,
        "archetype_scores": scores,
        "margin": margin,
        # When the leader barely clears the field, say so rather than pretending
        # the label was decisive.
        "runner_up": (
            sig.archetype_dict(sorted(scores, key=scores.get, reverse=True)[1])["name"]
            if archetype_id and margin < 0.10 else None
        ),
        # `current_books` is the recency-weighted vector over books ALONE. The
        # other two span the journal, and the journal is private: this is the only
        # vector a public surface is allowed to read (see card_payload).
        "profiles": {"enduring": enduring, "current": current,
                     "current_books": sig.frequency_vector(sigs, weighted=True)},
        "drift": drift_val,
        "reads_for": sig._canon_list(reads_for),
        # Already known here (it gates `has_two_snapshots` above), so returning it
        # costs nothing. Without it the DNA tab had to spend a whole extra
        # GET /dna/evolution purely to learn a list length.
        "snapshot_count": snapshot_count,
        "has_two_snapshots": snapshot_count >= 2,
        # How much of the profile above came from named days rather than books. The
        # client can say so; without it, a reader couldn't tell why their vectors
        # moved after a week of journalling.
        "journal_entry_count": journal_count,
    }

