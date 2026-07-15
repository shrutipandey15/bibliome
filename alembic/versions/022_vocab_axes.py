"""New 18-emotion vocabulary data pass + verdict / dnf_reason axes.

Part A of the vocabulary swap (13 → 18 emotions) and the small status/verdict/DNF
axes:

- One-time data pass applying the legacy slug map to existing rows
  (``entry_emotions.emotion_id`` and the ``book_entries.arc_*_emotion_id`` columns),
  so the removed slugs (chaos/wit/two_am/2am) land on their canonical targets.
  Read-time ``canonicalize()`` remains as the safety net for anything missed.
- ``book_entries.verdict``  — "would you read it again?" (yes | no | not_sure).
- ``book_entries.dnf_reason`` — why abandoned (only meaningful when status='abandoned').
- Widen the status check constraint to add abandoned / paused / reread.

No structural table changes.

Revision ID: 022_vocab_axes
Revises: 021_phase7_dna
Create Date: 2026-07-15 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "022_vocab_axes"
down_revision: Union[str, None] = "021_phase7_dna"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Old slug → canonical slug (mirrors app.utils.emotions.LEGACY_EMOTION_MAP).
# "nostalgia" is intentionally absent: it is now a canonical slug in its own right.
_LEGACY_MAP = {
    "healing": "catharsis",
    "obsession": "desire",
    "seen": "tenderness",
    "chaos": "confusion",
    "wit": "amusement",
    "two_am": "longing",
    "2am": "longing",
}


def upgrade() -> None:
    # --- Part A: new axes ---
    op.add_column("book_entries", sa.Column("verdict", sa.String(10), nullable=True))
    op.add_column("book_entries", sa.Column("dnf_reason", sa.String(20), nullable=True))

    # --- Part A: freeze the vocabulary in existing data ---
    for old, new in _LEGACY_MAP.items():
        # entry_emotions has UNIQUE(entry_id, emotion_id): if an entry already
        # carries the target slug, drop the legacy row instead of colliding.
        op.execute(
            sa.text(
                "DELETE FROM entry_emotions WHERE emotion_id = :old "
                "AND entry_id IN (SELECT entry_id FROM entry_emotions WHERE emotion_id = :new)"
            ).bindparams(old=old, new=new)
        )
        op.execute(
            sa.text("UPDATE entry_emotions SET emotion_id = :new WHERE emotion_id = :old")
            .bindparams(old=old, new=new)
        )
        for col in ("arc_start_emotion_id", "arc_middle_emotion_id", "arc_end_emotion_id"):
            op.execute(
                sa.text(f"UPDATE book_entries SET {col} = :new WHERE {col} = :old")
                .bindparams(old=old, new=new)
            )

    # --- Part A: widen status; constrain the new axes ---
    op.drop_constraint("check_entry_status", "book_entries", type_="check")
    op.create_check_constraint(
        "check_entry_status",
        "book_entries",
        "status IN ('want_to_read','reading','finished','abandoned','paused','reread')",
    )
    op.create_check_constraint(
        "check_entry_verdict",
        "book_entries",
        "verdict IS NULL OR verdict IN ('yes','no','not_sure')",
    )
    op.create_check_constraint(
        "check_entry_dnf_reason",
        "book_entries",
        "dnf_reason IS NULL OR dnf_reason IN "
        "('bored','too_much','badly_written','wrong_time','lost_me','drifted')",
    )


def downgrade() -> None:
    # The legacy-slug data pass is not reversed — the old slugs are dead vocabulary.
    op.drop_constraint("check_entry_dnf_reason", "book_entries", type_="check")
    op.drop_constraint("check_entry_verdict", "book_entries", type_="check")
    op.drop_constraint("check_entry_status", "book_entries", type_="check")
    op.create_check_constraint(
        "check_entry_status",
        "book_entries",
        "status IN ('want_to_read','reading','finished')",
    )
    op.drop_column("book_entries", "dnf_reason")
    op.drop_column("book_entries", "verdict")
