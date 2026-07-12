import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Single source of truth for who can see a profile (blueprint §2.3, B2.1).
VISIBILITY_VALUES = ("private", "community", "public")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    username: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    display_name: Mapped[str | None] = mapped_column(String(100))
    bio: Mapped[str | None] = mapped_column(String(300), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    personality_type: Mapped[str | None] = mapped_column(String(100))
    # Pseudonymous public handle (Phase 3). Defaults to the username; changeable,
    # rate-limited, with old handles kept in handle_history for a grace window.
    handle: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    handle_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Visibility spine (§2.3): private (default) / community / public.
    profile_visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="private"
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    reset_token: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    reset_token_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # DNA caching — dirty flag flips true on entry create/update/delete
    dna_dirty: Mapped[bool] = mapped_column(Boolean, default=True)
    cached_dna_profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Reading Room
    room_layout: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    room_unlocks: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Mirror insight cache
    cached_insight: Mapped[str | None] = mapped_column(Text, nullable=True)
    cached_insight_week: Mapped[str | None] = mapped_column(String(10), nullable=True)
    cached_weekly_memory: Mapped[str | None] = mapped_column(Text, nullable=True)
    cached_weekly_memory_week: Mapped[str | None] = mapped_column(String(10), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    entries: Mapped[list["BookEntry"]] = relationship(
        "BookEntry", back_populates="user", cascade="all, delete-orphan"
    )
    dna_snapshots: Mapped[list["DNASnapshot"]] = relationship(
        "DNASnapshot", back_populates="user", cascade="all, delete-orphan"
    )
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )
    share_tokens: Mapped[list["ShareToken"]] = relationship(
        "ShareToken", back_populates="user", cascade="all, delete-orphan"
    )

    @hybrid_property
    def is_public(self) -> bool:
        """Back-compat derived flag — the real control is profile_visibility."""
        return self.profile_visibility == "public"

    @is_public.expression
    def is_public(cls):
        return cls.profile_visibility == "public"