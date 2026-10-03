"""Aliveness probe: is the climate steady, does it still follow real change, and
are the moments rare enough to mean something?

Simulated readers (half with focused taste, half mixed; 40% change taste partway
through) log 60 books, one every 5–30 days. Every number comes from the real
replay in app.services.dna_signals — the same code the DNA page runs.

Run:  python -m scripts.dna_alive_probe
Exit code 1 when any gate fails. The gates are the ones the DNA Aliveness spec
signed off; change a CLIMATE_*/SEASON_*/FIRST_* constant and this tells you
whether the experience it promises still holds.

No DB, no I/O.
"""

import random
import statistics as st
import sys
from datetime import datetime, timedelta, timezone

from app.services.dna_signals import (
    EntrySig,
    book_archetype_of,
    firsts,
    replay_climate,
    replay_season,
)

# What a typical book of each kind makes people feel (simulation only).
BUNDLES = {
    "romantasy_dark": ["desire", "conflicted", "awe", "shock", "thrill"],
    "romantasy_soft": ["swoon", "awe", "joy", "attachment"],
    "romcom": ["swoon", "amusement", "joy"],
    "dark_romance": ["desire", "conflicted", "rage", "dread"],
    "angsty_romance": ["longing", "desire", "grief", "attachment"],
    "grief_litfic": ["grief", "catharsis", "haunted", "beauty"],
    "quiet_litfic": ["recognition", "beauty", "nostalgia", "longing"],
    "memoir": ["recognition", "catharsis", "hope", "insight"],
    "cozy": ["comfort", "attachment", "joy", "hope"],
    "comic_novel": ["amusement", "joy", "insight"],
    "satire": ["amusement", "rage", "insight", "shock"],
    "thriller": ["thrill", "dread", "shock"],
    "horror": ["dread", "haunted", "shock", "thrill"],
    "epic_fantasy": ["awe", "thrill", "attachment", "grief"],
    "scifi_ideas": ["awe", "insight", "dread"],
    "pop_nonfiction": ["insight", "awe", "amusement"],
    "literary_classic": ["beauty", "insight", "recognition", "grief"],
    "ya_coming_of_age": ["nostalgia", "attachment", "hope", "joy"],
    "injustice_novel": ["rage", "grief", "catharsis", "insight"],
}
KEYS = list(BUNDLES)
T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)

# Gates (spec: Build order, tests and success).
MAX_STEADY_CHANGES_PER_100 = 7.0     # label changes for readers whose taste didn't change
MIN_DRIFTERS_RELABELLED = 0.80       # taste changers who reach their new archetype (measured ~0.83)
MAX_FIRSTS_SHARE = 0.15              # share of saves with a first
MIN_BOOKS_PER_SEASON = 4.0           # a season shouldn't turn every couple of books


def reader(rng: random.Random, concentration: float, changes: bool, n: int = 60):
    def taste():
        return [rng.gammavariate(concentration, 1) for _ in KEYS]
    before, after = taste(), (taste() if changes else None)
    switch = rng.randint(20, 35) if changes else None
    books, ts = [], T0
    for i in range(n):
        kind = rng.choices(KEYS, weights=after if (changes and i >= switch) else before)[0]
        feelings = [e for e in BUNDLES[kind] if rng.random() < 0.7][:3] or [BUNDLES[kind][0]]
        ts += timedelta(days=rng.randint(5, 30))
        books.append(EntrySig(emotions=feelings, intensity=rng.randint(3, 10), ts=ts,
                              status="finished", verdict="liked", entry_id=f"b{i:03d}"))
    return books, switch


def main(n_readers: int = 1000, seed: int = 11) -> int:
    rng = random.Random(seed)
    steady, relabelled, still, firsts_share, books_per_season = [], [], [], [], []
    for r in range(n_readers):
        changes = r % 5 < 2                      # 40% change taste
        books, switch = reader(rng, 0.35 if r % 2 else 1.0, changes)
        eras = replay_climate(books)["eras"]
        steps = len(books) - 4
        if changes:
            # Reached: the label was the new taste's archetype at some point after
            # the change (it may later move on — taste keeps moving).
            target = book_archetype_of(books[switch:])
            relabelled.append(any(e["id"] == target and (e["to"] is None or e["to"] > books[switch].ts)
                                  for e in eras))
            still.append(eras[-1]["id"] == target)
        else:
            steady.append((len(eras) - 1) / steps * 100)
        firsts_share.append(len({m["entry_id"] for m in firsts(books)}) / steps)
        seasons = replay_season(books)["seasons"]
        books_per_season.append((len(books) - 9) / max(len(seasons), 1))

    results = [
        ("label changes per 100 books, steady taste", st.mean(steady), "<=", MAX_STEADY_CHANGES_PER_100),
        ("taste changers relabelled", sum(relabelled) / len(relabelled), ">=", MIN_DRIFTERS_RELABELLED),
        ("saves with a first", st.mean(firsts_share), "<=", MAX_FIRSTS_SHARE),
        ("books per season", st.mean(books_per_season), ">=", MIN_BOOKS_PER_SEASON),
    ]
    print(f"info  taste changers whose label is the new archetype at the end: {sum(still) / len(still):.3f}")
    failed = False
    for name, value, op, gate in results:
        ok = value <= gate if op == "<=" else value >= gate
        failed |= not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {value:.3f} (gate {op} {gate})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
