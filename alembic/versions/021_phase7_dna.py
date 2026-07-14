"""Phase 7 DNA engine: users.reads_for (stated preference) + dna_snapshots.trigger

- users.reads_for: the *stated* half of stated-vs-revealed — 1–2 canonical emotion
  slugs, "what do you read for?" (B7.1).
- dna_snapshots.trigger: manual | drift | cadence, so the evolution timeline can
  tell an auto-captured shift from a user-forced capture (B7.4).

Revision ID: 021_phase7_dna
Revises: 020_add_prompts
Create Date: 2026-07-14 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "021_phase7_dna"
down_revision: Union[str, None] = "020_add_prompts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("reads_for", postgresql.JSONB, nullable=True))
    op.add_column("users", sa.Column("cached_dna_v2", postgresql.JSONB, nullable=True))
    op.add_column("dna_snapshots", sa.Column("trigger", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("dna_snapshots", "trigger")
    op.drop_column("users", "cached_dna_v2")
    op.drop_column("users", "reads_for")
