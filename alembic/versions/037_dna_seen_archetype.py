"""DNA aliveness: remember which archetype the reader last acknowledged.

The shift card ("You've become a World-Diver") shows once, on the next DNA visit
after the archetype changes. This column is what "once" is measured against.
Left NULL for everyone: the next DNA recompute fills it with the reader's current
archetype without showing a card, so nobody is told about a "shift" that was only
the new switch rule settling in.

Revision ID: 037_dna_seen_archetype
Revises: 036_feelings_v3
Create Date: 2026-10-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "037_dna_seen_archetype"
down_revision: Union[str, None] = "036_feelings_v3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("dna_seen_archetype", sa.String(40), nullable=True))
    # Every cached DNA payload predates the aliveness layer and the new archetype
    # rule; mark them dirty so the next read recomputes rather than waiting on
    # `is_fresh` to notice.
    op.execute(sa.text("UPDATE users SET dna_dirty = true"))


def downgrade() -> None:
    op.drop_column("users", "dna_seen_archetype")
