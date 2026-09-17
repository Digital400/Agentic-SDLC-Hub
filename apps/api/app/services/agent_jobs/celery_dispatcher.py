"""CeleryJobDispatcher — the production background-worker adapter,
Celery + Redis behind the same AgentJobDispatcher interface
InlineJobDispatcher implements.

Only ever imported when Settings.AGENT_JOB_DISPATCHER_MODE == "celery" is
explicitly selected — see app/services/agent_jobs/__init__.py's
select_dispatcher, which lazily imports this module exactly like
app.model_gateway.gateway_factory's own lazy-import pattern for its own
optional backends (Phase 05). The default ("inline") mode never imports
`celery` or `redis` at all.

WORKER-SIDE EXECUTION: `submit()` (the API/request-serving process) only
ever calls `.delay()` — it never runs a handler itself. `_run_job_task`
(the function Celery actually invokes, IN THE WORKER PROCESS) opens its
own fresh database session via app.core.database.SessionLocal, since a
worker process shares no state with the process that called `submit()`.
"""

from __future__ import annotations

import uuid

from celery import Celery
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.agent_jobs.dispatcher import AgentJobDispatcher, get_handler
from app.services.agent_jobs.failure_classification import classify_failure
from app.services.agent_jobs.job_service import AgentJobService


def _build_celery_app() -> Celery:
    settings = get_settings()
    if not settings.CELERY_BROKER_URL:
        raise RuntimeError(
            "AGENT_JOB_DISPATCHER_MODE is 'celery' but CELERY_BROKER_URL is not set — "
            "configure a real Redis URL (e.g. redis://localhost:6379/0) before selecting this mode."
        )
    app = Celery("agent_jobs", broker=settings.CELERY_BROKER_URL, backend=settings.CELERY_RESULT_BACKEND_URL or settings.CELERY_BROKER_URL)
    app.conf.task_serializer = "json"
    app.conf.result_serializer = "json"
    app.conf.accept_content = ["json"]
    return app


# Constructed lazily (not at import time) — importing this module must
# never itself require a reachable Redis broker; only actually submitting
# or running a task does. See select_dispatcher's own lazy-import comment.
_celery_app: Celery | None = None


def get_celery_app() -> Celery:
    global _celery_app
    if _celery_app is None:
        _celery_app = _build_celery_app()
        _celery_app.task(name="agent_jobs.run")(_run_job_task)
    return _celery_app


def _run_job_task(job_id_str: str, handler_name: str) -> None:
    """The actual Celery task body — runs in a worker process. Opens its
    own DB session (see module docstring), runs the handler exactly like
    InlineJobDispatcher does, and records the same failure-classification
    on an unhandled exception."""
    from app.core.database import SessionLocal

    db: Session = SessionLocal()
    try:
        service = AgentJobService(db)
        job = service.get_or_404(uuid.UUID(job_id_str))
        handler = get_handler(handler_name)

        service.mark_preparing(job, dispatcher_backend="celery")
        db.commit()
        try:
            handler(service, job)
            db.commit()
        except Exception as exc:  # noqa: BLE001 — must be recorded on the job row, never just logged to the worker's own stdout
            db.rollback()
            job = service.get_or_404(uuid.UUID(job_id_str))
            if job.status.value not in ("FAILED", "COMPLETED", "CANCELLED", "STALE", "WAITING_INPUT", "WAITING_APPROVAL"):
                service.mark_failed(job, error_message=str(exc), failure_category=classify_failure(exc))
                db.commit()
            raise
    finally:
        db.close()


class CeleryJobDispatcher(AgentJobDispatcher):
    name = "celery"

    def submit(self, *, job_id: uuid.UUID, handler_name: str, db: Session | None = None) -> None:
        """`db` is accepted (interface compatibility with
        InlineJobDispatcher) but ignored — the caller's own transaction
        has nothing to do with when/where the worker actually runs; the
        caller is expected to have already committed the job's initial
        QUEUED row before calling submit (see
        app/api/routes/agent_jobs.py's start_agent_job)."""
        get_celery_app().send_task("agent_jobs.run", args=[str(job_id), handler_name])
