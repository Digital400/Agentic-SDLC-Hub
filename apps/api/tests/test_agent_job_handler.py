"""Integration tests for app/services/agent_jobs/handler.py's
default_job_handler — the first place app.agent_runtime (Phase 01),
app.services.execution_profile_service (Phase 03), app.prompt_compiler
(Phase 04), and app.model_gateway (Phase 05) are all actually wired
together and exercised end-to-end.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.agent_runtime import WorkObjective, WorkPacket, WorkPacketTaskType
from app.model_gateway.cost import Cost
from app.model_gateway.fake_gateway import FakeCallOutcome, FakeModelGateway
from app.models import AgentJobStatus, User, UserRole
from app.services.agent_jobs.dispatcher import InlineJobDispatcher
from app.services.agent_jobs.job_service import AgentJobService
from app.services.execution_profile_service import ProjectExecutionProfileService


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
    return profile


def _work_packet(project_id, *, task_type=WorkPacketTaskType.IMPLEMENT_STORY) -> WorkPacket:
    return WorkPacket(
        packet_id=uuid.uuid4(), task_type=task_type, project_id=project_id,
        objective=WorkObjective(goal="Implement the password reset endpoint.", success_definition="Endpoint exists and tests pass."),
        created_at=datetime.now(timezone.utc),
    )


def _install_fake_gateway(monkeypatch, gateway: FakeModelGateway) -> None:
    # handler.py imports select_gateway at module top-level (`from
    # app.model_gateway import ... select_gateway`), so the name to patch
    # is handler.py's OWN namespace, not app.model_gateway's — see
    # app/services/agent_jobs/handler.py's import block.
    import app.services.agent_jobs.handler as handler_module

    monkeypatch.setattr(handler_module, "select_gateway", lambda: gateway)


def test_handler_completes_a_job_end_to_end(db, project, actor, monkeypatch):
    _approve_execution_profile(db, project, actor)
    _install_fake_gateway(monkeypatch, FakeModelGateway(default_outcome=FakeCallOutcome(
        content_markdown='{"needs_clarification": false, "clarification_questions": []}\n---\nImplemented the endpoint.',
        prompt_tokens=200, completion_tokens=80, cost=Cost.of(0.01),
    )))

    job, _ = AgentJobService(db).create_job(work_packet=_work_packet(project.id), created_by=actor)
    db.commit()

    InlineJobDispatcher().submit(job_id=job.id, handler_name="default", db=db)

    assert job.status == AgentJobStatus.COMPLETED
    assert job.result["state"] == "COMPLETED"
    assert "Implemented the endpoint." in job.result["summary"]

    events = AgentJobService(db).list_events(job.id)
    event_types = [e.event_type.value for e in events]
    assert "PLAN_SUMMARY" in event_types
    assert "USAGE" in event_types
    assert "COMPLETED" in event_types


def test_handler_fails_permanently_when_no_execution_profile_exists(db, project, actor, monkeypatch):
    """No ProjectExecutionProfile approved for this project — Phase 03's
    own gate philosophy applies here too."""
    _install_fake_gateway(monkeypatch, FakeModelGateway(default_outcome=FakeCallOutcome()))

    job, _ = AgentJobService(db).create_job(work_packet=_work_packet(project.id), created_by=actor)
    db.commit()

    with pytest.raises(ValueError, match="ProjectExecutionProfile"):
        InlineJobDispatcher().submit(job_id=job.id, handler_name="default", db=db)

    assert job.status == AgentJobStatus.FAILED
    assert job.failure_category.value == "PERMANENT"


def test_handler_transitions_to_waiting_input_on_clarification(db, project, actor, monkeypatch):
    # FakeModelGateway is a fully scripted double, not a text parser — it
    # returns needs_clarification/clarification_questions exactly as
    # configured on the FakeCallOutcome, the same way LegacyModelGateway's
    # own already-parsed signal is trusted directly (see
    # app/services/ai_generation.py's _generate_via_gateway).
    _approve_execution_profile(db, project, actor)
    _install_fake_gateway(monkeypatch, FakeModelGateway(default_outcome=FakeCallOutcome(
        content_markdown="", needs_clarification=True, clarification_questions=["Which auth method?"],
    )))

    job, _ = AgentJobService(db).create_job(work_packet=_work_packet(project.id), created_by=actor)
    db.commit()

    InlineJobDispatcher().submit(job_id=job.id, handler_name="default", db=db)

    assert job.status == AgentJobStatus.WAITING_INPUT
    assert job.result is None


def test_handler_records_a_usage_event_with_unknown_cost_never_zero(db, project, actor, monkeypatch):
    _approve_execution_profile(db, project, actor)
    _install_fake_gateway(monkeypatch, FakeModelGateway(default_outcome=FakeCallOutcome(
        content_markdown='{}\n---\nok', cost=Cost.unknown(),
    )))

    job, _ = AgentJobService(db).create_job(work_packet=_work_packet(project.id), created_by=actor)
    db.commit()
    InlineJobDispatcher().submit(job_id=job.id, handler_name="default", db=db)

    events = AgentJobService(db).list_events(job.id)
    usage_events = [e for e in events if e.event_type.value == "USAGE"]
    assert len(usage_events) == 1
    assert usage_events[0].payload["cost_usd"] is None


def test_handler_emits_an_error_event_and_reraises_on_gateway_failure(db, project, actor, monkeypatch):
    from app.model_gateway.base import ModelGatewayError

    _approve_execution_profile(db, project, actor)
    _install_fake_gateway(monkeypatch, FakeModelGateway(default_outcome=FakeCallOutcome(raises=ModelGatewayError("provider down"))))

    job, _ = AgentJobService(db).create_job(work_packet=_work_packet(project.id), created_by=actor)
    db.commit()

    with pytest.raises(Exception):  # PolicyExhaustedError wraps every attempt's ModelGatewayError
        InlineJobDispatcher().submit(job_id=job.id, handler_name="default", db=db)

    events = AgentJobService(db).list_events(job.id)
    assert any(e.event_type.value == "ERROR" for e in events)
    assert job.status == AgentJobStatus.FAILED


def test_handler_respects_cooperative_cancellation_before_the_model_call(db, project, actor, monkeypatch):
    _approve_execution_profile(db, project, actor)
    _install_fake_gateway(monkeypatch, FakeModelGateway(default_outcome=FakeCallOutcome()))

    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id), created_by=actor)
    db.commit()

    # Simulate a cancellation request arriving before the job is dispatched.
    job.cancellation_requested = True
    db.commit()

    InlineJobDispatcher().submit(job_id=job.id, handler_name="default", db=db)
    assert job.status == AgentJobStatus.CANCELLED
