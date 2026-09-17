"""Durable AgentJob execution — Phase 06.

STATUS: additive, strangler-migration architecture, same posture as every
prior phase (app.agent_runtime, app.services.execution_profile_service's
runtime gate, app.prompt_compiler, app.model_gateway). Settings.
AGENT_JOB_DISPATCHER_MODE defaults to "inline" — select_dispatcher()
returns InlineJobDispatcher, which runs a job's handler synchronously in
the caller's own request/transaction: this preserves today's existing
agent-run behavior exactly (Phase 00 baseline section 1), just reachable
through the new AgentJob/AgentJobEvent persistence layer. Set
AGENT_JOB_DISPATCHER_MODE="celery" (with a real CELERY_BROKER_URL) only
once a Celery worker + Redis broker have been provisioned for a given
deployment.

WHY THIS EXISTS (this phase's requirements, each satisfied by a specific
piece of this package):
  - AgentJobDispatcher / InlineJobDispatcher / CeleryJobDispatcher —
    dispatcher.py, celery_dispatcher.py.
  - Persisted job states (QUEUED..STALE) — app/models/agent_job.py's
    AgentJob + app/models/enums.py's AgentJobStatus; every transition goes
    through job_service.py's AgentJobService, never set directly.
  - Normalized runtime events (STATUS..COMPLETED) — events.py,
    app/models/agent_job.py's AgentJobEvent.
  - Idempotency keys — AgentJobService.create_job's idempotency_key param
    (job-level) and AgentJobOutboxEntry's own, separate, always-required
    idempotency_key (external-write level — see outbox.py).
  - Heartbeat / stale-job detection — job_service.py's record_heartbeat /
    find_stale_jobs / sweep_stale_jobs.
  - Cancellation — job_service.py's request_cancellation / mark_cancelled;
    InlineJobDispatcher/CeleryJobDispatcher both check
    AgentJob.cancellation_requested cooperatively (see handler.py).
  - Transient vs. permanent failure classification —
    failure_classification.py's classify_failure.
  - Resuming clarification/approval via a NEW continuation job —
    job_service.py's create_continuation (never an in-place resume — see
    AgentJob's own class docstring for why).
  - Outbox/idempotency pattern for external writes — outbox.py's
    OutboxService.
  - "Do not expose chain-of-thought" — events.py's scrub_chain_of_thought,
    applied to every event payload before it is ever persisted.
  - Preserved synchronous mode behind a flag — see STATUS above.
  - Polling endpoints first, optional event streaming — see
    app/api/routes/agent_jobs.py.
"""

from app.core.config import get_settings
from app.services.agent_jobs.dispatcher import AgentJobDispatcher, InlineJobDispatcher, JobHandler, UnknownHandlerError, get_handler, register_handler
from app.services.agent_jobs.job_service import AgentJobError, AgentJobService
from app.services.agent_jobs.outbox import OutboxConflictError, OutboxService

# Registers the "default" handler (see dispatcher.py's register_handler
# decorator) as a side effect of importing this package — a dispatcher
# resolving handler_name="default" must find it without every caller
# remembering to import handler.py itself first.
from app.services.agent_jobs import handler as _handler  # noqa: F401


class UnknownDispatcherModeError(Exception):
    pass


def select_dispatcher() -> AgentJobDispatcher:
    """The one place Settings.AGENT_JOB_DISPATCHER_MODE resolves to a
    concrete dispatcher — mirrors app.model_gateway.gateway_factory.
    select_gateway's exact pattern (Phase 05): the default branch imports
    nothing beyond this package; only "celery" mode imports celery_
    dispatcher.py (and, transitively, the celery/redis packages)."""
    settings = get_settings()
    mode = settings.AGENT_JOB_DISPATCHER_MODE
    if mode == "inline":
        return InlineJobDispatcher()
    if mode == "celery":
        # Phase 07's security gate — "Keep all external coding runtimes
        # disabled until this security gate passes." InlineJobDispatcher
        # (above) runs in-process, under the calling request's own
        # authorization context, so it is never gated; "celery" is this
        # codebase's only actual external-runtime boundary. Raises
        # SecurityGateNotPassedError (never silently falls back to
        # inline) if the gate isn't satisfied — see
        # app/runtime_security/security_gate.py.
        from app.runtime_security.security_gate import require_security_gate_passed

        require_security_gate_passed(settings)

        from app.services.agent_jobs.celery_dispatcher import CeleryJobDispatcher

        return CeleryJobDispatcher()
    raise UnknownDispatcherModeError(f"Unknown AGENT_JOB_DISPATCHER_MODE '{mode}'.")


__all__ = [
    "AgentJobDispatcher",
    "InlineJobDispatcher",
    "JobHandler",
    "UnknownHandlerError",
    "get_handler",
    "register_handler",
    "AgentJobError",
    "AgentJobService",
    "OutboxConflictError",
    "OutboxService",
    "select_dispatcher",
    "UnknownDispatcherModeError",
]
