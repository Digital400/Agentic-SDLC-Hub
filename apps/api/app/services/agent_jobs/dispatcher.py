"""AgentJobDispatcher — the interface every job-execution backend
implements, and InlineJobDispatcher, the default/test/local adapter.

InlineJobDispatcher IS this codebase's preserved synchronous mode (this
phase's "Preserve current synchronous mode behind a feature flag"
requirement): it runs a job's handler immediately, in the caller's own
thread and database transaction, exactly like every existing
request-scoped agent call in this codebase already does (Phase 00
baseline section 1) — nothing about *when* work happens changes when
Settings.AGENT_JOB_DISPATCHER_MODE is left at its default ("inline"); only
the NEW AgentJob bookkeeping (state, events, heartbeat) is new.

HANDLER REGISTRATION, NOT RAW CALLABLES: a dispatcher's `submit` takes a
`handler_name` (a string key into `_HANDLER_REGISTRY`) rather than a
Python callable — a live closure can't cross a process boundary, which
CeleryJobDispatcher's worker process must do (see celery_dispatcher.py).
Registering handlers by name keeps InlineJobDispatcher and
CeleryJobDispatcher callable through the exact same interface.
"""

from __future__ import annotations

import abc
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.models import AgentJob, JobFailureCategory
from app.services.agent_jobs.failure_classification import classify_failure
from app.services.agent_jobs.job_service import AgentJobService

JobHandler = Callable[[AgentJobService, AgentJob], None]
"""A handler receives the service (for recording events/transitions) and
the job itself; it is responsible for calling exactly one of
mark_completed/mark_failed/mark_waiting_input/mark_waiting_approval before
returning — see handler.py's default_job_handler for the reference
implementation. A handler that raises is treated as an unhandled failure
(caught and classified by the dispatcher — see InlineJobDispatcher.submit)."""

_HANDLER_REGISTRY: dict[str, JobHandler] = {}


def register_handler(name: str) -> Callable[[JobHandler], JobHandler]:
    def decorator(fn: JobHandler) -> JobHandler:
        _HANDLER_REGISTRY[name] = fn
        return fn

    return decorator


class UnknownHandlerError(Exception):
    pass


def get_handler(name: str) -> JobHandler:
    handler = _HANDLER_REGISTRY.get(name)
    if handler is None:
        raise UnknownHandlerError(f"No job handler registered under '{name}' — available: {sorted(_HANDLER_REGISTRY)}.")
    return handler


class AgentJobDispatcher(abc.ABC):
    name: str

    @abc.abstractmethod
    def submit(self, *, job_id: uuid.UUID, handler_name: str, db: Session | None = None) -> None:
        """Runs (or schedules) `handler_name`'s registered handler against
        the job identified by `job_id`. `db` is required for
        InlineJobDispatcher (it executes in the caller's own session/
        transaction) and ignored by CeleryJobDispatcher (its worker opens
        its own session when the task actually runs — see
        celery_dispatcher.py)."""


class InlineJobDispatcher(AgentJobDispatcher):
    name = "inline"

    def submit(self, *, job_id: uuid.UUID, handler_name: str, db: Session | None = None) -> None:
        if db is None:
            raise ValueError("InlineJobDispatcher.submit requires a db session — it runs synchronously in the caller's own transaction.")

        handler = get_handler(handler_name)
        service = AgentJobService(db)
        job = service.get_or_404(job_id)

        service.mark_preparing(job, dispatcher_backend=self.name)
        try:
            handler(service, job)
        except Exception as exc:  # noqa: BLE001 — every handler failure must be recorded on the job, never left as an unhandled 500
            # job.status already reflects whatever the handler itself set
            # (via service.mark_* calls) before raising — no need to
            # reload from the database within the same session/
            # transaction. Only mark_failed here if the handler hadn't
            # already reached some other terminal-or-waiting state itself.
            if job.status.value not in ("FAILED", "COMPLETED", "CANCELLED", "STALE", "WAITING_INPUT", "WAITING_APPROVAL"):
                service.mark_failed(job, error_message=str(exc), failure_category=classify_failure(exc))
            raise
