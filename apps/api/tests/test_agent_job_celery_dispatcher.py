"""Tests for app/services/agent_jobs/celery_dispatcher.py — never connects
to a real Redis broker; every test either mocks the Celery app/task
dispatch, or exercises _run_job_task directly against the real (SQLite)
test database, the way a worker process actually would.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.agent_runtime import WorkObjective, WorkPacket, WorkPacketTaskType
from app.models import AgentJobStatus, JobFailureCategory
from app.services.agent_jobs.dispatcher import register_handler
from app.services.agent_jobs.job_service import AgentJobService


def _work_packet(project_id) -> WorkPacket:
    return WorkPacket(
        packet_id=uuid.uuid4(), task_type=WorkPacketTaskType.HLD, project_id=project_id,
        objective=WorkObjective(goal="g", success_definition="s"), created_at=datetime.now(timezone.utc),
    )


def test_build_celery_app_requires_a_broker_url(monkeypatch):
    from app.services.agent_jobs import celery_dispatcher

    monkeypatch.setattr(celery_dispatcher, "get_settings", lambda: type("S", (), {"CELERY_BROKER_URL": None, "CELERY_RESULT_BACKEND_URL": None})())
    with pytest.raises(RuntimeError, match="CELERY_BROKER_URL"):
        celery_dispatcher._build_celery_app()


def test_build_celery_app_constructs_without_connecting(monkeypatch):
    """Constructing a Celery app object never itself opens a network
    connection to the broker — only actually sending/consuming a task
    does. This is what makes CeleryJobDispatcher importable/testable
    without a real Redis instance."""
    from app.services.agent_jobs import celery_dispatcher

    monkeypatch.setattr(celery_dispatcher, "get_settings", lambda: type("S", (), {"CELERY_BROKER_URL": "redis://localhost:6379/0", "CELERY_RESULT_BACKEND_URL": None})())
    app = celery_dispatcher._build_celery_app()
    assert app.conf.broker_url == "redis://localhost:6379/0"
    assert app.conf.task_serializer == "json"


def test_dispatcher_submit_calls_send_task_not_the_handler_directly(monkeypatch):
    """The API/request-serving process must never run a handler itself in
    celery mode — it only ever enqueues."""
    from app.services.agent_jobs.celery_dispatcher import CeleryJobDispatcher

    calls = []

    class _FakeApp:
        def send_task(self, name, args):
            calls.append((name, args))

    monkeypatch.setattr("app.services.agent_jobs.celery_dispatcher.get_celery_app", lambda: _FakeApp())

    job_id = uuid.uuid4()
    CeleryJobDispatcher().submit(job_id=job_id, handler_name="default")

    assert calls == [("agent_jobs.run", [str(job_id), "default"])]


def test_dispatcher_submit_ignores_a_db_session_if_given(monkeypatch):
    from app.services.agent_jobs.celery_dispatcher import CeleryJobDispatcher

    calls = []
    monkeypatch.setattr("app.services.agent_jobs.celery_dispatcher.get_celery_app", lambda: type("A", (), {"send_task": lambda self, name, args: calls.append(1)})())

    CeleryJobDispatcher().submit(job_id=uuid.uuid4(), handler_name="default", db="not-really-a-session")
    assert len(calls) == 1


# --- _run_job_task: what actually runs inside a worker process -----------------------------


def test_run_job_task_executes_the_handler_against_a_real_session(db, project, monkeypatch):
    """Runs _run_job_task exactly as a worker would, but pointed at the
    test's own SQLite session (via a patched SessionLocal) instead of a
    real Postgres connection — proves the worker-side session-per-task
    plumbing is correct without needing a live worker/broker."""
    from app.services.agent_jobs import celery_dispatcher

    @register_handler("test-celery-succeeds")
    def _handler(service, job):
        service.mark_running(job)
        service.mark_completed(job, result={"done": True})

    job, _ = AgentJobService(db).create_job(work_packet=_work_packet(project.id))
    db.commit()

    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)
    # db.close() would end the test's own session — the task calls it in
    # a `finally`; harmless to no-op here since the test fixture closes
    # its own session afterward regardless.
    monkeypatch.setattr(db, "close", lambda: None)

    celery_dispatcher._run_job_task(str(job.id), "test-celery-succeeds")

    db.refresh(job)
    assert job.status == AgentJobStatus.COMPLETED
    assert job.dispatcher_backend == "celery"
    assert job.result == {"done": True}


def test_run_job_task_marks_failed_and_reraises_on_handler_exception(db, project, monkeypatch):
    from app.services.agent_jobs import celery_dispatcher

    @register_handler("test-celery-raises")
    def _handler(service, job):
        service.mark_running(job)
        raise ConnectionError("worker-side network error")

    job, _ = AgentJobService(db).create_job(work_packet=_work_packet(project.id))
    db.commit()

    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)

    with pytest.raises(ConnectionError):
        celery_dispatcher._run_job_task(str(job.id), "test-celery-raises")

    db.refresh(job)
    assert job.status == AgentJobStatus.FAILED
    assert job.failure_category == JobFailureCategory.TRANSIENT
