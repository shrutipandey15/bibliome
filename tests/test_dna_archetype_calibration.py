"""Calibration invariants for the centered archetype scorer.

These exist because the P1-5 re-anchor was validated against independently-sampled
readers, a model under which the scorer looks fair. Real tagging is correlated —
a book makes you feel several things at once — and under correlation the
uncentered scorer gave `control_intellectual` 43% of readers against a 12.5% fair
share. Anything that touches PERSONALITY_TYPES or BASELINE_VECTOR has to keep
these passing.
"""

import random
from collections import Counter

import pytest

from app.services import dna_signals as S
from app.services.dna_engine import PERSONALITY_TYPES
from app.services.dna_signals import score_archetype
from app.utils.emotions import EMOTIONS

FAMILY = {e["slug"]: e["family"] for e in EMOTIONS}
EXPERIENTIAL = [s for s in S._ALL_SLUGS if FAMILY[s] != "It lost me"]

BOOK_BUNDLES = {
    "romantasy_dark": ["desire", "dread", "devastation", "rage", "awe"],
    "romantasy_soft": ["desire", "longing", "joy", "awe"],
    "grief_litfic":   ["grief", "devastation", "catharsis", "tenderness"],
    "cozy":           ["comfort", "tenderness", "joy"],
    "thriller":       ["dread", "rage", "awe"],
    "quiet_litfic":   ["recognition", "tenderness", "longing", "awe"],
    "memoir":         ["recognition", "grief", "catharsis", "nostalgia"],
    "comic_novel":    ["amusement", "joy", "recognition"],
    "epic_fantasy":   ["awe", "dread", "devastation", "longing"],
    "sad_romance":    ["longing", "grief", "desire", "devastation"],
}


def _norm(counts):
    total = sum(counts.values())
    return {s: counts.get(s, 0) / total for s in S._ALL_SLUGS} if total else \
        {s: 0.0 for s in S._ALL_SLUGS}


def _correlated_readers(n=4000, seed=5):
    random.seed(seed)
    keys = list(BOOK_BUNDLES)
    for _ in range(n):
        taste = [random.gammavariate(2, 1) for _ in keys]
        counts: Counter = Counter()
        for _book in range(random.randint(10, 50)):
            tags = [s for s in BOOK_BUNDLES[random.choices(keys, weights=taste)[0]]
                    if random.random() < 0.75]
            if not tags:
                continue
            for slug in tags:                    # one entry, one vote
                counts[slug] += 1.0 / len(tags)
        yield _norm(counts)


def test_no_archetype_dominates_a_correlated_population():
    """No label may take more than 2x its fair share of realistic readers."""
    wins: Counter = Counter()
    readers = list(_correlated_readers())
    for vec in readers:
        wins[score_archetype(vec)[0]] += 1
    fair = 1 / len(PERSONALITY_TYPES)
    worst = max((wins[t["id"]] / len(readers), t["id"]) for t in PERSONALITY_TYPES)
    assert worst[0] <= 2 * fair, (
        f"{worst[1]} takes {worst[0]:.1%} of readers (fair share {fair:.1%}). "
        "An archetype table that funnels most readers into one noun makes the "
        "noun meaningless."
    )


def test_balanced_reader_is_not_handed_a_label_by_list_order():
    """A reader with an even spread across every experiential tag has no archetype.

    Uncentered this produced a five-way tie at 0.1786 that PERSONALITY_TYPES order
    silently resolved into control_intellectual.
    """
    best, scores, gap = score_archetype(_norm(Counter(dict.fromkeys(EXPERIENTIAL, 1))))
    assert gap < S.HEDGE_ARCHETYPE_GAP, (
        f"the most average reader possible leads by {gap}, which would be "
        "presented as a decided label"
    )


def test_exact_ties_are_vanishingly_rare():
    """Ties fall to declaration order, so they must not be a routine outcome."""
    readers = list(_correlated_readers(n=2000, seed=11))
    ties = 0
    for vec in readers:
        ranked = sorted(score_archetype(vec)[1].values(), reverse=True)
        if ranked[0] == ranked[1]:
            ties += 1
    assert ties / len(readers) < 0.01


@pytest.mark.parametrize("tags,expected", [
    (["comfort", "tenderness", "joy"], "comfort_architect"),
    (["grief", "devastation", "catharsis"], "grief_romantic"),
    (["amusement", "joy", "recognition"], "midnight_arsonist"),
    (["dread", "rage", "devastation"], "soft_masochist"),
])
def test_unambiguous_readers_still_get_the_obvious_label(tags, expected):
    """Calibration must not cost the engine its plain-language correctness."""
    assert score_archetype(_norm(Counter(dict.fromkeys(tags, 1))))[0] == expected


def test_baseline_covers_every_canonical_slug():
    assert set(S.BASELINE_VECTOR) == set(S._ALL_SLUGS)
    assert abs(sum(S.BASELINE_VECTOR.values()) - 1.0) < 0.02
