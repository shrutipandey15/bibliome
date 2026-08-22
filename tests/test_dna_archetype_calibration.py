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
from app.utils.emotions import EMOTIONS, LOST_ME_SLUGS

FAMILY = {e["slug"]: e["family"] for e in EMOTIONS}
EXPERIENTIAL = [s for s in S._ALL_SLUGS if s not in LOST_ME_SLUGS]

BOOK_BUNDLES = {
    "romantasy_dark":       ["desire", "dread", "devastation", "rage", "awe", "absorption"],
    "romantasy_soft":       ["desire", "longing", "joy", "awe", "absorption"],
    "grief_litfic":         ["grief", "devastation", "catharsis", "tenderness"],
    "cozy":                 ["comfort", "tenderness", "joy"],
    "thriller":             ["dread", "rage", "awe", "absorption"],
    "quiet_litfic":         ["recognition", "tenderness", "longing", "awe"],
    "memoir":               ["recognition", "grief", "catharsis", "nostalgia"],
    "comic_novel":          ["amusement", "joy", "recognition"],
    "epic_fantasy":         ["awe", "dread", "devastation", "longing"],
    "epic_fantasy_hopeful": ["awe", "absorption", "joy", "longing"],
    "horror":               ["dread", "revulsion", "absorption", "rage"],
    "sad_romance":          ["longing", "grief", "desire", "devastation", "absorption"],
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


def test_balanced_reader_is_not_handed_a_label_by_list_order(monkeypatch):
    """A reader with an even spread across every experiential tag must not be
    labelled by position in PERSONALITY_TYPES.

    Uncentered this produced a five-way tie at 0.1786 that list order silently
    resolved into control_intellectual.

    This asserts the absence of a TIE, not a small gap. The original version of
    this test required the gap to fall under HEDGE_ARCHETYPE_GAP, on the reasoning
    that this is "the most average reader possible" — but that stopped being true
    the moment scores were centered. Average now means BASELINE_VECTOR, which is
    nowhere near flat (awe 0.126 against nostalgia 0.024), so a reader who tags all
    14 experiential emotions equally is genuinely unusual and genuinely leans
    somewhere. Their lead of ~0.024 sits above the 40th percentile of the gap
    distribution. Demanding it be hedged would mean setting the hedge threshold
    above the median, which is what made the hedge meaningless in the first place.
    """
    vec = _norm(Counter(dict.fromkeys(EXPERIENTIAL, 1)))
    best, scores, gap = score_archetype(vec)
    ranked = sorted(scores.values(), reverse=True)
    assert ranked[0] != ranked[1], (
        "the balanced reader is an exact tie, so the winner is decided by "
        "PERSONALITY_TYPES order rather than by the reader"
    )

    # The strong form: permuting the table must not change who wins. Ties break on
    # insertion order, so if any part of this result rests on list position, a
    # shuffle will expose it.
    shuffled = list(PERSONALITY_TYPES)
    for seed in range(8):
        random.Random(seed).shuffle(shuffled)
        monkeypatch.setattr(S, "PERSONALITY_TYPES", shuffled)
        assert score_archetype(vec)[0] == best, (
            f"winner changed under a PERSONALITY_TYPES permutation (seed={seed})"
        )


def test_hedge_fires_on_a_minority_of_readers():
    """The hedge must stay an exception, or it stops carrying information.

    HEDGE_ARCHETYPE_GAP is an absolute cut on a quantity whose scale depends on
    BASELINE_VECTOR and on PERSONALITY_TYPES, so it does not survive changes to
    either on its own. At 0.05 — carried over by eye from the pre-centering scale,
    where gaps were fractions of the leader's score — it hedged 69% of readers:
    every second card said "or maybe this other one", which is not a hedge but a
    shrug.
    """
    readers = list(_correlated_readers(n=3000, seed=5))
    labelled = [g for g in (score_archetype(v) for v in readers) if g[0] is not None]
    hedged = sum(1 for _, _, gap in labelled if gap < S.HEDGE_ARCHETYPE_GAP)
    rate = hedged / len(labelled)
    assert 0.10 <= rate <= 0.35, (
        f"hedging {rate:.1%} of labelled readers. Below ~10% the hedge never fires "
        "and close calls are asserted flatly; above ~35% it is the default state. "
        "Re-measure with `python -m scripts.dna_bias_probe` and set the threshold "
        "at a percentile of the gap distribution."
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


# Shelves that are genuinely unambiguous, meaning every tag points one way.
#
# `["amusement", "joy", "recognition"] -> midnight_arsonist` used to sit in this
# list and was never actually unambiguous: midnight_arsonist is anchored on
# amusement + awe + rage, so that reader matched ONE of its three anchors and won
# on the strength of the others being unclaimed. When quiet_witness took over
# recognition the fixture flipped to quiet_witness — correctly, on a one-anchor
# match each way. A test that asserts an obvious answer has to use a shelf whose
# answer is obvious.
@pytest.mark.parametrize("tags,expected", [
    (["comfort", "tenderness", "joy"], "comfort_architect"),
    (["grief", "devastation", "catharsis"], "grief_romantic"),
    (["dread", "rage", "devastation"], "soft_masochist"),
    (["amusement", "awe", "rage"], "midnight_arsonist"),
    (["tenderness", "recognition", "nostalgia"], "quiet_witness"),
    (["desire", "absorption", "longing"], "obsessive_romantic"),
    (["awe", "absorption", "joy"], "world_diver"),
    (["dread", "revulsion", "absorption"], "adrenaline_seeker"),
    (["recognition", "dread", "awe"], "control_intellectual"),
    (["longing", "joy", "catharsis"], "emotional_archaeologist"),
])
def test_unambiguous_readers_still_get_the_obvious_label(tags, expected):
    """Calibration must not cost the engine its plain-language correctness.

    Every archetype is covered, so a re-anchor cannot quietly make one of them
    unreachable on its own anchors.
    """
    assert score_archetype(_norm(Counter(dict.fromkeys(tags, 1))))[0] == expected


def test_baseline_covers_every_canonical_slug():
    assert set(S.BASELINE_VECTOR) == set(S._ALL_SLUGS)
    assert abs(sum(S.BASELINE_VECTOR.values()) - 1.0) < 0.02
