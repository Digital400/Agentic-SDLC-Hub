"""story test executions

Revision ID: c3e8a5b9f217
Revises: a2c9f6e1b8d4
Create Date: 2026-09-03 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3e8a5b9f217'
down_revision: Union[str, None] = 'a2c9f6e1b8d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: written by hand (no reachable database in this environment to
    # run `alembic revision --autogenerate` against).
    op.create_table(
        'story_test_executions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('story_id', sa.Uuid(), nullable=False),
        sa.Column('lane_id', sa.Uuid(), nullable=True),
        sa.Column('test_scenario_artifact_id', sa.Uuid(), nullable=True),
        sa.Column('pull_request_link_id', sa.Uuid(), nullable=True),
        sa.Column('executed_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('results_json', sa.JSON(), nullable=False),
        sa.Column('agent_checklist', sa.JSON(), nullable=False),
        sa.Column('evidence_urls', sa.JSON(), nullable=False),
        sa.Column('bugs_found', sa.JSON(), nullable=False),
        sa.Column('qa_decision', sa.String(length=20), nullable=False),
        sa.Column('qa_decision_reason', sa.Text(), nullable=False),
        sa.Column('qa_decided_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('used_mock', sa.Boolean(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_story_test_executions')),
        sa.ForeignKeyConstraint(['story_id'], ['stories.id'], name=op.f('fk_story_test_executions_story_id_stories'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(
            ['lane_id'], ['story_delivery_lanes.id'], name=op.f('fk_story_test_executions_lane_id_story_delivery_lanes'), ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['test_scenario_artifact_id'], ['story_artifacts.id'],
            name=op.f('fk_story_test_executions_test_scenario_artifact_id_story_artifacts'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['pull_request_link_id'], ['pull_request_links.id'],
            name=op.f('fk_story_test_executions_pull_request_link_id_pull_request_links'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['executed_by_user_id'], ['users.id'], name=op.f('fk_story_test_executions_executed_by_user_id_users'),
        ),
        sa.ForeignKeyConstraint(
            ['qa_decided_by_user_id'], ['users.id'], name=op.f('fk_story_test_executions_qa_decided_by_user_id_users'),
        ),
    )


def downgrade() -> None:
    op.drop_table('story_test_executions')
