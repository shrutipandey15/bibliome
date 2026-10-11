"""Daily book fact: the reader's switch and hour, and what each reader was sent.

- notification_prefs.fact_enabled: on by default (decided 2026-10-11).
- notification_prefs.fact_hour: local hour it arrives, 10 am by default.
- daily_facts: one row per reader per local day; fact_id keeps a fact from
  ever reaching the same reader twice.

Revision ID: 040_daily_fact
Revises: 039_weekly_digest
Create Date: 2026-10-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "040_daily_fact"
down_revision: Union[str, None] = "039_weekly_digest"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notification_prefs", sa.Column("fact_enabled", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("notification_prefs", sa.Column("fact_hour", sa.SmallInteger(), nullable=False, server_default="10"))
    op.create_check_constraint("ck_prefs_fact_hour", "notification_prefs", "fact_hour BETWEEN 0 AND 23")
    op.create_table(
        "daily_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("fact_id", sa.String(200), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "day", name="uq_daily_fact_day"),
    )


def downgrade() -> None:
    op.drop_table("daily_facts")
    op.drop_constraint("ck_prefs_fact_hour", "notification_prefs")
    op.drop_column("notification_prefs", "fact_hour")
    op.drop_column("notification_prefs", "fact_enabled")
