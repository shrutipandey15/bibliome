"""Canonical 19-emotion vocabulary — single source of truth for Bibliome.

The six *families* ("It wrecked me", "It felt good", …) are a UI grouping only. We
store and reason over the flat ``slug``; the ``family`` field is served so the
frontend can group without hardcoding its own taxonomy (that divergence is exactly
what caused the old P2-9 drift).

Consumers must derive family membership from these constants (or from
``LOST_ME_SLUGS``), never from a hardcoded family string — a rename would then
fail silently rather than loudly.
"""

# family → the emotions under it, in display order. Families are UI-only.
FAMILY_WRECKED = "It wrecked me"
FAMILY_GOOD = "It felt good"
FAMILY_SKIN = "It got under my skin"
FAMILY_WANT = "It made me want"
FAMILY_GOT = "It got me"
FAMILY_LOST = "It lost me"

# Each emotion carries a `name` (the plain word, e.g. "confusion") and a `phrase`
# (the first-person line the UI shows, e.g. "I have no idea what happened"). The
# frontend displays the phrase; `name` is the canonical label used where a single
# word is wanted.
EMOTIONS = [
    # It wrecked me
    {"slug": "devastation", "family": FAMILY_WRECKED, "name": "devastation", "phrase": "I was not okay after",                     "symbol": "🖤", "color": "#3D2B3D", "description": "Complete emotional destruction — the books that ruin you"},
    {"slug": "grief",       "family": FAMILY_WRECKED, "name": "grief",       "phrase": "I'm still not over it",                    "symbol": "💧", "color": "#6B4F8E", "description": "Loss, absence, mourning — the ache"},
    {"slug": "catharsis",   "family": FAMILY_WRECKED, "name": "catharsis",   "phrase": "I needed that cry more than I knew",       "symbol": "✨", "color": "#C9A96E", "description": "Release, relief, the exhale after tension"},
    # It felt good
    {"slug": "comfort",     "family": FAMILY_GOOD, "name": "comfort",     "phrase": "it felt like a blanket",                      "symbol": "☕", "color": "#8E6B4B", "description": "Safety, warmth, being held by a book"},
    {"slug": "tenderness",  "family": FAMILY_GOOD, "name": "tenderness",  "phrase": "it was so soft with me",                      "symbol": "🌸", "color": "#9B6B7B", "description": "Gentle love, care, soft emotional moments"},
    {"slug": "joy",         "family": FAMILY_GOOD, "name": "joy",         "phrase": "I closed it grinning",                        "symbol": "☀️", "color": "#E0A458", "description": "Delight, gladness, the lightness a book can give"},
    {"slug": "amusement",   "family": FAMILY_GOOD, "name": "amusement",   "phrase": "I laughed out loud, alone, like a lunatic",   "symbol": "😄", "color": "#C9B24B", "description": "Sharp humour, wit, the perfectly placed line that makes you grin"},
    # It got under my skin
    {"slug": "dread",       "family": FAMILY_SKIN, "name": "dread",       "phrase": "I read it with my shoulders up",              "symbol": "😰", "color": "#4B6B8E", "description": "Anxiety, foreboding, existential unease"},
    {"slug": "rage",        "family": FAMILY_SKIN, "name": "rage",        "phrase": "I wanted to throw it",                        "symbol": "⚡", "color": "#C44B4B", "description": "Fury, injustice, the urge to burn things down"},
    {"slug": "revulsion",   "family": FAMILY_SKIN, "name": "revulsion",   "phrase": "I need a shower",                             "symbol": "🤢", "color": "#6B7A4B", "description": "Disgust, recoil, the book that crawls on you"},
    # It made me want
    {"slug": "longing",     "family": FAMILY_WANT, "name": "longing",     "phrase": "I wanted it so badly it hurt",                "symbol": "🕊", "color": "#5B6B8E", "description": "Distance, wanting what you cannot have"},
    {"slug": "desire",      "family": FAMILY_WANT, "name": "desire",      "phrase": "the tension nearly killed me",                "symbol": "💜", "color": "#9B5B8E", "description": "Wanting, romantic tension, the pull toward"},
    {"slug": "nostalgia",   "family": FAMILY_WANT, "name": "nostalgia",   "phrase": "it sent me straight back",                    "symbol": "🍂", "color": "#B07B4B", "description": "The ache of memory, a time you cannot return to"},
    # It got me
    {"slug": "awe",         "family": FAMILY_GOT, "name": "awe",         "phrase": "I had to put it down and stare at a wall",     "symbol": "🌟", "color": "#4B7B6B", "description": "Wonder, scale, the sublime"},
    {"slug": "recognition", "family": FAMILY_GOT, "name": "recognition", "phrase": "how did it know that about me",                "symbol": "🪞", "color": "#4B8E8A", "description": "Being seen — the book that knew you already"},
    {"slug": "absorption",  "family": FAMILY_GOT, "name": "absorption",  "phrase": "I couldn't put it down",                       "symbol": "🧲", "color": "#4A7B9D", "description": "Immersion — the book that took the night from you"},
    # It lost me
    {"slug": "boredom",     "family": FAMILY_LOST, "name": "boredom",       "phrase": "my two brain cells died",                   "symbol": "😐", "color": "#8A8A7A", "description": "Flatness, the pages that wouldn't turn"},
    {"slug": "confusion",   "family": FAMILY_LOST, "name": "confusion",     "phrase": "I have no idea what happened",              "symbol": "🌀", "color": "#7B6B9B", "description": "Lost the thread, couldn't follow, unmoored"},
    {"slug": "indifference","family": FAMILY_LOST, "name": "indifference",  "phrase": "I felt absolutely nothing",                 "symbol": "◻️", "color": "#9A9A9A", "description": "Nothing landed — you closed it and felt nothing"},
]

EMOTIONS_BY_SLUG = {e["slug"]: e for e in EMOTIONS}
VALID_SLUGS = set(EMOTIONS_BY_SLUG.keys())

# The "It lost me" family are registers of *disengagement* — they describe a book
# failing you, not a reading identity. They're valid to tag and to score against
# (as anti-emotions), but no archetype is anchored on them.
#
# Note revulsion is NOT among them: disgust is a book doing something *to* you, an
# experience with a pulse behind it, so it lives under "It got under my skin" and
# is free to anchor an archetype (The Adrenaline Seeker). Disengagement is only
# boredom, confusion, indifference.
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
    # "obsession" stays pointed at desire, NOT at the new "absorption": obsession is
    # wanting, absorption is immersion. Absorption therefore launches with zero
    # historical tags, which is expected cold-start behaviour.
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
    "catharsis":    {"category": "release",         "feeling": "let go"},
    "comfort":      {"category": "warmth",          "feeling": "feel safe"},
    "tenderness":   {"category": "softness",        "feeling": "be gentle"},
    "joy":          {"category": "delight",         "feeling": "feel light"},
    "amusement":    {"category": "humour",          "feeling": "laugh"},
    "dread":        {"category": "fear",            "feeling": "sit with fear"},
    "rage":         {"category": "fury",            "feeling": "get angry"},
    "revulsion":    {"category": "disgust",         "feeling": "recoil"},
    "longing":      {"category": "absence",         "feeling": "miss things"},
    "desire":       {"category": "wanting",         "feeling": "long for things"},
    "nostalgia":    {"category": "memory",          "feeling": "miss the past"},
    "awe":          {"category": "wonder",          "feeling": "be small in something vast"},
    "recognition":  {"category": "being seen",      "feeling": "see yourself"},
    "absorption":   {"category": "immersion",       "feeling": "get lost in something"},
    "boredom":      {"category": "dullness",        "feeling": "admit disinterest"},
    "confusion":    {"category": "disorientation",  "feeling": "be lost"},
    "indifference": {"category": "detachment",      "feeling": "feel nothing"},
}


if __name__ == "__main__":
    assert canonicalize("grief") == "grief"
    assert canonicalize("chaos") == "confusion"
    assert canonicalize("nostalgia") == "nostalgia"
    assert canonicalize("made_up") is None
    assert set(BLIND_SPOT_HINTS) == VALID_SLUGS
    print("All assertions passed.")
    for e in EMOTIONS:
        print(f"  {e['slug']:12}  {e['symbol']}  {e['phrase']:38}  {e['family']}")
