"""Edge cases where a signal is true of the data it read but false as a sentence.

The recurring failure mode in this layer is a claim computed over one population
and worded as though it covered another: an insight built on 5 tagged books that
opens "You've logged 30 books". The numbers are individually correct and the
sentence is a lie.
"""

from datetime import datetime, timedelta, timezone

from app.services.dna_insights import build_dna
from app.services import dna_signals as sig
from app.services.dna_signals import EntrySig
from app.utils.emotions import EMOTIONS

NOW = datetime.now(timezone.utc)


def _sig(emotions, days_ago, status="finished"):
    return EntrySig(emotions=list(emotions), intensity=7,
                    ts=NOW - timedelta(days=days_ago), status=status)


def _mostly_untagged_shelf(tagged=5, untagged=25):
    """The shelf from the brief: 30 books, 5 of them carrying any feeling.

    The 5 tagged books all use the same narrow palette, so the blind-spot and
    range insights both have something to say — and both would be saying it about
    5 books while quoting 30.
    """
    sigs = [_sig(["joy"], 10 + i) for i in range(tagged)]
    sigs += [_sig([], 100 + i) for i in range(untagged)]
    return sigs


def _dna(sigs):
    return build_dna(sigs, ["escape"], insight_limit=12)


def _texts(dna):
    return [i["text"] for i in dna["insights"]]


def test_shelf_of_mostly_untagged_books_still_computes_dna():
    """Guard for the fixture itself: 5 tagged books clears MIN_BOOKS_FOR_DNA."""
    dna = _dna(_mostly_untagged_shelf())
    assert dna["enough"] is True
    assert dna["book_count"] == 30


def test_no_insight_quotes_the_untagged_book_count():
    """No insight may cite 30 when it was computed from 5 tagged books."""
    dna = _dna(_mostly_untagged_shelf())
    for text in _texts(dna):
        assert "30 books" not in text, (
            f"insight quotes the raw shelf size: {text!r}. It was computed from "
            "5 books that carry a feeling."
        )


def test_insight_n_reports_the_population_the_claim_covers():
    """`n` is what the client renders as 'based on N books'."""
    dna = _dna(_mostly_untagged_shelf())
    for insight in dna["insights"]:
        assert insight["n"] == 5, (
            f"{insight['category']}/{insight['variant']} reports n={insight['n']} "
            "on a shelf with 5 tagged books"
        )


def test_blind_spot_gate_counts_tagged_books_not_titles():
    """The blind-spot gate is 10. A 30-book shelf with 5 tagged has not met it.

    This is the headline case: 'You've logged 30 books. You have never once
    reached for devastation.' — built on five books, one of which is the only
    thing that could possibly have carried devastation.
    """
    dna = _dna(_mostly_untagged_shelf())
    assert "blind_spot" not in {i["category"] for i in dna["insights"]}
    assert "blind_spot" in {l["category"] for l in dna["locked"]}


def test_blind_spot_unlocks_once_ten_books_carry_a_feeling():
    """And the gate must still open when the tagged books are actually there."""
    dna = _dna([_sig(["joy"], 10 + i) for i in range(12)])
    assert "blind_spot" in {i["category"] for i in dna["insights"]}


# 1/TAGGED must land exactly on the rare band's 0.05 edge, so the test proves the
# denominator rather than the band.
TAGGED = 20


def test_rare_share_denominator_is_tagged_books():
    """`rare` says an emotion 'shows up in under X% of what you read'.

    Every emotion is tagged at least once here, so there are no blind spots and
    `rare` is the only variant its category can pick — otherwise the deterministic
    rotation picks `never` and this never exercises the denominator at all.

    Each of the 20 tagged books carries one distinct emotion, so every emotion sits
    at exactly 1/20 = 5%, just outside the `0 < v < 0.05` rare band. Divided by the
    raw shelf of 30 instead, the same books read as 3.3% and every one of them is
    reported as rare. The raw denominator manufactures rarity out of untagged
    imports.

    The tagged count is pinned to 20 and the padding derived from the vocabulary
    size, not hardcoded: at 18 slugs this read `18 + 2`, and the 19th slug silently
    moved every emotion to 1/21 = 4.8% — inside the rare band — so the test failed
    on the boundary it exists to hold rather than on the behaviour it describes.
    """
    slugs = [e["slug"] for e in EMOTIONS]
    assert len(slugs) <= TAGGED, "vocabulary outgrew the 1/20 = 5% construction"
    sigs = [_sig([slug], 10 + i) for i, slug in enumerate(slugs)]
    # Top up to exactly TAGGED tagged books by repeating emotions that are already
    # present — a repeat lifts that one above 5% and leaves every other at exactly 5%.
    sigs += [_sig([slugs[i % len(slugs)]], 40 + i) for i in range(TAGGED - len(slugs))]
    sigs += [_sig([], 200 + i) for i in range(10)]                  # untagged padding
    dna = _dna(sigs)
    rare_texts = [i["text"] for i in dna["insights"] if i["variant"] == "rare"]
    assert not rare_texts, (
        f"emotions at a 5% tagged-book share reported as rare: {rare_texts}"
    )


# ── Abandonment reads the status column that migration 022 actually added ──

def _ab(sigs):
    from app.services.dna_signals import abandonment
    return abandonment(sigs)


def test_reread_is_not_an_abandonment():
    """`reread` is a book finished TWICE. The old `status != 'finished'` proxy
    counted it as a DNF, which is as wrong as this layer gets."""
    sigs = [_sig(["joy"], 10 + i, status="reread") for i in range(6)]
    sigs += [_sig(["joy"], 30 + i, status="finished") for i in range(6)]
    assert _ab(sigs) is None


def test_want_to_read_is_excluded_from_the_denominator():
    """A book on the pile was never opened — it can be neither finished nor
    abandoned, and counting it as 'not finished' inflates the rate."""
    opened = [_sig(["dread"], 10 + i, status="abandoned") for i in range(3)]
    opened += [_sig(["joy"], 20 + i, status="finished") for i in range(3)]
    # dread is abandoned 3/3 against an overall 3/6 — the finding is "1.0", and it
    # must not move when 20 unopened books are sitting on the pile.
    without_pile = _ab(opened)
    with_pile = _ab(opened + [_sig(["comfort"], 50 + i, status="want_to_read")
                              for i in range(20)])
    assert without_pile == with_pile
    assert with_pile["emotion"] == "dread"
    assert with_pile["fraction"] == 1.0


def test_paused_counts_as_abandoned():
    """Documented decision, asserted so it cannot drift silently."""
    sigs = [_sig(["dread"], 10 + i, status="paused") for i in range(3)]
    sigs += [_sig(["joy"], 20 + i, status="finished") for i in range(9)]
    res = _ab(sigs)
    assert res is not None and res["emotion"] == "dread"


def test_reading_counts_in_the_denominator_but_is_not_a_dnf():
    """A book in progress has not been put down."""
    sigs = [_sig(["dread"], 10 + i, status="reading") for i in range(6)]
    sigs += [_sig(["joy"], 20 + i, status="finished") for i in range(6)]
    assert _ab(sigs) is None


def test_abandonment_reports_the_reason_the_reader_gave():
    sigs = [EntrySig(emotions=["dread"], intensity=7, ts=NOW - timedelta(days=10 + i),
                     status="abandoned", dnf_reason="lost_me") for i in range(3)]
    sigs += [_sig(["joy"], 30 + i, status="finished") for i in range(9)]
    res = _ab(sigs)
    assert res["emotion"] == "dread"
    assert res["dnf_reason"] == "lost_me"
    assert res["dnf_reason_books"] == 3


def test_reason_variant_replaces_the_weaker_emotion_only_sentence():
    """When the reader said why, that sentence must not be rotated away."""
    sigs = [EntrySig(emotions=["dread"], intensity=7, ts=NOW - timedelta(days=10 + i),
                     status="abandoned", dnf_reason="lost_me") for i in range(3)]
    sigs += [_sig(["joy"], 30 + i, status="finished") for i in range(9)]
    variants = {i["variant"] for i in _dna(sigs)["insights"] if i["category"] == "abandonment"}
    assert variants == {"dnf_reason"}


# ── Blind spots are ranked by surprise, not by declaration order ──

def test_blind_spot_names_the_most_surprising_absence_not_the_first_slug():
    """A reader missing devastation, nostalgia and awe should hear about awe.

    Awe is the most commonly tagged emotion in BASELINE_VECTOR and nostalgia the
    rarest, so awe's absence is the finding. Devastation merely happens to be
    first in EMOTIONS, which is what the old implementation reported.
    """
    from app.services.dna_signals import BASELINE_VECTOR, blind_spots
    present = [e["slug"] for e in EMOTIONS
               if e["slug"] not in {"devastation", "nostalgia", "awe"}]
    sigs = [_sig([slug], 10 + i) for i, slug in enumerate(present)]
    ranked = blind_spots(sigs)

    assert set(ranked) == {"devastation", "nostalgia", "awe"}
    assert ranked[0] == "awe"
    assert ranked[-1] == "nostalgia"
    assert BASELINE_VECTOR["awe"] > BASELINE_VECTOR["devastation"] > BASELINE_VECTOR["nostalgia"]


def test_blind_spot_order_survives_a_permutation_of_EMOTIONS(monkeypatch):
    """The output must be decided by the baseline, not by list position.

    This is the regression guard for the whole class of bug: if permuting the
    declaration order changes what the reader is told, the ranking is fake.
    """
    import random
    from app.services import dna_signals as S

    present = [e["slug"] for e in EMOTIONS
               if e["slug"] not in {"devastation", "nostalgia", "awe", "grief"}]
    sigs = [_sig([slug], 10 + i) for i, slug in enumerate(present)]
    baseline = S.blind_spots(sigs)

    for seed in range(8):
        shuffled = list(S._ALL_SLUGS)
        random.Random(seed).shuffle(shuffled)
        monkeypatch.setattr(S, "_ALL_SLUGS", shuffled)
        assert S.blind_spots(sigs) == baseline, (
            f"blind-spot order changed under EMOTIONS permutation seed={seed}"
        )


# ── A gate counts the books that could have evidenced THAT claim ──

def _arc(em, days_ago, start=None, end=None):
    return EntrySig(emotions=list(em), intensity=7, ts=NOW - timedelta(days=days_ago),
                    status="finished", arc_start=start, arc_end=end)


def _arc_shelf():
    """5 tagged books carrying no arc, 20 untagged books carrying one.

    arc_shape reads arc_start/arc_end and never looks at emotions, so this
    reader's arc finding rests on 20 books — but every other insight here rests
    on 5.
    """
    sigs = [_arc(["joy"], 10 + i) for i in range(5)]
    sigs += [_arc([], 50 + i, "dread", "catharsis") for i in range(20)]
    return sigs


def test_arc_insight_reports_its_own_denominator_not_the_tagged_count():
    """`n` is the population the claim covers, and for arc that is arc books.

    Phase 3 moved every gate and every `n` onto tagged_count, which is right for
    claims about emotions and wrong for this one. Reporting n=5 under a finding
    computed from 20 books understates the reader's own evidence.
    """
    dna = _dna(_arc_shelf())
    arc = next(i for i in dna["insights"] if i["category"] == "arc")
    assert arc["n"] == 20, f"arc claim covers 20 books but reports n={arc['n']}"


def test_arc_copy_names_the_population_it_measured():
    """fraction is over books with arc data, not over books finished."""
    dna = _dna(_arc_shelf())
    arc = next(i for i in dna["insights"] if i["category"] == "arc")
    assert "books you finish" not in arc["text"], (
        f"arc copy claims a denominator it did not measure: {arc['text']!r}"
    )


def test_emotion_claims_still_report_the_tagged_count():
    """The per-category denominator must not leak into the emotion insights."""
    dna = _dna(_arc_shelf())
    for insight in dna["insights"]:
        if insight["category"] != "arc":
            assert insight["n"] == 5, (
                f"{insight['category']} reports n={insight['n']}, not the 5 tagged books"
            )


# ── The effective-sample-size half of the DNA gate ──
#
# MIN_BOOKS_FOR_DNA counts tagged books. It cannot see that the recency-weighted
# vector the archetype is actually computed from may be listening to only one of
# them, which is a present-tense label built on a shelf that no longer exists.

def test_ess_is_scale_invariant_so_an_old_shelf_is_not_a_small_one():
    """Age alone must not shrink the effective sample.

    Five books all read two years ago decay to the same tiny weight, and the
    weighted vector still averages five opinions evenly. Punishing that would gate
    out every reader who simply stopped logging for a while — a different problem
    from the one this exists to catch, and not one a DNA label is wrong about.
    """
    recent = sig.effective_sample_size([_sig(["joy"], 7) for _ in range(5)])
    ancient = sig.effective_sample_size([_sig(["joy"], 730) for _ in range(5)])
    assert round(recent, 6) == round(ancient, 6) == 5.0


def test_ess_collapses_when_one_recent_book_dominates_a_stale_shelf():
    """The case the condition is for: raw count 5, effective sample ~1."""
    skewed = [_sig(["joy"], 2)] + [_sig(["grief"], 900) for _ in range(4)]
    ess = sig.effective_sample_size(skewed)
    assert ess < 1.5, ess
    assert len([s for s in skewed if s.emotions]) == 5  # the raw gate sees five


def test_untagged_books_do_not_prop_up_the_effective_sample():
    """An untagged book contributes nothing to the vector, so it must not count
    toward the gate that protects the vector either."""
    tagged = [_sig(["joy"], 10 + i) for i in range(5)]
    padded = tagged + [_sig([], 10 + i) for i in range(25)]
    assert sig.effective_sample_size(tagged) == sig.effective_sample_size(padded)


def test_skewed_shelf_is_gated_even_though_it_clears_the_raw_count():
    """End to end: five tagged books, refused, because four of them are gone."""
    skewed = [_sig(["joy"], 2)] + [_sig(["grief"], 900) for _ in range(4)]
    out = _dna(skewed)
    assert out["enough"] is False
    assert out["tagged_count"] == 5           # the raw count was satisfied
    assert out["needed"] == sig.MIN_BOOKS_FOR_DNA


def test_an_ordinary_slow_reader_is_not_gated():
    """The threshold must not land on people who merely read slowly.

    Five tagged books spread evenly across two years is a normal shelf, not a
    degenerate one, and it is exactly the population sitting at the five-book
    minimum — the worst group to withhold from silently. This is the assertion
    that pins MIN_EFFECTIVE_BOOKS_FOR_DNA down: at 3.0 this reader is refused.
    """
    slow = [_sig(["joy"], d) for d in (5, 90, 200, 400, 700)]
    assert sig.effective_sample_size(slow) >= sig.MIN_EFFECTIVE_BOOKS_FOR_DNA
    assert _dna(slow)["enough"] is True
