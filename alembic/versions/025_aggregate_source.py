"""Provenance on book aggregates: reader-derived vs. LLM seed (B8.5).

The ``predicted`` confidence tier already existed but nothing wrote it. Seeding
predicted profiles from an LLM makes provenance load-bearing rather than
cosmetic: a seed must never be mistakable for reader data, and it must be
overwritable the moment a real reader tags the book.

``confidence`` answers "how much should you trust this?"; ``source`` answers
"where did it come from?". They are not the same question — a row can be
``predicted`` only because it is ``llm``, but the reverse inference is not one we
want the code to have to make.

Revision ID: 025_aggregate_source
Revises: 024_book_aggregates
Create Date: 2026-07-27 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "025_aggregate_source"
down_revision: Union[str, None] = "024_book_aggregates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Everything that exists today was computed from real reader tags.
    op.add_column(
        "book_emotion_aggregates",
        sa.Column("source", sa.String(20), nullable=False, server_default="readers"),
    )
    op.create_check_constraint(
        "check_aggregate_source",
        "book_emotion_aggregates",
        "source IN ('readers','llm')",
    )
    # The seeding script's working set is "books with no aggregate or an llm one",
    # and the deviation engine's is "readers-sourced and trustworthy".
    op.create_index(
        "ix_book_aggregates_source",
        "book_emotion_aggregates",
        ["source"],
    )


def downgrade() -> None:
    op.drop_index("ix_book_aggregates_source", table_name="book_emotion_aggregates")
    op.drop_constraint("check_aggregate_source", "book_emotion_aggregates", type_="check")
    op.drop_column("book_emotion_aggregates", "source")
