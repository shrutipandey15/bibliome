"""
OG Image Generator — Creates shareable Book DNA card images.
"""

import math
import os
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

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
FONT_BOLD = _find_font(["DejaVuSans-Bold", "LiberationSans-Bold", "Arial Bold"])
FONT_REGULAR = _find_font(["DejaVuSans.ttf", "LiberationSans-Regular", "Arial"])
FONT_ITALIC = _find_font(["DejaVuSans-Oblique", "DejaVuSerif-Italic", "Times New Roman Italic"])
FONT_MONO = _find_font(["DejaVuSansMono", "LiberationMono", "Courier New"])
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


def _draw_rounded_rect(draw, xy, radius, fill, outline=None, width=1):
    """Draw a rounded rectangle."""
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


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

STORY_EMOTIONS = {
    "rage": {"label": "RAGE", "desc": "Righteous fury.", "color": "#E63946"},
    "dread": {"label": "DREAD", "desc": "A knot in your stomach.", "color": "#457B9D"},
    "chaos": {"label": "CHAOS", "desc": "Unhinged energy.", "color": "#E9C46A"},
    "obsession": {"label": "DESIRE", "desc": "Yearning and pinning.", "color": "#8338EC"},
    "comfort": {"label": "COMFORT", "desc": "Warm light in the dark.", "color": "#2A9D8F"},
    "healing": {"label": "CATHARSIS", "desc": "Release after pain.", "color": "#264653"},
    "seen": {"label": "SEEN", "desc": "Validation.", "color": "#F4A261"},
    "grief": {"label": "MELANCHOLY", "desc": "Beautiful sadness.", "color": "#264653"},
    "wit": {"label": "WIT", "desc": "Sharp and clever.", "color": "#9D4EDD"},
    "awe": {"label": "AWE", "desc": "The Sublime.", "color": "#48CAE4"},
    "nostalgia": {"label": "NOSTALGIA", "desc": "Bittersweet past.", "color": "#F4A261"},
    "nothing": {"label": "EMPTY", "desc": "No connection.", "color": "#6C757D"},
    "2am": {"label": "2AM", "desc": "Unputdownable.", "color": "#1D3557"}
}

def _create_cinematic_bg(width, height, cover_bytes, accent_color):
    """Creates blurred background from cover or gradient."""
    if cover_bytes:
        try:
            bg = Image.open(BytesIO(cover_bytes)).convert("RGB")
            # Zoom to fill
            target_ratio = width / height
            bg_ratio = bg.width / bg.height
            
            if bg_ratio > target_ratio:
                new_width = int(bg.height * target_ratio)
                left = (bg.width - new_width) // 2
                bg = bg.crop((left, 0, left + new_width, bg.height))
            else:
                new_height = int(bg.width / target_ratio)
                top = (bg.height - new_height) // 2
                bg = bg.crop((0, top, bg.width, top + new_height))
                
            bg = bg.resize((width, height), Image.Resampling.LANCZOS)
            bg = bg.filter(ImageFilter.GaussianBlur(radius=60))
            
            # Dark Overlay
            overlay = Image.new("RGBA", (width, height), (10, 10, 12, 190))
            bg.paste(overlay, (0,0), mask=overlay)
            return bg
        except:
            pass
            
    # Fallback Gradient
    img = Image.new("RGB", (width, height), (15, 15, 20))
    draw = ImageDraw.Draw(img)
    c = _hex_to_rgb(accent_color)
    for y in range(height):
        r = int((c[0] * 0.2) + (15 * 0.8))
        draw.line([(0,y), (width,y)], fill=(r, 15, 25))
    return img

def _draw_medical_ekg(draw, x, y, width, intensity, color):
    """Draws realistic EKG spike scaling with intensity."""
    amp = 30 + (intensity * 12) # Max ~150px
    
    # Key points as percentage of width (x_pct, y_offset)
    key_points = [
        (0.00, 0), (0.10, 0), (0.15, -15), (0.25, 0), (0.30, 0), 
        (0.32, 10), (0.35, -amp), (0.38, 30), (0.42, 0), (0.45, 0), 
        (0.55, -25), (0.65, 0), (1.00, 0)
    ]
    
    screen_points = []
    for px, py in key_points:
        screen_points.append((x + (px * width), y + py))
        
    draw.line(screen_points, fill=(*color, 80), width=8, joint="curve")
    draw.line(screen_points, fill=(255, 255, 255, 230), width=3, joint="curve")

def _add_rounded_corners_img(im, rad):
    """Rounds corners of an image object."""
    circle = Image.new('L', (rad * 2, rad * 2), 0)
    draw = ImageDraw.Draw(circle)
    draw.ellipse((0, 0, rad * 2, rad * 2), fill=255)
    alpha = Image.new('L', im.size, 255)
    w, h = im.size
    alpha.paste(circle.crop((0, 0, rad, rad)), (0, 0))
    alpha.paste(circle.crop((0, rad, rad, rad * 2)), (0, h - rad))
    alpha.paste(circle.crop((rad, 0, rad * 2, rad)), (w - rad, 0))
    alpha.paste(circle.crop((rad, rad, rad * 2, rad * 2)), (w - rad, h - rad))
    im.putalpha(alpha)
    return im

def _fit_text_to_box(text, max_width, max_height, font_path, start_size=52):
    """Auto-scales text to fit available height."""
    # Try decreasing sizes
    for size in [start_size, start_size-6, start_size-12, start_size-18]:
        font = _load_font(font_path, size)
        line_height = int(size * 1.3)
        lines = _wrap_text(text, font, max_width)
        total_h = len(lines) * line_height
        if total_h <= max_height:
            return font, lines, line_height
    
    # Min size fallback
    font = _load_font(font_path, 32)
    line_height = int(32 * 1.3)
    lines = _wrap_text(text, font, max_width)
    max_lines = max_height // line_height
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        if lines: lines[-1] = lines[-1][:len(lines[-1])-3] + "..."
    return font, lines, line_height


# --- GENERATORS ---

def generate_dna_card_image(
    personality_name: str,
    personality_description: str,
    personality_color: str,
    personality_glyph: str,
    username: str,
    book_count: int,
    top_emotions: list[dict],
    year: int = 2026,
) -> bytes:
    """
    Generate a 1200x630 OG image for a user's DNA card.
    (EXISTING FUNCTION PRESERVED)
    """
    W, H = 1200, 630
    BG = (8, 8, 10)  # #08080a
    accent = _hex_to_rgb(personality_color)
    accent_dim = tuple(int(c * 0.15) for c in accent)

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    for x in range(400):
        alpha = int((1 - x / 400) * 12)
        color = tuple(min(255, c + alpha) for c in accent_dim)
        draw.line([(x, 0), (x, H)], fill=color)

    _draw_dna_helix(draw, W - 80, 50, H - 100, personality_color, "#B8964E", opacity=25)
    draw.rectangle([60, 45, 64, 120], fill=accent)

    font_label = _load_font(FONT_MONO, 13)
    font_small = _load_font(FONT_MONO, 12)
    draw.text((80, 50), "BOOK DNA™", font=font_label, fill=(80, 78, 86))
    draw.text((80, 68), f"{book_count} books · {year}", font=font_small, fill=(46, 44, 51))

    font_glyph = _load_font(FONT_SERIF_BOLD, 48)
    draw.text((W - 120, 45), personality_glyph, font=font_glyph, fill=(*accent, 120))

    font_name = _load_font(FONT_SERIF_BOLD, 44)
    name_y = 110
    draw.text((80, name_y), personality_name, font=font_name, fill=accent)

    font_desc = _load_font(FONT_REGULAR, 17)
    desc_lines = _wrap_text(personality_description, font_desc, 550)
    desc_y = name_y + 60
    for line in desc_lines[:3]:
        draw.text((80, desc_y), line, font=font_desc, fill=(138, 134, 144))
        desc_y += 26

    divider_y = desc_y + 18
    for x in range(80, 500):
        progress = (x - 80) / 420
        r = int(accent[0] * (1 - progress) * 0.3)
        g = int(accent[1] * (1 - progress) * 0.3)
        b = int(accent[2] * (1 - progress) * 0.3)
        draw.point((x, divider_y), fill=(max(r, 10), max(g, 10), max(b, 10)))

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
        font_emo_label = _load_font(FONT_MONO, 14)
        draw.text((80, y + 2), label, font=font_emo_label, fill=(138, 134, 144))

        bar_x = 200
        bar_w = 300
        bar_h = 8
        _draw_rounded_rect(draw, (bar_x, y + 6, bar_x + bar_w, y + 6 + bar_h), 4, fill=(17, 17, 20))

        fill_w = int((count / max_count) * bar_w)
        if fill_w > 0:
            _draw_rounded_rect(draw, (bar_x, y + 6, bar_x + fill_w, y + 6 + bar_h), 4, fill=emo_color)

        font_count = _load_font(FONT_MONO, 13)
        draw.text((bar_x + bar_w + 15, y + 2), str(count), font=font_count, fill=(78, 75, 84))

    dot_x_start = 700
    dot_y_start = 140
    circle_r = 160
    cx, cy = dot_x_start + 200, dot_y_start + 180
    for r in range(circle_r, circle_r - 40, -1):
        alpha = int(((circle_r - r) / 40) * 8)
        color = tuple(min(255, c + alpha) for c in accent_dim)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color)

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
            for g in range(size + 10, size, -2):
                glow_color = tuple(int(c * 0.1) for c in emo_color)
                draw.ellipse([ex - g, ey - g, ex + g, ey + g], fill=glow_color)
            draw.ellipse([ex - size, ey - size, ex + size, ey + size], fill=emo_color)
            font_dot_label = _load_font(FONT_MONO, 10)
            label = EMOTION_LABELS.get(emo_id, emo_id)
            bbox = font_dot_label.getbbox(label)
            lw = bbox[2] - bbox[0]
            draw.text((ex - lw // 2, ey + size + 5), label, font=font_dot_label, fill=(100, 96, 108))

    footer_y = H - 55
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

    dot_start_x = W // 2 - 30
    for i, emo_data in enumerate(top_emotions[:5]):
        emo_id = emo_data["emotion_id"]
        emo_color = _hex_to_rgb(EMOTION_COLORS.get(emo_id, "#666666"))
        dx = dot_start_x + i * 14
        draw.ellipse([dx, footer_y + 2, dx + 8, footer_y + 10], fill=emo_color)

    buffer = BytesIO()
    img.save(buffer, format="PNG", quality=95)
    buffer.seek(0)
    return buffer.getvalue()


# Removed (Phase 5 B5.7): generate_echo_card_image / generate_arc_card_image / generate_story_image.
