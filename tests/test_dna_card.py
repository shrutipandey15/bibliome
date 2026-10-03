"""The share card's pure parts (DNA card spec, "Tests"): the bloom, the palettes,
the copy, name fitting, privacy of the payload, and the link-preview image."""

import io
import re

import pytest

from app.services import card_preview, dna_card
from app.services import dna_signals as sig
from app.services.dna_engine import DISCERNING_READER, PERSONALITY_TYPES
from app.services.dna_insights import build_dna

ALL_TYPES = [*PERSONALITY_TYPES, DISCERNING_READER]


# ── The bloom ──

def _petal_count(layer_d: str) -> int:
    return layer_d.count("Z")


def _layer(b, kind):
    for layer in b["layers"]:
        if kind == "ghost" and "stroke" in layer:
            return layer
        if kind == "solid" and layer.get("fill") == 1 and layer["d"].count("Q"):
            return layer
        if kind == "soft" and layer.get("fill") == dna_card.SOFT_OPACITY:
            return layer
        if kind == "dots" and layer.get("fill") == dna_card.DOT_OPACITY:
            return layer
    return None


def test_same_counts_draw_the_same_bloom():
    counts = {"grief": 14, "catharsis": 9, "haunted": 7, "hope": 1}
    assert dna_card.bloom(counts) == dna_card.bloom(dict(reversed(list(counts.items()))))


def test_ghost_outlines_always_draw_all_21():
    for counts in ({}, {"grief": 1}, {s: 3 for s in dna_card.SLUGS}):
        assert _petal_count(_layer(dna_card.bloom(counts), "ghost")["d"]) == 21


def test_one_feeling_is_one_long_petal_and_twenty_dots():
    b = dna_card.bloom({"awe": 5})
    assert _petal_count(_layer(b, "solid")["d"]) == 1
    assert _layer(b, "soft") is None
    assert _petal_count(_layer(b, "dots")["d"]) == 20
    assert [t["slug"] for t in b["top"]] == ["awe"]


def test_every_feeling_evenly_is_a_full_flower_with_three_solid():
    b = dna_card.bloom({s: 4 for s in dna_card.SLUGS})
    assert _petal_count(_layer(b, "solid")["d"]) == 3
    assert _petal_count(_layer(b, "soft")["d"]) == 18
    assert _layer(b, "dots") is None
    # Ties go to vocabulary order.
    assert [t["slug"] for t in b["top"]] == dna_card.SLUGS[:3]


def test_length_is_relative_to_the_readers_own_top_feeling():
    small = dna_card.bloom({"grief": 10, "hope": 5})
    big = dna_card.bloom({"grief": 300, "hope": 150})
    assert small["layers"] == big["layers"]


def test_a_rarely_felt_feeling_still_reaches_the_minimum():
    # 1 of 100 would be a 1% petal; it is drawn at MIN_SHARE instead, which is
    # the same length as any other felt-once feeling on this shelf.
    b = dna_card.bloom({"grief": 100, "catharsis": 90, "haunted": 80, "hope": 1, "joy": 2})
    petals = _layer(b, "soft")["d"].split("Z")[:-1]
    assert len(petals) == 2

    def tip_distance(p):
        nums = [float(x) for x in re.findall(r"-?\d+\.\d", p)]
        # The arc's end point is the tip's far side; distance from centre.
        x, y = nums[8], nums[9]
        return ((x - 500) ** 2 + (y - 500) ** 2) ** 0.5

    expected = dna_card._R0 + (dna_card._R - dna_card._R0) * dna_card.MIN_SHARE
    for p in petals:
        assert abs(tip_distance(p) - expected) < dna_card._W  # within the tip's width


def test_retired_and_unknown_feelings_are_never_drawn_or_counted():
    b = dna_card.bloom({"tenderness": 40, "grief": 2, "made_up": 9})
    assert [t["slug"] for t in b["top"]] == ["grief"]
    assert b["felt"] == 1


def test_no_feelings_is_ghosts_dots_and_centre():
    b = dna_card.bloom({})
    assert b["top"] == []
    assert _petal_count(_layer(b, "dots")["d"]) == 21


# ── Colour ──

def _lum(h):
    r, g, b = (int(h.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def test_every_archetype_has_a_palette():
    assert {t["id"] for t in ALL_TYPES} == set(dna_card.PALETTES)


@pytest.mark.parametrize("type_id", sorted(dna_card.PALETTES))
def test_every_text_colour_meets_4_5_on_every_ground(type_id):
    p = dna_card.palette_for(type_id)
    for ground in (p["top"], p["bottom"]):
        assert _contrast(p["ink"], ground) >= 4.5, ("ink", ground)
        assert _contrast(p["accent"], ground) >= 4.5, ("accent", ground)
    # Season sticker: ground colour on the accent. Red-flag chip: ground on ink.
    assert _contrast(p["bottom"], p["accent"]) >= 4.5
    assert _contrast(p["bottom"], p["ink"]) >= 4.5


# ── Copy ──

# Words the DNA tests already ban, plus anything that compares the reader with
# other readers or counts books still to read.
BANNED = ("reveal", "streak", "%", "percent", "rarest", "than most", "other readers",
          "no two alike", "to read", "tbr")


@pytest.mark.parametrize("t", ALL_TYPES, ids=lambda t: t["id"])
def test_card_lines_are_first_person_short_and_clean(t):
    for line in (t["share_line"], t["red_flag"]):
        assert len(line) < 60, line
        assert re.search(r"\b(I|I'm|I'd|me|my)\b", line, re.I), line
        assert not re.search(r"\byou(r)?\b", line, re.I), line
        for word in BANNED:
            assert word not in line.lower(), (word, line)
    assert t["article"] == ("an" if dna_card.display_name(t["name"])[0] in "AEIOU" else "a")


@pytest.mark.parametrize("t", ALL_TYPES, ids=lambda t: t["id"])
def test_every_name_fits_the_story_at_120px_or_more(t):
    # The story sets the name at 168px and shrinks until its longest line fits
    # 888px. Measured with the same fonts the browser loads.
    first, second = card_preview.name_lines(t["name"])
    lines = [(first, second is None)] + ([(second, True)] if second else [])
    size = card_preview.fit_size(lines, "Newsreader", 168, 100, 888)
    assert size >= 120, (t["id"], size)


# ── The payload ──

def _v2(**over):
    v2 = {
        "enough": True, "archetype": sig.archetype_dict("grief_romantic"),
        "archetype_scores": {}, "margin": 0.1, "runner_up": None, "leaning": None,
        "basis": None, "book_count": 40, "tagged_count": 34,
        "emotion_counts": {"grief": 14, "catharsis": 9, "haunted": 7},
        "profiles": {"current_books": {"grief": 0.5}},
        "season": {"id": "world_diver", "home": False, "since": "2026-08-02", "books": 6,
                   "counts": {"awe": 4, "thrill": 3}},
    }
    v2.update(over)
    return v2


def test_no_card_below_the_gate_or_without_a_name():
    assert dna_card.card_payload_from({"enough": False}) is None
    assert dna_card.card_payload_from(_v2(archetype=None)) is None


def test_switches_leave_season_and_red_flag_off():
    on = dna_card.card_payload_from(_v2())
    assert on["season"]["name"] == "The World-Diver"
    assert on["red_flag"] == "I avoid neat happy endings"
    off = dna_card.card_payload_from(_v2(), show_season=False, show_red_flag=False)
    assert off["season"] is None and off["red_flag"] is None
    # Not smuggled along inside the archetype either.
    assert "red_flag" not in off["archetype"]
    assert "I avoid neat happy endings" not in repr(off)
    spec = dna_card.preview_spec(off, "shruti")
    assert spec["season"] is None
    assert dna_card.preview_hash(spec) != dna_card.preview_hash(dna_card.preview_spec(on, "shruti"))


def test_copy_comes_from_the_live_table_not_the_cache():
    stale = {**sig.archetype_dict("grief_romantic")}
    for k in ("share_line", "red_flag", "article"):
        stale.pop(k)
    card = dna_card.card_payload_from(_v2(archetype=stale))
    assert card["archetype"]["share_line"] == "Loss isn't my enemy. Numbness is."


def test_handle_is_cut_at_twenty():
    assert dna_card.handle_for_card("a" * 20) == "a" * 20
    assert dna_card.handle_for_card("a" * 25) == "a" * 19 + "…"


def test_preview_hash_follows_the_handle():
    card = dna_card.card_payload_from(_v2())
    a = dna_card.preview_hash(dna_card.preview_spec(card, "shruti"))
    b = dna_card.preview_hash(dna_card.preview_spec(card, "shruti_reads"))
    assert a != b


def _sig(emotions, day, status="finished"):
    from datetime import datetime, timezone

    return sig.entry_sig({
        "emotions": emotions, "intensity": 7, "status": status,
        "created_at": datetime(2026, 1, day, tzinfo=timezone.utc),
        "finished_at": None, "arc_start": None, "arc_end": None,
        "dnf_reason": None, "verdict": None, "id": str(day), "title": f"b{day}",
        "updated_at": None,
    })


def test_journal_feelings_never_reach_the_card():
    books = [_sig(["grief", "catharsis"], d) for d in range(1, 13)]
    journal = [_sig(["joy", "swoon", "awe"], d) for d in range(1, 25)]
    v2 = build_dna(books, journal_sigs=journal)
    card = dna_card.card_payload_from(v2)
    drawn = {t["slug"] for t in card["bloom"]["top"]}
    assert drawn <= {"grief", "catharsis"}
    assert card["bloom"]["felt"] == 2
    if card["season"]:
        assert {t["slug"] for t in card["season"]["bloom"]["top"]} <= {"grief", "catharsis"}


def test_the_card_carries_no_titles_notes_or_verdicts():
    books = [_sig(["grief", "catharsis"], d) for d in range(1, 13)]
    card = dna_card.card_payload_from(build_dna(books))
    blob = repr(card)
    for leak in ("b1'", "title", "verdict", "notes", "journal"):
        assert leak not in blob, leak


def test_season_counts_are_the_last_six_tagged_books():
    books = [_sig(["grief"], d) for d in range(1, 9)] + [_sig(["thrill", "dread"], d) for d in range(9, 15)]
    assert sig.recent_counts(books, 6) == {"thrill": 6, "dread": 6}


# ── The link preview ──

@pytest.mark.parametrize("t", ALL_TYPES, ids=lambda t: t["id"])
def test_preview_is_a_jpeg_under_250kb_for_every_archetype(t):
    from PIL import Image

    card = dna_card.card_payload_from(_v2(
        archetype=sig.archetype_dict(t["id"]),
        leaning={"id": "world_diver", "name": "The World-Diver"},
    ))
    jpeg = card_preview.render_jpeg(card, "averyveryverylonghandle")
    assert len(jpeg) <= card_preview.MAX_BYTES
    img = Image.open(io.BytesIO(jpeg))
    assert img.format == "JPEG" and img.size == (1200, 630)


def test_preview_svg_escapes_the_handle():
    card = dna_card.card_payload_from(_v2())
    svg = card_preview.preview_svg(card, '<b>&"x')
    assert "<b>" not in svg and "&lt;b&gt;" in svg


def test_preview_alt_names_the_card_and_its_feelings():
    card = dna_card.card_payload_from(_v2())
    assert dna_card.preview_alt(card, "shruti") == (
        "@shruti's reading DNA card: The Grief Romantic. Most-felt: heartbreak, catharsis, haunted."
    )
