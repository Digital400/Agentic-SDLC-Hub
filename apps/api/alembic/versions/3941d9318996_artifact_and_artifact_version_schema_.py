"""artifact and artifact version schema for artifact APIs

Revision ID: 3941d9318996
Revises: dd39ff0be636
Create Date: 2026-08-30 00:22:34.844619

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3941d9318996'
down_revision: Union[str, None] = 'dd39ff0be636'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ARTIFACT_STATUS_ENUM = sa.Enum(
    'DRAFT', 'READY_FOR_REVIEW', 'APPROVED', 'NEEDS_CHANGES', 'REJECTED',
    name='artifactstatus', native_enum=False, length=20,
)


def upgrade() -> None:
    # --- artifacts.status: remap old WorkflowStatus strings to ArtifactStatus ---
    # (native_enum=False means this is just a VARCHAR at the DB level, so
    # existing rows aren't rejected by the schema change itself — but the
    # ORM will fail to deserialize a value that isn't a valid ArtifactStatus
    # member the next time it's read, so the data must be fixed here.)
    op.execute(
        """
        UPDATE artifacts SET status = CASE status
            WHEN 'WAITING_FOR_REVIEW' THEN 'READY_FOR_REVIEW'
            WHEN 'COMPLETED' THEN 'APPROVED'
            WHEN 'APPROVED' THEN 'APPROVED'
            WHEN 'NEEDS_CHANGES' THEN 'NEEDS_CHANGES'
            WHEN 'REJECTED' THEN 'REJECTED'
            ELSE 'DRAFT'
        END
        """
    )

    # --- artifacts.created_by_id: new required column, backfill from the owning project's owner ---
    op.add_column('artifacts', sa.Column('created_by_id', sa.Uuid(), nullable=True))
    op.execute(
        """
        UPDATE artifacts SET created_by_id = (
            SELECT projects.created_by_id FROM projects WHERE projects.id = artifacts.project_id
        )
        WHERE created_by_id IS NULL
        """
    )

    with op.batch_alter_table('artifacts') as batch_op:
        batch_op.alter_column('created_by_id', existing_type=sa.Uuid(), nullable=False)
        batch_op.alter_column(
            'status',
            existing_type=sa.VARCHAR(length=30),
            type_=ARTIFACT_STATUS_ENUM,
            existing_nullable=False,
        )
        batch_op.create_foreign_key(op.f('fk_artifacts_created_by_id_users'), 'users', ['created_by_id'], ['id'])

    # --- artifact_versions: content -> content_markdown/content_json, authored_by_* -> created_by_id ---
    op.add_column('artifact_versions', sa.Column('content_markdown', sa.Text(), nullable=True))
    op.add_column('artifact_versions', sa.Column('content_json', sa.JSON(), nullable=True))
    op.add_column('artifact_versions', sa.Column('created_by_id', sa.Uuid(), nullable=True))

    op.execute("UPDATE artifact_versions SET content_markdown = content")
    op.execute(
        "UPDATE artifact_versions SET created_by_id = authored_by_user_id WHERE authored_by_user_id IS NOT NULL"
    )
    # Agent-authored versions (authored_by_user_id was NULL) have no human
    # to attribute to under the new single-author model — fall back to the
    # owning project's owner, same placeholder logic used for
    # artifacts.created_by_id above.
    op.execute(
        """
        UPDATE artifact_versions SET created_by_id = (
            SELECT projects.created_by_id
            FROM artifacts
            JOIN projects ON projects.id = artifacts.project_id
            WHERE artifacts.id = artifact_versions.artifact_id
        )
        WHERE created_by_id IS NULL
        """
    )

    with op.batch_alter_table('artifact_versions') as batch_op:
        batch_op.alter_column('content_markdown', existing_type=sa.Text(), nullable=False)
        batch_op.alter_column('created_by_id', existing_type=sa.Uuid(), nullable=False)
        # This CHECK constraint referenced the two columns being dropped
        # below — must go first, or SQLite's batch-mode table rebuild
        # carries it over verbatim and the rebuild fails.
        batch_op.drop_constraint(op.f('ck_artifact_versions_has_author'), type_='check')
        batch_op.drop_constraint('fk_artifact_versions_authored_by_user_id_users', type_='foreignkey')
        batch_op.drop_constraint('fk_artifact_versions_authored_by_agent_run_id_agent_runs', type_='foreignkey')
        batch_op.create_foreign_key(op.f('fk_artifact_versions_created_by_id_users'), 'users', ['created_by_id'], ['id'])
        batch_op.drop_column('content')
        batch_op.drop_column('content_format')
        batch_op.drop_column('authored_by_user_id')
        batch_op.drop_column('authored_by_agent_run_id')


def downgrade() -> None:
    # Best-effort structural rollback only — the authorship/content split
    # this migration performs is lossy (e.g. which versions were
    # agent- vs human-authored isn't recoverable), so this restores the
    # old columns/shape but not the original data semantics.
    with op.batch_alter_table('artifact_versions') as batch_op:
        batch_op.add_column(sa.Column('content', sa.TEXT(), nullable=True))
        batch_op.add_column(sa.Column('content_format', sa.VARCHAR(length=30), nullable=True))
        batch_op.add_column(sa.Column('authored_by_user_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('authored_by_agent_run_id', sa.Uuid(), nullable=True))

    op.execute("UPDATE artifact_versions SET content = content_markdown, content_format = 'markdown'")
    op.execute("UPDATE artifact_versions SET authored_by_user_id = created_by_id")

    with op.batch_alter_table('artifact_versions') as batch_op:
        batch_op.alter_column('content', existing_type=sa.TEXT(), nullable=False)
        batch_op.alter_column('content_format', existing_type=sa.VARCHAR(length=30), nullable=False)
        batch_op.drop_constraint(op.f('fk_artifact_versions_created_by_id_users'), type_='foreignkey')
        batch_op.create_foreign_key(
            'fk_artifact_versions_authored_by_agent_run_id_agent_runs', 'agent_runs', ['authored_by_agent_run_id'], ['id']
        )
        batch_op.create_foreign_key(
            'fk_artifact_versions_authored_by_user_id_users', 'users', ['authored_by_user_id'], ['id']
        )
        batch_op.create_check_constraint(
            op.f('ck_artifact_versions_has_author'),
            'authored_by_user_id IS NOT NULL OR authored_by_agent_run_id IS NOT NULL',
        )
        batch_op.drop_column('created_by_id')
        batch_op.drop_column('content_json')
        batch_op.drop_column('content_markdown')

    with op.batch_alter_table('artifacts') as batch_op:
        batch_op.drop_constraint(op.f('fk_artifacts_created_by_id_users'), type_='foreignkey')
        batch_op.alter_column(
            'status',
            existing_type=ARTIFACT_STATUS_ENUM,
            type_=sa.VARCHAR(length=30),
            existing_nullable=False,
        )
        batch_op.drop_column('created_by_id')
