"""P0 guarantees: one engine, an engine allowed to abstain, and a stated-vs-revealed
claim that needs more than one book behind it.

The regression these guard against is the worst kind this project can ship — a
confident sentence about someone's inner life that the data doesn't support, or two
surfaces telling the same reader two different things about themselves.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import dna_signals as S
from app.services.dna_insights import build_dna
from app.services.dna_signals import MIN_BOOKS_PER_CLAIM, score_archetype, stated_vs_revealed
from app.utils.emotions import LOST_ME_SLUGS

NOW = datetime.now(timezone.utc)


def sig(emotions, intensity=5, days=0, status="finished"):
    return S.EntrySig(emotions=list(emotions), intensity=intensity,
                      ts=NOW - timedelta(days=days), status=status)


# ── P0-2: the engine is allowed to say nothing ──

def test_score_archetype_abstains_on_empty_tally():
    """An all-zero vector must not fall through to whichever type is listed first."""
    empty = {slug: 0.0 for slug in S._ALL_SLUGS}
    best, scores, margin = score_archetype(empty)
    assert best is None
    assert margin == 0.0
    assert set(scores) == {t["id"] for t in S.PERSONALITY_TYPES}
    assert all(v == 0.0 for v in scores.values())


def test_score_archetype_abstains_when_only_lost_me_tags():
    """"It lost me" tags are registers of disengagement, never a reading identity.

    They appear only as anti_emotions, so a reader who has tagged nothing else
    scores zero or negative everywhere — and gets no label.
    """
    vec = {slug: 0.0 for slug in S._ALL_SLUGS}
    for slug in LOST_ME_SLUGS:
        vec[slug] = 1.0 / len(LOST_ME_SLUGS)
    best, scores, margin = score_archetype(vec)
    assert best is None
    assert margin == 0.0
    assert max(scores.values()) <= 0


def test_score_archetype_still_names_a_type_when_there_is_a_signal():
    vec = {slug: 0.0 for slug in S._ALL_SLUGS}
    vec["comfort"], vec["tenderness"] = 0.6, 0.4
    best, scores, margin = score_archetype(vec)
    assert best == "comfort_architect"
    assert scores[best] == max(scores.values())
    assert 0.0 < margin <= 1.0


def test_untagged_import_does_not_produce_an_archetype():
    """Five imported books with no feelings logged is five titles, not a profile.

    Before P0-2 this produced `enough: True`, all-zero scores, and a confident
    "The Grief Romantic" — purely because it is first in PERSONALITY_TYPES.
    """
    res = build_dna([sig([]) for _ in range(5)])
    assert res["enough"] is False
    assert res["book_count"] == 5
    assert res["tagged_count"] == 0
    assert "archetype" not in res


def test_partially_tagged_shelf_gates_on_the_tagged_books():
    """Ten books, four of them tagged: the gate counts the four."""
    sigs = [sig(["comfort"]) for _ in range(4)] + [sig([]) for _ in range(6)]
    res = build_dna(sigs)
    assert res["enough"] is False
    assert res["book_count"] == 10 and res["tagged_count"] == 4

    res = build_dna(sigs + [sig(["comfort"])])
    assert res["enough"] is True
    assert res["tagged_count"] == 5
    assert res["archetype"]["id"] == "comfort_architect"


def test_enough_payload_carries_margin_and_hedges_a_close_call():
    res = build_dna([sig(["comfort", "tenderness"]) for _ in range(6)])
    assert res["margin"] is not None and res["archetype"] is not None
    if res["margin"] < 0.10:
        assert res["runner_up"]          # a coin-flip says so
    else:
        assert res["runner_up"] is None


# ── P0-3: a claim needs books behind it ──

def test_stated_vs_revealed_requires_three_books():
    """One 10/10 devastation book must not outweigh a shelf of comfort reads."""
    sigs = [sig(["comfort"], intensity=7) for _ in range(30)]
    sigs += [sig(["devastation"], intensity=10) for _ in range(MIN_BOOKS_PER_CLAIM - 1)]

    res = stated_vs_revealed(sigs, ["comfort"])
    assert res["stated"] == "comfort"
    assert res["revealed_top"] == "devastation"   # frequency still reports honestly
    assert res["delta"] is None                   # but no gap is claimed
    assert res["revealed_hi"] is None

    # One more devastation book and the comparison becomes fair game.
    sigs.append(sig(["devastation"], intensity=10))
    res = stated_vs_revealed(sigs, ["comfort"])
    assert res["revealed_hi"] == "devastation"
    assert res["delta"] == pytest.approx(3.0)


def test_stated_vs_revealed_compares_on_disjoint_sets():
    """A book carrying both tags feeds the identical intensity to both averages,
    so it can only dilute a gap it can't evidence. It is excluded from both sides."""
    sigs = [sig(["comfort"], intensity=4) for _ in range(3)]
    sigs += [sig(["devastation"], intensity=10) for _ in range(3)]
    # Co-tagged books, rated in between — they must not move the measured gap.
    sigs += [sig(["comfort", "devastation"], intensity=7) for _ in range(5)]

    res = stated_vs_revealed(sigs, ["comfort"])
    assert res["disjoint"] is True
    assert res["revealed_hi"] == "devastation"
    assert res["delta"] == pytest.approx(6.0)     # 10 - 4, not diluted toward 7


def test_stated_vs_revealed_scores_both_stated_emotions():
    """reads_for allows two. The reader hears about whichever claim their shelf
    contradicts more — the second answer is not silently discarded."""
    sigs = [sig(["comfort"], intensity=6) for _ in range(4)]      # weak claim
    sigs += [sig(["tenderness"], intensity=2) for _ in range(4)]  # the real gap
    sigs += [sig(["devastation"], intensity=9) for _ in range(4)]

    first_only = stated_vs_revealed(sigs, ["comfort"])
    both = stated_vs_revealed(sigs, ["comfort", "tenderness"])
    assert first_only["stated"] == "comfort"
    assert both["stated"] == "tenderness"
    assert both["delta"] > first_only["delta"]


def test_stated_vs_revealed_still_none_without_a_stated_preference():
    assert stated_vs_revealed([sig(["comfort"]) for _ in range(9)], None) is None
    assert stated_vs_revealed([sig(["comfort"]) for _ in range(9)], []) is None
