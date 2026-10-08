"""knowledge source project scope

Revision ID: b6f2a1c9d0e4
Revises: b29eee6fa654
Create Date: 2026-09-12 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6f2a1c9d0e4'
down_revision: Union[str, None] = 'b29eee6fa654'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: written by hand (no reachable database in this environment to
    # run `alembic revision --autogenerate` against).
    #
    # Nullable, so every pre-existing KnowledgeSource stays org-wide
    # (visible to every project's retrieval, unchanged behavior) — see
    # app/models/knowledge.py's KnowledgeSource.project_id docstring and
    # app/services/retrieval.py, which now matches project_id IS NULL OR
    # project_id = the run's project.
    op.add_column('knowledge_sources', sa.Column('project_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f('fk_knowledge_sources_project_id_projects'), 'knowledge_sources', 'projects',
        ['project_id'], ['id'], ondelete='CASCADE',
    )
    op.create_index('ix_knowledge_sources_project_id', 'knowledge_sources', ['project_id'])


def downgrade() -> None:
    op.drop_index('ix_knowledge_sources_project_id', table_name='knowledge_sources')
    op.drop_constraint(op.f('fk_knowledge_sources_project_id_projects'), 'knowledge_sources', type_='foreignkey')
    op.drop_column('knowledge_sources', 'project_id')
