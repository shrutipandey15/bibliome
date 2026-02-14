"""add share_token

Revision ID: 3b2939823d0c
Revises: 002_cover_url
Create Date: 2026-02-14 23:04:58.362500
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3b2939823d0c'
down_revision: Union[str, None] = '002_cover_url'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('share_token', sa.String(length=50), nullable=True))
    op.create_index(op.f('ix_users_share_token'), 'users', ['share_token'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_users_share_token'), table_name='users')
    op.drop_column('users', 'share_token')
