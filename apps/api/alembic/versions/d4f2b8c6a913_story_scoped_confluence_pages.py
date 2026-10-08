"""story scoped confluence pages

Revision ID: d4f2b8c6a913
Revises: c3e8a5b9f217
Create Date: 2026-09-04 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4f2b8c6a913'
down_revision: Union[str, None] = 'c3e8a5b9f217'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: written by hand (no reachable database in this environment to
    # run `alembic revision --autogenerate` against).
    op.drop_constraint(
        'uq_confluence_page_links_confluence_space_link_id_artifact_type', 'confluence_page_links', type_='unique'
    )
    op.alter_column('confluence_page_links', 'artifact_id', existing_type=sa.Uuid(), nullable=True)
    op.alter_column('confluence_page_links', 'artifact_version_id', existing_type=sa.Uuid(), nullable=True)
    op.add_column('confluence_page_links', sa.Column('story_id', sa.Uuid(), nullable=True))
    op.add_column('confluence_page_links', sa.Column('story_artifact_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f('fk_confluence_page_links_story_id_stories'), 'confluence_page_links', 'stories',
        ['story_id'], ['id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        op.f('fk_confluence_page_links_story_artifact_id_story_artifacts'), 'confluence_page_links', 'story_artifacts',
        ['story_artifact_id'], ['id'], ondelete='CASCADE',
    )
    # Partial unique indexes replace the old plain UniqueConstraint — see
    # ConfluencePageLink's own docstring for why a plain 3-column
    # constraint including story_id would silently stop enforcing "one
    # page per artifact kind" for project-level (story_id IS NULL) rows.
    op.create_index(
        'uq_confluence_page_links_project_scope', 'confluence_page_links', ['confluence_space_link_id', 'artifact_type'],
        unique=True, postgresql_where=sa.text('story_id IS NULL'),
    )
    op.create_index(
        'uq_confluence_page_links_story_scope', 'confluence_page_links',
        ['confluence_space_link_id', 'artifact_type', 'story_id'],
        unique=True, postgresql_where=sa.text('story_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_confluence_page_links_story_scope', table_name='confluence_page_links')
    op.drop_index('uq_confluence_page_links_project_scope', table_name='confluence_page_links')
    op.drop_constraint(op.f('fk_confluence_page_links_story_artifact_id_story_artifacts'), 'confluence_page_links', type_='foreignkey')
    op.drop_constraint(op.f('fk_confluence_page_links_story_id_stories'), 'confluence_page_links', type_='foreignkey')
    op.drop_column('confluence_page_links', 'story_artifact_id')
    op.drop_column('confluence_page_links', 'story_id')
    op.alter_column('confluence_page_links', 'artifact_version_id', existing_type=sa.Uuid(), nullable=False)
    op.alter_column('confluence_page_links', 'artifact_id', existing_type=sa.Uuid(), nullable=False)
    op.create_unique_constraint(
        'uq_confluence_page_links_confluence_space_link_id_artifact_type', 'confluence_page_links',
        ['confluence_space_link_id', 'artifact_type'],
    )
