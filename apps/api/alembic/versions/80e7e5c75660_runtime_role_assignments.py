"""runtime role assignments

Revision ID: 80e7e5c75660
Revises: cf45e931d07e
Create Date: 2026-09-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '80e7e5c75660'
down_revision: Union[str, None] = 'cf45e931d07e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'runtime_role_assignments',
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('scope', sa.Enum('ORGANIZATION', 'PROJECT', name='roleassignmentscope', native_enum=False, length=15), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=True),
        sa.Column('role', sa.Enum('ADMIN', 'PROJECT_OWNER', 'ARCHITECT', 'DEVELOPER', 'REVIEWER', 'QA', 'AUDITOR', 'VIEWER', name='runtimerole', native_enum=False, length=20), nullable=False),
        sa.Column('granted_by_id', sa.Uuid(), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['granted_by_id'], ['users.id'], name=op.f('fk_runtime_role_assignments_granted_by_id_users')),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_runtime_role_assignments_project_id_projects'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_runtime_role_assignments_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_runtime_role_assignments')),
        sa.UniqueConstraint('user_id', 'scope', 'project_id', 'role', name='uq_runtime_role_assignments_user_scope_project_role'),
    )


def downgrade() -> None:
    op.drop_table('runtime_role_assignments')
