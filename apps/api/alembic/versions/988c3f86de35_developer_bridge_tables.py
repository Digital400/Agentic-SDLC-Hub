"""developer bridge tables (Phase 13)

Revision ID: 988c3f86de35
Revises: 80e7e5c75660
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '988c3f86de35'
down_revision: Union[str, None] = '80e7e5c75660'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


bridge_device_authorization_status = sa.Enum(
    "PENDING", "APPROVED", "DENIED", "EXPIRED", name="bridgedeviceauthorizationstatus"
)
bridge_session_status = sa.Enum("CONNECTED", "OFFLINE", name="bridgesessionstatus")
bridge_job_assignment_status = sa.Enum(
    "ASSIGNED", "ACCEPTED", "REJECTED", "EVIDENCE_UPLOADED", "CREDENTIAL_EXPIRED", name="bridgejobassignmentstatus"
)


def upgrade() -> None:
    op.create_table(
        'bridge_device_authorizations',
        sa.Column('client_id', sa.String(length=200), nullable=False),
        sa.Column('device_code', sa.String(length=200), nullable=False),
        sa.Column('user_code', sa.String(length=20), nullable=False),
        sa.Column('status', bridge_device_authorization_status, nullable=False),
        sa.Column('approved_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('access_token', sa.String(length=200), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('access_token_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['approved_by_user_id'], ['users.id'], name=op.f('fk_bridge_device_authorizations_approved_by_user_id_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_bridge_device_authorizations')),
        sa.UniqueConstraint('device_code', name=op.f('uq_bridge_device_authorizations_device_code')),
        sa.UniqueConstraint('user_code', name=op.f('uq_bridge_device_authorizations_user_code')),
        sa.UniqueConstraint('access_token', name=op.f('uq_bridge_device_authorizations_access_token')),
    )
    op.create_table(
        'bridge_sessions',
        sa.Column('developer_user_id', sa.Uuid(), nullable=False),
        sa.Column('status', bridge_session_status, nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['developer_user_id'], ['users.id'], name=op.f('fk_bridge_sessions_developer_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_bridge_sessions')),
        sa.UniqueConstraint('developer_user_id', name=op.f('uq_bridge_sessions_developer_user_id')),
    )
    op.create_table(
        'bridge_job_assignments',
        sa.Column('implementation_task_id', sa.Uuid(), nullable=False),
        sa.Column('developer_user_id', sa.Uuid(), nullable=False),
        sa.Column('runtime_key', sa.String(length=200), nullable=False),
        sa.Column('repository_remote_url', sa.String(length=500), nullable=False),
        sa.Column('repository_branch', sa.String(length=200), nullable=False),
        sa.Column('repository_base_commit_sha', sa.String(length=64), nullable=False),
        sa.Column('status', bridge_job_assignment_status, nullable=False),
        sa.Column('rejected_reason', sa.Text(), nullable=True),
        sa.Column('credential_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('evidence_json', sa.JSON(), nullable=True),
        sa.Column('evidence_trusted', sa.Boolean(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['implementation_task_id'], ['implementation_tasks.id'], name=op.f('fk_bridge_job_assignments_implementation_task_id_implementation_tasks'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['developer_user_id'], ['users.id'], name=op.f('fk_bridge_job_assignments_developer_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_bridge_job_assignments')),
    )


def downgrade() -> None:
    op.drop_table('bridge_job_assignments')
    op.drop_table('bridge_sessions')
    op.drop_table('bridge_device_authorizations')
    bridge_job_assignment_status.drop(op.get_bind(), checkfirst=True)
    bridge_session_status.drop(op.get_bind(), checkfirst=True)
    bridge_device_authorization_status.drop(op.get_bind(), checkfirst=True)
