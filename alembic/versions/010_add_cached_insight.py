"""add cached_insight and cached_insight_week to users

Revision ID: 010_add_cached_insight
Revises: 009_add_arc_status_checkins
Create Date: 2026-04-20

"""
from alembic import op
import sqlalchemy as sa


revision = "010_add_cached_insight"
down_revision = "009_add_arc_status_checkins"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("cached_insight", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("cached_insight_week", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "cached_insight_week")
    op.drop_column("users", "cached_insight")
