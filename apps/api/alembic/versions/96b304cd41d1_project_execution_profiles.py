"""project execution profiles

Revision ID: 96b304cd41d1
Revises: f85a6e6ff762
Create Date: 2026-09-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '96b304cd41d1'
down_revision: Union[str, None] = 'f85a6e6ff762'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'project_execution_profiles',
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', sa.Enum('DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'SUPERSEDED', name='projectexecutionprofilestatus', native_enum=False, length=20), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('profile_type', sa.Enum('NEW_PROJECT', 'EXISTING_REPOSITORY', name='projectexecutionprofiletype', native_enum=False, length=20), nullable=False),
        sa.Column('source', sa.Enum('DETECTED', 'TEMPLATE', 'MANUAL', name='projectexecutionprofilesource', native_enum=False, length=10), nullable=False),
        sa.Column('template_key', sa.String(length=100), nullable=True),
        sa.Column('repository_id', sa.Uuid(), nullable=True),
        sa.Column('repository_snapshot_id', sa.Uuid(), nullable=True),
        sa.Column('default_branch', sa.String(length=255), nullable=True),
        sa.Column('working_directories', sa.JSON(), nullable=False),
        sa.Column('detected_languages', sa.JSON(), nullable=False),
        sa.Column('detected_frameworks', sa.JSON(), nullable=False),
        sa.Column('package_manager', sa.String(length=100), nullable=True),
        sa.Column('runtime_image', sa.String(length=255), nullable=True),
        sa.Column('install_command', sa.Text(), nullable=True),
        sa.Column('lint_command', sa.Text(), nullable=True),
        sa.Column('format_check_command', sa.Text(), nullable=True),
        sa.Column('type_check_command', sa.Text(), nullable=True),
        sa.Column('unit_test_command', sa.Text(), nullable=True),
        sa.Column('integration_test_command', sa.Text(), nullable=True),
        sa.Column('build_command', sa.Text(), nullable=True),
        sa.Column('approved_security_scan_commands', sa.JSON(), nullable=False),
        sa.Column('allowed_command_patterns', sa.JSON(), nullable=False),
        sa.Column('denied_command_patterns', sa.JSON(), nullable=False),
        sa.Column('allowed_paths', sa.JSON(), nullable=False),
        sa.Column('denied_paths', sa.JSON(), nullable=False),
        sa.Column('network_policy', sa.JSON(), nullable=False),
        sa.Column('environment_variable_names', sa.JSON(), nullable=False),
        sa.Column('branch_naming_convention', sa.String(length=255), nullable=True),
        sa.Column('commit_convention', sa.String(length=255), nullable=True),
        sa.Column('required_coding_skills', sa.JSON(), nullable=False),
        sa.Column('required_testing_skills', sa.JSON(), nullable=False),
        sa.Column('quality_gates', sa.JSON(), nullable=False),
        sa.Column('data_classification', sa.Enum('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED', name='dataclassification', native_enum=False, length=15), nullable=False),
        sa.Column('detection_metadata', sa.JSON(), nullable=False),
        sa.Column('created_by_id', sa.Uuid(), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approved_by_id', sa.Uuid(), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rejected_by_id', sa.Uuid(), nullable=True),
        sa.Column('rejected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['approved_by_id'], ['users.id'], name=op.f('fk_project_execution_profiles_approved_by_id_users')),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_project_execution_profiles_created_by_id_users')),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_project_execution_profiles_project_id_projects'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['rejected_by_id'], ['users.id'], name=op.f('fk_project_execution_profiles_rejected_by_id_users')),
        sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], name=op.f('fk_project_execution_profiles_repository_id_repositories'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['repository_snapshot_id'], ['repository_snapshots.id'], name=op.f('fk_project_execution_profiles_repository_snapshot_id_repository_snapshots'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_project_execution_profiles')),
        sa.UniqueConstraint('project_id', 'version', name=op.f('uq_project_execution_profiles_project_id_version')),
    )


def downgrade() -> None:
    op.drop_table('project_execution_profiles')
