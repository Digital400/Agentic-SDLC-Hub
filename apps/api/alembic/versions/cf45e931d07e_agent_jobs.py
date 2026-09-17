"""agent jobs

Revision ID: cf45e931d07e
Revises: 96b304cd41d1
Create Date: 2026-09-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cf45e931d07e'
down_revision: Union[str, None] = '96b304cd41d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'agent_jobs',
        sa.Column('idempotency_key', sa.String(length=255), nullable=True),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('story_id', sa.Uuid(), nullable=True),
        sa.Column('task_type', sa.String(length=50), nullable=False),
        sa.Column('work_packet', sa.JSON(), nullable=False),
        sa.Column('status', sa.Enum('QUEUED', 'PREPARING', 'RUNNING', 'WAITING_INPUT', 'WAITING_APPROVAL', 'COMPLETED', 'FAILED', 'CANCELLED', 'STALE', name='agentjobstatus', native_enum=False, length=20), nullable=False),
        sa.Column('dispatcher_backend', sa.String(length=50), nullable=True),
        sa.Column('failure_category', sa.Enum('TRANSIENT', 'PERMANENT', name='jobfailurecategory', native_enum=False, length=10), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False),
        sa.Column('continuation_of_job_id', sa.Uuid(), nullable=True),
        sa.Column('cancellation_requested', sa.Boolean(), nullable=False),
        sa.Column('cancellation_requested_by_id', sa.Uuid(), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('queued_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('created_by_id', sa.Uuid(), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['cancellation_requested_by_id'], ['users.id'], name=op.f('fk_agent_jobs_cancellation_requested_by_id_users')),
        sa.ForeignKeyConstraint(['continuation_of_job_id'], ['agent_jobs.id'], name='fk_agent_jobs_continuation_of_job_id', ondelete='SET NULL', use_alter=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_agent_jobs_created_by_id_users')),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_agent_jobs_project_id_projects'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['story_id'], ['stories.id'], name=op.f('fk_agent_jobs_story_id_stories'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_jobs')),
        sa.UniqueConstraint('idempotency_key', name='uq_agent_jobs_idempotency_key'),
    )

    op.create_table(
        'agent_job_events',
        sa.Column('job_id', sa.Uuid(), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.Enum('STATUS', 'PLAN_SUMMARY', 'TOOL_REQUEST', 'TOOL_RESULT', 'FILE_CHANGE', 'COMMAND', 'TEST_RESULT', 'USAGE', 'APPROVAL_REQUIRED', 'ARTIFACT', 'WARNING', 'ERROR', 'COMPLETED', name='agentjobeventtype', native_enum=False, length=20), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['agent_jobs.id'], name=op.f('fk_agent_job_events_job_id_agent_jobs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_job_events')),
        sa.UniqueConstraint('job_id', 'sequence', name='uq_agent_job_events_job_id_sequence'),
    )

    op.create_table(
        'agent_job_outbox_entries',
        sa.Column('job_id', sa.Uuid(), nullable=False),
        sa.Column('idempotency_key', sa.String(length=255), nullable=False),
        sa.Column('external_target', sa.String(length=100), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'SENT', 'FAILED', name='outboxentrystatus', native_enum=False, length=10), nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['agent_jobs.id'], name=op.f('fk_agent_job_outbox_entries_job_id_agent_jobs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_job_outbox_entries')),
        sa.UniqueConstraint('idempotency_key', name='uq_agent_job_outbox_entries_idempotency_key'),
    )


def downgrade() -> None:
    op.drop_table('agent_job_outbox_entries')
    op.drop_table('agent_job_events')
    op.drop_table('agent_jobs')
