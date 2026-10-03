"""DNA share card: the reader's share choices, the stored link preview, and
first-party daily counts.

- users.card_show_season / card_show_red_flag: both on by default (DNA card
  spec, open decisions 1 and 2). They apply to the images, the /s/ page and the
  link preview.
- users.card_link_off: the reader turned their card link off; no device makes
  a new one until they ask for one.
- card_previews: one JPEG per reader, rendered before the first crawler asks.
- card_counts: one row per (day, metric, key). Aggregate totals only; nothing in
  it can point at a reader.

Every cached DNA payload is marked dirty so the season story's own counts are
computed on the next read.

Revision ID: 038_dna_card
Revises: 037_dna_seen_archetype
Create Date: 2026-10-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "038_dna_card"
down_revision: Union[str, None] = "037_dna_seen_archetype"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("card_show_season", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("users", sa.Column("card_show_red_flag", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("users", sa.Column("card_link_off", sa.Boolean(), nullable=False, server_default="false"))
    op.create_table(
        "card_previews",
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("hash", sa.String(32), nullable=False),
        sa.Column("jpeg", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "card_counts",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("metric", sa.String(16), primary_key=True),
        sa.Column("key", sa.String(24), primary_key=True),
        sa.Column("n", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(sa.text("UPDATE users SET dna_dirty = true"))


def downgrade() -> None:
    op.drop_table("card_counts")
    op.drop_table("card_previews")
    op.drop_column("users", "card_link_off")
    op.drop_column("users", "card_show_red_flag")
    op.drop_column("users", "card_show_season")
