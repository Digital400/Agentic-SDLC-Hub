"""graph engine node statuses and override fields

Revision ID: e477e224c4c6
Revises: c76ec5e8fb65
Create Date: 2026-08-30 20:16:48.598639

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e477e224c4c6'
down_revision: Union[str, None] = 'c76ec5e8fb65'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('workflow_nodes', sa.Column('blocked_reason', sa.Text(), nullable=True))
    op.add_column('workflow_nodes', sa.Column('override_reason', sa.Text(), nullable=True))

    # WorkflowStatus's value set changed (see app/models/enums.py) — this
    # column is a plain VARCHAR (native_enum=False), so no DDL type change
    # is needed, but existing rows holding a status value that no longer
    # exists must be converted or the ORM will fail to deserialize them.
    #   NOT_STARTED -> LOCKED    (same meaning: prerequisites not satisfied)
    #   IN_PROGRESS -> READY     (same meaning: unlocked, not yet run)
    #   REJECTED    -> BLOCKED   (a rejected review is now a hard stop
    #                             requiring manual_override, not its own
    #                             status — see GraphEngineService)
    op.execute("UPDATE workflow_nodes SET status = 'LOCKED' WHERE status = 'NOT_STARTED'")
    op.execute("UPDATE workflow_nodes SET status = 'READY' WHERE status = 'IN_PROGRESS'")
    op.execute(
        "UPDATE workflow_nodes SET status = 'BLOCKED', "
        "blocked_reason = 'Migrated from the old REJECTED status — original rejection reason not recorded.' "
        "WHERE status = 'REJECTED'"
    )


def downgrade() -> None:
    op.execute("UPDATE workflow_nodes SET status = 'NOT_STARTED' WHERE status = 'LOCKED'")
    op.execute("UPDATE workflow_nodes SET status = 'IN_PROGRESS' WHERE status = 'READY'")
    op.execute(
        "UPDATE workflow_nodes SET status = 'REJECTED' WHERE status = 'BLOCKED' "
        "AND blocked_reason = 'Migrated from the old REJECTED status — original rejection reason not recorded.'"
    )
    # Any other BLOCKED/WAITING_FOR_INPUT/RUNNING/SKIPPED row has no old
    # equivalent — left as-is; a downgrade this far back is not expected
    # to be lossless.
    op.drop_column('workflow_nodes', 'override_reason')
    op.drop_column('workflow_nodes', 'blocked_reason')
