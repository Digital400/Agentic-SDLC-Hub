from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import PullRequestStatus


class PullRequestLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A real GitHub pull request created from one ACCEPTED
    ImplementationRun — see app/api/routes/implementation_runs.py's
    create_pull_request and app/services/github_integration.py's write
    methods. One row per PR ever opened; created only after a human has
    Accepted the run's diff, and only ever targets a freshly created
    feature branch (`branch_name`), never `base_branch` directly.

    Links a PR back to every level this session's Implementation Agent
    work has built: the project, the `implementation` workflow node, the
    ImplementationTask it was generated for, and the ImplementationRun
    whose accepted output it commits — so "what did an agent actually
    change on GitHub, and why" is answerable end to end.
    """

    __tablename__ = "pull_request_links"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    workflow_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_nodes.id", ondelete="CASCADE"), nullable=False)
    implementation_task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("implementation_tasks.id", ondelete="CASCADE"), nullable=False
    )
    implementation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("implementation_runs.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)

    branch_name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    pr_url: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[PullRequestStatus] = mapped_column(
        Enum(PullRequestStatus, native_enum=False, length=10, validate_strings=True),
        default=PullRequestStatus.OPEN,
        nullable=False,
    )
    # Always True today — the whole branch/commit/PR sequence is agent-
    # executed end to end; a human only triggers it (see triggered_by_user_id).
    # Modeled as a real column, not a hardcoded constant, so a future
    # human-initiated PR path (if one is ever built) has somewhere to
    # record the difference.
    created_by_agent: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    commit_message: Mapped[str] = mapped_column(Text, nullable=False)

    project: Mapped["Project"] = relationship("Project")
    workflow_node: Mapped["WorkflowNode"] = relationship("WorkflowNode")
    implementation_task: Mapped["ImplementationTask"] = relationship("ImplementationTask")
    implementation_run: Mapped["ImplementationRun"] = relationship("ImplementationRun")
    repository: Mapped["Repository"] = relationship("Repository")
    triggered_by: Mapped["User | None"] = relationship("User", foreign_keys=[triggered_by_user_id])
