"""project work_type

Adds Project.work_type (NEW_PROJECT / EXISTING_PROJECT_FEATURE / BUG_FIX /
TECHNICAL_IMPROVEMENT) — see WorkType's own docstring in
app/models/enums.py. Every existing project backfills to NEW_PROJECT,
which is exactly what every project up to this point already was (this
field didn't exist before, so nothing was ever anything else) — no
behavior changes for any existing project.

Revision ID: 7a2d9c4e1f38
Revises: 1b041aec4ba2
Create Date: 2026-09-07
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "7a2d9c4e1f38"
down_revision = "1b041aec4ba2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("work_type", sa.String(length=30), nullable=False, server_default="NEW_PROJECT"),
    )
    op.alter_column("projects", "work_type", server_default=None)


def downgrade() -> None:
    op.drop_column("projects", "work_type")
