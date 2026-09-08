"""Canonical 18-emotion vocabulary — single source of truth for Bibliome.

The five *families* ("it messed me up", "it held me", …) are a UI grouping only. We store
and reason over the flat ``slug``; the ``family`` field is served so the frontend
can group without hardcoding its own taxonomy (that divergence is exactly what
caused the old P2-9 drift).
"""

# family → the emotions under it, in display order. Families are UI-only.
FAMILY_HURT = "it messed me up"
FAMILY_HELD = "it held me"
FAMILY_WANTED = "the yearning"
FAMILY_MOVED = "it hit different"
FAMILY_LOST = "it lost me"

# Each emotion carries a `name` (the plain word, e.g. "confusion") and a `phrase`
# (the first-person line the UI shows, e.g. "I lost the plot"). The frontend displays
# the phrase; `name` is the canonical label used where a single word is wanted.
EMOTIONS = [
    # it messed me up
    {"slug": "devastation", "family": FAMILY_HURT, "name": "devastation", "phrase": "it wrecked me",                          "symbol": "🖤", "color": "#3D2B3D", "description": "The books that take something out of you. You finish and just sit there for a while."},
    {"slug": "grief",       "family": FAMILY_HURT, "name": "grief",       "phrase": "I'm still not over it",                   "symbol": "💧", "color": "#6B4F8E", "description": "Loss and mourning. The ache that doesn't leave when the book ends."},
    {"slug": "dread",       "family": FAMILY_HURT, "name": "dread",       "phrase": "shoulders up by my ears the entire time", "symbol": "😰", "color": "#4B6B8E", "description": "The low hum of something-bad-is-coming that you read the whole book with."},
    {"slug": "rage",        "family": FAMILY_HURT, "name": "rage",        "phrase": "I wanted to throw it across the room",    "symbol": "⚡", "color": "#C44B4B", "description": "Injustice you can't let go of. The book that makes you want to burn it all down."},
    # it held me
    {"slug": "comfort",     "family": FAMILY_HELD, "name": "comfort",     "phrase": "it felt like being tucked in",            "symbol": "☕", "color": "#8E6B4B", "description": "The book that's a soft place to land. Safe, warm, yours."},
    {"slug": "tenderness",  "family": FAMILY_HELD, "name": "tenderness",  "phrase": "handle-with-care kind of love",           "symbol": "🌸", "color": "#9B6B7B", "description": "Gentle, careful love. The book that's kind to you."},
    {"slug": "joy",         "family": FAMILY_HELD, "name": "joy",         "phrase": "I closed it smiling",                     "symbol": "☀️", "color": "#E0A458", "description": "Pure lightness. You put it down happier than you picked it up."},
    {"slug": "amusement",   "family": FAMILY_HELD, "name": "amusement",   "phrase": "I actually laughed out loud",             "symbol": "😄", "color": "#C9B24B", "description": "Genuinely funny. The lines you stop to read out loud to someone."},
    # the yearning
    {"slug": "longing",     "family": FAMILY_WANTED, "name": "longing",   "phrase": "the yearning was unreal",                 "symbol": "🕊", "color": "#5B6B8E", "description": "Wanting something you can't quite name, or can't have."},
    {"slug": "desire",      "family": FAMILY_WANTED, "name": "desire",    "phrase": "the tension nearly killed me",            "symbol": "💜", "color": "#9B5B8E", "description": "The pull toward. Romantic tension, want, the ache of almost."},
    {"slug": "nostalgia",   "family": FAMILY_WANTED, "name": "nostalgia", "phrase": "it smelled like a memory",                "symbol": "🍂", "color": "#B07B4B", "description": "The ache of a time you can't go back to. It puts you somewhere you used to be."},
    # it hit different
    {"slug": "awe",         "family": FAMILY_MOVED, "name": "awe",         "phrase": "I had to put it down and just sit there", "symbol": "🌟", "color": "#4B7B6B", "description": "Wonder at the sheer scale of it. You have to stop and let it land."},
    {"slug": "recognition", "family": FAMILY_MOVED, "name": "recognition", "phrase": "it read my mind",                        "symbol": "🪞", "color": "#4B8E8A", "description": "Being seen. The book that already knew you."},
    {"slug": "catharsis",   "family": FAMILY_MOVED, "name": "catharsis",   "phrase": "I cried and felt lighter after",         "symbol": "✨", "color": "#C9A96E", "description": "The release after the tension. A cry that leaves you lighter."},
    # it lost me
    {"slug": "boredom",     "family": FAMILY_LOST, "name": "boredom",       "phrase": "my two brain cells died",               "symbol": "😐", "color": "#8A8A7A", "description": "The pages wouldn't turn. You kept checking how much was left."},
    {"slug": "revulsion",   "family": FAMILY_LOST, "name": "revulsion",     "phrase": "I felt a little sick",                  "symbol": "🤢", "color": "#6B7A4B", "description": "Recoil. Something in it you couldn't sit with."},
    {"slug": "confusion",   "family": FAMILY_LOST, "name": "confusion",     "phrase": "I have no idea what happened",          "symbol": "🌀", "color": "#7B6B9B", "description": "You lost the thread and never found it again."},
    {"slug": "indifference","family": FAMILY_LOST, "name": "indifference",  "phrase": "closed it and forgot it existed",       "symbol": "◻️", "color": "#9A9A9A", "description": "It closed and left nothing behind. You felt nothing either way."},
]

EMOTIONS_BY_SLUG = {e["slug"]: e for e in EMOTIONS}
VALID_SLUGS = set(EMOTIONS_BY_SLUG.keys())

# The "it lost me" family are registers of *disengagement* — they describe a book
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
