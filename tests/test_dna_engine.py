"""DNA engine correctness tests.

The invariant test here (``test_personality_slugs_are_canonical``) is the one
that would have caught P0-1 / B1.1: the engine scoring against a dead emotion
vocabulary. Keep it green.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import dna_engine
from app.services.dna_engine import (
    BIBLE_DNA_SLUGS,
    DNA_TYPE_SLUG_MAP,
    PERSONALITY_TYPES,
    calculate_personality,
    generate_stats,
    build_heatmap_data,
)
from app.utils.emotions import VALID_SLUGS, LEGACY_EMOTION_MAP, LOST_ME_SLUGS


def _entry(emotions, intensity=5, days_ago=0, title="Book", author="Author"):
    return {
        "id": f"e-{title}-{days_ago}",
        "title": title,
        "author": author,
        "emotions": emotions,
        "intensity": intensity,
        "created_at": datetime.now(timezone.utc) - timedelta(days=days_ago),
    }


# ── The invariant (B1.20 / P0-1) ──

def test_personality_slugs_are_canonical():
    """Every emotion referenced by a personality type must be a canonical slug.

    This is the guard against the vocabulary-cutover regression: a slug that is
    not in VALID_SLUGS can never appear on a new entry and silently scores 0.
    """
    for ptype in PERSONALITY_TYPES:
        for slug in ptype["primary_emotions"] + ptype["anti_emotions"]:
            assert slug in VALID_SLUGS, (
                f"{ptype['id']} references non-canonical emotion {slug!r}"
            )


def test_every_experiential_emotion_is_used_somewhere():
    """Every *experiential* emotion anchors at least one archetype.

    The "It lost me" family (boredom/revulsion/confusion/indifference) is excluded:
    those are registers of disengagement — a book failing you, not a reading
    identity — so they only ever appear as anti_emotions, never as a primary.
    """
    used = {
        slug
        for ptype in PERSONALITY_TYPES
        for slug in ptype["primary_emotions"]
    }
    experiential = VALID_SLUGS - LOST_ME_SLUGS
    missing = experiential - used
    assert not missing, f"experiential emotions never used as a primary: {sorted(missing)}"


def test_type_slug_map_targets_bible_slugs():
    assert set(DNA_TYPE_SLUG_MAP.values()) == BIBLE_DNA_SLUGS
    for ptype in PERSONALITY_TYPES:
        assert ptype["id"] in DNA_TYPE_SLUG_MAP


# ── calculate_personality behaviour ──

def test_below_three_entries_returns_no_personality():
    result = calculate_personality([_entry(["grief"]), _entry(["rage"])])
    assert result["personality"] is None


def test_output_frequency_keys_are_canonical():
    entries = [_entry(["chaos", "awe"], days_ago=i) for i in range(5)]
    result = calculate_personality(entries)
    assert set(result["emotion_frequency"]).issubset(VALID_SLUGS)


def test_strong_signal_selects_expected_type():
    # amusement + awe + rage is the midnight_arsonist fingerprint.
    entries = [
        _entry(["amusement", "awe", "rage"], intensity=9, days_ago=i, title=f"B{i}")
        for i in range(6)
    ]
    result = calculate_personality(entries)
    assert result["personality"]["id"] == "midnight_arsonist"
    assert result["dna_type_slug"] == DNA_TYPE_SLUG_MAP["midnight_arsonist"]


# ── canonicalize-on-read (legacy rows still count) ──

def test_legacy_slugs_are_remapped_not_dropped():
    # Pre-cutover slugs must be counted under their canonical name, not ignored.
    legacy = list(LEGACY_EMOTION_MAP.items())
    entries = [_entry([old], days_ago=i, title=f"L{i}") for i, (old, _) in enumerate(legacy * 2)]
    result = calculate_personality(entries)
    freq = result["emotion_frequency"]
    # No legacy key should survive in the output.
    for old, canon in LEGACY_EMOTION_MAP.items():
        assert old not in freq
        assert canon in freq


def test_retired_slugs_canonicalize_forward():
    """The 13→18 cutover retired chaos/wit/two_am; they must map forward, not drop."""
    from app.utils.emotions import canonicalize
    assert canonicalize("chaos") == "confusion"
    assert canonicalize("wit") == "amusement"
    assert canonicalize("two_am") == "longing"
    assert canonicalize("2am") == "longing"
    # nostalgia was a legacy alias; it is now canonical in its own right.
    assert canonicalize("nostalgia") == "nostalgia"


def test_unknown_slugs_are_ignored():
    entries = [_entry(["not_a_real_emotion", "grief"], days_ago=i) for i in range(4)]
    result = calculate_personality(entries)
    assert "not_a_real_emotion" not in result["emotion_frequency"]
    assert "grief" in result["emotion_frequency"]


# ── stats / heatmap also canonicalize ──

def test_generate_stats_counts_legacy_slugs():
    # '2am' is retired vocab; it canonicalizes forward to 'longing'.
    entries = [_entry(["2am"], days_ago=i) for i in range(3)]
    stats = generate_stats(entries)
    assert stats["most_common_emotion"] == "longing"
    assert stats["total_books"] == 3


def test_heatmap_cells_are_canonical():
    entries = [_entry(["healing", "grief"])]
    heatmap = build_heatmap_data(entries)
    cell_emotions = {c["emotion_id"] for c in heatmap["cells"]}
    assert "healing" not in cell_emotions
    assert "catharsis" in cell_emotions  # healing -> catharsis
    assert cell_emotions.issubset(VALID_SLUGS)
