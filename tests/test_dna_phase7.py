"""Phase 7 DNA engine — the anti-horoscope guarantees (pure, no DB).

These tests encode the philosophy: no insight below its gate, every template
falsifiable and fillable, and DNA that actually MOVES when the reader changes.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import dna_signals as S
from app.services.dna_insights import REGISTRY, build_dna, generate_insights
from app.services.dna_signals import GATES, MIN_BOOKS_FOR_DNA

NOW = datetime.now(timezone.utc)


def sig(emotions, intensity=5, days=0, status="finished", arc_start=None, arc_end=None):
    return S.EntrySig(emotions=list(emotions), intensity=intensity,
                      ts=NOW - timedelta(days=days), status=status,
                      arc_start=arc_start, arc_end=arc_end)


def categories(sigs, reads_for=None):
    """All insight categories build_dna would emit (uncapped)."""
    res = build_dna(sigs, reads_for, insight_limit=99)
    if not res.get("enough"):
        return set()
    return {i["category"] for i in res["insights"]}


# ── Below the floor: honest "not enough yet" (B7.6, DoD) ──

def test_below_five_books_returns_not_enough():
    res = build_dna([sig(["comfort"]) for _ in range(4)])
    assert res["enough"] is False
    assert res["book_count"] == 4
    assert res["needed"] == MIN_BOOKS_FOR_DNA
    assert "insights" not in res and "archetype" not in res


# ── The anti-horoscope test: nothing emitted below its gate ──

# (category, builder producing N books that DO exhibit the signal, reads_for)
def _blind(n):        return [sig(["comfort"]) for _ in range(n)]                       # 12 never-tagged
def _intensity(n):    return [sig(["comfort"], intensity=9) for _ in range(n)]          # share_high=1
def _range(n):        return [sig(["comfort"]) for _ in range(n)]
def _pairing(n):      return [sig(["comfort", "dread"]) for _ in range(n)]              # co-occur
def _contra(n):       return [sig(["devastation"], intensity=9) for _ in range(n)]      # vs stated comfort
def _abandon(n):      return ([sig(["comfort"]) for _ in range(n - 3)]
                              + [sig(["dread"], status="reading") for _ in range(3)])   # 3 unfinished dread
def _arc(n):          return [sig(["grief"], arc_start="dread", arc_end="catharsis") for _ in range(n)]

CASES = [
    ("blind_spot", _blind, None),
    ("intensity_signature", _intensity, None),
    ("range", _range, None),
    ("pairing", _pairing, None),
    ("contradiction", _contra, ["comfort"]),
    ("abandonment", _abandon, None),
    ("arc", _arc, None),
]


@pytest.mark.parametrize("category,builder,reads_for", CASES)
def test_no_insight_below_its_gate(category, builder, reads_for):
    gate = GATES[category]
    # One book under the gate: the category must NOT appear, even though the data
    # would support it. This is the whole defence against confident nonsense.
    below = builder(gate - 1)
    if len(below) >= MIN_BOOKS_FOR_DNA:
        assert category not in categories(below, reads_for)
    # At the gate: it's allowed to appear (data is present by construction).
    assert category in categories(builder(gate), reads_for)


# ── Every template is signed off and its slots are fillable (B7.7) ──

def test_every_template_is_signed_off():
    for t in REGISTRY:
        assert t.signed_off, f"{t.category}/{t.variant} not signed off"


def test_every_applicable_template_renders_without_error():
    """Every template's slots must fill to a real string. Some variants are mutually
    exclusive (e.g. 8-or-nothing vs careful rater), so we try each template against
    both an 'intense' and a 'careful' context and require at least one to apply."""
    base = {
        # Gates and "based on N books" read tagged_count; book_count is only for
        # copy about the shelf itself. Every book in this fixture carries a tag.
        "book_count": 40,
        "tagged_count": 40,
        "range": {"entropy": 0.4, "distinct": 4},
        "range_prev_distinct": 9,
        "blind_spots": ["tenderness"],
        "rare": [("amusement", 0.03)],
        "top_pair": (("comfort", "dread"), 12),
        "stated": {"stated": "comfort", "revealed_top": "devastation",
                   "revealed_hi": "devastation", "delta": 2.3,
                   "verdict": "contradicted", "reason": None,
                   # Frequency claims compare COUNTS, not the rank — a tie in
                   # `revealed_top` is not "more often".
                   "stated_books": 12, "revealed_top_books": 20,
                   "evidence": {"stated": {"emotion": "comfort", "books": 12, "avg": 6.1},
                                "compared": {"emotion": "devastation", "books": 9, "avg": 8.4}}},
        "abandonment": {"emotion": "amusement", "fraction": 0.8},
        "arc": {"start": "dread", "end": "catharsis", "fraction": 0.7, "n_arc": 20},
        "drift": 0.4, "has_two_snapshots": True,
        "old_top": "comfort", "new_top": "grief",
    }
    ctx_intense = {**base, "intensity_signature": {"mean": 8.5, "variance": 0.5, "skew": 0.0,
                   "share_high": 0.8, "band_lo": 8, "band_hi": 9}}
    ctx_careful = {**base, "intensity_signature": {"mean": 6.5, "variance": 1.0, "skew": 0.0,
                   "share_high": 0.1, "band_lo": 6, "band_hi": 7}}

    # The stated-vs-revealed verdicts are mutually exclusive by construction, so
    # the confirmation templates need their own contexts to be reachable at all.
    confirmed = {**base["stated"], "verdict": "confirmed", "delta": -2.3,
                 "revealed_hi": "devastation",
                 "evidence": {"stated": {"emotion": "comfort", "books": 12, "avg": 8.4},
                              "compared": {"emotion": "devastation", "books": 9, "avg": 6.1}}}
    ctx_confirmed = {**ctx_intense, "stated": {**confirmed, "revealed_top": "comfort"}}
    ctx_confirmed_elsewhere = {**ctx_intense, "stated": {**confirmed, "revealed_top": "devastation"}}

    candidates = [ctx_intense, ctx_careful, ctx_confirmed, ctx_confirmed_elsewhere]
    for t in REGISTRY:
        ctx = next((c for c in candidates if t.applicable(c)), None)
        assert ctx is not None, f"{t.category}/{t.variant} applicable to no crafted ctx"
        text = t.render(ctx)
        assert isinstance(text, str) and len(text) > 10
        assert "{" not in text and "}" not in text  # no unfilled slots
        assert 0.0 <= float(t.surprise(ctx)) <= 1.0


# ── DNA moves when the reader reads (B7.3, DoD) ──

def test_dna_moves_when_recent_books_flip():
    # A comfort reader whose comfort reading is now ~a year in the past…
    sigs = [sig(["comfort", "tenderness"], intensity=6, days=330 + i * 10) for i in range(15)]
    settled = build_dna(sigs)
    assert settled["archetype"]["id"] == "comfort_architect"
    # …then three recent devastating books. Recency weighting lets the fresh reading
    # dominate once the old has decayed — the headline moves (DoD).
    sigs += [sig(["devastation", "grief"], intensity=9, days=i) for i in range(3)]
    moved = build_dna(sigs)

    assert moved["drift"] > 0.1                       # the profile genuinely moved
    assert moved["archetype"]["id"] != settled["archetype"]["id"]  # headline changed


def test_uniform_reader_shows_no_drift():
    sigs = [sig(["comfort"], days=i * 10) for i in range(20)]
    res = build_dna(sigs)
    assert res["drift"] < 0.05


# ── stated_vs_revealed only fires with a stated preference ──

def test_contradiction_requires_reads_for():
    sigs = [sig(["devastation"], intensity=9) for _ in range(12)]
    assert "contradiction" not in categories(sigs, reads_for=None)
    assert "contradiction" in categories(sigs, reads_for=["comfort"])


# ── Locked list is honest and always includes seasonality ──

def test_locked_list_names_what_unlocks_and_includes_seasonality():
    _, locked = generate_insights({"book_count": 6, "tagged_count": 6, "blind_spots": [], "range": {"distinct": 3, "entropy": 0.3},
                                   "intensity_signature": {"share_high": 0.1, "variance": 3.0},
                                   "stated": None, "abandonment": None, "arc": None, "top_pair": None,
                                   "rare": [], "drift": 0.0, "has_two_snapshots": False,
                                   "old_top": None, "new_top": None, "range_prev_distinct": None})
    cats = {l["category"] for l in locked}
    assert "seasonality" in cats
    assert "pairing" in cats  # gate 15, not reached at 6 books
    for l in locked:
        assert l["reason"] and l["unlocks_at"]
