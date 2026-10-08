"""release planning from story lanes

Revision ID: a4d19e2f6b31
Revises: 521b141a78b7
Create Date: 2026-09-02 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4d19e2f6b31'
down_revision: Union[str, None] = '521b141a78b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: written by hand (no reachable database to run
    # `alembic revision --autogenerate` against in this environment) —
    # follows the exact create_table conventions of
    # alembic/versions/115697daea96_sprint_planning.py (its sibling
    # sprint_stories table) and 8b2b6dcf48ad_scrum_story_lanes.py.
    op.create_table(
        'releases',
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('version', sa.String(length=50), nullable=False),
        sa.Column('target_date', sa.Date(), nullable=True),
        sa.Column('status', sa.Enum('DRAFT', 'APPROVED', 'RELEASED', 'CANCELLED', name='releasestatus', native_enum=False, length=20), nullable=False),
        sa.Column('release_notes', sa.Text(), nullable=False),
        sa.Column('created_by_id', sa.Uuid(), nullable=False),
        sa.Column('approved_by_id', sa.Uuid(), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['approved_by_id'], ['users.id'], name=op.f('fk_releases_approved_by_id_users')),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_releases_created_by_id_users')),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_releases_project_id_projects'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_releases')),
    )
    op.create_table(
        'release_stories',
        sa.Column('release_id', sa.Uuid(), nullable=False),
        sa.Column('story_id', sa.Uuid(), nullable=False),
        sa.Column('added_by_id', sa.Uuid(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['added_by_id'], ['users.id'], name=op.f('fk_release_stories_added_by_id_users')),
        sa.ForeignKeyConstraint(['release_id'], ['releases.id'], name=op.f('fk_release_stories_release_id_releases'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['story_id'], ['stories.id'], name=op.f('fk_release_stories_story_id_stories'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_release_stories')),
        sa.UniqueConstraint('release_id', 'story_id', name=op.f('uq_release_stories_release_id_story_id')),
    )


def downgrade() -> None:
    op.drop_table('release_stories')
    op.drop_table('releases')
