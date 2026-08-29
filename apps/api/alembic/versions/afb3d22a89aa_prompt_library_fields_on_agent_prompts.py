"""prompt library fields on agent_prompts

Revision ID: afb3d22a89aa
Revises: 3941d9318996
Create Date: 2026-08-30 01:33:49.832090

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'afb3d22a89aa'
down_revision: Union[str, None] = '3941d9318996'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New columns, added nullable first — existing rows get backfilled
    # below before any of these are tightened to NOT NULL.
    op.add_column('agent_prompts', sa.Column('name', sa.String(length=255), nullable=True))
    op.add_column('agent_prompts', sa.Column('stage', sa.String(length=100), nullable=True))
    op.add_column('agent_prompts', sa.Column('output_format', sa.Text(), nullable=True))
    op.add_column('agent_prompts', sa.Column('validation_checklist', sa.JSON(), nullable=True))

    # name: reuse the owning agent's own name — a reasonable real value,
    # not just a placeholder (e.g. "Requirement Intake Agent").
    op.execute(
        """
        UPDATE agent_prompts SET name = (
            SELECT agent_definitions.name FROM agent_definitions
            WHERE agent_definitions.id = agent_prompts.agent_definition_id
        )
        WHERE name IS NULL
        """
    )

    # stage: derived from the agent_key convention every seeded agent
    # follows ("<stage-with-hyphens>-agent"), e.g. "hld-agent" -> "hld",
    # "requirement-intake-agent" -> "requirement_intake" — reconstructing
    # the real WorkflowNode.node_key, not a placeholder.
    op.execute(
        """
        UPDATE agent_prompts SET stage = (
            SELECT REPLACE(SUBSTR(agent_definitions.agent_key, 1, LENGTH(agent_definitions.agent_key) - 6), '-', '_')
            FROM agent_definitions
            WHERE agent_definitions.id = agent_prompts.agent_definition_id
        )
        WHERE stage IS NULL
        """
    )

    op.execute("UPDATE agent_prompts SET output_format = 'Markdown document.' WHERE output_format IS NULL")
    op.execute("UPDATE agent_prompts SET validation_checklist = '[]' WHERE validation_checklist IS NULL")

    with op.batch_alter_table('agent_prompts') as batch_op:
        batch_op.alter_column('name', existing_type=sa.String(length=255), nullable=False)
        batch_op.alter_column('stage', existing_type=sa.String(length=100), nullable=False)
        batch_op.alter_column('output_format', existing_type=sa.Text(), nullable=False)
        batch_op.alter_column('validation_checklist', existing_type=sa.JSON(), nullable=False)
        # A true rename (not drop+add) — preserves every prompt's existing content.
        batch_op.alter_column('template', new_column_name='system_prompt', existing_type=sa.Text(), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('agent_prompts') as batch_op:
        batch_op.alter_column('system_prompt', new_column_name='template', existing_type=sa.Text(), existing_nullable=False)
        batch_op.drop_column('validation_checklist')
        batch_op.drop_column('output_format')
        batch_op.drop_column('stage')
        batch_op.drop_column('name')
