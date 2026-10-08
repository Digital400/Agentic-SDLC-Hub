from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ImplementationRunReviewStatus, ImplementationRunStatus


class ImplementationRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One Implementation Agent execution against one approved
    ImplementationTask — see app/services/implementation_agent.py.

    Deliberately its own model rather than a reuse/extension of AgentRun
    (app/models/agent.py): AgentRun is keyed to a WorkflowNode +
    AgentDefinition + AgentPrompt role (draft/improve/validate) and its
    output feeds an Artifact/ArtifactVersion. This run is keyed to one
    ImplementationTask, its output is diff-shaped (see the fields below),
    and it carries its own independent human review decision — none of
    that fits AgentRun's shape without distorting it.

    SCOPE: generating a run and Accepting/Rejecting it never itself writes
    to GitHub in any way, regardless of `review_status`. A real write only
    ever happens through a separate, explicit action — creating a pull
    request (see app/api/routes/implementation_runs.py's
    create_pull_request and app/models/pull_request_link.py's
    PullRequestLink) — which is only reachable once `review_status ==
    ACCEPTED`, and even then only ever writes to a freshly created feature
    branch, never this repository's default branch.
    """

    __tablename__ = "implementation_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    implementation_task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("implementation_tasks.id", ondelete="CASCADE"), nullable=False
    )
    # Nullable + SET NULL — a run's own record should outlive the snapshot
    # it was generated against (no endpoint deletes a snapshot today, but
    # nothing here should assume one never will).
    repository_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("repository_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    # --- Story-level implementation workflow — all null for a
    # project-level (task.story_id is None) run, unchanged from before
    # this feature; set together for a story-scoped run (see
    # app/api/routes/implementation_runs.py's start_implementation_run).
    # One run is always exactly one story — enforced structurally, not by
    # a runtime check: ImplementationTask.story_id (this run's own task)
    # is a single nullable FK, never a collection, so a run spanning
    # multiple stories isn't representable in the first place.
    story_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), nullable=True)
    lane_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("story_delivery_lanes.id", ondelete="CASCADE"), nullable=True)
    # The approved StoryArtifact (artifact_type="story_lld") this run was
    # generated against — see app/services/story_lld_agent.py. SET NULL
    # (not CASCADE): this run's own record should outlive that artifact
    # row exactly like it already outlives a deleted repository_snapshot.
    story_lld_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("story_artifacts.id", ondelete="SET NULL"), nullable=True
    )
    # Who this run's output is assigned to for review/ownership — set to
    # whoever started the run. Distinct from triggered_by_user_id only in
    # spirit today (same person); kept separate so a future "assign this
    # run to someone else without re-running it" action has a field to
    # write to without overloading triggered_by's own audit meaning.
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Snapshot of task.assigned_agent_type at run time (see
    # app/services/implementation_planner.py's AREA_TO_AGENT_TYPE) — the
    # task itself could in principle be regenerated later; this run keeps
    # a record of which agent type actually produced its output.
    agent_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # Same value as agent_type above, kept under this name too because
    # the story-level implementation workflow spec names it explicitly as
    # its own field — not derived differently, just addressable by this
    # name for story-scoped runs/consumers.
    assigned_agent_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[ImplementationRunStatus] = mapped_column(
        Enum(ImplementationRunStatus, native_enum=False, length=20, validate_strings=True),
        default=ImplementationRunStatus.PENDING,
        nullable=False,
    )

    # --- Output (requirement 3) ----------------------------------------
    proposed_file_changes: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    diff_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    explanation: Mapped[str] = mapped_column(Text, default="", nullable=False)
    test_command: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    risks: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Drafted by the agent itself now (see app/services/implementation_agent.py),
    # not just synthesized at PR-creation time — create_pull_request below
    # still builds its own PR body today for backward compatibility with
    # project-level runs that predate this column being populated, but a
    # story-scoped run's PR body prefers this field when it's set.
    pr_description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    used_mock: Mapped[bool] = mapped_column(default=True, nullable=False)
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Agent Context Builder rule 5 — same role as AgentRun's own
    # engineering_setup_context_snapshot (see app/models/agent.py): exactly
    # which Project Engineering Setup fields (repo config, build/test
    # commands, coding standards, guardrails, ...) were actually included
    # for this implementation run, for audit/debugging.
    engineering_setup_context_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Human review before anything downstream exists to apply this --
    review_status: Mapped[ImplementationRunReviewStatus] = mapped_column(
        Enum(ImplementationRunReviewStatus, native_enum=False, length=20, validate_strings=True),
        default=ImplementationRunReviewStatus.PENDING_REVIEW,
        nullable=False,
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped["Project"] = relationship("Project")
    implementation_task: Mapped["ImplementationTask"] = relationship("ImplementationTask")
    repository_snapshot: Mapped["RepositorySnapshot | None"] = relationship("RepositorySnapshot")
    triggered_by: Mapped["User | None"] = relationship("User", foreign_keys=[triggered_by_user_id])
    reviewed_by: Mapped["User | None"] = relationship("User", foreign_keys=[reviewed_by_user_id])
    story: Mapped["Story | None"] = relationship("Story")
    lane: Mapped["StoryDeliveryLane | None"] = relationship("StoryDeliveryLane")
    story_lld_artifact: Mapped["StoryArtifact | None"] = relationship("StoryArtifact")
    assigned_user: Mapped["User | None"] = relationship("User", foreign_keys=[assigned_user_id])
