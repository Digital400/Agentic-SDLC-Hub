from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import MaintenanceRunStatus


class MaintenanceRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One Maintenance Agent execution — a repeatable, manually-triggered
    project health report (requirement 6: "generate weekly maintenance
    report manually") — see app/services/maintenance_agent.py.

    Deliberately bespoke, not the generic AgentRun/LoopEngine draft flow
    the `maintenance` WorkflowNode is otherwise wired to (see that node's
    own RICH_DEFAULT_PROMPTS entry in app/db/seed.py): that generic path
    auto-finalizes to a terminal WorkflowStatus after one run for a
    no-approval stage (see app/api/routes/agent_runs.py's
    save_agent_output_to_artifact) and is never re-runnable afterward —
    incompatible with "weekly, manually, repeatedly." This model's runs
    are independent of that WorkflowNode's own status entirely; only its
    node_key/edit-role config (app/services/permissions.py) is reused.

    ARTIFACT (requirement 2): completing a run creates-or-reuses a real
    `Artifact` (artifact_type=MAINTENANCE_REPORT_ARTIFACT_TYPE — see
    app/services/maintenance_agent.py — deliberately distinct from this
    WorkflowNode's own `output_artifact_type`, "maintenance_log", which
    stays the generic path's independent, untouched output) and always
    appends a new `ArtifactVersion` — proving repeatability the same way
    TestRun's Artifact-versioning does (see app/models/test_run.py).

    NO REVIEW (rule: "cannot change production, recommend actions only" —
    nothing here needs approval): unlike TestRun, completing a run never
    creates a `Review` and sets `Artifact.status` straight to APPROVED —
    the same "no approval gate" terminal semantics
    app/api/routes/agent_runs.py already applies to every other
    requires_human_approval=False stage, just reached via this bespoke
    path instead of the generic one.
    """

    __tablename__ = "maintenance_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    workflow_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_nodes.id", ondelete="CASCADE"), nullable=False)
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    status: Mapped[MaintenanceRunStatus] = mapped_column(
        Enum(MaintenanceRunStatus, native_enum=False, length=20, validate_strings=True),
        default=MaintenanceRunStatus.PENDING,
        nullable=False,
    )

    # --- Optional inputs (requirement 3 — "if available") ----------------
    error_logs_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_feedback_input: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Output (requirement 4) -------------------------------------------
    report_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The maintenance_report Artifact/ArtifactVersion this run produced —
    # null only while a run is still RUNNING or if it FAILED before
    # reaching that step.
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True)
    artifact_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifact_versions.id", ondelete="SET NULL"), nullable=True
    )

    used_mock: Mapped[bool] = mapped_column(default=True, nullable=False)
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship("Project")
    workflow_node: Mapped["WorkflowNode"] = relationship("WorkflowNode")
    triggered_by: Mapped["User | None"] = relationship("User", foreign_keys=[triggered_by_user_id])
    artifact: Mapped["Artifact | None"] = relationship("Artifact")
    artifact_version: Mapped["ArtifactVersion | None"] = relationship("ArtifactVersion")
