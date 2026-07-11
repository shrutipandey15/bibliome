"""
Room decoration registry — single source of truth for all decorative items.
"""

DECORATIONS = [
    # --- Starters (always available) ---
    {
        "id": "plant_basic",
        "name": "Small Succulent",
        "description": "A quiet little plant that asks for nothing.",
        "unlock_condition": "Available from the start",
        "check": lambda user, count, **kw: True,
    },
    {
        "id": "mug",
        "name": "Coffee Mug",
        "description": "Still warm. Always warm.",
        "unlock_condition": "Available from the start",
        "check": lambda user, count, **kw: True,
    },
    {
        "id": "bookend",
        "name": "Metal Bookend",
        "description": "Holds everything together.",
        "unlock_condition": "Available from the start",
        "check": lambda user, count, **kw: True,
    },

    # --- Milestone unlocks ---
    {
        "id": "pothos",
        "name": "Trailing Pothos",
        "description": "It grows when you're not looking.",
        "unlock_condition": "Log 5 books",
        "check": lambda user, count, **kw: count >= 5,
    },
    {
        "id": "candle",
        "name": "Flickering Candle",
        "description": "The light dances on nearby spines.",
        "unlock_condition": "Log 10 books",
        "check": lambda user, count, **kw: count >= 10,
    },
    {
        "id": "sleeping_cat",
        "name": "Sleeping Cat",
        "description": "Curled up between your favorites.",
        "unlock_condition": "Log 15 books",
        "check": lambda user, count, **kw: count >= 15,
    },

    # --- Event unlocks ---
    {
        "id": "glyph_figurine",
        "name": "Personality Figurine",
        "description": "Your reading identity, cast in miniature.",
        "unlock_condition": "Generate your DNA",
        "check": lambda user, count, **kw: user.personality_type is not None,
    },
    {
        "id": "mini_dna_frame",
        "name": "Mini DNA Frame",
        "description": "Your card, framed and tiny.",
        "unlock_condition": "Share your DNA card",
        "check": lambda user, count, **kw: kw.get("has_share_token", False),
    },
    {
        "id": "crystal_prism",
        "name": "Crystal Prism",
        "description": "Refracts everything into feeling.",
        "unlock_condition": "Tag 5 different emotions",
        "check": lambda user, count, **kw: (
            len(user.cached_dna_profile.get("emotion_frequency", {})) >= 5
            if user.cached_dna_profile else False
        ),
    },
    {
        "id": "broken_heart",
        "name": "Cracked Heart",
        "description": "A book broke this. Gold holds the pieces.",
        "unlock_condition": "Log a book at intensity 10",
        "check": lambda user, count, **kw: kw.get("has_intensity_10", False),
    },
    {
        "id": "mini_clock",
        "name": "2AM Clock",
        "description": "Frozen at the hour you couldn't stop reading.",
        "unlock_condition": "Tag a book with the 2AM emotion",
        "check": lambda user, count, **kw: kw.get("has_2am_tag", False),
    },
]

DECORATION_MAP = {d["id"]: d for d in DECORATIONS}
VALID_DECO_IDS = set(DECORATION_MAP.keys())


def compute_unlocks(
    user,
    entry_count: int,
    has_intensity_10: bool = False,
    has_2am_tag: bool = False,
    has_share_token: bool = False,
) -> list[str]:
    """Compute all decoration IDs a user has earned. Pure function."""
    return [
        d["id"] for d in DECORATIONS
        if d["check"](
            user, entry_count,
            has_intensity_10=has_intensity_10,
            has_2am_tag=has_2am_tag,
            has_share_token=has_share_token,
        )
    ]


def build_decoration_catalog(unlocked_ids: list[str]) -> list[dict]:
    """Build the full catalog with unlock status for the frontend."""
    unlocked_set = set(unlocked_ids)
    return [
        {
            "id": d["id"],
            "name": d["name"],
            "description": d["description"],
            "unlock_condition": d["unlock_condition"],
            "unlocked": d["id"] in unlocked_set,
        }
        for d in DECORATIONS
    ]
