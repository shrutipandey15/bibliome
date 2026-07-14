"""remove Reading Room: drop users.room_layout / room_unlocks (Phase 5 B5.5)

Revision ID: 018_remove_reading_room
Revises: 017_profile_collections
Create Date: 2026-07-13 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "018_remove_reading_room"
down_revision: Union[str, None] = "017_profile_collections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The Reading Room (unlock/decoration mechanic) is removed — reward-for-volume
    # dopamine the product refuses. shelf_position stays (the plain shelf uses it).
    op.drop_column("users", "room_layout")
    op.drop_column("users", "room_unlocks")


def downgrade() -> None:
    op.add_column("users", sa.Column("room_unlocks", JSONB, nullable=True))
    op.add_column("users", sa.Column("room_layout", JSONB, nullable=True))
