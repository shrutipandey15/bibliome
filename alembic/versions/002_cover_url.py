"""add cover_url and isbn to book_entries

Revision ID: 002_cover_url
Revises: 001_dna_cache
Create Date: 2026-02-13

"""
from alembic import op
import sqlalchemy as sa

revision = "002_cover_url"
down_revision = "001_dna_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("book_entries", sa.Column("cover_url", sa.String(500), nullable=True))
    op.add_column("book_entries", sa.Column("isbn", sa.String(13), nullable=True))


def downgrade() -> None:
    op.drop_column("book_entries", "isbn")
    op.drop_column("book_entries", "cover_url")