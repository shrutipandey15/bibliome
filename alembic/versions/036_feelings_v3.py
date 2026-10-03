"""21-feeling vocabulary + "How did it land?" verdict (Emotion & DNA rework, v3).

- Data pass: the two merged slugs are rewritten in place, everywhere a slug is
  stored (``devastation`` → ``grief``, ``tenderness`` → ``comfort``, and the old
  ``seen`` alias, which pointed at tenderness). Read-time ``canonicalize()``
  remains the safety net. Rows whose entry already carries the target slug are
  dropped instead of colliding with the per-entry unique constraints.
- The retired "it lost me" tags (boredom, revulsion, confusion, indifference) are
  NOT touched: they are the reader's own history and stay stored and readable.
  They simply no longer feed any vector (canonicalize → None).
- ``book_entries.verdict``: "would you read it again?" (yes | no | not_sure)
  becomes "how did it land?" (loved | liked | mixed | not_for_me). Old answers
  map forward: yes → liked, no → not_for_me, not_sure → mixed.
- New ``book_entries.verdict_reason``: why a finished book disappointed
  (only after mixed / not_for_me).
- New ``book_entries.other_feeling``: the reader's own words from the picker's
  "something else…" option — how Bibliome finds the feelings it is still missing.

Revision ID: 036_feelings_v3
Revises: 035_chat_image_attachments
Create Date: 2026-09-30 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "036_feelings_v3"
down_revision: Union[str, None] = "035_chat_image_attachments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Mirrors app.utils.emotions.LEGACY_EMOTION_MAP for the slugs this release retires.
_MERGE = {"devastation": "grief", "tenderness": "comfort", "seen": "comfort"}

# (table, parent-key column) for tables with UNIQUE(parent, emotion_id).
_TAG_TABLES = (("entry_emotions", "entry_id"), ("journal_emotions", "journal_entry_id"))

# Columns holding a single slug with no uniqueness to protect.
_SLUG_COLUMNS = (
    ("book_entries", "arc_start_emotion_id"),
    ("book_entries", "arc_middle_emotion_id"),
    ("book_entries", "arc_end_emotion_id"),
    ("entry_checkins", "emotion_id"),
    ("echoes", "primary_emotion"),
    ("echoes", "secondary_emotion"),
)

_VERDICT_MAP = {"yes": "liked", "no": "not_for_me", "not_sure": "mixed"}


def upgrade() -> None:
    # --- merge retired slugs into their targets ---
    for old, new in _MERGE.items():
        for table, parent in _TAG_TABLES:
            # Where an entry carries both, the merged tag keeps the stronger of
            # the two strengths before the duplicate is dropped.
            op.execute(sa.text(
                f"UPDATE {table} AS t SET strength = GREATEST(t.strength, o.strength) "
                f"FROM {table} AS o WHERE t.{parent} = o.{parent} "
                f"AND t.emotion_id = :new AND o.emotion_id = :old"
            ).bindparams(old=old, new=new))
            op.execute(sa.text(
                f"DELETE FROM {table} WHERE emotion_id = :old AND {parent} IN "
                f"(SELECT {parent} FROM {table} WHERE emotion_id = :new)"
            ).bindparams(old=old, new=new))
            op.execute(sa.text(
                f"UPDATE {table} SET emotion_id = :new WHERE emotion_id = :old"
            ).bindparams(old=old, new=new))
        for table, col in _SLUG_COLUMNS:
            op.execute(sa.text(
                f"UPDATE {table} SET {col} = :new WHERE {col} = :old"
            ).bindparams(old=old, new=new))

    # --- verdict: "read again?" → "how did it land?" ---
    op.drop_constraint("check_entry_verdict", "book_entries", type_="check")
    for old, new in _VERDICT_MAP.items():
        op.execute(sa.text(
            "UPDATE book_entries SET verdict = :new WHERE verdict = :old"
        ).bindparams(old=old, new=new))
    op.create_check_constraint(
        "check_entry_verdict",
        "book_entries",
        "verdict IS NULL OR verdict IN ('loved','liked','mixed','not_for_me')",
    )

    # --- new axes ---
    op.add_column("book_entries", sa.Column("verdict_reason", sa.String(24), nullable=True))
    op.create_check_constraint(
        "check_entry_verdict_reason",
        "book_entries",
        "verdict_reason IS NULL OR verdict_reason IN "
        "('ending_let_me_down','overhyped','didnt_connect','badly_written','forgettable')",
    )
    op.add_column("book_entries", sa.Column("other_feeling", sa.String(80), nullable=True))


def downgrade() -> None:
    # The slug merge is not reversed: devastation/tenderness are dead vocabulary.
    op.drop_column("book_entries", "other_feeling")
    op.drop_constraint("check_entry_verdict_reason", "book_entries", type_="check")
    op.drop_column("book_entries", "verdict_reason")
    op.drop_constraint("check_entry_verdict", "book_entries", type_="check")
    for old, new in _VERDICT_MAP.items():
        # loved has no old equivalent; it lands on yes along with liked.
        op.execute(sa.text(
            "UPDATE book_entries SET verdict = :old WHERE verdict = :new"
        ).bindparams(old=old, new=new))
    op.execute(sa.text("UPDATE book_entries SET verdict = 'yes' WHERE verdict = 'loved'"))
    op.create_check_constraint(
        "check_entry_verdict",
        "book_entries",
        "verdict IS NULL OR verdict IN ('yes','no','not_sure')",
    )
