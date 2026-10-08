"""multi repository per project

Adds Repository.is_primary (exactly one "default" repo per project, used
by any task that doesn't explicitly name one) and
ImplementationTask.repository_id (which of a project's — now possibly
several — repositories a task's code changes target).

Backfill marks each project's single most-recently-created repository as
primary, matching the exact "latest repo for this project" resolution
every consumer used before this migration — so no project's behavior
changes until a second repository is actually connected.

Revision ID: 1b041aec4ba2
Revises: d4f2b8c6a913
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "1b041aec4ba2"
down_revision = "d4f2b8c6a913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("repositories", sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("repositories", "is_primary", server_default=None)

    op.add_column("implementation_tasks", sa.Column("repository_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_implementation_tasks_repository_id_repositories"),
        "implementation_tasks", "repositories", ["repository_id"], ["id"], ondelete="SET NULL",
    )

    # Backfill: one primary per project — the most-recently-created repo,
    # i.e. exactly what every existing "latest repo for this project"
    # query already resolved to before this migration.
    op.execute(
        """
        UPDATE repositories
        SET is_primary = TRUE
        WHERE id IN (
            SELECT DISTINCT ON (project_id) id
            FROM repositories
            ORDER BY project_id, created_at DESC
        )
        """
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_implementation_tasks_repository_id_repositories"), "implementation_tasks", type_="foreignkey")
    op.drop_column("implementation_tasks", "repository_id")
    op.drop_column("repositories", "is_primary")
