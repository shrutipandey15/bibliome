"""Canonical 21-feeling vocabulary — single source of truth for Bibliome.

v3 (Emotion & DNA rework, Sept 2026). The six *families* ("it broke me", "it
hooked me", …) are a UI grouping only. We store and reason over the flat
``slug``; the ``family`` field is served so the frontend can group without
hardcoding its own taxonomy (that divergence is exactly what caused the old P2-9
drift).

What changed from the 18-emotion set, and why (tested with a blind reader panel,
see the "Bibliome Emotion & DNA Rework Spec"):

- The "it lost me" family (boredom, revulsion, confusion, indifference) is gone
  from the picker. Those are verdicts about a book, not feelings it caused; they
  now live in the verdict step (``book_entries.verdict`` / ``verdict_reason`` /
  ``dnf_reason``). Existing tags stay stored and readable (``RETIRED_EMOTIONS``)
  but no longer feed any vector.
- devastation merged into grief, tenderness merged into comfort.
- New: haunted, thrill, shock, attachment, hope, swoon, conflicted, insight,
  beauty.
- Every phrase must carry its whole meaning alone: the picker shows group name
  and phrase only, never the description. Phrases that describe something the
  reader *did* or *felt in their body* tested best.
"""

FAMILY_BROKE = "it broke me"
FAMILY_HOOKED = "it hooked me"
FAMILY_HELD = "it held me"
FAMILY_LIT = "it lit me up"
FAMILY_HEART = "it got my heart"
FAMILY_EYES = "it opened my eyes"

# Each emotion carries a `name` (the plain word, e.g. "heartbreak") and a `phrase`
# (the first-person line the UI shows, e.g. "it broke my heart"). The frontend
# displays the phrase; `name` is the label used where a single word is wanted.
EMOTIONS = [
    # it broke me
    {"slug": "grief",       "family": FAMILY_BROKE,  "name": "heartbreak",  "phrase": "it broke my heart",                    "symbol": "💧", "color": "#6B4F8E", "description": "Sorrow over a loss in the story that stays with you after the last page."},
    {"slug": "catharsis",   "family": FAMILY_BROKE,  "name": "catharsis",   "phrase": "I cried and felt lighter",             "symbol": "✨", "color": "#C9A96E", "description": "The release after the tension. A cry that leaves you lighter."},
    {"slug": "haunted",     "family": FAMILY_BROKE,  "name": "haunted",     "phrase": "a scene keeps coming back to me",      "symbol": "🕯", "color": "#3D2B3D", "description": "An image or scene that keeps returning, often eerie, long after you finish."},
    # it hooked me
    {"slug": "thrill",      "family": FAMILY_HOOKED, "name": "page-turner", "phrase": "I read it in one sitting",             "symbol": "⚡", "color": "#D08A3C", "description": "Pull and momentum. One more chapter until it's 3am — fast plot or quietly absorbing."},
    {"slug": "dread",       "family": FAMILY_HOOKED, "name": "tension",     "phrase": "it had me scared or on edge",          "symbol": "😰", "color": "#4B6B8E", "description": "Fear, suspense and unease while reading — or real-world worry it leaves behind."},
    {"slug": "shock",       "family": FAMILY_HOOKED, "name": "shock",       "phrase": "I did NOT see that coming",            "symbol": "🌀", "color": "#8E4B6B", "description": "The twist, the reveal, the turn that rearranged everything."},
    {"slug": "rage",        "family": FAMILY_HOOKED, "name": "anger",       "phrase": "what happened made me furious",        "symbol": "🔥", "color": "#C44B4B", "description": "Anger at an injustice inside the story — not anger at the book itself."},
    # it held me
    {"slug": "comfort",     "family": FAMILY_HELD,   "name": "comfort",     "phrase": "like a hug on a rainy day",            "symbol": "☕", "color": "#8E6B4B", "description": "Soothing: safe, warm and gentle, or calm and still. A soft place to land."},
    {"slug": "attachment",  "family": FAMILY_HELD,   "name": "attachment",  "phrase": "they felt like my friends",            "symbol": "🤝", "color": "#9B6B7B", "description": "Loving the characters like real people, and missing them when it ends."},
    {"slug": "nostalgia",   "family": FAMILY_HELD,   "name": "nostalgia",   "phrase": "it took me back to a younger me",      "symbol": "🍂", "color": "#B07B4B", "description": "It brings back your own past — a time, a place, an earlier you."},
    # it lit me up
    {"slug": "joy",         "family": FAMILY_LIT,    "name": "joy",         "phrase": "it made me so happy",                  "symbol": "☀️", "color": "#E0A458", "description": "Pure lightness. You put it down happier than you picked it up."},
    {"slug": "amusement",   "family": FAMILY_LIT,    "name": "laughter",    "phrase": "it made me laugh",                     "symbol": "😄", "color": "#C9B24B", "description": "Genuinely funny — out loud or quietly, dry or silly."},
    {"slug": "hope",        "family": FAMILY_LIT,    "name": "hope",        "phrase": "I closed it feeling inspired",         "symbol": "🌱", "color": "#7BA05B", "description": "Hopeful, uplifted, proud or motivated. Believing in people, or yourself, again."},
    # it got my heart
    {"slug": "swoon",       "family": FAMILY_HEART,  "name": "swoon",       "phrase": "it gave me butterflies",               "symbol": "🦋", "color": "#D47A9B", "description": "Giddy romantic delight. Kicking your feet over a love story."},
    {"slug": "desire",      "family": FAMILY_HEART,  "name": "desire",      "phrase": "the chemistry nearly killed me",       "symbol": "💜", "color": "#9B5B8E", "description": "Romantic or sexual tension between characters. The slow burn, the almost."},
    {"slug": "longing",     "family": FAMILY_HEART,  "name": "longing",     "phrase": "a soft ache for what I can't have",    "symbol": "🕊", "color": "#5B6B8E", "description": "Your own ache for something you can't have or can't quite name."},
    {"slug": "conflicted",  "family": FAMILY_HEART,  "name": "conflicted",  "phrase": "I shouldn't love this but I do",       "symbol": "🥀", "color": "#6B3A4D", "description": "Loving what you feel you shouldn't — a villain, a dark story. Guilty pleasure."},
    # it opened my eyes
    {"slug": "awe",         "family": FAMILY_EYES,   "name": "awe",         "phrase": "so vast it made me go quiet",          "symbol": "🌌", "color": "#4B7B6B", "description": "Wonder at something vast or grand. You feel small, and go quiet."},
    {"slug": "recognition", "family": FAMILY_EYES,   "name": "recognition", "phrase": "it knew me",                           "symbol": "🪞", "color": "#4B8E8A", "description": "Being seen. The book that already knew you."},
    {"slug": "insight",     "family": FAMILY_EYES,   "name": "insight",     "phrase": "it changed how I think",               "symbol": "💡", "color": "#5A7A9A", "description": "Ideas that engaged your mind. You learned something, or now see it differently."},
    {"slug": "beauty",      "family": FAMILY_EYES,   "name": "beauty",      "phrase": "the writing was so beautiful",         "symbol": "🖋", "color": "#A08BB8", "description": "Delight in the writing itself — sentences you read twice."},
]

EMOTIONS_BY_SLUG = {e["slug"]: e for e in EMOTIONS}
VALID_SLUGS = set(EMOTIONS_BY_SLUG.keys())

# Retired "it lost me" tags. Still stored on older entries and still displayable,
# but no longer offered and no longer valid for new writes. ``canonicalize`` maps
# them to None, so they never enter an emotion vector: disengagement is carried by
# the verdict step now, where it is a judgment and not a feeling.
RETIRED_EMOTIONS = [
    {"slug": "boredom",      "family": "retired", "name": "boredom",      "phrase": "my two brain cells died",         "symbol": "😐", "color": "#8A8A7A", "description": "Retired: now a verdict reason."},
    {"slug": "revulsion",    "family": "retired", "name": "revulsion",    "phrase": "I felt a little sick",            "symbol": "🤢", "color": "#6B7A4B", "description": "Retired: now a verdict reason."},
    {"slug": "confusion",    "family": "retired", "name": "confusion",    "phrase": "I have no idea what happened",    "symbol": "🌀", "color": "#7B6B9B", "description": "Retired: now a verdict reason."},
    {"slug": "indifference", "family": "retired", "name": "indifference", "phrase": "closed it and forgot it existed", "symbol": "◻️", "color": "#9A9A9A", "description": "Retired: now a verdict reason."},
]
RETIRED_BY_SLUG = {e["slug"]: e for e in RETIRED_EMOTIONS}
RETIRED_SLUGS = set(RETIRED_BY_SLUG)

# Old slug → canonical slug. Historical rows are remapped forward on read so they
# still count (migration 036 also rewrites them in place); anything with no
# sensible target is absent here and canonicalizes to None (skipped on read).
LEGACY_EMOTION_MAP: dict[str, str] = {
    "devastation": "grief",
    "tenderness":  "comfort",
    "healing":     "catharsis",
    "obsession":   "desire",
    "seen":        "comfort",      # was tenderness, which merged into comfort
    "wit":         "amusement",
    "two_am":      "longing",
    "2am":         "longing",
    "fascination": "insight",
}


def get_emotion(slug: str) -> dict | None:
    return EMOTIONS_BY_SLUG.get(slug) or RETIRED_BY_SLUG.get(slug)


def canonicalize(slug: str) -> str | None:
    """Return the canonical slug, remapping via LEGACY_EMOTION_MAP if needed.

    Retired slugs return None: they are readable history, not live vocabulary.
    """
    if slug in VALID_SLUGS:
        return slug
    return LEGACY_EMOTION_MAP.get(slug)


# Backward-compat alias used by existing entry schema and dna_engine
VALID_EMOTION_IDS = VALID_SLUGS


# ── The verdict step ──
# "How did it land?" — asked when a book is finished or reread. Replaced the old
# "would you read it again?" (yes | no | not_sure), which gave the wrong signal for
# books that wrecked a reader who loved them.
VERDICTS = ("loved", "liked", "mixed", "not_for_me")
NEGATIVE_VERDICTS = frozenset({"not_for_me"})

# Why a finished book disappointed — only after mixed / not_for_me.
VERDICT_REASONS = ("ending_let_me_down", "overhyped", "didnt_connect", "badly_written", "forgettable")

# Why a book was put down — only when status is abandoned / paused. Slugs are the
# stored values from migration 022; the UI shows "too slow" for `bored` and
# "I got lost" for `lost_me`.
DNF_REASONS = ("bored", "too_much", "badly_written", "wrong_time", "lost_me", "drifted")

# Old "read again" answers carried forward (migration 036).
LEGACY_VERDICT_MAP = {"yes": "liked", "no": "not_for_me", "not_sure": "mixed"}


# Hints used by the blind-spots endpoint:
#   "You have never tagged {emotion}. Either you avoid {category}, or you do not let yourself {feeling}."
BLIND_SPOT_HINTS: dict[str, dict[str, str]] = {
    "grief":        {"category": "loss",                "feeling": "mourn"},
    "catharsis":    {"category": "release",             "feeling": "let go"},
    "haunted":      {"category": "books that linger",   "feeling": "be unsettled"},
    "thrill":       {"category": "page-turners",        "feeling": "get swept along"},
    "dread":        {"category": "fear",                "feeling": "sit with fear"},
    "shock":        {"category": "twists",              "feeling": "be caught off guard"},
    "rage":         {"category": "injustice",           "feeling": "get angry"},
    "comfort":      {"category": "warmth",              "feeling": "feel safe"},
    "attachment":   {"category": "beloved characters",  "feeling": "get attached"},
    "nostalgia":    {"category": "memory",              "feeling": "miss the past"},
    "joy":          {"category": "delight",             "feeling": "feel light"},
    "amusement":    {"category": "humour",              "feeling": "laugh"},
    "hope":         {"category": "uplift",              "feeling": "believe again"},
    "swoon":        {"category": "romance",             "feeling": "swoon"},
    "desire":       {"category": "chemistry",           "feeling": "want"},
    "longing":      {"category": "absence",             "feeling": "miss things"},
    "conflicted":   {"category": "dark pleasures",      "feeling": "enjoy what you shouldn't"},
    "awe":          {"category": "wonder",              "feeling": "be small in something vast"},
    "recognition":  {"category": "being seen",          "feeling": "see yourself"},
    "insight":      {"category": "ideas",               "feeling": "change your mind"},
    "beauty":       {"category": "beautiful writing",   "feeling": "savour a sentence"},
}


if __name__ == "__main__":
    assert canonicalize("grief") == "grief"
    assert canonicalize("devastation") == "grief"
    assert canonicalize("tenderness") == "comfort"
    assert canonicalize("boredom") is None
    assert canonicalize("made_up") is None
    assert len(EMOTIONS) == 21
    print("All assertions passed.")
    for e in EMOTIONS:
        print(f"  {e['slug']:12}  {e['symbol']}  {e['phrase']:36}  {e['family']}")
