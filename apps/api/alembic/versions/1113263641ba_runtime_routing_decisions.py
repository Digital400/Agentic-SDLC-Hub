"""runtime routing decisions (Phase 17)

Revision ID: 1113263641ba
Revises: 95d157a1305a
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1113263641ba'
down_revision: Union[str, None] = '95d157a1305a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'runtime_routing_decisions',
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('task_type', sa.String(length=100), nullable=False),
        sa.Column('requested_runtime', sa.String(length=100), nullable=False),
        sa.Column('requested_model', sa.String(length=100), nullable=False),
        sa.Column('candidates_json', sa.JSON(), nullable=False),
        sa.Column('selected_runtime', sa.String(length=100), nullable=True),
        sa.Column('selected_model', sa.String(length=100), nullable=True),
        sa.Column('is_premium', sa.Boolean(), nullable=False),
        sa.Column('estimated_cost_usd', sa.Float(), nullable=True),
        sa.Column('actual_cost_usd', sa.Float(), nullable=True),
        sa.Column('cache_savings_usd', sa.Float(), nullable=False),
        sa.Column('escalation_reason', sa.Text(), nullable=True),
        sa.Column('cost_owner', sa.String(length=30), nullable=False),
        sa.Column('stop_for_human', sa.Boolean(), nullable=False),
        sa.Column('stop_reason', sa.Text(), nullable=True),
        sa.Column('artifact_approved', sa.Boolean(), nullable=False),
        sa.Column('patch_accepted', sa.Boolean(), nullable=False),
        sa.Column('story_merged', sa.Boolean(), nullable=False),
        sa.Column('execution_failed', sa.Boolean(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_runtime_routing_decisions_project_id_projects'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_runtime_routing_decisions')),
    )


def downgrade() -> None:
    op.drop_table('runtime_routing_decisions')
