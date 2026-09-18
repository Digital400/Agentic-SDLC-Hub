"""agent migration shadow runs (Phase 16)

Revision ID: 95d157a1305a
Revises: 988c3f86de35
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '95d157a1305a'
down_revision: Union[str, None] = '988c3f86de35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'agent_migration_shadow_runs',
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('node_key', sa.String(length=100), nullable=False),
        sa.Column('legacy_total_tokens', sa.Integer(), nullable=False),
        sa.Column('legacy_cost_usd', sa.Float(), nullable=False),
        sa.Column('legacy_needs_clarification', sa.Boolean(), nullable=False),
        sa.Column('legacy_content_length', sa.Integer(), nullable=False),
        sa.Column('v2_total_tokens', sa.Integer(), nullable=False),
        sa.Column('v2_cost_usd', sa.Float(), nullable=False),
        sa.Column('v2_needs_clarification', sa.Boolean(), nullable=False),
        sa.Column('v2_content_length', sa.Integer(), nullable=False),
        sa.Column('v2_repair_attempted', sa.Boolean(), nullable=False),
        sa.Column('clarification_outcomes_matched', sa.Boolean(), nullable=False),
        sa.Column('human_acceptance', sa.String(length=20), nullable=True),
        sa.Column('extra_data', sa.JSON(), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_agent_migration_shadow_runs_project_id_projects'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_migration_shadow_runs')),
    )


def downgrade() -> None:
    op.drop_table('agent_migration_shadow_runs')
