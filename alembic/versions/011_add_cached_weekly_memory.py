"""add cached_weekly_memory columns to users

Revision ID: 011_add_cached_weekly_memory
Revises: 010_add_cached_insight
Create Date: 2026-04-20

"""
from alembic import op
import sqlalchemy as sa


revision = "011_add_cached_weekly_memory"
down_revision = "010_add_cached_insight"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("cached_weekly_memory", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("cached_weekly_memory_week", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "cached_weekly_memory_week")
    op.drop_column("users", "cached_weekly_memory")
