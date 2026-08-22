"""P0 guarantees: one engine, an engine allowed to abstain, and a stated-vs-revealed
claim that needs more than one book behind it.

The regression these guard against is the worst kind this project can ship — a
confident sentence about someone's inner life that the data doesn't support, or two
surfaces telling the same reader two different things about themselves.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import dna_signals as S
from app.services.dna_engine import PERSONALITY_TYPES
from app.services.dna_insights import build_dna
from app.services.dna_signals import (
    MIN_BOOKS_PER_CLAIM,
    basis_for,
    score_archetype,
    stated_vs_revealed,
)
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
    # Scores are centered on BASELINE_VECTOR, so an empty tally scores -offset,
    # not zero. What matters is that nothing is named, not what the numbers are.


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


# ── Three verdicts: the signal measures, it doesn't only accuse ──

def _shelf(stated_n, stated_avg, other_n, other_avg, stated="comfort", other="devastation"):
    """A shelf with two disjoint groups, so the verdict is arithmetic, not luck."""
    return ([sig([stated], intensity=stated_avg) for _ in range(stated_n)]
            + [sig([other], intensity=other_avg) for _ in range(other_n)])


def test_stated_vs_revealed_verdict_contradicted():
    res = stated_vs_revealed(_shelf(10, 6, 10, 9), ["comfort"])
    assert res["verdict"] == "contradicted"
    assert res["reason"] is None
    assert res["delta"] == pytest.approx(3.0)
    assert res["revealed_hi"] == "devastation"


def test_stated_vs_revealed_verdict_confirmed():
    """The reader was right about themselves. That is a result, not a silence."""
    res = stated_vs_revealed(_shelf(10, 9, 10, 6), ["comfort"])
    assert res["verdict"] == "confirmed"
    assert res["reason"] is None
    assert res["delta"] == pytest.approx(-3.0)
    # The closest challenger, i.e. the narrowest margin the claim survives by.
    assert res["revealed_hi"] == "devastation"


def test_confirmed_requires_beating_every_comparable_emotion():
    """Out-rating the loudest challenger is not enough — one quiet emotion rated
    higher than the stated one is enough to make the claim not-confirmed."""
    sigs = _shelf(10, 8, 10, 6)                                   # comfort clears devastation
    sigs += [sig(["awe"], intensity=9) for _ in range(3)]          # but not awe
    res = stated_vs_revealed(sigs, ["comfort"])
    assert res["verdict"] != "confirmed"
    assert res["revealed_hi"] == "awe"


def test_stated_vs_revealed_inconclusive_on_a_dead_heat():
    res = stated_vs_revealed(_shelf(10, 7, 10, 7), ["comfort"])
    assert res["verdict"] == "inconclusive"
    assert res["reason"] == "dead_heat"
    assert res["delta"] == pytest.approx(0.0)
    # Inconclusive still carries its evidence — the reader can see it was level.
    assert res["evidence"]["stated"]["avg"] == 7.0
    assert res["evidence"]["compared"]["avg"] == 7.0


def test_stated_vs_revealed_inconclusive_when_too_few_books():
    """Two devastating books is not a verdict in either direction."""
    res = stated_vs_revealed(_shelf(30, 7, 2, 10), ["comfort"])
    assert res["verdict"] == "inconclusive"
    assert res["reason"] == "too_few_books"
    assert res["delta"] is None
    assert res["evidence"] is None
    # Still not silence: the reader told us something, so we answer.
    assert res["stated"] == "comfort"


def test_a_single_emotion_shelf_is_inconclusive_not_confirmed():
    """Nothing to out-rate makes "beats every other" vacuously true — and a
    sentence that is vacuously true of one reader is true of all of them."""
    res = stated_vs_revealed([sig(["comfort"], intensity=9) for _ in range(20)], ["comfort"])
    assert res["verdict"] == "inconclusive"
    assert res["reason"] == "too_few_books"


def test_none_means_never_asked_and_nothing_else():
    assert stated_vs_revealed(_shelf(10, 6, 10, 9), None) is None
    assert stated_vs_revealed(_shelf(10, 6, 10, 9), []) is None
    # Every other outcome is a dict with a verdict in it.
    for shelf in (_shelf(10, 6, 10, 9), _shelf(10, 9, 10, 6), _shelf(10, 7, 10, 7)):
        assert stated_vs_revealed(shelf, ["comfort"])["verdict"] in {
            "contradicted", "confirmed", "inconclusive"}


def test_every_verdict_carries_the_same_evidence_shape():
    for shelf in (_shelf(10, 6, 10, 9), _shelf(10, 9, 10, 6), _shelf(10, 7, 10, 7)):
        ev = stated_vs_revealed(shelf, ["comfort"])["evidence"]
        assert set(ev) == {"stated", "compared"}
        for side in ev.values():
            assert set(side) == {"emotion", "books", "avg"}
            assert side["books"] >= MIN_BOOKS_PER_CLAIM


def test_the_decisive_claim_wins_and_accusation_gets_no_head_start():
    """Two stated emotions, one confirmed and one contradicted. The one with the
    LARGER gap is reported — ranking by the signed gap would mean the reader
    always hears the accusation, which is the bias this whole change removes."""
    sigs = [sig(["comfort"], intensity=9) for _ in range(6)]      # confirmed by 3
    sigs += [sig(["tenderness"], intensity=5) for _ in range(6)]  # contradicted by 1
    sigs += [sig(["devastation"], intensity=6) for _ in range(6)]  # the actual challenger

    res = stated_vs_revealed(sigs, ["tenderness", "comfort"])
    assert res["stated"] == "comfort"
    assert res["verdict"] == "confirmed"
    # And the other claim was judged against the SHELF, not against comfort —
    # comfort is something the reader also said, so it cannot play challenger.
    assert stated_vs_revealed(sigs, ["tenderness", "comfort"])["revealed_hi"] != "comfort"

    # A decisive verdict outranks an inconclusive one whichever order they arrive
    # in — the reader's second answer is not a tiebreak, it's a second claim.
    sigs2 = [sig(["comfort"], intensity=9) for _ in range(6)]        # comfort: confirmed
    sigs2 += [sig(["devastation"], intensity=6) for _ in range(6)]
    # tenderness: never tagged, so nothing is comparable → inconclusive.
    for order in (["tenderness", "comfort"], ["comfort", "tenderness"]):
        res2 = stated_vs_revealed(sigs2, order)
        assert res2["stated"] == "comfort"
        assert res2["verdict"] == "confirmed"


# ── The copy layer must not hand a confirmed reader the accusation ──

def test_confirmed_reader_gets_no_contradiction_copy():
    sigs = _shelf(12, 9, 12, 6)
    assert stated_vs_revealed(sigs, ["comfort"])["verdict"] == "confirmed"
    res = build_dna(sigs, reads_for=["comfort"], insight_limit=99)
    cats = {i["category"] for i in res["insights"]}
    texts = " ".join(i["text"] for i in res["insights"])

    assert "confirmation" in cats
    assert "contradiction" not in cats
    # No "center of gravity is elsewhere" arguing with the confirmation.
    assert "center of gravity" not in texts

    # It reads as a measurement, not a compliment: both averages on the page, and
    # none of the praise vocabulary a horoscope would reach for.
    confirmation = next(i["text"] for i in res["insights"] if i["category"] == "confirmation")
    assert "9.0" in confirmation and "6.0" in confirmation
    for praise in ("right", "well", "know yourself", "good", "honest", "self-aware"):
        assert praise not in confirmation.lower(), f"praise language: {confirmation!r}"


def test_contradicted_reader_still_gets_the_contradiction_copy():
    sigs = _shelf(12, 6, 12, 9)
    assert stated_vs_revealed(sigs, ["comfort"])["verdict"] == "contradicted"
    res = build_dna(sigs, reads_for=["comfort"], insight_limit=99)
    cats = {i["category"] for i in res["insights"]}
    assert "contradiction" in cats
    assert "confirmation" not in cats


def test_inconclusive_reader_gets_neither_verdict_copy():
    """A dead heat says nothing about the claim — but the frequency observation
    is a different measurement and may still stand."""
    # Equal counts AND equal averages: nothing to say in either direction, and
    # "your center of gravity is elsewhere" is false on a 12–12 split.
    sigs = _shelf(12, 7, 12, 7)
    assert stated_vs_revealed(sigs, ["comfort"])["verdict"] == "inconclusive"
    res = build_dna(sigs, reads_for=["comfort"], insight_limit=99)
    cats = {i["category"] for i in res["insights"]}
    assert "confirmation" not in cats and "contradiction" not in cats

    # But out-frequented with a level rating IS a standing frequency observation.
    sigs = _shelf(6, 7, 20, 7)
    assert stated_vs_revealed(sigs, ["comfort"])["verdict"] == "inconclusive"
    res = build_dna(sigs, reads_for=["comfort"], insight_limit=99)
    contra = [i for i in res["insights"] if i["category"] == "contradiction"]
    assert [i["variant"] for i in contra] == ["center_of_gravity"]
    assert "confirmation" not in {i["category"] for i in res["insights"]}


def test_a_reader_is_never_confirmed_and_contradicted_at_once():
    """The payload must never argue with itself.

    `intensity_gap` and `rates_above_what_it_reaches_for` describe the same two
    averages; if both could fire, one panel would confirm the reader's
    self-knowledge and the next would undercut it. A reader can't tell those are
    two measurements — they just see the app contradicting itself.

    Swept rather than asserted: exclusivity is a property of the verdict, and a
    property is worth testing over a range, not at one point.
    """
    for n_stated in (4, 12, 30):
        for n_other in (4, 12, 30):
            for avg_stated in (2, 5, 7, 8, 10):
                for avg_other in (2, 5, 7, 8, 10):
                    sigs = _shelf(n_stated, avg_stated, n_other, avg_other)
                    res = build_dna(sigs, reads_for=["comfort"], insight_limit=99)
                    cats = {i["category"] for i in res["insights"]
                            if i["category"] in ("contradiction", "confirmation")}
                    assert len(cats) <= 1, (
                        f"{n_stated}@{avg_stated} vs {n_other}@{avg_other}: {cats}")


def test_no_frequency_claim_on_a_tie():
    """`revealed_top` is a RANK and ties silently. "You reach for X more often"
    and "your center of gravity is X" are both false on equal counts."""
    res = stated_vs_revealed(_shelf(12, 9, 12, 6), ["comfort"])
    assert res["verdict"] == "confirmed"
    assert res["stated_books"] == res["revealed_top_books"] == 12

    texts = " ".join(i["text"] for i in build_dna(
        _shelf(12, 9, 12, 6), reads_for=["comfort"], insight_limit=99)["insights"])
    assert "more often" not in texts
    assert "center of gravity" not in texts


# ── P1-5: the archetype table's own invariants ──

def test_grief_romantic_and_soft_masochist_do_not_tie_on_grief_devastation():
    """The twins shared two of three primaries, so a reader tagging exactly those
    two scored 1.0 against 1.0 — and list order, not the reader, picked."""
    vec = {slug: 0.0 for slug in S._ALL_SLUGS}
    vec["grief"], vec["devastation"] = 0.5, 0.5
    best, scores, margin = score_archetype(vec)

    assert scores["grief_romantic"] != scores["soft_masochist"]
    assert best == "grief_romantic"       # both of its primaries; the other has one
    assert margin > 0


# Pairs sharing two primaries, which is a tie waiting to be broken by declaration
# order. P1-5 separated grief_romantic/soft_masochist. The last one —
# comfort_architect/obsessive_romantic, where a reader tagging exactly comfort +
# longing scored 1.0 against 1.0 and got The Comfort Architect purely because it is
# listed first — was closed by re-anchoring obsessive_romantic from comfort onto
# absorption. The voice decision that note was waiting on got made.
#
# This is now EMPTY and must stay empty. It is deliberately kept as a named set
# rather than deleted, so a future violation reads as "this list grew" instead of
# as a fresh mystery.
KNOWN_TWIN_OVERLAP: set[frozenset[str]] = set()


def test_no_two_archetypes_share_more_than_one_primary():
    """A shared pair is a tie waiting to be broken by declaration order.

    All 45 pairs across the 10 archetypes, not the 28 across the original 8: the
    two absorption-anchored additions are exactly the kind of change that
    reintroduces an overlap, and absorption itself now anchors three types.
    """
    assert len(PERSONALITY_TYPES) == 10, "update this test's coverage note"
    offenders = set()
    for i, a in enumerate(PERSONALITY_TYPES):
        for b in PERSONALITY_TYPES[i + 1:]:
            if len(set(a["primary_emotions"]) & set(b["primary_emotions"])) > 1:
                offenders.add(frozenset({a["id"], b["id"]}))
    # Equality, not subset: fixing the known pair must delete it from the list,
    # and a NEW overlap must fail rather than blend in with the old one.
    assert offenders == KNOWN_TWIN_OVERLAP, (
        f"unexpected: {[sorted(p) for p in offenders ^ KNOWN_TWIN_OVERLAP]}"
    )


def test_every_archetype_carries_exactly_two_anti_emotions():
    """A third anti-emotion is a permanent scoring handicap. Comfort Architect
    carried one and won 5.4% of simulated readers against an expected ~12.5%."""
    for t in PERSONALITY_TYPES:
        assert len(t["anti_emotions"]) == 2, f"{t['id']}: {t['anti_emotions']}"


def test_no_archetype_carries_a_dead_anti_emotion():
    """Both antis must be emotions readers actually tag.

    Counting to two is not enough. Under centering an anti_emotion contributes
    0.5 * (population_rate - this reader's rate), so an anti drawn from "It lost
    me" — indifference, boredom, confusion, all at a 0.004 baseline — is ~0 for
    everyone. Six of the ten types carried one, which meant they were measured on
    ONE dimension of avoidance while the other four were measured on two: exactly
    the asymmetry the "exactly two" rule exists to prevent, in a form the count
    could not see.

    Disengagement still tells against a reader, but through the vector rather than
    a penalty term — frequency_vector normalises over the whole vocabulary, so
    tagging boredom dilutes every other share. The anti was double-counting it.
    """
    for t in PERSONALITY_TYPES:
        dead = [e for e in t["anti_emotions"] if e in LOST_ME_SLUGS]
        assert not dead, (
            f"{t['id']} has disengagement anti-emotion(s) {dead}; nobody tags them, "
            "so this type effectively carries one anti, not two"
        )
        for e in t["anti_emotions"]:
            rate = S.BASELINE_VECTOR.get(e, 0.0)
            assert rate > 0.010, (
                f"{t['id']}: anti {e!r} sits at baseline {rate:.4f}, at or below the "
                "floor — its penalty never fires. See scripts/dna_audit.py FREE_ANTI"
            )


def test_comfort_architect_is_reachable_on_its_own_primaries():
    vec = {slug: 0.0 for slug in S._ALL_SLUGS}
    vec["comfort"], vec["longing"], vec["tenderness"] = 0.4, 0.3, 0.3
    best, _, _ = score_archetype(vec)
    assert best == "comfort_architect"


# ── P1-6: the receipt ──

def test_basis_for_counts_only_what_it_can_evidence():
    sigs = ([sig(["grief", "catharsis"], intensity=5) for _ in range(14)]
            + [sig(["comfort"], intensity=4) for _ in range(17)])
    basis = basis_for("grief_romantic", sigs)

    counts = {r["emotion"]: r for r in basis["counts"]}
    assert counts["grief"] == {"emotion": "grief", "books": 14, "of": 31}
    assert counts["catharsis"]["books"] == 14
    # devastation is a primary of this type but never tagged — it is absent, not
    # reported as a zero. A zero on a receipt reads like a finding.
    assert "devastation" not in counts
    # Ordered by weight of evidence.
    assert basis["counts"] == sorted(basis["counts"], key=lambda r: -r["books"])


def test_basis_for_names_what_the_top_of_the_scale_is_reserved_for():
    sigs = [sig(["comfort"], intensity=3) for _ in range(10)]
    sigs += [sig(["devastation"], intensity=10) for _ in range(3)]
    basis = basis_for("grief_romantic", sigs)
    assert basis["top_rated_emotions"] == ["devastation"]
    assert basis["top_rated_n"] == 3


def test_build_dna_carries_the_basis_and_counts_books_not_journal_days():
    """The line says "your books". It must not be counting named journal days."""
    books = [sig(["grief", "catharsis"]) for _ in range(6)]
    journal = [sig(["grief"]) for _ in range(20)]
    res = build_dna(books, journal_sigs=journal)

    assert res["archetype"]["id"] == "grief_romantic"
    grief = next(r for r in res["basis"]["counts"] if r["emotion"] == "grief")
    assert grief == {"emotion": "grief", "books": 6, "of": 6}


def test_basis_is_absent_when_the_engine_abstained():
    res = build_dna([sig([]) for _ in range(6)] + [sig(["boredom"]) for _ in range(5)])
    assert res["enough"] is True
    assert res["archetype"] is None
    assert res["basis"] is None


def test_score_archetype_returns_scores_in_true_rank_order():
    """`scores` is ordered by the exact floats, so second place is list(scores)[1].

    dna_insights builds the card's `runner_up` from that position. It used to
    re-sort the dict itself, which ranked the ROUNDED values and could name a
    runner-up that was not really second — the same rounding bug as ranking the
    leader, one function further out. Measured at ~1 in 6000 simulated readers
    before the fix (`python -m scripts.dna_audit`, code ROUNDED_RANK_DISAGREES).
    """
    vec = {slug: 0.0 for slug in S._ALL_SLUGS}
    vec["dread"], vec["revulsion"], vec["absorption"], vec["awe"] = 0.3, 0.3, 0.3, 0.1
    best, scores, _gap = score_archetype(vec)
    assert list(scores)[0] == best
    assert list(scores.values()) == sorted(scores.values(), reverse=True)


def test_runner_up_position_matches_a_recomputed_exact_ranking():
    """The hedge names a specific other archetype, so it must be the right one.

    Recomputes the exact (unrounded) scores from the raw scorer and checks the
    dict's second key against them, rather than against the dict's own order —
    otherwise the assertion is true by construction and proves nothing.
    """
    for vec in (
        {**{s: 0.0 for s in S._ALL_SLUGS}, "awe": 0.5, "absorption": 0.5},
        {**{s: 0.0 for s in S._ALL_SLUGS}, "tenderness": 0.4, "awe": 0.4, "nostalgia": 0.2},
        {**{s: 0.0 for s in S._ALL_SLUGS}, "dread": 0.34, "absorption": 0.33, "rage": 0.33},
    ):
        best, scores, _ = score_archetype(vec)
        exact = sorted(
            ((S._raw_archetype_score(vec, t) - S._BASELINE_OFFSET[t["id"]], t["id"])
             for t in PERSONALITY_TYPES),
            reverse=True,
        )
        assert list(scores)[0] == exact[0][1] == best
        assert list(scores)[1] == exact[1][1]
