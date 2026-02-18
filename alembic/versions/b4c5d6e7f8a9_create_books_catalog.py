"""create books catalog table with trigram search

Revision ID: b4c5d6e7f8a9
Revises: a1b2c3d4e5f6
Create Date: 2025-02-18 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable trigram extension for fuzzy search
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "books",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("author", sa.String(200)),
        sa.Column("cover_url", sa.String(500)),
        sa.Column("published_year", sa.String(4)),
        sa.Column("description", sa.Text),
        sa.Column("title_normalized", sa.String(300), nullable=False),
        sa.Column("author_normalized", sa.String(200)),
        sa.Column("isbn_13", sa.String(13), unique=True),
        sa.Column("isbn_10", sa.String(10), unique=True),
        sa.Column("source", sa.String(20), server_default="google"),
        sa.Column("popularity", sa.Integer, server_default="0"),
        sa.Column("cover_verified", sa.Boolean, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Trigram index for fast fuzzy title search
    op.execute(
        "CREATE INDEX idx_books_title_trgm ON books USING gin (title_normalized gin_trgm_ops)"
    )
    # Regular index for author search
    op.execute(
        "CREATE INDEX idx_books_author_norm ON books (author_normalized)"
    )
    # Popularity index for sorting
    op.create_index("idx_books_popularity", "books", ["popularity"], postgresql_using="btree")


def downgrade() -> None:
    op.drop_table("books")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")