from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AgentJobEventType, AgentJobStatus, JobFailureCategory, OutboxEntryStatus


class AgentJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One durable, dispatcher-agnostic unit of agent work — the
    persistence layer app/services/agent_jobs/job_service.py's
    AgentJobService reads/writes, and what
    app/services/agent_jobs/dispatcher.py's AgentJobDispatcher
    implementations (InlineJobDispatcher, CeleryJobDispatcher) actually
    execute.

    Distinct from AgentRun (app/models/agent.py): AgentRun is the
    existing, unmodified, synchronous project-workflow-stage generation
    record (Phase 00 baseline) — nothing here replaces it. AgentJob is a
    NEW, additive concept: a WorkPacket (app/agent_runtime, Phase 01)
    submitted for durable, possibly-asynchronous execution, with its own
    state machine, normalized event stream, idempotency, heartbeat/stale
    detection, and cancellation — none of which AgentRun has.

    CONTINUATION, NOT IN-PLACE RESUME: a job that reaches WAITING_INPUT or
    WAITING_APPROVAL is terminal-for-now — it is never resumed in place.
    Answering a clarification or approving/rejecting creates a NEW
    AgentJob row with `continuation_of_job_id` set to this one's id (see
    job_service.py's create_continuation) — mirrors how this codebase's
    existing Review/clarification flow already works (Phase 00 baseline
    section 6.1: a human answers, then a fresh run happens; nothing here
    is resumed mid-flight).
    """

    __tablename__ = "agent_jobs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_agent_jobs_idempotency_key"),)

    # NULL is a valid, common value (idempotency is opt-in per caller) —
    # UniqueConstraint allows any number of NULLs across every SQL backend
    # this codebase targets (Postgres, and SQLite for the test suite).
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    story_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), nullable=True)

    # Mirrors app.agent_runtime.WorkPacketTaskType.value — kept as a plain
    # string (not a DB enum) so this table never needs its own migration
    # just because agent_runtime.py's closed task-type set grows; the
    # real validation happens when work_packet is parsed back into a real
    # WorkPacket (see job_service.py).
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # The full serialized WorkPacket (app.agent_runtime.WorkPacket, Phase
    # 01) this job executes — a reference-shaped contract already (hard
    # rule 3: no upstream artifact content duplicated), so storing it
    # whole here duplicates nothing beyond what WorkPacket itself already
    # keeps bounded.
    work_packet: Mapped[dict] = mapped_column(JSON, nullable=False)

    status: Mapped[AgentJobStatus] = mapped_column(
        Enum(AgentJobStatus, native_enum=False, length=20, validate_strings=True), default=AgentJobStatus.QUEUED, nullable=False,
    )
    # Which dispatcher backend actually ran (or is running) this job —
    # "inline" | "celery" | any future adapter's own `name` — recorded for
    # audit, since Settings.AGENT_JOB_DISPATCHER_MODE could change between
    # when a job was queued and when it's inspected later.
    dispatcher_backend: Mapped[str | None] = mapped_column(String(50), nullable=True)

    failure_category: Mapped[JobFailureCategory | None] = mapped_column(
        Enum(JobFailureCategory, native_enum=False, length=10, validate_strings=True), nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Self-referential — see class docstring's CONTINUATION note.
    continuation_of_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_jobs.id", ondelete="SET NULL", use_alter=True, name="fk_agent_jobs_continuation_of_job_id"), nullable=True,
    )

    cancellation_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cancellation_requested_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    # Updated by whichever dispatcher backend is actually executing this
    # job — see job_service.py's record_heartbeat / find_stale_jobs. NULL
    # until the job starts running at all.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # The final app.agent_runtime.ExecutionResult (Phase 01), serialized —
    # NULL until the job reaches a terminal state.
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    project: Mapped["Project"] = relationship("Project")
    story: Mapped["Story | None"] = relationship("Story")
    created_by: Mapped["User | None"] = relationship("User", foreign_keys=[created_by_id])
    cancellation_requested_by: Mapped["User | None"] = relationship("User", foreign_keys=[cancellation_requested_by_id])
    continuation_of: Mapped["AgentJob | None"] = relationship("AgentJob", remote_side="AgentJob.id", foreign_keys=[continuation_of_job_id])
    events: Mapped[list["AgentJobEvent"]] = relationship(
        "AgentJobEvent", back_populates="job", cascade="all, delete-orphan", order_by="AgentJobEvent.sequence",
    )
    outbox_entries: Mapped[list["AgentJobOutboxEntry"]] = relationship(
        "AgentJobOutboxEntry", back_populates="job", cascade="all, delete-orphan",
    )


class AgentJobEvent(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One normalized runtime event for one AgentJob — append-only,
    immutable, exactly like AgentRunLoopEvent (app/models/agent.py). See
    app/models/enums.py's AgentJobEventType for the closed event-kind set,
    and app/services/agent_jobs/events.py's HARD RULE: `payload` must
    never carry a chain-of-thought/reasoning field — enforced by that
    module's scrub function before a row is ever written here, not by
    this table's schema (a JSON column can't structurally forbid a key
    name).
    """

    __tablename__ = "agent_job_events"
    __table_args__ = (UniqueConstraint("job_id", "sequence", name="uq_agent_job_events_job_id_sequence"),)

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_jobs.id", ondelete="CASCADE"), nullable=False)
    # Monotonically increasing per job (assigned by
    # AgentJobService._next_sequence), starting at 1 — what a poller's
    # `since_sequence` query param compares against, and what makes
    # GET /agent-jobs/{id}/events safe to call repeatedly without
    # re-fetching events already seen.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[AgentJobEventType] = mapped_column(
        Enum(AgentJobEventType, native_enum=False, length=20, validate_strings=True), nullable=False,
    )
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    job: Mapped["AgentJob"] = relationship("AgentJob", back_populates="events")


class AgentJobOutboxEntry(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One pending-or-sent external write, recorded BEFORE the write is
    attempted — see app/services/agent_jobs/outbox.py's module docstring
    for the full write-ahead pattern this backs (a completed job's
    "create the GitHub PR" step, for example, is recorded here first, so
    a crash between "job completed" and "PR actually created" is
    recoverable/idempotent rather than silently lost or double-sent).
    """

    __tablename__ = "agent_job_outbox_entries"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_agent_job_outbox_entries_idempotency_key"),)

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_jobs.id", ondelete="CASCADE"), nullable=False)
    # The key that makes re-processing this entry safe — e.g.
    # f"{job_id}:create_pull_request" — never NULL (unlike AgentJob's own
    # idempotency_key, which is caller-opt-in; every outbox entry MUST be
    # idempotent by construction, since it represents a real external
    # side effect).
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    external_target: Mapped[str] = mapped_column(String(100), nullable=False, doc="e.g. 'github_pull_request', 'jira_issue' — which external system/action this entry represents.")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[OutboxEntryStatus] = mapped_column(
        Enum(OutboxEntryStatus, native_enum=False, length=10, validate_strings=True), default=OutboxEntryStatus.PENDING, nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped["AgentJob"] = relationship("AgentJob", back_populates="outbox_entries")
