"""How far into a book you are.

A single nullable percentage on the entry. Nullable is the point: "I haven't said"
and "I'm at 0%" are different states, and a book with no answer must render as no
answer rather than a bar sitting at zero. Only meaningful while a book is open —
finishing one doesn't set it to 100, because the status already says that.

Deliberately a percentage and not a page count: the catalog has no reliable page
count per edition, and asking a reader which of four printings they hold is a
worse question than "roughly how far in?".

Revision ID: 028_entry_progress
Revises: 027_resonance
Create Date: 2026-08-09 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "028_entry_progress"
down_revision: Union[str, None] = "027_resonance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("book_entries", sa.Column("progress", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_book_entries_progress_range",
        "book_entries",
        "progress IS NULL OR (progress >= 0 AND progress <= 100)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_book_entries_progress_range", "book_entries", type_="check")
    op.drop_column("book_entries", "progress")
