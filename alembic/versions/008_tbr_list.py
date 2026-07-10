"""add tbr_list to users (reconstructed to match DB state)

Revision ID: 008_tbr_list
Revises: 007_reading_room
Create Date: 2026-04

This migration was applied to the database but its file was never committed.
Reconstructed from the live schema so alembic history matches. The actual
column already exists in the DB; this file exists only to keep alembic's
revision chain consistent.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "008_tbr_list"
down_revision = "007_reading_room"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("tbr_list", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "tbr_list")
