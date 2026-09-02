"""story jira sync status

Revision ID: d38f2c9a7b41
Revises: c7f4a1b8e293
Create Date: 2026-09-02 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd38f2c9a7b41'
down_revision: Union[str, None] = 'c7f4a1b8e293'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: written by hand (no reachable database in this environment to
    # run `alembic revision --autogenerate` against).
    op.add_column('stories', sa.Column('jira_issue_id', sa.String(length=50), nullable=True))
    op.add_column('stories', sa.Column('jira_issue_url', sa.String(length=500), nullable=True))
    # NOTE: server_default added by hand — `stories` already has rows.
    op.add_column(
        'stories',
        sa.Column(
            'jira_sync_status',
            sa.Enum('NOT_SYNCED', 'SYNC_PENDING', 'SYNCED', 'SYNC_FAILED', name='storyjirasyncstatus', native_enum=False, length=20),
            nullable=False, server_default='NOT_SYNCED',
        ),
    )
    op.alter_column('stories', 'jira_sync_status', server_default=None)
    # Backfill: a story that already has a jira_issue_key from before this
    # column existed is, by definition, already synced.
    op.execute("UPDATE stories SET jira_sync_status = 'SYNCED' WHERE jira_issue_key IS NOT NULL")


def downgrade() -> None:
    op.drop_column('stories', 'jira_sync_status')
    op.drop_column('stories', 'jira_issue_url')
    op.drop_column('stories', 'jira_issue_id')
