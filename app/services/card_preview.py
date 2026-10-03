"""The /s/ link preview: a 1200×630 JPEG of the reader's card, drawn on the server.

Rendered from an SVG template with resvg and the three brand fonts bundled in
app/assets/fonts — resvg shapes right-to-left and complex scripts, which Satori
cannot, so Hindi or Urdu copy would still draw correctly. The bloom is the same
path data the browser draws (dna_card.bloom), so the preview and the image the
reader posted can't disagree.

Rendered and stored ahead of the first crawler (see ensure_preview); a crawler
only ever gets stored bytes. WhatsApp caches a failed fetch against the URL.
"""

import io
import logging
from functools import lru_cache
from html import escape
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models.card import CardPreview
from app.models.user import User
from app.services import dna_card

logger = logging.getLogger("bibliome.card")

W, H = 1200, 630
MAX_BYTES = 250 * 1024
FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_FILES = {
    ("Newsreader", 400, False): "Newsreader-Regular.ttf",
    ("Newsreader", 500, False): "Newsreader-Medium.ttf",
    ("Newsreader", 400, True): "Newsreader-Italic.ttf",
    ("Newsreader", 500, True): "Newsreader-MediumItalic.ttf",
    ("DM Sans", 500, False): "DMSans-Medium.ttf",
    ("JetBrains Mono", 500, False): "JetBrainsMono-Medium.ttf",
}


# ── Measuring text, so long names shrink instead of running off the image ──

@lru_cache(maxsize=None)
def _metrics(family: str, weight: int, italic: bool):
    from fontTools.ttLib import TTFont

    f = TTFont(FONT_DIR / FONT_FILES[(family, weight, italic)])
    return f.getBestCmap(), f["hmtx"].metrics, f["head"].unitsPerEm


def text_width(text: str, family: str, size: float, *, weight: int = 400,
               italic: bool = False, tracking: float = 0.0) -> float:
    """Advance width in px, without kerning (which only ever narrows a line).
    `tracking` is letter-spacing in em."""
    cmap, hmtx, upm = _metrics(family, weight, italic)
    units = 0
    for ch in text:
        glyph = cmap.get(ord(ch))
        units += hmtx[glyph][0] if glyph in hmtx else upm // 2
    return units * size / upm + tracking * size * max(len(text) - 1, 0)


def fit_size(lines: list[tuple[str, bool]], family: str, start: float, floor: float,
             width: float, *, weight: int = 400) -> float:
    """The largest size from `start` down to `floor` at which every line fits.
    Each line is (text, italic)."""
    size = start
    while size > floor and any(
        text_width(t, family, size, weight=weight, italic=it) > width for t, it in lines
    ):
        size -= 2
    return max(size, floor)


def name_lines(name: str) -> tuple[str, str | None]:
    """"Grief Romantic" → ("Grief", "Romantic."); "World-Diver" → ("World-Diver.", None)."""
    words = dna_card.display_name(name).split(" ")
    if len(words) == 1:
        return words[0] + ".", None
    return words[0], " ".join(words[1:]) + "."


def wrap(text: str, family: str, size: float, width: float, *, italic: bool = False) -> list[str]:
    lines, line = [], ""
    for word in text.split(" "):
        trial = f"{line} {word}".strip()
        if line and text_width(trial, family, size, italic=italic) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + ([line] if line else [])


# ── The template ──

def _t(x, y, text, *, family, size, weight=400, italic=False, fill, tracking=0.0, anchor="start"):
    style = "italic" if italic else "normal"
    ls = f' letter-spacing="{tracking * size:.2f}"' if tracking else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" font-size="{size:.1f}" '
            f'font-weight="{weight}" font-style="{style}" fill="{fill}"{ls} '
            f'text-anchor="{anchor}">{escape(text)}</text>')


def _bloom(card_bloom: dict, ink: str, x: float, y: float, size: float) -> str:
    k = size / card_bloom["size"]
    out = [f'<g transform="translate({x:.1f} {y:.1f}) scale({k:.4f})">']
    for layer in card_bloom["layers"]:
        if "stroke" in layer:
            out.append(f'<path d="{layer["d"]}" fill="none" stroke="{ink}" '
                       f'stroke-opacity="{layer["stroke"]}" stroke-width="{layer["width"]}"/>')
        else:
            out.append(f'<path d="{layer["d"]}" fill="{ink}" fill-opacity="{layer["fill"]}"/>')
    out.append("</g>")
    return "".join(out)


def _sticker(season: dict, pal: dict, cx: float, cy: float, r: float) -> str:
    """"now in a World-Diver season", or "in my element" when the season is home."""
    out = [f'<g transform="rotate(12 {cx:.1f} {cy:.1f})">',
           f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{pal["accent"]}"/>']
    ink = pal["bottom"]
    mono = dict(family="JetBrains Mono", size=r * 0.17, weight=500, fill=ink, tracking=0.12, anchor="middle")
    if season["home"]:
        out.append(_t(cx, cy - r * 0.12, "IN MY", **mono))
        out.append(_t(cx, cy + r * 0.28, "element", family="Newsreader", size=r * 0.36,
                      weight=500, italic=True, fill=ink, anchor="middle"))
    else:
        nm = dna_card.display_name(season["name"])
        size = fit_size([(nm, True)], "Newsreader", r * 0.34, r * 0.18, r * 1.6, weight=500)
        out.append(_t(cx, cy - r * 0.32, f"NOW IN {season['article'].upper()}", **mono))
        out.append(_t(cx, cy + size * 0.35, nm, family="Newsreader", size=size, weight=500,
                      italic=True, fill=ink, anchor="middle"))
        out.append(_t(cx, cy + r * 0.48, "SEASON", **mono))
    out.append("</g>")
    return "".join(out)


def preview_svg(card: dict, handle: str | None) -> str:
    pal = card["palette"]
    a = card["archetype"]
    col_x, col_w = 574.0, W - 574.0 - 72.0

    first, second = name_lines(a["name"])
    lines = [(first, second is None)] + ([(second, True)] if second else [])
    name_size = fit_size(lines, "Newsreader", 108, 60, col_w)
    q_size = 34.0
    question = wrap("What does your reading say about you?", "Newsreader", q_size, col_w, italic=True)
    leaning = (card.get("leaning") or {}).get("name")

    # Stack the column, then centre it vertically.
    label_h, name_lh, lean_h, q_lh, foot_h = 24, name_size * 0.92, 34, q_size * 1.2, 24
    total = label_h + 22 + name_lh * len(lines) + (lean_h + 10 if leaning else 0) \
        + 22 + q_lh * len(question) + 22 + foot_h
    y = (H - total) / 2

    who = dna_card.handle_for_card(handle)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        '<defs><radialGradient id="g" gradientUnits="userSpaceOnUse" cx="264" cy="315" r="980">'
        f'<stop offset="0" stop-color="{pal["top"]}"/><stop offset="1" stop-color="{pal["bottom"]}"/>'
        '</radialGradient></defs>',
        f'<rect width="{W}" height="{H}" fill="url(#g)"/>',
        _bloom(card["bloom"], pal["ink"], 56, 80, 470),
    ]
    if card.get("season"):
        parts.append(_sticker(card["season"], pal, 470, 132, 78))

    y += label_h
    article = a.get("article") or "a"
    label = f"@{who} reads like {article}" if handle else f"This reader reads like {article}"
    parts.append(_t(col_x, y, label, family="JetBrains Mono", size=22, weight=500,
                    fill=pal["accent"], tracking=0.06))
    y += 22
    for text, italic in lines:
        y += name_lh
        parts.append(_t(col_x, y - name_lh * 0.12, text, family="Newsreader", size=name_size,
                        italic=italic, fill=pal["accent"] if italic else pal["ink"]))
    if leaning:
        y += lean_h + 10
        parts.append(_t(col_x, y - 6, f"leaning toward the {dna_card.display_name(leaning)}",
                        family="DM Sans", size=24, weight=500, fill=pal["accent"]))
    y += 22
    for line in question:
        y += q_lh
        parts.append(_t(col_x, y - q_size * 0.25, line, family="Newsreader", size=q_size,
                        italic=True, fill=pal["ink"]))
    y += 22 + foot_h
    parts.append(_t(col_x, y, "bibliome.app", family="JetBrains Mono", size=24, weight=500,
                    fill=pal["ink"], tracking=0.12))
    parts.append("</svg>")
    return "".join(parts)


def render_jpeg(card: dict, handle: str | None) -> bytes:
    """The preview as JPEG, under MAX_BYTES. CPU-bound; call via a thread."""
    import resvg_py
    from PIL import Image

    png = resvg_py.svg_to_bytes(
        svg_string=preview_svg(card, handle),
        skip_system_fonts=True,
        font_files=[str(FONT_DIR / f) for f in FONT_FILES.values()],
        font_family="DM Sans", serif_family="Newsreader", monospace_family="JetBrains Mono",
    )
    img = Image.open(io.BytesIO(bytes(png))).convert("RGB")
    for quality in (88, 80, 72, 64, 56):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
        if buf.tell() <= MAX_BYTES:
            break
    return buf.getvalue()


# ── Stored previews ──

async def stored_preview(db: AsyncSession, user_id) -> CardPreview | None:
    # populate_existing: ensure_preview writes with a Core upsert, which the
    # session's identity map never sees. Without this, a row loaded before the
    # upsert comes back with the OLD bytes.
    return (await db.execute(
        select(CardPreview).where(CardPreview.user_id == user_id)
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()


async def ensure_preview(db: AsyncSession, user: User, card: dict) -> str | None:
    """Make sure the stored preview matches the card as it stands. Returns its
    hash (the image URL's ?v=), or None if rendering failed — the caller then
    falls back to the generic image rather than point a crawler at nothing."""
    spec = dna_card.preview_spec(card, user.handle)
    want = dna_card.preview_hash(spec)
    have = await stored_preview(db, user.id)
    if have is not None and have.hash == want:
        return want
    try:
        jpeg = await run_in_threadpool(render_jpeg, card, user.handle)
    except Exception:  # never let a preview take the share page down
        logger.exception("card preview render failed for user %s", user.id)
        return None
    stmt = insert(CardPreview).values(user_id=user.id, hash=want, jpeg=jpeg)
    await db.execute(stmt.on_conflict_do_update(
        index_elements=[CardPreview.user_id], set_={"hash": want, "jpeg": jpeg}))
    await db.flush()
    return want
