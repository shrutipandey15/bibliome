"""The shareable DNA card: its bloom, its colours and the one payload every card
surface draws from (DNA card spec).

Everything here is pure: no I/O, no clock except the year in the header. The
browser's canvas renderer, the in-app card and the server's link preview all
draw the bloom from the SAME path strings computed here, so the three can never
disagree about a reader's shape.
"""

import hashlib
import json
import math
from datetime import datetime, timezone

from app.services import dna_signals as sig
from app.utils.emotions import EMOTIONS

# Bump when anything the link preview draws changes (layout, type, palette), so
# every stored preview is re-rendered under a new URL rather than served stale.
RENDER_REV = "card-1"

# Cream on every ground. Each palette below is checked against it, and against
# its own accent, for 4.5:1 (tests/test_dna_card.py) — on the lighter `top`
# stop, which is the worst case for light text.
INK = "#F6EEDF"

# The archetype's ground (a radial gradient from `top` at the centre to
# `bottom` at the edges) and its accent: the second line of the name, the
# labels, the season sticker. Derived from each archetype's own colour, darkened
# until cream text clears 4.5:1 — the Sunshine Romantic's first mockup ground
# (#B8644A) did not, so its ground here is deeper than the canvas showed.
PALETTES: dict[str, dict[str, str]] = {
    "grief_romantic": {"top": "#22343E", "bottom": "#121C22", "accent": "#9FC3D4"},
    "control_intellectual": {"top": "#2E2E50", "bottom": "#16162A", "accent": "#BFC0F2"},
    "soft_masochist": {"top": "#4A2140", "bottom": "#220E1D", "accent": "#F2AEDB"},
    "comfort_architect": {"top": "#33402B", "bottom": "#171F13", "accent": "#C9DEB5"},
    "midnight_arsonist": {"top": "#5A3014", "bottom": "#2A1408", "accent": "#FFBE80"},
    "quiet_witness": {"top": "#45391B", "bottom": "#211A0A", "accent": "#EFD49C"},
    "obsessive_romantic": {"top": "#5A1A2C", "bottom": "#2A0A13", "accent": "#FFA8BC"},
    "emotional_archaeologist": {"top": "#3B2B54", "bottom": "#1A1226", "accent": "#D5BCF4"},
    "world_diver": {"top": "#1E5260", "bottom": "#0D272E", "accent": "#A9E2E6"},
    "adrenaline_seeker": {"top": "#5E2222", "bottom": "#2A0E0E", "accent": "#FF9F8C"},
    "sunshine_romantic": {"top": "#8E4A34", "bottom": "#4E2418", "accent": "#FFD9B8"},
    "discerning_reader": {"top": "#3A3A3A", "bottom": "#1A1A1A", "accent": "#D8D8D8"},
}
_FALLBACK_PALETTE = PALETTES["discerning_reader"]


def palette_for(type_id: str | None) -> dict[str, str]:
    return {**PALETTES.get(type_id or "", _FALLBACK_PALETTE), "ink": INK}


# ── The bloom ──
#
# One petal per feeling at a fixed angle (vocabulary order, clockwise from 12
# o'clock, so families sit together and a reader's blooms stay comparable with
# their own past ones). Length is LINEAR in books against the reader's own top
# feeling, with any felt feeling at least MIN_SHARE of the way out: square-root
# scaling made most 30-book shelves a near-full circle (a 30-book reader has
# felt a median of 18 of the 21). The top three are solid and match the three
# numbers under the name; the rest are soft. A feeling never felt is a dot.
# Faint outlines of all 21 sit behind, so a sparse shelf is still a flower.

BLOOM_SIZE = 1000
SLUGS = [e["slug"] for e in EMOTIONS]
LABELS = {e["slug"]: e["name"] for e in EMOTIONS}

_C = BLOOM_SIZE / 2
_R0 = 0.055 * BLOOM_SIZE          # where every petal starts
_R = 0.48 * BLOOM_SIZE            # a full-length petal's tip
_W = 0.052 * BLOOM_SIZE           # a petal's half-width at its widest
_TIP = 0.55                       # tip radius, as a share of the half-width
_DOT_AT = 0.13 * BLOOM_SIZE       # never-felt dots sit on this ring
_DOT_R = 7.0
_CENTRE_R = 0.03 * BLOOM_SIZE
MIN_SHARE = 0.18
TOP_N = 3

# Opacities, in the payload so every renderer uses the same ones.
GHOST_OPACITY = 0.08
GHOST_WIDTH = 3.0
SOFT_OPACITY = 0.4
DOT_OPACITY = 0.35


def _f(x: float) -> str:
    """One decimal, no '-0.0'. Path strings must be identical wherever they are
    computed, so the formatting is part of the contract."""
    v = round(x, 1)
    return f"{0.0 if v == 0 else v:.1f}"


def _angle(i: int) -> float:
    return -math.pi / 2 + 2 * math.pi * i / len(SLUGS)


def _petal(i: int, length: float) -> str:
    a = _angle(i)
    px, py = math.cos(a), math.sin(a)
    nx, ny = -py, px
    w, k = _W, _TIP
    p0 = (_C + px * _R0, _C + py * _R0)
    tip = (_C + px * length, _C + py * length)
    mid = _R0 + (length - _R0) * 0.62
    c1 = (_C + px * mid + nx * w, _C + py * mid + ny * w)
    c2 = (_C + px * mid - nx * w, _C + py * mid - ny * w)
    t1 = (tip[0] + nx * w * k, tip[1] + ny * w * k)
    t2 = (tip[0] - nx * w * k, tip[1] - ny * w * k)
    return (f"M{_f(p0[0])} {_f(p0[1])}"
            f"Q{_f(c1[0])} {_f(c1[1])} {_f(t1[0])} {_f(t1[1])}"
            f"A{_f(w * k)} {_f(w * k)} 0 0 0 {_f(t2[0])} {_f(t2[1])}"
            f"Q{_f(c2[0])} {_f(c2[1])} {_f(p0[0])} {_f(p0[1])}Z")


def _circle(cx: float, cy: float, r: float) -> str:
    return (f"M{_f(cx - r)} {_f(cy)}"
            f"A{_f(r)} {_f(r)} 0 1 0 {_f(cx + r)} {_f(cy)}"
            f"A{_f(r)} {_f(r)} 0 1 0 {_f(cx - r)} {_f(cy)}Z")


def top_feelings(counts: dict[str, int], n: int = TOP_N) -> list[dict]:
    """The n most-felt feelings, ties to vocabulary order. Retired or unknown
    slugs are never counted."""
    order = {s: i for i, s in enumerate(SLUGS)}
    felt = [(s, c) for s, c in (counts or {}).items() if s in order and c > 0]
    felt.sort(key=lambda kv: (-kv[1], order[kv[0]]))
    return [{"slug": s, "label": LABELS[s], "count": c} for s, c in felt[:n]]


def bloom(counts: dict[str, int] | None) -> dict:
    """The bloom as layers of SVG path data in a BLOOM_SIZE square.

    Each layer is one path drawn in the ink colour: `fill` is an opacity, or a
    `stroke` opacity and `width` for the outlines. Clients draw the layers in
    order and need no geometry of their own.
    """
    counts = {s: c for s, c in (counts or {}).items() if s in LABELS and c > 0}
    peak = max(counts.values(), default=0)
    top = top_feelings(counts)
    top_slugs = {t["slug"] for t in top}

    ghost, soft, solid, dots = [], [], [], []
    for i, slug in enumerate(SLUGS):
        ghost.append(_petal(i, _R))
        c = counts.get(slug, 0)
        if c == 0:
            a = _angle(i)
            dots.append(_circle(_C + math.cos(a) * _DOT_AT, _C + math.sin(a) * _DOT_AT, _DOT_R))
            continue
        share = max(MIN_SHARE, c / peak)
        (solid if slug in top_slugs else soft).append(_petal(i, _R0 + (_R - _R0) * share))

    layers = [{"d": "".join(ghost), "stroke": GHOST_OPACITY, "width": GHOST_WIDTH}]
    if soft:
        layers.append({"d": "".join(soft), "fill": SOFT_OPACITY})
    if solid:
        layers.append({"d": "".join(solid), "fill": 1})
    if dots:
        layers.append({"d": "".join(dots), "fill": DOT_OPACITY})
    layers.append({"d": _circle(_C, _C, _CENTRE_R), "fill": 1})
    return {"size": BLOOM_SIZE, "layers": layers, "top": top, "felt": len(counts)}


def bloom_alt(top: list[dict]) -> str:
    names = [t["label"] for t in top]
    if not names:
        return "A bloom of petals, one per feeling."
    if len(names) == 1:
        most = names[0]
    else:
        most = ", ".join(names[:-1]) + " and " + names[-1]
    return f"A bloom of petals, one per feeling, longest for {most}."


def display_name(name: str) -> str:
    """"The Grief Romantic" → "Grief Romantic": the card says "I'm a" above it."""
    return name[4:] if name.startswith("The ") else name


def _archetype(type_id: str, cached: dict) -> dict:
    """The live table entry for an archetype, so copy edits reach every card
    without a recompute; the cached dict only if the id is no longer known."""
    try:
        return sig.archetype_dict(type_id)
    except KeyError:
        return cached


def card_payload_from(v2: dict | None, *, show_season: bool = True, show_red_flag: bool = True,
                      choices: dict | None = None) -> dict | None:
    """The card, from a reader's cached DNA payload. None when there is none.

    Only what the books-only card has always carried, plus what is drawn from
    it: the bloom (from `emotion_counts`), the season (from the last six
    books), the archetype's own two lines. Never titles, notes, verdicts or
    anything from the journal.
    """
    if not v2 or not v2.get("enough") or not v2.get("archetype"):
        return None
    live = _archetype(v2["archetype"]["id"], v2["archetype"])
    # The red flag travels once, at the top level, and only when it's on: a
    # switched-off line must not ride along inside the archetype either.
    a = {k: v for k, v in live.items() if k != "red_flag"}
    counts = v2.get("emotion_counts") or {}
    card = {
        "archetype": a,
        "archetype_scores": v2["archetype_scores"],
        "margin": v2.get("margin"),
        # The hedge travels with the label on every format (see DNAView).
        "runner_up": v2.get("runner_up"),
        "leaning": v2.get("leaning"),
        "basis": v2.get("basis"),
        "book_count": v2["book_count"],
        # The header's number: tagged books, not the shelf ("34 books").
        "tagged_count": v2.get("tagged_count", v2["book_count"]),
        "emotion_counts": counts or None,
        # Books only — see the note on `current_books` in dna_insights.
        "top_emotions": [
            {"emotion_id": s, "weight": round(w, 4)}
            for s, w in sorted(
                v2["profiles"].get("current_books", {}).items(), key=lambda kv: -kv[1]
            )[:5]
            if w > 0
        ],
        "palette": palette_for(a["id"]),
        "bloom": bloom(counts),
        "year": datetime.now(timezone.utc).year,
        "red_flag": live.get("red_flag") if show_red_flag else None,
        "season": None,
    }
    season = v2.get("season")
    if show_season and season and season.get("id") and "counts" in season:
        s = _archetype(season["id"], season)
        card["season"] = {
            "id": s["id"], "name": s["name"], "article": s.get("article", "a"),
            "home": bool(season.get("home")), "since": season.get("since"),
            "books": season.get("books"),
            "palette": palette_for(s["id"]),
            "bloom": bloom(season["counts"]),
        }
    if choices is not None:
        card["choices"] = choices
    return card


# ── The link preview's identity ──

def handle_for_card(handle: str | None) -> str:
    h = handle or "a reader"
    return h if len(h) <= 20 else h[:19] + "…"


def preview_spec(card: dict, handle: str | None) -> dict:
    """Everything the link preview draws, and nothing else."""
    season = card.get("season")
    return {
        "rev": RENDER_REV,
        "handle": handle_for_card(handle),
        "archetype": {k: card["archetype"].get(k) for k in ("id", "name", "article")},
        "leaning": (card.get("leaning") or {}).get("name"),
        "season": ({"name": season["name"], "home": season["home"]} if season else None),
        "palette": card["palette"],
        "bloom": [layer["d"] for layer in card["bloom"]["layers"]],
    }


def preview_hash(spec: dict) -> str:
    blob = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def preview_alt(card: dict, handle: str | None) -> str:
    """"@shruti's reading DNA card: The Grief Romantic. Most-felt: heartbreak,
    catharsis, haunted." Up to X's 420 characters, which it never nears."""
    who = f"@{handle}'s" if handle else "A reader's"
    alt = f"{who} reading DNA card: {card['archetype']['name']}."
    top = card["bloom"]["top"]
    if top:
        alt += " Most-felt: " + ", ".join(t["label"] for t in top) + "."
    return alt[:420]
