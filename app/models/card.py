"""The share card's stored link preview, and its first-party daily counts."""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CardPreview(Base):
    """The 1200×630 JPEG a /s/ link previews as, one per reader.

    Rendered ahead of the first crawler (when the link is made, and whenever the
    /s/ page sees the card has changed) and served as stored bytes, because
    WhatsApp caches a slow or failed image fetch against the URL. `hash` covers
    everything drawn on it and is the `?v=` in the image URL, so a changed card
    is a new URL and no crawler cache can hold the old one against it.
    """

    __tablename__ = "card_previews"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    hash: Mapped[str] = mapped_column(String(32), nullable=False)
    jpeg: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CardCount(Base):
    """One day's total for one card measure. Aggregate only, by design.

    No user id, no token, no IP: a row says "14 story shares on 3 October", never
    who. That is the whole of Bibliome's measurement (DNA card spec, "Goals and
    measures"); there are no third-party scripts.

      metric  share      key = story | season | square | link
              visit      key = human           (/s/ page opened by a person)
              preview    key = crawler name    (link-preview image fetched)
              signup     key = card            (arrived from "Find yours")
    """

    __tablename__ = "card_counts"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    metric: Mapped[str] = mapped_column(String(16), primary_key=True)
    key: Mapped[str] = mapped_column(String(24), primary_key=True)
    n: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
