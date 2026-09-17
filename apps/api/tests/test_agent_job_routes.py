"""Tests for app/api/routes/agent_jobs.py — direct calls into the real
route functions, same convention as every other route test in this repo
(no TestClient exists in this codebase)."""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.agent_runtime import WorkObjective, WorkPacket, WorkPacketTaskType
from app.api.routes.agent_jobs import (
    cancel_agent_job,
    continue_agent_job,
    get_agent_job,
    list_agent_job_events,
    start_agent_job,
)
from app.model_gateway.cost import Cost
from app.model_gateway.fake_gateway import FakeCallOutcome, FakeModelGateway
from app.models import AgentJobStatus, User, UserRole
from app.schemas.agent_job import (
    CancelAgentJobRequest,
    ContinueAgentJobRequest,
    StartAgentJobRequest,
)
from app.services.execution_profile_service import ProjectExecutionProfileService


def _work_packet(project_id, **overrides) -> WorkPacket:
    defaults = dict(
        packet_id=uuid.uuid4(), task_type=WorkPacketTaskType.HLD, project_id=project_id,
        objective=WorkObjective(goal="g", success_definition="s"), created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return WorkPacket(**defaults)


def _product_owner(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="PO", role=UserRole.PRODUCT_OWNER)
    db.add(user)
    db.flush()
    return user


def _approve_execution_profile(db, project, actor):
    po = _product_owner(db)
    service = ProjectExecutionProfileService(db)
    profile = service.propose_from_template(project=project, template_key="python-fastapi-postgres", triggered_by=actor)
    db.flush()
    service.approve(profile=profile, approved_by=po)
    db.flush()


def _install_fake_gateway(monkeypatch, gateway: FakeModelGateway) -> None:
    import app.services.agent_jobs.handler as handler_module

    monkeypatch.setattr(handler_module, "select_gateway", lambda: gateway)


@pytest.fixture(autouse=True)
def _fake_gateway_default(db, project, actor, monkeypatch):
    """Every route test needs an execution profile + a working gateway to
    actually complete a job via InlineJobDispatcher (the default) — set
    up once here so individual tests stay focused on the route contract."""
    _approve_execution_profile(db, project, actor)
    _install_fake_gateway(monkeypatch, FakeModelGateway(default_outcome=FakeCallOutcome(
        content_markdown="{}\n---\nDone.", cost=Cost.free(),
    )))


# --- Start -----------------------------------------------------------------------------


def test_start_agent_job_runs_synchronously_by_default_and_returns_completed(db, project, actor):
    result = start_agent_job(StartAgentJobRequest(work_packet=_work_packet(project.id), triggered_by_user_id=actor.id), db)
    assert result.status == AgentJobStatus.COMPLETED
    assert result.dispatcher_backend == "inline"


def test_start_agent_job_rejects_an_unknown_handler(db, project, actor):
    with pytest.raises(HTTPException) as exc_info:
        start_agent_job(StartAgentJobRequest(work_packet=_work_packet(project.id), handler_name="not-a-real-handler"), db)
    assert exc_info.value.status_code == 400


def test_start_agent_job_rejects_an_unknown_user(db, project):
    with pytest.raises(HTTPException) as exc_info:
        start_agent_job(StartAgentJobRequest(work_packet=_work_packet(project.id), triggered_by_user_id=uuid.uuid4()), db)
    assert exc_info.value.status_code == 400


def test_start_agent_job_with_repeated_idempotency_key_returns_the_same_job(db, project, actor):
    r1 = start_agent_job(StartAgentJobRequest(work_packet=_work_packet(project.id), idempotency_key="req-1"), db)
    r2 = start_agent_job(StartAgentJobRequest(work_packet=_work_packet(project.id), idempotency_key="req-1"), db)
    assert r1.id == r2.id


def test_start_agent_job_without_a_user_is_allowed(db, project):
    result = start_agent_job(StartAgentJobRequest(work_packet=_work_packet(project.id)), db)
    assert result.created_by_id is None
    assert result.status == AgentJobStatus.COMPLETED


# --- Polling (get + events) -----------------------------------------------------------------


def test_get_agent_job_404s_for_unknown_id(db):
    with pytest.raises(HTTPException) as exc_info:
        get_agent_job(uuid.uuid4(), db)
    assert exc_info.value.status_code == 404


def test_get_agent_job_returns_the_current_state(db, project, actor):
    started = start_agent_job(StartAgentJobRequest(work_packet=_work_packet(project.id)), db)
    fetched = get_agent_job(started.id, db)
    assert fetched.id == started.id
    assert fetched.status == AgentJobStatus.COMPLETED


def test_list_agent_job_events_returns_the_full_history(db, project, actor):
    started = start_agent_job(StartAgentJobRequest(work_packet=_work_packet(project.id)), db)
    events = list_agent_job_events(started.id, since_sequence=0, db=db)
    assert len(events) >= 3  # QUEUED status, at least PLAN_SUMMARY/USAGE/COMPLETED from the handler
    assert events == sorted(events, key=lambda e: e.sequence)


def test_list_agent_job_events_since_sequence_returns_only_newer_events(db, project, actor):
    started = start_agent_job(StartAgentJobRequest(work_packet=_work_packet(project.id)), db)
    all_events = list_agent_job_events(started.id, since_sequence=0, db=db)
    partial = list_agent_job_events(started.id, since_sequence=all_events[0].sequence, db=db)
    assert len(partial) == len(all_events) - 1


def test_list_agent_job_events_404s_for_unknown_job(db):
    with pytest.raises(HTTPException) as exc_info:
        list_agent_job_events(uuid.uuid4(), since_sequence=0, db=db)
    assert exc_info.value.status_code == 404


# --- Cancel ------------------------------------------------------------------------------


def test_cancel_a_completed_job_returns_409(db, project, actor):
    started = start_agent_job(StartAgentJobRequest(work_packet=_work_packet(project.id)), db)
    with pytest.raises(HTTPException) as exc_info:
        cancel_agent_job(started.id, CancelAgentJobRequest(requested_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409


def test_cancel_rejects_unknown_user(db, project, actor):
    started = start_agent_job(StartAgentJobRequest(work_packet=_work_packet(project.id)), db)
    with pytest.raises(HTTPException) as exc_info:
        cancel_agent_job(started.id, CancelAgentJobRequest(requested_by_user_id=uuid.uuid4()), db)
    assert exc_info.value.status_code == 400


# --- Continue (resume clarification/approval) ------------------------------------------------


def test_continue_creates_a_new_job_with_continuation_link(db, project, actor, monkeypatch):
    import app.services.agent_jobs.handler as handler_module

    monkeypatch.setattr(handler_module, "select_gateway", lambda: FakeModelGateway(default_outcome=FakeCallOutcome(
        content_markdown="", needs_clarification=True, clarification_questions=["Which auth method?"],
    )))
    original = start_agent_job(StartAgentJobRequest(work_packet=_work_packet(project.id)), db)
    assert original.status == AgentJobStatus.WAITING_INPUT

    monkeypatch.setattr(handler_module, "select_gateway", lambda: FakeModelGateway(default_outcome=FakeCallOutcome(content_markdown="{}\n---\nFinal answer.")))
    continuation = continue_agent_job(original.id, ContinueAgentJobRequest(updated_work_packet=_work_packet(project.id), triggered_by_user_id=actor.id), db)

    assert continuation.continuation_of_job_id == original.id
    assert continuation.status == AgentJobStatus.COMPLETED
    assert continuation.id != original.id


def test_continue_rejects_a_job_that_is_not_waiting(db, project, actor):
    started = start_agent_job(StartAgentJobRequest(work_packet=_work_packet(project.id)), db)  # already COMPLETED
    with pytest.raises(HTTPException) as exc_info:
        continue_agent_job(started.id, ContinueAgentJobRequest(updated_work_packet=_work_packet(project.id)), db)
    assert exc_info.value.status_code == 409


def test_continue_404s_for_unknown_job(db):
    with pytest.raises(HTTPException) as exc_info:
        continue_agent_job(uuid.uuid4(), ContinueAgentJobRequest(updated_work_packet=_work_packet(uuid.uuid4())), db)
    assert exc_info.value.status_code == 404
