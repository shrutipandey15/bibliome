"""Shared reaction vocabulary for chat surfaces (collection rooms, resonance
threads) — distinct from Echo's own `REACTION_KINDS` (`app/models/echo.py`),
which is scoped to marginalia on a public post. One vocabulary here rather than
one per surface, so the two chat rooms don't drift into different reaction sets
over time.

Not emoji, on purpose — this app's house style (see Echo's `felt_this`/`⌇` etc.)
is a small set of named, meaningful marks rather than an open emoji picker.
"""

CHAT_REACTION_KINDS = ("resonated", "noted", "reconsidered", "warm")
