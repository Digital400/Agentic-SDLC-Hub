from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import TestAgentType, TestRunStatus


class TestRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One Testing Agent execution against one accepted ImplementationRun —
    see app/services/testing_agent.py.

    GATE (requirement 1): starting a run requires the task's latest
    ImplementationRun to be ACCEPTED, and either a PullRequestLink or a
    non-empty diff to exist on it — see
    app/api/routes/test_runs.py's start_test_run. This deliberately does
    NOT go through GraphEngineService.validate_can_run against the
    `testing` WorkflowNode: that node's `required_inputs` still names a
    `code_change` Artifact type nothing in this codebase ever produces
    (the Implementation Agent work built ImplementationRun/PullRequestLink
    as a separate model, not that legacy Artifact-drafting path). This
    run's own gate is what actually governs starting testing.

    HONESTY: this codebase has no test-execution sandbox — it never shells
    out to run a real test suite against a checkout. `tests_executed` and
    the derived pass/fail counts are the agent's own reasoning-based
    assessment from the diff and acceptance criteria, not a real CI
    result — disclosed here, in testing_agent.py, and in the rendered
    test_report Artifact itself, never silently presented as a real run.

    QA GATE (requirements 6, "should not silently approve its own result",
    "QA approval is required"): completing a run creates a real `Artifact`
    (artifact_type="test_report") + `Review`, reusing the existing generic
    human-approval mechanism unmodified (see app/api/routes/reviews.py).
    Nothing in this model or the service that populates it ever sets
    ArtifactStatus.APPROVED or ReviewStatus.APPROVED — only a human QA
    reviewer's POST /reviews/{id}/approve can.
    """

    __tablename__ = "test_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    workflow_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_nodes.id", ondelete="CASCADE"), nullable=False)
    implementation_task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("implementation_tasks.id", ondelete="CASCADE"), nullable=False
    )
    implementation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("implementation_runs.id", ondelete="CASCADE"), nullable=False
    )
    pull_request_link_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pull_request_links.id", ondelete="SET NULL"), nullable=True
    )
    # The test_report Artifact/ArtifactVersion this run produced — null
    # only while a run is still RUNNING or if it FAILED before reaching
    # that step.
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True)
    artifact_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifact_versions.id", ondelete="SET NULL"), nullable=True
    )
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    agent_type: Mapped[TestAgentType] = mapped_column(
        Enum(TestAgentType, native_enum=False, length=20, validate_strings=True), nullable=False
    )
    status: Mapped[TestRunStatus] = mapped_column(
        Enum(TestRunStatus, native_enum=False, length=20, validate_strings=True),
        default=TestRunStatus.PENDING,
        nullable=False,
    )

    # --- Output (requirement 3) ----------------------------------------
    test_plan: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tests_to_add: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    tests_executed: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    pass_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bugs_found: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Rule: "Testing agent can suggest fixes."
    suggested_fixes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Always a placeholder dict — no coverage tooling exists in this
    # codebase (see class docstring / testing_agent.py).
    coverage_impact: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    used_mock: Mapped[bool] = mapped_column(default=True, nullable=False)
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship("Project")
    workflow_node: Mapped["WorkflowNode"] = relationship("WorkflowNode")
    implementation_task: Mapped["ImplementationTask"] = relationship("ImplementationTask")
    implementation_run: Mapped["ImplementationRun"] = relationship("ImplementationRun")
    pull_request_link: Mapped["PullRequestLink | None"] = relationship("PullRequestLink")
    artifact: Mapped["Artifact | None"] = relationship("Artifact")
    artifact_version: Mapped["ArtifactVersion | None"] = relationship("ArtifactVersion")
    triggered_by: Mapped["User | None"] = relationship("User", foreign_keys=[triggered_by_user_id])
