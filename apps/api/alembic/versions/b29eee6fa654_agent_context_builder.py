"""agent context builder

Revision ID: b29eee6fa654
Revises: 36d5f7616d86
Create Date: 2026-09-08 08:30:55.464480

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b29eee6fa654'
down_revision: Union[str, None] = '36d5f7616d86'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agent_runs', sa.Column('engineering_setup_context_snapshot', sa.JSON(), nullable=True))
    op.add_column('implementation_runs', sa.Column('engineering_setup_context_snapshot', sa.JSON(), nullable=True))
    # server_default so this NOT NULL column can be added to an existing,
    # possibly non-empty table — every pre-existing row backfills to
    # GENERAL, the same category a newly-created standard defaults to.
    op.add_column(
        'project_coding_standards',
        sa.Column(
            'category',
            sa.Enum('GENERAL', 'ARCHITECTURE', 'SECURITY', 'TESTING', 'GIT', 'DOCUMENTATION', name='codingstandardcategory', native_enum=False, length=20),
            nullable=False,
            server_default='GENERAL',
        ),
    )
    op.alter_column('project_coding_standards', 'category', server_default=None)
    # NOTE: alembic --autogenerate also detected two unrelated missing FKs
    # (stories.source_artifact_version_id, story_delivery_lanes.current_node_id)
    # — pre-existing drift between the ORM models and this DB, not
    # anything this migration's own feature touches. Deliberately left out
    # of this migration to keep it scoped to the Agent Context Builder
    # only; that drift should be its own separate migration if addressed.


def downgrade() -> None:
    op.drop_column('project_coding_standards', 'category')
    op.drop_column('implementation_runs', 'engineering_setup_context_snapshot')
    op.drop_column('agent_runs', 'engineering_setup_context_snapshot')
