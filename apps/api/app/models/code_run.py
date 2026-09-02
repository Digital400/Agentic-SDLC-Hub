from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import CodeRunStatus


class CodeRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One CodeRunnerService execution — the isolated workspace/clone/
    branch/apply-changes/test/commit/push pipeline for one story's Code
    Implementation stage (see app/services/code_runner.py). Distinct
    from ImplementationRun (app/models/implementation_run.py, which
    holds the *drafted* diff/summary an agent produced): a CodeRun is
    what actually materializes that diff into a real, isolated git
    workspace and pushes a real branch. One ImplementationRun's accepted
    output is expected to drive at most one active CodeRun at a time,
    but that relationship isn't yet a hard FK here — this is the
    foundation model; a later step wires the two together explicitly.

    SCOPE (foundation step): this model and its service only take a
    story through PUSHED — actual PR creation is a separate, already-
    existing action (see app/api/routes/implementation_runs.py's
    create_pull_request, which uses GitHub's REST API directly, not a
    local git push). CodeRunnerService.prepare_pr_creation only ever
    assembles the fields a PR would need; it never calls GitHub itself.
    """

    __tablename__ = "code_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    story_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), nullable=False)
    lane_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("story_delivery_lanes.id", ondelete="CASCADE"), nullable=True)
    repository_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    branch_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[CodeRunStatus] = mapped_column(
        Enum(CodeRunStatus, native_enum=False, length=20, validate_strings=True),
        default=CodeRunStatus.QUEUED,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Structured log entries — [{"timestamp": iso8601, "level": "INFO"|
    # "ERROR", "message": str}, ...], appended to by
    # CodeRunnerService._log (see that module's docstring for the
    # redaction guarantee: a token is never present in any entry here).
    logs: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The local filesystem path this run's isolated workspace was
    # created under (see CodeRunnerService.create_workspace) — recorded
    # for operator debugging; never rendered to an end user as-is since
    # it's a path on this server's own disk, not anything meaningful to
    # a browser client.
    workspace_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    project: Mapped["Project"] = relationship("Project")
    story: Mapped["Story"] = relationship("Story")
    lane: Mapped["StoryDeliveryLane | None"] = relationship("StoryDeliveryLane")
    repository: Mapped["Repository"] = relationship("Repository")
    triggered_by: Mapped["User | None"] = relationship("User", foreign_keys=[triggered_by_user_id])
