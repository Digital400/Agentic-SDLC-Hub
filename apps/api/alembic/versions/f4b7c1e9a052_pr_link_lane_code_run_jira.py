"""pull request link lane/code_run/jira

Revision ID: f4b7c1e9a052
Revises: e91a4c6f2d38
Create Date: 2026-09-02 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4b7c1e9a052'
down_revision: Union[str, None] = 'e91a4c6f2d38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: written by hand (no reachable database in this environment to
    # run `alembic revision --autogenerate` against).
    op.add_column('pull_request_links', sa.Column('lane_id', sa.Uuid(), nullable=True))
    op.add_column('pull_request_links', sa.Column('code_run_id', sa.Uuid(), nullable=True))
    op.add_column('pull_request_links', sa.Column('jira_issue_key', sa.String(length=50), nullable=True))
    op.create_foreign_key(
        op.f('fk_pull_request_links_lane_id_story_delivery_lanes'), 'pull_request_links', 'story_delivery_lanes',
        ['lane_id'], ['id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        op.f('fk_pull_request_links_code_run_id_code_runs'), 'pull_request_links', 'code_runs',
        ['code_run_id'], ['id'], ondelete='SET NULL',
    )
    # code_runs.implementation_run_id — added here too (same migration,
    # discovered needed together): GitHub PR creation from a CodeRun
    # needs to look its own originating ImplementationRun back up (diff,
    # summary, risks) without a second lookup mechanism.
    op.add_column('code_runs', sa.Column('implementation_run_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f('fk_code_runs_implementation_run_id_implementation_runs'), 'code_runs', 'implementation_runs',
        ['implementation_run_id'], ['id'], ondelete='CASCADE',
    )


def downgrade() -> None:
    op.drop_constraint(op.f('fk_code_runs_implementation_run_id_implementation_runs'), 'code_runs', type_='foreignkey')
    op.drop_column('code_runs', 'implementation_run_id')
    op.drop_constraint(op.f('fk_pull_request_links_code_run_id_code_runs'), 'pull_request_links', type_='foreignkey')
    op.drop_constraint(op.f('fk_pull_request_links_lane_id_story_delivery_lanes'), 'pull_request_links', type_='foreignkey')
    op.drop_column('pull_request_links', 'jira_issue_key')
    op.drop_column('pull_request_links', 'code_run_id')
    op.drop_column('pull_request_links', 'lane_id')
