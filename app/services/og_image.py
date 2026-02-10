"""
OG Image Generator — Creates shareable Book DNA card images.

Generates a 1200x630 image (standard OG size) for social sharing.
Falls back gracefully to system fonts if custom fonts aren't available.
"""

import math
import os
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Font paths — try multiple locations
FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype/liberation",
    "/usr/share/fonts/truetype/google-fonts",
    "/usr/local/share/fonts",
    str(Path.home() / ".fonts"),
]


def _find_font(name_patterns: list[str], fallback: str = "DejaVuSans.ttf") -> str:
    """Find a font file matching any of the name patterns."""
    for font_dir in FONT_DIRS:
        if not os.path.isdir(font_dir):
            continue
        for f in os.listdir(font_dir):
            for pattern in name_patterns:
                if pattern.lower() in f.lower():
                    return os.path.join(font_dir, f)

    # Fallback
    for font_dir in FONT_DIRS:
        path = os.path.join(font_dir, fallback)
        if os.path.exists(path):
            return path

    return fallback


# Resolve fonts once
FONT_BOLD = _find_font(["DejaVuSans-Bold", "LiberationSans-Bold"])
FONT_REGULAR = _find_font(["DejaVuSans.ttf", "LiberationSans-Regular"])
FONT_ITALIC = _find_font(["DejaVuSans-Oblique", "DejaVuSerif-Italic"])
FONT_MONO = _find_font(["DejaVuSansMono", "LiberationMono"])
FONT_SERIF_BOLD = _find_font(["DejaVuSerif-Bold", "LiberationSerif-Bold"])


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    """Load a font with fallback to default."""
    try:
        return ImageFont.truetype(path, size)
    except (OSError, IOError):
        try:
            return ImageFont.truetype(FONT_REGULAR, size)
        except (OSError, IOError):
            return ImageFont.load_default()


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color string to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def _draw_rounded_rect(draw, xy, radius, fill):
    """Draw a rounded rectangle."""
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def _draw_dna_helix(draw, x, y_start, height, color1, color2, opacity=40):
    """Draw decorative DNA helix."""
    c1 = _hex_to_rgb(color1)
    c2 = _hex_to_rgb(color2)
    for i in range(50):
        t = i * 0.3
        y = y_start + (i / 50) * height
        x1 = x + 20 * math.sin(t)
        x2 = x - 20 * math.sin(t)
        # Draw dots with reduced opacity by blending with background
        dot_c1 = tuple(int(c * opacity / 100) for c in c1)
        dot_c2 = tuple(int(c * opacity / 100) for c in c2)
        draw.ellipse([x1 - 2, y - 2, x1 + 2, y + 2], fill=dot_c1)
        draw.ellipse([x2 - 2, y - 2, x2 + 2, y + 2], fill=dot_c2)


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Word-wrap text to fit within max_width pixels."""
    words = text.split()
    lines = []
    current_line = ""

    for word in words:
        test = f"{current_line} {word}".strip()
        bbox = font.getbbox(test)
        if bbox[2] <= max_width:
            current_line = test
        else:
            if current_line:
                lines.append(current_line)
            current_line = word

    if current_line:
        lines.append(current_line)

    return lines


# Emotion data for rendering
EMOTION_ICONS = {
    "rage": "🔥", "comfort": "🧣", "dread": "🌀", "healing": "🌿",
    "obsession": "💜", "grief": "🌊", "seen": "👁", "chaos": "⚡",
    "nothing": "◻️", "2am": "🌙",
}

EMOTION_COLORS = {
    "rage": "#C4553A", "comfort": "#7A8B6F", "dread": "#5A5A8A",
    "healing": "#5A8B6F", "obsession": "#6B3A5D", "grief": "#3A5A6B",
    "seen": "#B8964E", "chaos": "#C47A3A", "nothing": "#6A6A6A",
    "2am": "#7A5A9B",
}

EMOTION_LABELS = {
    "rage": "Rage", "comfort": "Comfort", "dread": "Dread",
    "healing": "Healing", "obsession": "Obsession", "grief": "Grief",
    "seen": "Seen", "chaos": "Chaos", "nothing": "Nothing", "2am": "2AM",
}


def generate_dna_card_image(
    personality_name: str,
    personality_description: str,
    personality_color: str,
    personality_glyph: str,
    username: str,
    book_count: int,
    top_emotions: list[dict],  # [{"emotion_id": "grief", "count": 5}, ...]
    year: int = 2026,
) -> bytes:
    """
    Generate a 1200x630 OG image for a user's DNA card.

    Returns: PNG image as bytes.
    """
    W, H = 1200, 630
    BG = (8, 8, 10)  # #08080a
    accent = _hex_to_rgb(personality_color)
    accent_dim = tuple(int(c * 0.15) for c in accent)
    gold = _hex_to_rgb("#B8964E")

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # === Background elements ===

    # Subtle gradient overlay on left
    for x in range(400):
        alpha = int((1 - x / 400) * 12)
        color = tuple(min(255, c + alpha) for c in accent_dim)
        draw.line([(x, 0), (x, H)], fill=color)

    # DNA helix decoration on right
    _draw_dna_helix(draw, W - 80, 50, H - 100, personality_color, "#B8964E", opacity=25)

    # Accent line at top
    draw.rectangle([60, 45, 64, 120], fill=accent)

    # === Header ===
    font_label = _load_font(FONT_MONO, 13)
    font_small = _load_font(FONT_MONO, 12)

    draw.text((80, 50), "BOOK DNA™", font=font_label, fill=(80, 78, 86))
    draw.text((80, 68), f"{book_count} books · {year}", font=font_small, fill=(46, 44, 51))

    # Glyph on far right
    font_glyph = _load_font(FONT_SERIF_BOLD, 48)
    draw.text((W - 120, 45), personality_glyph, font=font_glyph, fill=(*accent, 120))

    # === Personality Name ===
    font_name = _load_font(FONT_SERIF_BOLD, 44)
    name_y = 110
    draw.text((80, name_y), personality_name, font=font_name, fill=accent)

    # === Description ===
    font_desc = _load_font(FONT_REGULAR, 17)
    desc_lines = _wrap_text(personality_description, font_desc, 550)
    desc_y = name_y + 60
    for line in desc_lines[:3]:
        draw.text((80, desc_y), line, font=font_desc, fill=(138, 134, 144))
        desc_y += 26

    # === Divider ===
    divider_y = desc_y + 18
    for x in range(80, 500):
        progress = (x - 80) / 420
        r = int(accent[0] * (1 - progress) * 0.3)
        g = int(accent[1] * (1 - progress) * 0.3)
        b = int(accent[2] * (1 - progress) * 0.3)
        draw.point((x, divider_y), fill=(max(r, 10), max(g, 10), max(b, 10)))

    # === Emotional Fingerprint ===
    section_y = divider_y + 20
    draw.text((80, section_y), "EMOTIONAL FINGERPRINT", font=font_label, fill=(78, 75, 84))

    bar_y = section_y + 30
    max_count = top_emotions[0]["count"] if top_emotions else 1

    for i, emo_data in enumerate(top_emotions[:5]):
        emo_id = emo_data["emotion_id"]
        count = emo_data["count"]
        emo_color = _hex_to_rgb(EMOTION_COLORS.get(emo_id, "#666666"))
        label = EMOTION_LABELS.get(emo_id, emo_id)

        y = bar_y + i * 36

        # Label
        font_emo_label = _load_font(FONT_MONO, 14)
        draw.text((80, y + 2), label, font=font_emo_label, fill=(138, 134, 144))

        # Bar background
        bar_x = 200
        bar_w = 300
        bar_h = 8
        _draw_rounded_rect(draw, (bar_x, y + 6, bar_x + bar_w, y + 6 + bar_h), 4, fill=(17, 17, 20))

        # Bar fill
        fill_w = int((count / max_count) * bar_w)
        if fill_w > 0:
            _draw_rounded_rect(
                draw,
                (bar_x, y + 6, bar_x + fill_w, y + 6 + bar_h),
                4,
                fill=emo_color,
            )

        # Count
        font_count = _load_font(FONT_MONO, 13)
        draw.text((bar_x + bar_w + 15, y + 2), str(count), font=font_count, fill=(78, 75, 84))

    # === Right side — emotion dots cluster ===
    dot_x_start = 700
    dot_y_start = 140

    # Large decorative circle
    circle_r = 160
    cx, cy = dot_x_start + 200, dot_y_start + 180
    for r in range(circle_r, circle_r - 40, -1):
        alpha = int(((circle_r - r) / 40) * 8)
        color = tuple(min(255, c + alpha) for c in accent_dim)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color)

    # Emotion dots arranged in a pattern
    if top_emotions:
        for i, emo_data in enumerate(top_emotions[:6]):
            emo_id = emo_data["emotion_id"]
            emo_color = _hex_to_rgb(EMOTION_COLORS.get(emo_id, "#666666"))
            count = emo_data["count"]

            angle = (i / min(len(top_emotions), 6)) * 2 * math.pi - math.pi / 2
            dist = 60 + count * 12
            ex = int(cx + dist * math.cos(angle))
            ey = int(cy + dist * math.sin(angle))
            size = 8 + count * 3

            # Glow
            for g in range(size + 10, size, -2):
                glow_color = tuple(int(c * 0.1) for c in emo_color)
                draw.ellipse([ex - g, ey - g, ex + g, ey + g], fill=glow_color)

            # Dot
            draw.ellipse([ex - size, ey - size, ex + size, ey + size], fill=emo_color)

            # Label
            font_dot_label = _load_font(FONT_MONO, 10)
            label = EMOTION_LABELS.get(emo_id, emo_id)
            bbox = font_dot_label.getbbox(label)
            lw = bbox[2] - bbox[0]
            draw.text((ex - lw // 2, ey + size + 5), label, font=font_dot_label, fill=(100, 96, 108))

    # === Footer ===
    footer_y = H - 55

    # Gradient line
    for x in range(60, W - 60):
        progress = (x - 60) / (W - 120)
        if progress < 0.5:
            alpha = int(progress * 2 * 20)
        else:
            alpha = int((1 - progress) * 2 * 20)
        color = tuple(min(255, max(0, int(c * alpha / 20))) for c in accent)
        draw.point((x, footer_y - 15), fill=color)

    font_footer = _load_font(FONT_MONO, 11)
    draw.text((60, footer_y), "bookdna.app", font=font_footer, fill=(46, 44, 51))
    draw.text((W - 200, footer_y), f"@{username}", font=font_footer, fill=(78, 75, 84))

    # Color dots in footer
    dot_start_x = W // 2 - 30
    for i, emo_data in enumerate(top_emotions[:5]):
        emo_id = emo_data["emotion_id"]
        emo_color = _hex_to_rgb(EMOTION_COLORS.get(emo_id, "#666666"))
        dx = dot_start_x + i * 14
        draw.ellipse([dx, footer_y + 2, dx + 8, footer_y + 10], fill=emo_color)

    # === Export ===
    buffer = BytesIO()
    img.save(buffer, format="PNG", quality=95)
    buffer.seek(0)
    return buffer.getvalue()


def generate_echo_card_image(
    title: str,
    author: str,
    public_echo: str,
    emotions: list[str],
    intensity: int,
    username: str,
) -> bytes:
    """
    Generate a 1200x630 OG image for a single public echo.
    """
    W, H = 1200, 630
    BG = (8, 8, 10)

    primary_emo = emotions[0] if emotions else "seen"
    accent = _hex_to_rgb(EMOTION_COLORS.get(primary_emo, "#B8964E"))
    accent_dim = tuple(int(c * 0.08) for c in accent)

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Background tint
    for y in range(H):
        progress = y / H
        alpha = int((1 - progress) * 6)
        color = tuple(min(255, c + alpha) for c in accent_dim)
        draw.line([(0, y), (W, y)], fill=color)

    # Large quotation mark
    font_quote_mark = _load_font(FONT_SERIF_BOLD, 200)
    draw.text((60, 20), "\u201C", font=font_quote_mark, fill=(*accent, 15))

    # Header
    font_label = _load_font(FONT_MONO, 13)
    draw.text((80, 50), "BOOK DNA™ · PUBLIC ECHO", font=font_label, fill=(78, 75, 84))

    # Echo text
    font_echo = _load_font(FONT_ITALIC, 32)
    echo_lines = _wrap_text(f'"{public_echo}"', font_echo, W - 200)
    echo_y = 140
    for line in echo_lines[:4]:
        draw.text((80, echo_y), line, font=font_echo, fill=(245, 240, 232))
        echo_y += 48

    # Book info
    info_y = max(echo_y + 40, 380)
    font_title = _load_font(FONT_BOLD, 18)
    font_author = _load_font(FONT_REGULAR, 16)

    draw.text((80, info_y), title, font=font_title, fill=(138, 134, 144))
    draw.text((80, info_y + 28), f"by {author}", font=font_author, fill=(78, 75, 84))

    # Emotion tags
    tag_x = 80
    tag_y = info_y + 65
    for emo_id in emotions[:4]:
        label = EMOTION_LABELS.get(emo_id, emo_id)
        emo_color = _hex_to_rgb(EMOTION_COLORS.get(emo_id, "#666"))
        font_tag = _load_font(FONT_MONO, 12)
        bbox = font_tag.getbbox(label)
        tw = bbox[2] - bbox[0]

        # Tag background
        _draw_rounded_rect(
            draw,
            (tag_x, tag_y, tag_x + tw + 20, tag_y + 26),
            13,
            fill=(*emo_color, 30),
        )
        draw.text((tag_x + 10, tag_y + 5), label, font=font_tag, fill=emo_color)
        tag_x += tw + 30

    # Intensity bar
    bar_y = tag_y + 45
    draw.text((80, bar_y), "INTENSITY", font=_load_font(FONT_MONO, 10), fill=(78, 75, 84))
    bar_x = 160
    bar_w = 200
    _draw_rounded_rect(draw, (bar_x, bar_y + 2, bar_x + bar_w, bar_y + 8), 3, fill=(17, 17, 20))
    fill_w = int((intensity / 10) * bar_w)
    _draw_rounded_rect(draw, (bar_x, bar_y + 2, bar_x + fill_w, bar_y + 8), 3, fill=accent)

    # Footer
    footer_y = H - 55
    font_footer = _load_font(FONT_MONO, 11)
    draw.text((60, footer_y), "bookdna.app", font=font_footer, fill=(46, 44, 51))
    draw.text((W - 200, footer_y), f"@{username}", font=font_footer, fill=(78, 75, 84))

    buffer = BytesIO()
    img.save(buffer, format="PNG", quality=95)
    buffer.seek(0)
    return buffer.getvalue()