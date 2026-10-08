"""code runs

Revision ID: e91a4c6f2d38
Revises: d38f2c9a7b41
Create Date: 2026-09-02 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e91a4c6f2d38'
down_revision: Union[str, None] = 'd38f2c9a7b41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: written by hand (no reachable database in this environment to
    # run `alembic revision --autogenerate` against) — follows the exact
    # create_table conventions of the most recent hand-written migrations
    # (e.g. a4d19e2f6b31_release_planning_from_story_lanes.py).
    op.create_table(
        'code_runs',
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('story_id', sa.Uuid(), nullable=False),
        sa.Column('lane_id', sa.Uuid(), nullable=True),
        sa.Column('repository_id', sa.Uuid(), nullable=False),
        sa.Column('triggered_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('branch_name', sa.String(length=255), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'QUEUED', 'CLONING', 'BRANCH_CREATED', 'APPLYING_CHANGES', 'TESTING', 'COMMITTED', 'PUSHED', 'FAILED',
                name='coderunstatus', native_enum=False, length=20,
            ),
            nullable=False,
        ),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('logs', sa.JSON(), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('workspace_path', sa.String(length=1000), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['lane_id'], ['story_delivery_lanes.id'], name=op.f('fk_code_runs_lane_id_story_delivery_lanes'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_code_runs_project_id_projects'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], name=op.f('fk_code_runs_repository_id_repositories'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['story_id'], ['stories.id'], name=op.f('fk_code_runs_story_id_stories'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['triggered_by_user_id'], ['users.id'], name=op.f('fk_code_runs_triggered_by_user_id_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_code_runs')),
    )


def downgrade() -> None:
    op.drop_table('code_runs')
