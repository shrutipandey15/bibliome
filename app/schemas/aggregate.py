"""Read shapes for the per-book emotional aggregate (B8.6)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Confidence = Literal["predicted", "emerging", "confirmed"]


class EmotionAggregate(BaseModel):
    """One emotion's standing for this book, across readers."""

    mean_strength: float          # average intensity among readers who tagged it
    count: int                    # how many readers reached for it
    tagged_by_fraction: float     # ...as a fraction of all readers of the book


class BookProfileResponse(BaseModel):
    book_id: uuid.UUID
    title: str
    author: str | None

    reader_count: int
    emotion_profile: dict[str, EmotionAggregate]
    verdict_profile: dict[str, float]
    dnf_rate: float

    confidence: Confidence
    # Plain-language rendering of the tier. The UI must show this next to the
    # profile — the tier is the product's honesty, not a footnote (B8.4).
    confidence_label: str
    updated_at: datetime | None = None


class BookProfileWithheld(BaseModel):
    """Returned instead of a profile when too few readers back it (B8.6).

    An aggregate over one or two readers is those readers' private tagging wearing
    an aggregate's clothes, so it is never served to anyone else.
    """

    book_id: uuid.UUID
    title: str
    author: str | None
    available: Literal[False] = False
    reason: str
    readers_needed: int
