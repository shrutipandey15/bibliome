"""Resonance: pair matches, private threads, free-text messages.

One row per (pair, book), canonically ordered so the same two readers can never
be matched twice on the same book. Identity lives in the row because it must;
keeping it out of the response is the read path's job, not the schema's.

Deliberately absent: any counter, any denormalised "matches" tally on users or
books. There is nothing here to render a public number from.

Revision ID: 027_resonance
Revises: 026_journal
Create Date: 2026-07-30 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "027_resonance"
down_revision: Union[str, None] = "026_journal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "resonance_matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_a",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_b",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "book_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("shared_emotions", postgresql.JSONB(), nullable=False),
        sa.Column("strength", sa.String(10), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="suggested"),
        sa.Column("initiator_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("initiator_note", sa.Text(), nullable=True),
        sa.Column("responder_note", sa.Text(), nullable=True),
        sa.Column("declined_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("surfaced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        # The dedupe key. Enforced here, not in the batch job, because the batch
        # job will race itself the first time it runs on two workers.
        sa.UniqueConstraint("user_a", "user_b", "book_id", name="uq_resonance_pair_book"),
        sa.CheckConstraint("user_a < user_b", name="check_resonance_pair_order"),
        sa.CheckConstraint(
            "status IN ('suggested','pending','connected','declined')",
            name="check_resonance_status",
        ),
        sa.CheckConstraint("strength IN ('strong','light')", name="check_resonance_strength"),
    )
    op.create_index("ix_resonance_matches_book_id", "resonance_matches", ["book_id"])
    op.create_index("ix_resonance_user_a_status", "resonance_matches", ["user_a", "status"])
    op.create_index("ix_resonance_user_b_status", "resonance_matches", ["user_b", "status"])

    op.create_table(
        "resonance_threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "match_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("resonance_matches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("closed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("match_id", name="uq_thread_match"),
        sa.CheckConstraint("status IN ('open','closed')", name="check_thread_status"),
    )

    op.create_table(
        "resonance_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("resonance_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # The keyset paging key for a thread's transcript.
    op.create_index(
        "ix_resonance_messages_thread", "resonance_messages", ["thread_id", "created_at", "id"]
    )

    # The candidate query joins entry_emotions by emotion_id across users; without
    # this it is a sequential scan of every tag in the system per reader.
    op.create_index("ix_entry_emotions_emotion_id", "entry_emotions", ["emotion_id"])


def downgrade() -> None:
    op.drop_index("ix_entry_emotions_emotion_id", table_name="entry_emotions")
    op.drop_index("ix_resonance_messages_thread", table_name="resonance_messages")
    op.drop_table("resonance_messages")
    op.drop_table("resonance_threads")
    op.drop_index("ix_resonance_user_b_status", table_name="resonance_matches")
    op.drop_index("ix_resonance_user_a_status", table_name="resonance_matches")
    op.drop_index("ix_resonance_matches_book_id", table_name="resonance_matches")
    op.drop_table("resonance_matches")
