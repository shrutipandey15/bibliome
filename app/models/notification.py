"""Calm notifications (Phase 4, blueprint Feature 5).

Three tiers: 0 = security (immediate, non-disableable, bypasses quiet hours),
1 = direct & consented (batched), 2 = ambient/community (weekly digest only).
"""

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

TIER_SECURITY = 0
TIER_DIRECT = 1
TIER_DIGEST = 2


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index("ix_notifications_user_unread", "user_id", "read_at"),
        Index("ix_notifications_batch", "user_id", "batch_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Coalescing key for batching (e.g. "echo_reply:<echo_id>"); NULL = never batched.
    batch_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Quiet-hours deferral: the notification is surfaced only once now >= deliver_after.
    deliver_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationPrefs(Base):
    __tablename__ = "notification_prefs"
    __table_args__ = (
        CheckConstraint("digest_day BETWEEN 0 AND 6", name="ck_prefs_digest_day"),
        CheckConstraint("digest_hour BETWEEN 0 AND 23", name="ck_prefs_digest_hour"),
        CheckConstraint("fact_hour BETWEEN 0 AND 23", name="ck_prefs_fact_hour"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    digest_enabled: Mapped[bool] = mapped_column(nullable=False, server_default="true")  # tier 2
    # Quiet hours as local-time hours [start, end); NULL = no quiet hours.
    quiet_hours_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quiet_hours_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    # When the weekly digest is due, in `timezone`. Python weekday: 6 = Sunday.
    digest_day: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="6", default=6)
    digest_hour: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="9", default=9)
    # The daily book fact: on by default, at this local hour.
    fact_enabled: Mapped[bool] = mapped_column(nullable=False, server_default="true", default=True)
    fact_hour: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="10", default=10)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NotificationDigest(Base):
    """One row per user per period, so the weekly digest job is idempotent."""

    __tablename__ = "notification_digests"
    __table_args__ = (UniqueConstraint("user_id", "period", name="uq_digest_period"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period: Mapped[str] = mapped_column(String(12), nullable=False)  # e.g. "2026-W28"
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Which section led, and the subject line: the history the next week's
    # variety rules read.
    lead: Mapped[str | None] = mapped_column(String(30), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DigestClick(Base):
    """A reader clicked through a digest section. Once per (digest, section)."""

    __tablename__ = "digest_clicks"
    __table_args__ = (UniqueConstraint("digest_id", "section", name="uq_digest_click"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    digest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notification_digests.id", ondelete="CASCADE"), nullable=False
    )
    section: Mapped[str] = mapped_column(String(30), nullable=False)
    clicked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DailyFact(Base):
    """The fact a reader was sent on a (local) day. One per day; never the same fact twice."""

    __tablename__ = "daily_facts"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_daily_fact_day"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    fact_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
