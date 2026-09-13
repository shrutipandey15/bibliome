"""Shared reaction vocabulary for chat surfaces (collection rooms, resonance
threads) — distinct from Echo's own `REACTION_KINDS` (`app/models/echo.py`),
which is scoped to marginalia on a public post. One vocabulary here rather than
one per surface, so the two chat rooms don't drift into different reaction sets
over time.

Not emoji, on purpose — this app's house style (see Echo's `felt_this`/`⌇` etc.)
is a small set of named, meaningful marks rather than an open emoji picker.

The three added 2026-09 (`underlined`/`quotable`/`chills`) round the set out
from "how you felt about the thread" to also cover "what a specific line did to
you" — a reading room's whole reason to react at all. Existing kinds are never
renamed or removed here: they're persisted on old reaction rows, and changing
the slug would silently orphan them.
"""

CHAT_REACTION_KINDS = (
    "resonated", "noted", "reconsidered", "warm",
    "underlined", "quotable", "chills",
)
