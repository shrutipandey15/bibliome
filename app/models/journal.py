"""The journal — ciphertext the server cannot read, tags it can (VISION §6).

Three tables, and the split between them *is* the privacy model
(``journalCryptoContract.md``):

- ``journal_key_bundles`` — wrapped data-key material. Inert without the user's
  password or recovery code. We store it, serve it back, and can never unwrap it.
- ``journal_entries`` — opaque ciphertext. Not searchable, not indexable, not
  readable by the DB, the logs, or us.
- ``journal_emotions`` — the canonical 18 slugs + 1–10 strength, in PLAINTEXT, on
  purpose: tags are the only thing DNA needs and the only thing that isn't
  incriminating. Prose encrypted, tags readable. That is the deliberate line.

Journal entries are per-user and private. There is no sharing surface, no
visibility column, and no public read path — not "off by default", absent.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# AEADs and KDFs the API will accept. An allowlist, not a free-text field: it is
# the one thing the server *can* check about the crypto, so it checks it.
JOURNAL_CIPHERS = ("AES-GCM", "XChaCha20-Poly1305")
JOURNAL_KDFS = ("argon2id", "pbkdf2-sha256")


class JournalKeyBundle(Base):
    """One row per user. Everything here is useless to us by construction."""

    __tablename__ = "journal_key_bundles"

    # The user *is* the key — one journal, one bundle, no ambiguity about which
    # bundle a client should be unwrapping.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )

    cipher: Mapped[str] = mapped_column(String(30), nullable=False)
    kdf: Mapped[str] = mapped_column(String(30), nullable=False)
    # Cost parameters (argon2: memory/iterations/parallelism; pbkdf2: iterations).
    # Opaque to the server — the client that derived the key is the only thing
    # that needs to understand them; we store them so it can.
    kdf_params: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # The password path.
    password_salt: Mapped[str] = mapped_column(String(200), nullable=False)
    wrapped_dek: Mapped[str] = mapped_column(String(500), nullable=False)
    wrapped_dek_nonce: Mapped[str] = mapped_column(String(100), nullable=False)

    # The recovery-code path — independent of the password, which is exactly why a
    # password reset doesn't have to be the end of the journal. Non-nullable: a
    # journal set up without a recovery copy is one password reset from oblivion.
    recovery_salt: Mapped[str] = mapped_column(String(200), nullable=False)
    wrapped_dek_recovery: Mapped[str] = mapped_column(String(500), nullable=False)
    wrapped_dek_recovery_nonce: Mapped[str] = mapped_column(String(100), nullable=False)

    # Bumped on DEK rotation; entries record the version they were sealed under so
    # a rotation never has to be atomic across every row.
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # True when the account password changed without the client sending a re-wrap
    # (notably: any password *reset*). The password wrap is then dead and only the
    # recovery code can unlock the journal. Surfaced honestly on GET /journal/key.
    password_wrap_stale: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "cipher IN ('AES-GCM','XChaCha20-Poly1305')", name="check_journal_cipher"
        ),
        CheckConstraint(
            "kdf IN ('argon2id','pbkdf2-sha256')", name="check_journal_kdf"
        ),
        CheckConstraint("key_version >= 1", name="check_journal_key_version"),
    )

    user: Mapped["User"] = relationship("User", back_populates="journal_key_bundle")


class JournalEntry(Base):
    """A day's writing, sealed. ``ciphertext`` is opaque base64 — the DB cannot
    index it, our logs must never carry it, and no query can filter on it."""

    __tablename__ = "journal_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The day the writing belongs to (not created_at — you can write Tuesday's
    # entry on Wednesday). Entries group by this into one continuous book.
    # Deliberately NOT unique per user: a day can hold several passes.
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)

    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    nonce: Mapped[str] = mapped_column(String(100), nullable=False)
    # Which wrapping generation sealed this row (see JournalKeyBundle.key_version).
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("key_version >= 1", name="check_journal_entry_key_version"),
        # The paging key: newest day first, id as the tie-break — the same
        # (sort key, id) keyset shape the shelf uses, so cursors stay stable when
        # several entries share a date.
        Index("ix_journal_entries_user_date", "user_id", "entry_date", "id"),
    )

    user: Mapped["User"] = relationship("User", back_populates="journal_entries")
    emotions: Mapped[list["JournalEmotion"]] = relationship(
        "JournalEmotion", back_populates="entry", cascade="all, delete-orphan"
    )


class JournalEmotion(Base):
    """Plaintext tag on an encrypted entry. Mirrors ``EntryEmotion`` field for
    field on purpose — same vocabulary, same 1–10 strength, so the DNA pipeline
    treats a named day exactly like a named book."""

    __tablename__ = "journal_emotions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("journal_entries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    emotion_id: Mapped[str] = mapped_column(String(30), nullable=False)
    strength: Mapped[int] = mapped_column(Integer, default=5)

    __table_args__ = (
        UniqueConstraint("journal_entry_id", "emotion_id", name="uq_journal_emotion"),
        CheckConstraint(
            "strength >= 1 AND strength <= 10", name="check_journal_strength_range"
        ),
    )

    entry: Mapped["JournalEntry"] = relationship(
        "JournalEntry", back_populates="emotions"
    )
