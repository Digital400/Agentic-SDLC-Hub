"""nullable pr review / pull request link workflow_node_id

Revision ID: c7f4a1b8e293
Revises: a4d19e2f6b31
Create Date: 2026-09-02 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7f4a1b8e293'
down_revision: Union[str, None] = 'a4d19e2f6b31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: written by hand (no reachable database in this environment to
    # run `alembic revision --autogenerate` against).
    #
    # HARDENING FIX — a story-scoped PullRequestLink/PRReviewRun has no
    # project-level WorkflowNode to attach to (a story's PULL_REQUEST /
    # PR_REVIEW_AGENT stages live entirely in StoryDeliveryNode, a
    # different table — see app/services/story_delivery.py). Both columns
    # were still NOT NULL, so app/api/routes/implementation_runs.py's
    # create_pull_request and app/api/routes/pr_review_runs.py's
    # start_pr_review_run always 400'd for a story-scoped task before this
    # fix. Same nullable-relaxation precedent already applied to
    # ImplementationTask.workflow_node_id and TestRun.workflow_node_id.
    op.alter_column('pull_request_links', 'workflow_node_id', existing_type=sa.UUID(), nullable=True)
    op.alter_column('pr_review_runs', 'workflow_node_id', existing_type=sa.UUID(), nullable=True)


def downgrade() -> None:
    op.alter_column('pr_review_runs', 'workflow_node_id', existing_type=sa.UUID(), nullable=False)
    op.alter_column('pull_request_links', 'workflow_node_id', existing_type=sa.UUID(), nullable=False)
