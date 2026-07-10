"""add shelf_position to book_entries

Revision ID: 012_add_shelf_position
Revises: 011_add_cached_weekly_memory
Create Date: 2026-04-20

"""
from alembic import op
import sqlalchemy as sa


revision = "012_add_shelf_position"
down_revision = "011_add_cached_weekly_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("book_entries", sa.Column("shelf_position", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("book_entries", "shelf_position")
