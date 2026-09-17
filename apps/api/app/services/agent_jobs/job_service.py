"""AgentJobService — create, inspect, and transition AgentJob rows. The
one place every dispatcher backend (InlineJobDispatcher,
CeleryJobDispatcher) and every API route reads/writes AgentJob/
AgentJobEvent state, so every state transition is recorded the same way
regardless of which backend or endpoint triggered it — same discipline
app/services/graph_engine.py already established for WorkflowNode (Phase
00 baseline).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.agent_runtime import WorkPacket
from app.models import AgentJob, AgentJobEvent, AgentJobStatus, JobFailureCategory, User
from app.services.agent_jobs.events import NormalizedEvent, status_event

_TERMINAL_STATUSES = frozenset({
    AgentJobStatus.COMPLETED, AgentJobStatus.FAILED, AgentJobStatus.CANCELLED, AgentJobStatus.STALE,
})
# A job "in flight" (eligible for heartbeat/stale detection) — QUEUED
# hasn't started yet (no worker to heartbeat), and every _TERMINAL_STATUSES
# member is already done.
_HEARTBEAT_ELIGIBLE_STATUSES = frozenset({AgentJobStatus.PREPARING, AgentJobStatus.RUNNING})


class AgentJobError(Exception):
    """A caller/state error (bad id, invalid transition) — not a runtime
    failure of the job's own work."""


class AgentJobService:
    def __init__(self, db: Session):
        self.db = db

    # --- Creation / idempotency --------------------------------------------------------

    def create_job(
        self, *, work_packet: WorkPacket, created_by: User | None = None, idempotency_key: str | None = None,
    ) -> tuple[AgentJob, bool]:
        """Returns (job, created) — `created=False` means an existing job
        with this exact idempotency_key was found and returned instead of
        creating a duplicate (the standard idempotency-key contract: the
        SAME key always maps to the SAME job, regardless of how many
        times a caller submits it — e.g. a retried HTTP request after a
        dropped response)."""
        if idempotency_key is not None:
            existing = self.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing, False

        job = AgentJob(
            idempotency_key=idempotency_key,
            project_id=work_packet.project_id,
            story_id=work_packet.story_id,
            task_type=work_packet.task_type.value,
            work_packet=work_packet.model_dump(mode="json"),
            status=AgentJobStatus.QUEUED,
            queued_at=datetime.now(timezone.utc),
            created_by_id=created_by.id if created_by else None,
        )
        self.db.add(job)
        self.db.flush()
        self.record_event(job, status_event(status=AgentJobStatus.QUEUED.value, detail="Job created."))
        return job, True

    def get_by_idempotency_key(self, idempotency_key: str) -> AgentJob | None:
        return self.db.query(AgentJob).filter(AgentJob.idempotency_key == idempotency_key).first()

    def get_or_404(self, job_id: uuid.UUID) -> AgentJob:
        job = self.db.get(AgentJob, job_id)
        if job is None:
            raise AgentJobError(f"AgentJob {job_id} not found.")
        return job

    def parse_work_packet(self, job: AgentJob) -> WorkPacket:
        return WorkPacket.model_validate(job.work_packet)

    # --- State transitions ---------------------------------------------------------------

    def mark_preparing(self, job: AgentJob, *, dispatcher_backend: str) -> None:
        self._require_status(job, {AgentJobStatus.QUEUED})
        job.status = AgentJobStatus.PREPARING
        job.dispatcher_backend = dispatcher_backend
        job.started_at = job.started_at or datetime.now(timezone.utc)
        job.heartbeat_at = datetime.now(timezone.utc)
        self.record_event(job, status_event(status=AgentJobStatus.PREPARING.value))

    def mark_running(self, job: AgentJob) -> None:
        self._require_status(job, {AgentJobStatus.PREPARING, AgentJobStatus.RUNNING})
        job.status = AgentJobStatus.RUNNING
        job.heartbeat_at = datetime.now(timezone.utc)
        self.record_event(job, status_event(status=AgentJobStatus.RUNNING.value))

    def mark_waiting_input(self, job: AgentJob) -> None:
        self._require_status(job, {AgentJobStatus.PREPARING, AgentJobStatus.RUNNING})
        job.status = AgentJobStatus.WAITING_INPUT
        job.completed_at = datetime.now(timezone.utc)
        self.record_event(job, status_event(status=AgentJobStatus.WAITING_INPUT.value, detail="Awaiting a human clarification answer — see POST /agent-jobs/{id}/continue."))

    def mark_waiting_approval(self, job: AgentJob) -> None:
        self._require_status(job, {AgentJobStatus.PREPARING, AgentJobStatus.RUNNING})
        job.status = AgentJobStatus.WAITING_APPROVAL
        job.completed_at = datetime.now(timezone.utc)
        self.record_event(job, status_event(status=AgentJobStatus.WAITING_APPROVAL.value, detail="Awaiting a human approval decision — see POST /agent-jobs/{id}/continue."))

    def mark_completed(self, job: AgentJob, *, result: dict) -> None:
        self._require_status(job, {AgentJobStatus.PREPARING, AgentJobStatus.RUNNING})
        job.status = AgentJobStatus.COMPLETED
        job.result = result
        job.completed_at = datetime.now(timezone.utc)
        self.record_event(job, status_event(status=AgentJobStatus.COMPLETED.value))

    def mark_failed(self, job: AgentJob, *, error_message: str, failure_category: JobFailureCategory) -> None:
        self._require_status(job, {AgentJobStatus.QUEUED, AgentJobStatus.PREPARING, AgentJobStatus.RUNNING})
        job.status = AgentJobStatus.FAILED
        job.error_message = error_message
        job.failure_category = failure_category
        job.completed_at = datetime.now(timezone.utc)
        self.record_event(job, status_event(status=AgentJobStatus.FAILED.value, detail=f"{failure_category.value}: {error_message}"))

    def mark_stale(self, job: AgentJob) -> None:
        self._require_status(job, _HEARTBEAT_ELIGIBLE_STATUSES)
        job.status = AgentJobStatus.STALE
        job.failure_category = JobFailureCategory.TRANSIENT  # a stale job is presumed retryable — the worker likely died, not the work itself
        job.completed_at = datetime.now(timezone.utc)
        self.record_event(job, status_event(status=AgentJobStatus.STALE.value, detail=f"No heartbeat since {job.heartbeat_at}."))

    # --- Cancellation ----------------------------------------------------------------------

    def request_cancellation(self, job: AgentJob, *, requested_by: User) -> None:
        """Sets a flag a running handler is expected to poll (see
        InlineJobDispatcher/CeleryJobDispatcher's own cooperative-
        cancellation check) — this does NOT itself transition `status`;
        only the handler noticing the flag and stopping does that (via
        mark_cancelled). A QUEUED job, which has no running handler to
        notice the flag, is cancelled immediately instead — see below."""
        if job.status in _TERMINAL_STATUSES:
            raise AgentJobError(f"AgentJob {job.id} is already {job.status.value} — cannot cancel.")
        job.cancellation_requested = True
        job.cancellation_requested_by_id = requested_by.id
        if job.status == AgentJobStatus.QUEUED:
            self.mark_cancelled(job)

    def mark_cancelled(self, job: AgentJob) -> None:
        if job.status in _TERMINAL_STATUSES:
            raise AgentJobError(f"AgentJob {job.id} is already {job.status.value} — cannot cancel.")
        job.status = AgentJobStatus.CANCELLED
        job.completed_at = datetime.now(timezone.utc)
        self.record_event(job, status_event(status=AgentJobStatus.CANCELLED.value))

    # --- Heartbeat / stale detection --------------------------------------------------------

    def record_heartbeat(self, job: AgentJob) -> None:
        if job.status not in _HEARTBEAT_ELIGIBLE_STATUSES:
            raise AgentJobError(f"AgentJob {job.id} is {job.status.value} — heartbeat only applies to PREPARING/RUNNING jobs.")
        job.heartbeat_at = datetime.now(timezone.utc)

    def find_stale_jobs(self, *, threshold_seconds: float) -> list[AgentJob]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=threshold_seconds)
        return (
            self.db.query(AgentJob)
            .filter(AgentJob.status.in_(_HEARTBEAT_ELIGIBLE_STATUSES))
            .filter((AgentJob.heartbeat_at.is_(None)) | (AgentJob.heartbeat_at < cutoff))
            .all()
        )

    def sweep_stale_jobs(self, *, threshold_seconds: float) -> list[AgentJob]:
        """Finds and transitions every stale job in one pass — the
        function a scheduled sweep (a cron-triggered endpoint, or a
        Celery beat task once AGENT_JOB_DISPATCHER_MODE="celery") would
        call periodically."""
        stale = self.find_stale_jobs(threshold_seconds=threshold_seconds)
        for job in stale:
            self.mark_stale(job)
        return stale

    # --- Continuation (resume clarification/approval) ---------------------------------------

    def create_continuation(
        self, *, original_job: AgentJob, updated_work_packet: WorkPacket, created_by: User | None = None, idempotency_key: str | None = None,
    ) -> tuple[AgentJob, bool]:
        """The ONLY way a WAITING_INPUT/WAITING_APPROVAL job's work
        continues — see app/models/agent_job.py's class docstring for why
        this is a NEW job, never an in-place resume. `updated_work_packet`
        is the caller's responsibility to construct (typically: the
        original job's own WorkPacket, with the human's answer merged into
        its objective/extensions — this service doesn't prescribe how)."""
        if original_job.status not in (AgentJobStatus.WAITING_INPUT, AgentJobStatus.WAITING_APPROVAL):
            raise AgentJobError(f"AgentJob {original_job.id} is {original_job.status.value} — only a WAITING_INPUT/WAITING_APPROVAL job can be continued.")

        job, created = self.create_job(work_packet=updated_work_packet, created_by=created_by, idempotency_key=idempotency_key)
        if created:
            job.continuation_of_job_id = original_job.id
            self.db.flush()
        return job, created

    # --- Events --------------------------------------------------------------------------

    def record_event(self, job: AgentJob, event: NormalizedEvent) -> AgentJobEvent:
        next_sequence = (
            self.db.query(AgentJobEvent.sequence)
            .filter(AgentJobEvent.job_id == job.id)
            .order_by(AgentJobEvent.sequence.desc())
            .limit(1)
            .scalar()
        )
        row = AgentJobEvent(
            job_id=job.id, sequence=(next_sequence or 0) + 1, event_type=event.event_type, payload=event.to_stored_payload(),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def list_events(self, job_id: uuid.UUID, *, since_sequence: int = 0) -> list[AgentJobEvent]:
        return (
            self.db.query(AgentJobEvent)
            .filter(AgentJobEvent.job_id == job_id, AgentJobEvent.sequence > since_sequence)
            .order_by(AgentJobEvent.sequence)
            .all()
        )

    # --- Internal --------------------------------------------------------------------------

    def _require_status(self, job: AgentJob, allowed: set[AgentJobStatus]) -> None:
        if job.status not in allowed:
            allowed_label = ", ".join(sorted(s.value for s in allowed))
            raise AgentJobError(f"AgentJob {job.id} is {job.status.value}; expected one of: {allowed_label}.")
