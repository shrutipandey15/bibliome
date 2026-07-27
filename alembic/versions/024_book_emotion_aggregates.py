"""Per-book emotional aggregate table (B8.2).

One row per canonical book: the emotional profile across every reader who tagged
it, plus the confidence tier that keeps the claim honest at low reader counts.

Revision ID: 024_book_aggregates
Revises: 023_book_identity
Create Date: 2026-07-27 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "024_book_aggregates"
down_revision: Union[str, None] = "023_book_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "book_emotion_aggregates",
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reader_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("emotion_profile", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("verdict_profile", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("dnf_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.String(20), nullable=False, server_default="emerging"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("book_id"),
        sa.CheckConstraint(
            "confidence IN ('predicted','emerging','confirmed')",
            name="check_aggregate_confidence",
        ),
        sa.CheckConstraint("reader_count >= 0", name="check_aggregate_reader_count"),
        sa.CheckConstraint("dnf_rate >= 0 AND dnf_rate <= 1", name="check_aggregate_dnf_rate"),
    )
    # The feed/recommender will want "books with enough readers to trust".
    op.create_index(
        "ix_book_aggregates_confidence_readers",
        "book_emotion_aggregates",
        ["confidence", "reader_count"],
    )


def downgrade() -> None:
    op.drop_index("ix_book_aggregates_confidence_readers", table_name="book_emotion_aggregates")
    op.drop_table("book_emotion_aggregates")
