"""The encrypted journal: ciphertext blobs, wrapped key material, plaintext tags.

The schema encodes the irreversible decision (``journalCryptoContract.md``): prose
lives in an opaque ``ciphertext`` column with no index and no text search, the
data-key exists only in wrapped form, and the emotion tags sit in their own
plaintext table because the DNA pipeline needs them and they are not the
incriminating part.

Nothing here is decryptable by this database, its backups, or its logs.

Revision ID: 026_journal
Revises: 025_aggregate_source
Create Date: 2026-07-30 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "026_journal"
down_revision: Union[str, None] = "025_aggregate_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Wrapped key material. One bundle per user; the PK enforces it. ──
    op.create_table(
        "journal_key_bundles",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("cipher", sa.String(30), nullable=False),
        sa.Column("kdf", sa.String(30), nullable=False),
        sa.Column("kdf_params", postgresql.JSONB(), nullable=False),
        sa.Column("password_salt", sa.String(200), nullable=False),
        sa.Column("wrapped_dek", sa.String(500), nullable=False),
        sa.Column("wrapped_dek_nonce", sa.String(100), nullable=False),
        # The recovery path is mandatory: a journal with only a password wrap is
        # one password reset away from being unopenable by anyone, ever.
        sa.Column("recovery_salt", sa.String(200), nullable=False),
        sa.Column("wrapped_dek_recovery", sa.String(500), nullable=False),
        sa.Column("wrapped_dek_recovery_nonce", sa.String(100), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "password_wrap_stale",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "cipher IN ('AES-GCM','XChaCha20-Poly1305')", name="check_journal_cipher"
        ),
        sa.CheckConstraint(
            "kdf IN ('argon2id','pbkdf2-sha256')", name="check_journal_kdf"
        ),
        sa.CheckConstraint("key_version >= 1", name="check_journal_key_version"),
    )

    # ── The entries. `ciphertext` is TEXT with no index of any kind: there is no
    # server-side query that could look inside it, so there is nothing to index. ──
    op.create_table(
        "journal_entries",
        # ids are generated application-side (uuid4), as everywhere else here.
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("nonce", sa.String(100), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("key_version >= 1", name="check_journal_entry_key_version"),
    )
    op.create_index("ix_journal_entries_user_id", "journal_entries", ["user_id"])
    # The keyset paging key. No unique constraint on (user_id, entry_date): a day
    # can hold several passes, and the journal is a continuous book, not a card DB.
    op.create_index(
        "ix_journal_entries_user_date",
        "journal_entries",
        ["user_id", "entry_date", "id"],
    )

    # ── The readable half. Same vocabulary and strength model as entry_emotions. ──
    op.create_table(
        "journal_emotions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "journal_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("journal_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("emotion_id", sa.String(30), nullable=False),
        sa.Column("strength", sa.Integer(), server_default="5"),
        sa.UniqueConstraint("journal_entry_id", "emotion_id", name="uq_journal_emotion"),
        sa.CheckConstraint(
            "strength >= 1 AND strength <= 10", name="check_journal_strength_range"
        ),
    )
    op.create_index(
        "ix_journal_emotions_entry_id", "journal_emotions", ["journal_entry_id"]
    )
    # The DNA loader's access path: "every tag this user has ever named".
    op.create_index("ix_journal_emotions_emotion_id", "journal_emotions", ["emotion_id"])


def downgrade() -> None:
    op.drop_index("ix_journal_emotions_emotion_id", table_name="journal_emotions")
    op.drop_index("ix_journal_emotions_entry_id", table_name="journal_emotions")
    op.drop_table("journal_emotions")
    op.drop_index("ix_journal_entries_user_date", table_name="journal_entries")
    op.drop_index("ix_journal_entries_user_id", table_name="journal_entries")
    op.drop_table("journal_entries")
    op.drop_table("journal_key_bundles")
