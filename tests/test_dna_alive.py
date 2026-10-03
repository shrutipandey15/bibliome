"""The aliveness layer (DNA Aliveness spec): a slow climate, a reading season,
firsts, the leaning line and the after-save echo. Pure functions, no DB."""

import random
from datetime import datetime, timedelta, timezone

import pytest

from app.services import dna_signals as S
from app.services.dna_insights import build_dna

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)

GRIEF = ["grief", "catharsis", "haunted"]          # Grief Romantic's primaries
PULSE = ["thrill", "dread", "shock"]               # Adrenaline Seeker's
SWOON = ["swoon", "joy", "amusement"]              # Sunshine Romantic's


def shelf(*runs, start=0, gap_days=10, intensity=6, verdict="liked"):
    """Books in reading order: shelf((GRIEF, 6), (PULSE, 3)) = 6 grief then 3 pulse."""
    out, i = [], start
    for feelings, n in runs:
        for _ in range(n):
            out.append(S.EntrySig(emotions=list(feelings), intensity=intensity,
                                  ts=T0 + timedelta(days=gap_days * i), status="finished",
                                  verdict=verdict, entry_id=f"e{i:04d}", title=f"Book {i}"))
            i += 1
    return out


def climate(sigs, journal=None):
    return S.replay_climate(sigs, journal)["archetype_id"]


# ── The replay is a pure function of the shelf ──

def test_replay_ignores_insertion_order():
    books = shelf((GRIEF, 8), (PULSE, 6), (SWOON, 7), (GRIEF, 3))
    shuffled = books[:]
    random.Random(4).shuffle(shuffled)
    assert S.dna_state(books) == S.dna_state(shuffled)


def test_incremental_climate_matches_direct_scoring():
    rng = random.Random(9)
    kinds = [GRIEF, PULSE, SWOON, ["awe", "insight"], ["comfort", "hope"]]
    books = []
    for i in range(40):
        books.append(S.EntrySig(emotions=rng.choice(kinds), intensity=rng.randint(3, 10),
                                ts=T0 + timedelta(days=7 * i), status="finished",
                                entry_id=f"e{i:04d}"))
    replayed = S.replay_climate(books)["scores"]
    direct = S.score_archetype(S.frequency_vector(
        books, weighted=True, intensity_weighted=True, now=books[-1].ts,
        half_life=S.CLIMATE_HALF_LIFE_DAYS))[1]
    assert replayed == pytest.approx(direct, abs=1e-9)


# ── The climate moves only on a clear, repeated lead ──

def test_one_odd_book_never_flips_the_label():
    books = shelf((GRIEF, 12)) + shelf((PULSE, 1), start=12, intensity=10)
    assert climate(books) == "grief_romantic"


def test_label_switches_on_the_second_save_past_the_margin():
    books = shelf((GRIEF, 6), (PULSE, 14))
    qualifying = []
    for k in range(S.MIN_BOOKS_FOR_DNA, len(books) + 1):
        prefix = books[:k]
        best, scores, _ = S.score_archetype(S.frequency_vector(
            prefix, weighted=True, intensity_weighted=True, now=prefix[-1].ts,
            half_life=S.CLIMATE_HALF_LIFE_DAYS))
        qualifying.append(best == "adrenaline_seeker"
                          and scores[best] - scores["grief_romantic"] >= S.ARCHETYPE_SWITCH_MARGIN)
    first = qualifying.index(True)
    assert qualifying[first + 1], "fixture should keep qualifying"
    at = S.MIN_BOOKS_FOR_DNA + first          # shelf size at the first qualifying save
    assert climate(books[:at]) == "grief_romantic"
    assert climate(books[:at + 1]) == "adrenaline_seeker"
    eras = S.replay_climate(books)["eras"]
    assert [e["id"] for e in eras] == ["grief_romantic", "adrenaline_seeker"]
    assert eras[0]["to"] == eras[1]["from"] and eras[1]["to"] is None


def test_discerning_reader_leaves_only_below_the_exit_share():
    # 8 judged, 5 missed = 62.5%: in. One more liked book: 5/9 = 55.6%, still in
    # (exit is below 55%). Two more: 5/10 = 50%, out.
    books = shelf((GRIEF, 5), verdict="not_for_me") + shelf((GRIEF, 3), start=5)
    assert climate(books) == "discerning_reader"
    assert climate(books + shelf((GRIEF, 1), start=8)) == "discerning_reader"
    assert climate(books + shelf((GRIEF, 2), start=8)) == "grief_romantic"


def test_journal_days_count_toward_the_next_book_only():
    books = shelf((GRIEF, 6))
    late = [S.EntrySig(emotions=PULSE, intensity=9, ts=books[-1].ts + timedelta(days=1),
                       status="finished", source="journal") for _ in range(20)]
    assert S.replay_climate(books, late)["scores"] == S.replay_climate(books)["scores"]


# ── The season ──

def test_no_season_before_ten_books():
    assert S.replay_season(shelf((GRIEF, S.SEASON_MIN_BOOKS - 1)))["season_id"] is None
    assert S.replay_season(shelf((GRIEF, S.SEASON_MIN_BOOKS)))["season_id"] == "grief_romantic"


def test_season_turns_only_when_a_new_winner_holds_for_two_saves():
    base = shelf((GRIEF, 10))
    # Enough swoon books to take the 6-book window: the new winner must hold.
    states = [S.replay_season(base + shelf((SWOON, n), start=10))["season_id"] for n in range(1, 7)]
    first_win = next(i for i, n in enumerate(range(1, 7))
                     if S.book_archetype_of((base + shelf((SWOON, n), start=10))[-S.SEASON_WINDOW:])
                     == "sunshine_romantic")
    assert states[first_win] == "grief_romantic"
    assert states[first_win + 1] == "sunshine_romantic"


def test_build_dna_reports_season_against_the_climate():
    res = build_dna(shelf((GRIEF, 14), (SWOON, 5)))
    assert res["archetype"]["id"] == "grief_romantic"
    assert res["season"]["id"] == "sunshine_romantic"
    assert res["season"]["home"] is False
    assert res["seasons"][0]["to"] is None            # newest first
    home = build_dna(shelf((GRIEF, 12)))
    assert home["season"]["home"] is True


# ── Firsts ──

def test_blind_spot_broken_only_after_fifteen_books():
    early = shelf((GRIEF, 10), (["nostalgia"], 1))
    assert not [m for m in S.firsts(early) if m["kind"] == "first"]
    late = shelf((GRIEF, 15), (["nostalgia"], 1))
    [m] = [m for m in S.firsts(late) if m["kind"] == "first"]
    assert m["emotion"] == "nostalgia" and m["entry_id"] == "e0015" and m["title"] == "Book 15"


def test_feeling_returns_after_twenty_books_away():
    books = shelf((["nostalgia", "grief"], 2), (GRIEF, 20), (["nostalgia"], 1))
    [m] = [m for m in S.firsts(books) if m["kind"] == "return"]
    assert m["emotion"] == "nostalgia" and m["gap"] == 20
    short = shelf((["nostalgia", "grief"], 2), (GRIEF, 19), (["nostalgia"], 1))
    assert not [m for m in S.firsts(short) if m["kind"] == "return"]


def test_spectrum_moment_when_every_feeling_has_been_felt():
    slugs = [e for e in S._ALL_SLUGS]
    books = shelf(*[([s], 1) for s in slugs])
    assert [m["kind"] for m in S.firsts(books)].count("spectrum") == 1


# ── Leaning ──

def test_leaning_only_when_a_rival_is_close():
    scores = {t: 0.0 for t in S._TYPE_ORDER}
    scores.update(grief_romantic=0.10, world_diver=0.095, quiet_witness=0.02)
    assert S.leaning("grief_romantic", scores) == "world_diver"
    scores["world_diver"] = 0.05
    assert S.leaning("grief_romantic", scores) is None
    assert S.leaning("discerning_reader", scores) is None


def test_leaning_gap_is_looser_while_the_baseline_is_provisional(monkeypatch):
    scores = {t: 0.0 for t in S._TYPE_ORDER}
    scores.update(grief_romantic=0.10, world_diver=0.085)
    monkeypatch.setattr(S, "BASELINE_PROVISIONAL", True)
    assert S.leaning("grief_romantic", scores) == "world_diver"
    monkeypatch.setattr(S, "BASELINE_PROVISIONAL", False)
    assert S.leaning("grief_romantic", scores) is None


# ── The echo ──

def test_echo_deepened_when_the_book_matches_the_archetype():
    books = shelf((GRIEF, 8))
    echo = S.echo_for("e0007", books)
    assert echo["book_type"] == "grief_romantic" and echo["relation"] == "deepened"


def test_echo_pulled_when_the_book_points_elsewhere():
    books = shelf((GRIEF, 8), (SWOON, 1))
    echo = S.echo_for("e0008", books)
    assert echo["book_type"] == "sunshine_romantic"
    assert echo["relation"] == "pulled"


def test_echo_below_the_gate_only_names_the_book():
    echo = S.echo_for("e0001", shelf((PULSE, 3)))
    assert echo["relation"] == "reads_like" and echo["book_type"] == "adrenaline_seeker"


def test_echo_reports_the_shift_it_caused():
    books = shelf((GRIEF, 6), (PULSE, 14))
    eras = S.replay_climate(books)["eras"]
    switch_at = eras[1]["from"]
    trigger = next(b for b in books if b.ts == switch_at)
    upto = [b for b in books if b.ts <= switch_at]
    shown = S.compact_state(S.dna_state([b for b in upto if b is not trigger]))
    echo = S.echo_for(trigger.entry_id, upto, prev=shown)
    assert echo["extra"] == {"kind": "shift", "from": "grief_romantic", "to": "adrenaline_seeker"}


def test_echo_reports_a_first():
    books = shelf((GRIEF, 15), (["nostalgia", "grief"], 1))
    shown = S.compact_state(S.dna_state(books[:-1]))
    echo = S.echo_for("e0015", books, prev=shown)
    assert echo["extra"]["kind"] == "first" and echo["extra"]["emotion"] == "nostalgia"


def test_re_saving_a_book_announces_nothing_it_already_said():
    """An edit is measured against what the reader was last shown, not against
    the shelf without the book — or every save re-announces its old first."""
    books = shelf((GRIEF, 15), (["nostalgia", "grief"], 1))
    shown_after_first_save = S.compact_state(S.dna_state(books))
    echo = S.echo_for("e0015", books, prev=shown_after_first_save)
    assert echo["extra"] is None


def test_without_a_previous_state_there_is_no_extra():
    books = shelf((GRIEF, 15), (["nostalgia", "grief"], 1))
    assert S.echo_for("e0015", books)["extra"] is None


def test_a_first_season_is_not_announced_as_a_turn():
    books = shelf((GRIEF, S.SEASON_MIN_BOOKS))
    shown = S.compact_state(S.dna_state(books[:-1]))       # no season yet
    echo = S.echo_for(books[-1].entry_id, books, prev=shown)
    assert echo["extra"] is None or echo["extra"]["kind"] != "season"


def test_an_absurd_date_cannot_take_the_dna_down():
    books = shelf((GRIEF, 6))
    books.append(S.EntrySig(emotions=PULSE, intensity=6, ts=datetime(9999, 12, 31, tzinfo=timezone.utc),
                            status="finished", entry_id="typo"))
    books.append(S.EntrySig(emotions=PULSE, intensity=6, ts=datetime(1, 1, 2, tzinfo=timezone.utc),
                            status="finished", entry_id="ancient"))
    assert S.dna_state(books)["archetype_id"]


def test_untagged_put_downs_after_the_last_tagged_book_still_count():
    books = shelf((GRIEF, 6))
    for i in range(10):
        books.append(S.EntrySig(emotions=[], intensity=5, ts=T0 + timedelta(days=200 + i),
                                status="abandoned", entry_id=f"dnf{i}"))
    assert S.is_discerning(books)
    assert climate(books) == "discerning_reader"


def test_no_echo_without_feelings_or_for_an_unknown_book():
    books = shelf((GRIEF, 6)) + [S.EntrySig(emotions=[], intensity=5, ts=T0 + timedelta(days=99),
                                            status="finished", entry_id="bare")]
    assert S.echo_for("bare", books) is None
    assert S.echo_for("nope", books) is None
    assert S.echo_for(None, books) is None


def test_build_dna_carries_the_echo_with_names():
    res = build_dna(shelf((GRIEF, 8), (SWOON, 1)), echo_entry_id="e0008")
    assert res["echo"]["book_type"]["name"] == "The Sunshine Romantic"
    below = build_dna(shelf((GRIEF, 2)), echo_entry_id="e0001")
    assert below["enough"] is False and below["echo"]["relation"] == "reads_like"


def test_the_alive_probe_gates_hold():
    """The experience the spec promises, on 1,000 simulated readers: a steady
    label, real change still followed, moments rare, seasons not jumpy."""
    from scripts.dna_alive_probe import main
    assert main() == 0
