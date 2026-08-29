"""add project business_owner and current_stage

Revision ID: dd39ff0be636
Revises: 32e7ae2e6ed5
Create Date: 2026-08-29 23:14:03.371376

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dd39ff0be636'
down_revision: Union[str, None] = '32e7ae2e6ed5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Both columns are NOT NULL, but existing project rows have no value for
    # them yet — add nullable, backfill a placeholder, then tighten.
    # ("Unknown" / the project's own workflow_template's start node are
    # reasonable placeholders precisely because they're obviously
    # placeholders, prompting a real value to be set via the API.)
    op.add_column('projects', sa.Column('business_owner', sa.String(length=255), nullable=True))
    op.add_column('projects', sa.Column('current_stage', sa.String(length=100), nullable=True))

    op.execute("UPDATE projects SET business_owner = 'Unknown' WHERE business_owner IS NULL")
    op.execute(
        "UPDATE projects SET current_stage = ("
        "  SELECT node_key FROM workflow_nodes"
        "  WHERE workflow_nodes.project_id = projects.id"
        "  ORDER BY order_index ASC LIMIT 1"
        ") WHERE current_stage IS NULL"
    )

    with op.batch_alter_table('projects') as batch_op:
        batch_op.alter_column('business_owner', nullable=False)
        batch_op.alter_column('current_stage', nullable=False)


def downgrade() -> None:
    op.drop_column('projects', 'current_stage')
    op.drop_column('projects', 'business_owner')
