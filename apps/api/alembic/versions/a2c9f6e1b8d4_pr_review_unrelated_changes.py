"""pr review unrelated changes

Revision ID: a2c9f6e1b8d4
Revises: f4b7c1e9a052
Create Date: 2026-09-03 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2c9f6e1b8d4'
down_revision: Union[str, None] = 'f4b7c1e9a052'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: written by hand (no reachable database in this environment to
    # run `alembic revision --autogenerate` against).
    op.add_column(
        'pr_review_runs',
        sa.Column('unrelated_changes', sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.alter_column('pr_review_runs', 'unrelated_changes', server_default=None)


def downgrade() -> None:
    op.drop_column('pr_review_runs', 'unrelated_changes')
