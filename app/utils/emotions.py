"""Canonical 13-emotion vocabulary — single source of truth for Book DNA."""

EMOTIONS_13 = [
    {"slug": "grief",       "name": "Grief",       "symbol": "💧", "color": "#6B4F8E", "description": "Loss, absence, mourning — the ache"},
    {"slug": "desire",      "name": "Desire",      "symbol": "💜", "color": "#9B5B8E", "description": "Longing, wanting, romantic tension"},
    {"slug": "rage",        "name": "Rage",        "symbol": "⚡", "color": "#C44B4B", "description": "Fury, injustice, the urge to burn things down"},
    {"slug": "dread",       "name": "Dread",       "symbol": "😰", "color": "#4B6B8E", "description": "Anxiety, foreboding, existential unease"},
    {"slug": "comfort",     "name": "Comfort",     "symbol": "☕", "color": "#8E6B4B", "description": "Safety, warmth, being held by a book"},
    {"slug": "awe",         "name": "Awe",         "symbol": "🌟", "color": "#4B7B6B", "description": "Wonder, scale, the sublime"},
    {"slug": "catharsis",   "name": "Catharsis",   "symbol": "✨", "color": "#C9A96E", "description": "Release, relief, the exhale after tension"},
    {"slug": "two_am",      "name": "2AM",         "symbol": "🌙", "color": "#3D4B6B", "description": "Intimacy, rawness, the feeling of 3am thoughts"},
    {"slug": "chaos",       "name": "Chaos",       "symbol": "🌀", "color": "#6B8E4B", "description": "Unpredictability, wild energy, plot velocity"},
    {"slug": "tenderness",  "name": "Tenderness",  "symbol": "🌸", "color": "#9B6B7B", "description": "Gentle love, care, soft emotional moments"},
    {"slug": "wit",         "name": "Wit",         "symbol": "🔪", "color": "#7B8E4B", "description": "Sharp humour, intelligence, the perfectly placed line"},
    {"slug": "longing",     "name": "Longing",     "symbol": "🕊", "color": "#5B6B8E", "description": "Nostalgia, distance, wanting what you cannot have"},
    {"slug": "devastation", "name": "Devastation", "symbol": "🖤", "color": "#3D2B3D", "description": "Complete emotional destruction — the books that ruin you"},
]

EMOTIONS_BY_SLUG = {e["slug"]: e for e in EMOTIONS_13}
VALID_SLUGS = set(EMOTIONS_BY_SLUG.keys())

# Canonical slug + its legacy variants, for the "2am" room unlock (moon_lamp).
# Matching both means the unlock fires for new (two_am) and pre-cutover (2am) rows.
TWO_AM_SLUGS = ("two_am", "2am")

# Populated after running scripts/audit_emotions.py — maps old slugs → canonical slugs
LEGACY_EMOTION_MAP: dict[str, str] = {
    "healing":   "catharsis",
    "obsession": "desire",
    "nostalgia": "longing",
    "2am":       "two_am",
    "seen":      "tenderness",
}


def get_emotion(slug: str) -> dict | None:
    return EMOTIONS_BY_SLUG.get(slug)


def canonicalize(slug: str) -> str | None:
    """Return the canonical slug, remapping via LEGACY_EMOTION_MAP if needed."""
    if slug in VALID_SLUGS:
        return slug
    return LEGACY_EMOTION_MAP.get(slug)


# Backward-compat alias used by existing entry schema and dna_engine
VALID_EMOTION_IDS = VALID_SLUGS


# Hints used by the blind-spots endpoint:
#   "You have never tagged {emotion}. Either you avoid {category}, or you do not let yourself {feeling}."
BLIND_SPOT_HINTS: dict[str, dict[str, str]] = {
    "grief":       {"category": "sadness",         "feeling": "mourn"},
    "desire":      {"category": "wanting",         "feeling": "long for things"},
    "rage":        {"category": "fury",            "feeling": "get angry"},
    "dread":       {"category": "fear",            "feeling": "sit with fear"},
    "comfort":     {"category": "warmth",          "feeling": "feel safe"},
    "awe":         {"category": "wonder",          "feeling": "be small in something vast"},
    "catharsis":   {"category": "release",         "feeling": "let go"},
    "two_am":      {"category": "intimacy",        "feeling": "be raw"},
    "chaos":       {"category": "unpredictability","feeling": "lose control"},
    "tenderness":  {"category": "softness",        "feeling": "be gentle"},
    "wit":         {"category": "cleverness",      "feeling": "play"},
    "longing":     {"category": "absence",         "feeling": "miss things"},
    "devastation": {"category": "being wrecked",   "feeling": "fall apart"},
}


if __name__ == "__main__":
    assert canonicalize("grief") == "grief"
    assert canonicalize("made_up") is None
    print("All assertions passed.")
    for e in EMOTIONS_13:
        print(f"  {e['slug']:12}  {e['symbol']}  {e['name']}")
