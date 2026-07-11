"""books: unique (title_normalized, author_normalized) + non-null author_norm (B2.8 / P4-6)

Revision ID: 014_books_unique_title_author
Revises: 013_visibility_spine
Create Date: 2026-07-11 16:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "014_books_unique_title_author"
down_revision: Union[str, None] = "013_visibility_spine"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Normalize NULL authors to "" so the unique constraint dedupes them.
    op.execute("UPDATE books SET author_normalized = '' WHERE author_normalized IS NULL")

    # Collapse any pre-existing duplicates, keeping the most popular (then oldest,
    # then lowest id) row per (title_normalized, author_normalized). Catalog rows
    # have no FK dependents (entries store title/author inline), so this is safe.
    op.execute(
        """
        DELETE FROM books a USING books b
        WHERE a.title_normalized = b.title_normalized
          AND a.author_normalized = b.author_normalized
          AND (
                a.popularity < b.popularity
             OR (a.popularity = b.popularity AND a.created_at > b.created_at)
             OR (a.popularity = b.popularity AND a.created_at = b.created_at AND a.id > b.id)
          )
        """
    )

    op.alter_column("books", "author_normalized", nullable=False, server_default="")
    op.create_unique_constraint("uq_books_title_author", "books", ["title_normalized", "author_normalized"])


def downgrade() -> None:
    op.drop_constraint("uq_books_title_author", "books", type_="unique")
    op.alter_column("books", "author_normalized", nullable=True, server_default=None)
