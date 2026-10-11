"""Weekly digest v2: when it arrives, what it said, and what was clicked.

- notification_prefs.digest_day / digest_hour: when the reader's digest is due,
  in their own timezone. Python weekday (0 = Monday), so 6 is Sunday; 9 am.
- notification_digests.lead / subject: which section led and the subject line,
  the history the variety rules read.
- digest_clicks: one row per (digest, section) the reader clicked through.

Revision ID: 039_weekly_digest
Revises: 038_dna_card
Create Date: 2026-10-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "039_weekly_digest"
down_revision: Union[str, None] = "038_dna_card"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notification_prefs", sa.Column("digest_day", sa.SmallInteger(), nullable=False, server_default="6"))
    op.add_column("notification_prefs", sa.Column("digest_hour", sa.SmallInteger(), nullable=False, server_default="9"))
    op.create_check_constraint("ck_prefs_digest_day", "notification_prefs", "digest_day BETWEEN 0 AND 6")
    op.create_check_constraint("ck_prefs_digest_hour", "notification_prefs", "digest_hour BETWEEN 0 AND 23")

    op.add_column("notification_digests", sa.Column("lead", sa.String(30), nullable=True))
    op.add_column("notification_digests", sa.Column("subject", sa.String(200), nullable=True))

    op.create_table(
        "digest_clicks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("digest_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("notification_digests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section", sa.String(30), nullable=False),
        sa.Column("clicked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("digest_id", "section", name="uq_digest_click"),
    )


def downgrade() -> None:
    op.drop_table("digest_clicks")
    op.drop_column("notification_digests", "subject")
    op.drop_column("notification_digests", "lead")
    op.drop_constraint("ck_prefs_digest_hour", "notification_prefs")
    op.drop_constraint("ck_prefs_digest_day", "notification_prefs")
    op.drop_column("notification_prefs", "digest_hour")
    op.drop_column("notification_prefs", "digest_day")
