"""Tests for app/services/agent_jobs/dispatcher.py — the handler registry
and InlineJobDispatcher (the preserved-synchronous-mode adapter)."""

import uuid
from datetime import datetime, timezone

import pytest

from app.agent_runtime import WorkObjective, WorkPacket, WorkPacketTaskType
from app.models import AgentJobStatus, JobFailureCategory
from app.services.agent_jobs.dispatcher import (
    InlineJobDispatcher,
    UnknownHandlerError,
    get_handler,
    register_handler,
)
from app.services.agent_jobs.job_service import AgentJobService


def _work_packet(project_id) -> WorkPacket:
    return WorkPacket(
        packet_id=uuid.uuid4(), task_type=WorkPacketTaskType.HLD, project_id=project_id,
        objective=WorkObjective(goal="g", success_definition="s"), created_at=datetime.now(timezone.utc),
    )


# --- Handler registry ----------------------------------------------------------------------


def test_register_and_get_handler():
    @register_handler("test-handler-a")
    def _handler(service, job):
        pass

    assert get_handler("test-handler-a") is _handler


def test_get_unknown_handler_raises():
    with pytest.raises(UnknownHandlerError):
        get_handler("definitely-not-registered")


# --- InlineJobDispatcher: the preserved synchronous mode ---------------------------------


def test_inline_dispatcher_requires_a_db_session():
    dispatcher = InlineJobDispatcher()
    with pytest.raises(ValueError, match="db session"):
        dispatcher.submit(job_id=uuid.uuid4(), handler_name="anything", db=None)


def test_inline_dispatcher_runs_the_handler_synchronously_and_marks_completed(db, project):
    @register_handler("test-succeeds")
    def _handler(service: AgentJobService, job):
        service.mark_running(job)
        service.mark_completed(job, result={"ok": True})

    job, _ = AgentJobService(db).create_job(work_packet=_work_packet(project.id))
    db.flush()

    InlineJobDispatcher().submit(job_id=job.id, handler_name="test-succeeds", db=db)

    assert job.status == AgentJobStatus.COMPLETED
    assert job.result == {"ok": True}
    assert job.dispatcher_backend == "inline"


def test_inline_dispatcher_marks_preparing_before_calling_the_handler(db, project):
    seen_statuses = []

    @register_handler("test-observes-preparing")
    def _handler(service, job):
        seen_statuses.append(job.status)
        service.mark_running(job)
        service.mark_completed(job, result={})

    job, _ = AgentJobService(db).create_job(work_packet=_work_packet(project.id))
    db.flush()
    InlineJobDispatcher().submit(job_id=job.id, handler_name="test-observes-preparing", db=db)

    assert seen_statuses == [AgentJobStatus.PREPARING]


def test_inline_dispatcher_a_raising_handler_marks_the_job_failed_and_reraises(db, project):
    @register_handler("test-raises")
    def _handler(service, job):
        service.mark_running(job)
        raise ConnectionError("upstream unreachable")

    job, _ = AgentJobService(db).create_job(work_packet=_work_packet(project.id))
    db.flush()

    with pytest.raises(ConnectionError):
        InlineJobDispatcher().submit(job_id=job.id, handler_name="test-raises", db=db)

    assert job.status == AgentJobStatus.FAILED
    assert job.failure_category == JobFailureCategory.TRANSIENT
    assert "upstream unreachable" in job.error_message


def test_inline_dispatcher_does_not_override_a_handler_that_already_reached_waiting_input(db, project):
    @register_handler("test-waits-then-raises")
    def _handler(service, job):
        service.mark_running(job)
        service.mark_waiting_input(job)
        raise RuntimeError("should not matter — already WAITING_INPUT")

    job, _ = AgentJobService(db).create_job(work_packet=_work_packet(project.id))
    db.flush()

    with pytest.raises(RuntimeError):
        InlineJobDispatcher().submit(job_id=job.id, handler_name="test-waits-then-raises", db=db)

    assert job.status == AgentJobStatus.WAITING_INPUT  # not overwritten to FAILED
