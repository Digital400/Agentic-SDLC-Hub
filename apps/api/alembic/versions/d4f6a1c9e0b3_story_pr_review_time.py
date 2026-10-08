"""story pr review time

Revision ID: d4f6a1c9e0b3
Revises: c3a91e7d5b20
Create Date: 2026-09-30 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4f6a1c9e0b3'
down_revision: Union[str, None] = 'c3a91e7d5b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Hand-written (no reachable database in this environment to run
    # `alembic revision --autogenerate` against). Both columns nullable/
    # defaulted, so every existing story row is unaffected.
    op.add_column('stories', sa.Column('estimated_pr_review_time', sa.String(length=120), nullable=False, server_default=''))
    op.alter_column('stories', 'estimated_pr_review_time', server_default=None)
    op.add_column('stories', sa.Column('estimated_review_worst_case_minutes', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('stories', 'estimated_review_worst_case_minutes')
    op.drop_column('stories', 'estimated_pr_review_time')
