"""agent run fields for agent run apis

Revision ID: 0a0bc9eb4165
Revises: afb3d22a89aa
Create Date: 2026-08-30 02:05:39.774860

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0a0bc9eb4165'
down_revision: Union[str, None] = 'afb3d22a89aa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agent_runs', sa.Column('input_artifact_ids', sa.JSON(), nullable=True))
    op.add_column('agent_runs', sa.Column('output_artifact_id', sa.Uuid(), nullable=True))
    op.add_column('agent_runs', sa.Column('token_usage', sa.JSON(), nullable=True))
    op.add_column('agent_runs', sa.Column('cost', sa.Float(), nullable=True))

    # Existing runs (from seed data) predate this field — an empty list is
    # the correct value, not a placeholder: they simply weren't recorded
    # against any specific input artifacts.
    op.execute("UPDATE agent_runs SET input_artifact_ids = '[]' WHERE input_artifact_ids IS NULL")

    with op.batch_alter_table('agent_runs') as batch_op:
        batch_op.alter_column('input_artifact_ids', existing_type=sa.JSON(), nullable=False)
        batch_op.create_foreign_key(
            op.f('fk_agent_runs_output_artifact_id_artifacts'),
            'artifacts', ['output_artifact_id'], ['id'], ondelete='SET NULL',
        )


def downgrade() -> None:
    with op.batch_alter_table('agent_runs') as batch_op:
        batch_op.drop_constraint(op.f('fk_agent_runs_output_artifact_id_artifacts'), type_='foreignkey')
        batch_op.drop_column('cost')
        batch_op.drop_column('token_usage')
        batch_op.drop_column('output_artifact_id')
        batch_op.drop_column('input_artifact_ids')
