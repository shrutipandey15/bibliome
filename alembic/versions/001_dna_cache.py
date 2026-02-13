"""add dna cache fields to users

Revision ID: 001_dna_cache
Revises: None
Create Date: 2026-02-12

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = "001_dna_cache"
down_revision = "c91fec17e6a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("dna_dirty", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("users", sa.Column("cached_dna_profile", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "cached_dna_profile")
    op.drop_column("users", "dna_dirty")