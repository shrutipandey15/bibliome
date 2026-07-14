"""The Prompt — a small shared question that changes weekly (Echo UX B6.5).

Everyone answers the same question in the same window: a campfire, not a feed. It
manufactures a populated feed on day one without any follower graph, and — because
the only thing to "win" is answering — it can't become a popularity contest.

Prompts are CURATED (seeded by an operator), never user-generated: user-generated
prompts would be a whole moderation surface unto themselves.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Prompt(Base):
    __tablename__ = "prompts"
    __table_args__ = (
        # The "which prompt is live now" lookup keys on the active window.
        Index("ix_prompts_window", "starts_at", "ends_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question: Mapped[str] = mapped_column(String(200), nullable=False)

    # The window this prompt is the live campfire. `GET /prompts/today` returns the
    # prompt whose window contains "now" (most recent start wins if they ever overlap).
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
