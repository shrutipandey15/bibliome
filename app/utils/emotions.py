"""Canonical 18-emotion vocabulary — single source of truth for Book DNA.

The five *families* ("It hurt", "It held me", …) are a UI grouping only. We store
and reason over the flat ``slug``; the ``family`` field is served so the frontend
can group without hardcoding its own taxonomy (that divergence is exactly what
caused the old P2-9 drift).
"""

# family → the emotions under it, in display order. Families are UI-only.
FAMILY_HURT = "It hurt"
FAMILY_HELD = "It held me"
FAMILY_WANTED = "It wanted something"
FAMILY_MOVED = "It moved me"
FAMILY_LOST = "It lost me"

# Each emotion carries a `name` (the plain word, e.g. "confusion") and a `phrase`
# (the first-person line the UI shows, e.g. "it confused me"). The frontend displays
# the phrase; `name` is the canonical label used where a single word is wanted.
EMOTIONS = [
    # It hurt
    {"slug": "devastation", "family": FAMILY_HURT, "name": "devastation", "phrase": "it wrecked me",           "symbol": "🖤", "color": "#3D2B3D", "description": "Complete emotional destruction — the books that ruin you"},
    {"slug": "grief",       "family": FAMILY_HURT, "name": "grief",       "phrase": "it grieved me",           "symbol": "💧", "color": "#6B4F8E", "description": "Loss, absence, mourning — the ache"},
    {"slug": "dread",       "family": FAMILY_HURT, "name": "dread",       "phrase": "it kept me on edge",      "symbol": "😰", "color": "#4B6B8E", "description": "Anxiety, foreboding, existential unease"},
    {"slug": "rage",        "family": FAMILY_HURT, "name": "rage",        "phrase": "it made me furious",      "symbol": "⚡", "color": "#C44B4B", "description": "Fury, injustice, the urge to burn things down"},
    # It held me
    {"slug": "comfort",     "family": FAMILY_HELD, "name": "comfort",     "phrase": "it comforted me",         "symbol": "☕", "color": "#8E6B4B", "description": "Safety, warmth, being held by a book"},
    {"slug": "tenderness",  "family": FAMILY_HELD, "name": "tenderness",  "phrase": "it was tender",           "symbol": "🌸", "color": "#9B6B7B", "description": "Gentle love, care, soft emotional moments"},
    {"slug": "joy",         "family": FAMILY_HELD, "name": "joy",         "phrase": "it made me happy",        "symbol": "☀️", "color": "#E0A458", "description": "Delight, gladness, the lightness a book can give"},
    {"slug": "amusement",   "family": FAMILY_HELD, "name": "amusement",   "phrase": "it made me laugh",        "symbol": "😄", "color": "#C9B24B", "description": "Sharp humour, wit, the perfectly placed line that makes you grin"},
    # It wanted something
    {"slug": "longing",     "family": FAMILY_WANTED, "name": "longing",   "phrase": "it left me aching",       "symbol": "🕊", "color": "#5B6B8E", "description": "Distance, wanting what you cannot have"},
    {"slug": "desire",      "family": FAMILY_WANTED, "name": "desire",    "phrase": "it stirred me",           "symbol": "💜", "color": "#9B5B8E", "description": "Wanting, romantic tension, the pull toward"},
    {"slug": "nostalgia",   "family": FAMILY_WANTED, "name": "nostalgia", "phrase": "it took me back",         "symbol": "🍂", "color": "#B07B4B", "description": "The ache of memory, a time you cannot return to"},
    # It moved me
    {"slug": "awe",         "family": FAMILY_MOVED, "name": "awe",         "phrase": "it stunned me",          "symbol": "🌟", "color": "#4B7B6B", "description": "Wonder, scale, the sublime"},
    {"slug": "recognition", "family": FAMILY_MOVED, "name": "recognition", "phrase": "it saw me",              "symbol": "🪞", "color": "#4B8E8A", "description": "Being seen — the book that knew you already"},
    {"slug": "catharsis",   "family": FAMILY_MOVED, "name": "catharsis",   "phrase": "it broke something open","symbol": "✨", "color": "#C9A96E", "description": "Release, relief, the exhale after tension"},
    # It lost me
    {"slug": "boredom",     "family": FAMILY_LOST, "name": "boredom",       "phrase": "it bored me",           "symbol": "😐", "color": "#8A8A7A", "description": "Flatness, the pages that wouldn't turn"},
    {"slug": "revulsion",   "family": FAMILY_LOST, "name": "revulsion",     "phrase": "it repelled me",        "symbol": "🤢", "color": "#6B7A4B", "description": "Disgust, recoil, wanting to put it down"},
    {"slug": "confusion",   "family": FAMILY_LOST, "name": "confusion",     "phrase": "it confused me",        "symbol": "🌀", "color": "#7B6B9B", "description": "Lost the thread, couldn't follow, unmoored"},
    {"slug": "indifference","family": FAMILY_LOST, "name": "indifference",  "phrase": "it left me cold",       "symbol": "◻️", "color": "#9A9A9A", "description": "Nothing landed — you closed it and felt nothing"},
]

EMOTIONS_BY_SLUG = {e["slug"]: e for e in EMOTIONS}
VALID_SLUGS = set(EMOTIONS_BY_SLUG.keys())

# The "It lost me" family are registers of *disengagement* — they describe a book
# failing you, not a reading identity. They're valid to tag and to score against
# (as anti-emotions), but no archetype is anchored on them.
LOST_ME_SLUGS = {e["slug"] for e in EMOTIONS if e["family"] == FAMILY_LOST}

# Old slug → canonical slug. Historical rows are remapped forward on read so they
# still count; anything with no sensible target is absent here and canonicalizes
# to None (skipped on read). Renamed concepts land on their new slug.
#   chaos → confusion, wit → amusement, two_am/2am → longing.
LEGACY_EMOTION_MAP: dict[str, str] = {
    "healing":   "catharsis",
    "obsession": "desire",
    "seen":      "tenderness",
    "chaos":     "confusion",
    "wit":       "amusement",
    "two_am":    "longing",
    "2am":       "longing",
    # "nostalgia" was previously a legacy alias for "longing"; it is now a canonical
    # slug in its own right, so it is deliberately absent from this map.
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
    "devastation":  {"category": "being wrecked",   "feeling": "fall apart"},
    "grief":        {"category": "sadness",         "feeling": "mourn"},
    "dread":        {"category": "fear",            "feeling": "sit with fear"},
    "rage":         {"category": "fury",            "feeling": "get angry"},
    "comfort":      {"category": "warmth",          "feeling": "feel safe"},
    "tenderness":   {"category": "softness",        "feeling": "be gentle"},
    "joy":          {"category": "delight",         "feeling": "feel light"},
    "amusement":    {"category": "humour",          "feeling": "laugh"},
    "longing":      {"category": "absence",         "feeling": "miss things"},
    "desire":       {"category": "wanting",         "feeling": "long for things"},
    "nostalgia":    {"category": "memory",          "feeling": "miss the past"},
    "awe":          {"category": "wonder",          "feeling": "be small in something vast"},
    "recognition":  {"category": "being seen",      "feeling": "see yourself"},
    "catharsis":    {"category": "release",         "feeling": "let go"},
    "boredom":      {"category": "dullness",        "feeling": "admit disinterest"},
    "revulsion":    {"category": "disgust",         "feeling": "recoil"},
    "confusion":    {"category": "disorientation",  "feeling": "be lost"},
    "indifference": {"category": "detachment",      "feeling": "feel nothing"},
}


if __name__ == "__main__":
    assert canonicalize("grief") == "grief"
    assert canonicalize("chaos") == "confusion"
    assert canonicalize("nostalgia") == "nostalgia"
    assert canonicalize("made_up") is None
    print("All assertions passed.")
    for e in EMOTIONS:
        print(f"  {e['slug']:12}  {e['symbol']}  {e['phrase']:22}  {e['family']}")
