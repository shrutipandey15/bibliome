"""The Discerning Reader and the provisional-baseline hedge (Emotion & DNA rework, v3).

The Discerning Reader is assigned from verdicts, not feelings: a reader for whom
most books don't land has told us something true about themselves, and scoring
the few feelings they do tag would bury it.
"""

from datetime import datetime, timezone

from app.services import dna_signals as S
from app.services.dna_insights import build_dna

NOW = datetime.now(timezone.utc)


def book(emotions, verdict=None, status="finished", intensity=6):
    return S.EntrySig(emotions=list(emotions), intensity=intensity, ts=NOW,
                      status=status, verdict=verdict)


def test_mostly_disappointed_reader_is_discerning():
    sigs = ([book(["grief"], "not_for_me") for _ in range(5)]
            + [book([], status="abandoned") for _ in range(2)]
            + [book(["grief", "catharsis"], "loved") for _ in range(2)])
    share, judged = S.discerning_share(sigs)
    assert judged == 9 and share >= S.DISCERNING_SHARE
    best, _, _ = S.classify_reader(sigs, S.frequency_vector(sigs, weighted=False))
    assert best == "discerning_reader"


def test_ordinary_reader_is_not_discerning():
    sigs = ([book(["swoon", "joy"], "loved") for _ in range(6)]
            + [book(["swoon"], "mixed") for _ in range(2)]
            + [book([], status="abandoned")])
    assert not S.is_discerning(sigs)
    best, _, _ = S.classify_reader(sigs, S.frequency_vector(sigs, weighted=False))
    assert best == "sunshine_romantic"


def test_mixed_counts_half_and_paused_is_not_a_judgement():
    # 8 mixed = 4.0 of 8 = 50%: below the 60% line.
    sigs = [book(["awe"], "mixed") for _ in range(8)]
    sigs += [book([], status="paused") for _ in range(10)]
    share, judged = S.discerning_share(sigs)
    assert judged == 8 and share == 0.5
    assert not S.is_discerning(sigs)


def test_too_few_judgements_never_labels_discerning():
    sigs = [book(["grief"], "not_for_me") for _ in range(S.DISCERNING_MIN_BOOKS - 1)]
    assert not S.is_discerning(sigs)


def test_old_read_again_answers_map_forward_on_the_way_in():
    raw = {"emotions": ["grief"], "intensity": 5, "finished_at": None,
           "created_at": NOW, "status": "finished"}
    assert S.entry_sig({**raw, "verdict": "no"}).verdict == "not_for_me"
    assert S.entry_sig({**raw, "verdict": "yes"}).verdict == "liked"
    assert S.entry_sig({**raw, "verdict": "not_sure"}).verdict == "mixed"
    assert S.entry_sig({**raw, "verdict": "loved"}).verdict == "loved"


def test_build_dna_names_the_discerning_reader_with_a_verdict_receipt():
    sigs = ([book(["grief"], "not_for_me") for _ in range(6)]
            + [book(["awe"], "mixed") for _ in range(2)]
            + [book(["beauty"], "loved") for _ in range(1)])
    res = build_dna(sigs)
    assert res["archetype"]["id"] == "discerning_reader"
    assert res["runner_up"] is None        # never hedged against a feeling type
    verdicts = {r["verdict"]: r["books"] for r in res["basis"]["verdicts"]}
    assert verdicts == {"not_for_me": 6, "mixed": 2, "loved": 1}
    assert res["basis"]["counts"] == []


def test_journal_days_never_count_toward_discerning():
    books = [book(["joy"], "loved") for _ in range(6)]
    journal = [S.EntrySig(emotions=["grief"], intensity=5, ts=NOW, status="finished",
                          source="journal", verdict="not_for_me") for _ in range(20)]
    assert not S.is_discerning(books + journal)


def test_leaning_is_named_only_when_a_rival_is_close(monkeypatch):
    """The old provisional hedge named a runner-up for everyone. The leaning line
    names one only when a rival has caught up — a decisive shelf stands alone,
    and the gap is looser while the baseline is still a guess."""
    sigs = [book(["thrill", "dread", "shock"], "loved") for _ in range(8)]
    for provisional in (True, False):
        monkeypatch.setattr(S, "BASELINE_PROVISIONAL", provisional)
        res = build_dna(sigs)
        assert res["archetype"]["id"] == "adrenaline_seeker"
        assert res["margin"] >= S.TIPPING_GAP_PROVISIONAL
        assert res["runner_up"] is None and res["leaning"] is None
    monkeypatch.setattr(S, "BASELINE_PROVISIONAL", True)
    close = build_dna([book(["grief", "longing"], "liked") for _ in range(10)])
    assert close["leaning"] and close["runner_up"] == close["leaning"]["name"]
