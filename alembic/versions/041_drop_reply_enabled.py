"""Drop notification_prefs.reply_enabled.

Labelled "Replies to your echoes", it actually silenced every direct (tier 1)
notice: resonance, collection chat, mentions, reactions, DNA shifts. Rather
than relabel or split it, it's gone (decided 2026-10-11): direct notices always
arrive in-app, still held by quiet hours and blocks; push stays the reader's
own on/off.

Revision ID: 041_drop_reply_enabled
Revises: 040_daily_fact
Create Date: 2026-10-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "041_drop_reply_enabled"
down_revision: Union[str, None] = "040_daily_fact"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("notification_prefs", "reply_enabled")


def downgrade() -> None:
    op.add_column("notification_prefs", sa.Column("reply_enabled", sa.Boolean(), nullable=False, server_default="true"))
