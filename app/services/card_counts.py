"""First-party, aggregate daily counts for the share card (DNA card spec, Phase 3).

One integer per (day, metric, key). Nothing here takes a user, a token or an
IP: the counts say how the card is doing, never who did what.
"""

from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import CardCount

SHARE_FORMATS = ("story", "season", "square", "link")

# Link-preview fetchers, by the name we count them under. iMessage announces
# itself as facebookexternalhit + Twitterbot, so it counts as "facebook".
_CRAWLERS = (
    ("whatsapp", "whatsapp"),
    ("facebook", "facebookexternalhit"),
    ("facebook", "facebot"),
    ("x", "twitterbot"),
    ("telegram", "telegrambot"),
    ("slack", "slackbot"),
    ("discord", "discordbot"),
    ("linkedin", "linkedinbot"),
    ("apple", "applebot"),
    ("google", "googlebot"),
    ("bing", "bingbot"),
    ("other", "bot"),
    ("other", "crawler"),
    ("other", "spider"),
    ("other", "preview"),
    ("other", "curl/"),
    ("other", "python-"),
)


def crawler_name(user_agent: str | None) -> str | None:
    """Which crawler this is, or None for a person. An empty user agent is a
    script, not a person."""
    ua = (user_agent or "").lower()
    if not ua:
        return "other"
    for name, needle in _CRAWLERS:
        if needle in ua:
            return name
    return None


async def bump(db: AsyncSession, metric: str, key: str) -> None:
    day = datetime.now(timezone.utc).date()
    stmt = insert(CardCount).values(day=day, metric=metric, key=key[:24], n=1)
    await db.execute(stmt.on_conflict_do_update(
        index_elements=[CardCount.day, CardCount.metric, CardCount.key],
        set_={"n": CardCount.n + 1},
    ))
