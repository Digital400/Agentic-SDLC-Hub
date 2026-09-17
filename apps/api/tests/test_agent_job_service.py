"""Tests for app/services/agent_jobs/job_service.py — AgentJobService's
create/idempotency/state-transitions/heartbeat/stale/cancel/continuation
behavior.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.agent_runtime import WorkObjective, WorkPacket, WorkPacketTaskType
from app.models import AgentJobStatus, JobFailureCategory, User, UserRole
from app.services.agent_jobs.events import status_event
from app.services.agent_jobs.job_service import AgentJobError, AgentJobService


def _work_packet(project_id, **overrides) -> WorkPacket:
    defaults = dict(
        packet_id=uuid.uuid4(), task_type=WorkPacketTaskType.HLD, project_id=project_id,
        objective=WorkObjective(goal="Draft the HLD.", success_definition="A complete HLD exists."),
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return WorkPacket(**defaults)


# --- Creation / idempotency --------------------------------------------------------------


def test_create_job_persists_queued_status_and_a_status_event(db, project, actor):
    service = AgentJobService(db)
    job, created = service.create_job(work_packet=_work_packet(project.id), created_by=actor)
    db.flush()

    assert created is True
    assert job.status == AgentJobStatus.QUEUED
    assert job.project_id == project.id
    assert job.task_type == "HLD"
    events = service.list_events(job.id)
    assert len(events) == 1
    assert events[0].sequence == 1


def test_create_job_without_idempotency_key_always_creates_a_new_job(db, project, actor):
    service = AgentJobService(db)
    job1, created1 = service.create_job(work_packet=_work_packet(project.id))
    job2, created2 = service.create_job(work_packet=_work_packet(project.id))
    assert created1 is True and created2 is True
    assert job1.id != job2.id


def test_create_job_with_same_idempotency_key_returns_the_existing_job(db, project, actor):
    service = AgentJobService(db)
    job1, created1 = service.create_job(work_packet=_work_packet(project.id), idempotency_key="key-1")
    db.flush()
    job2, created2 = service.create_job(work_packet=_work_packet(project.id), idempotency_key="key-1")

    assert created1 is True
    assert created2 is False
    assert job1.id == job2.id


def test_different_idempotency_keys_create_different_jobs(db, project):
    service = AgentJobService(db)
    job1, _ = service.create_job(work_packet=_work_packet(project.id), idempotency_key="key-a")
    db.flush()
    job2, _ = service.create_job(work_packet=_work_packet(project.id), idempotency_key="key-b")
    assert job1.id != job2.id


def test_parse_work_packet_round_trips(db, project):
    service = AgentJobService(db)
    packet = _work_packet(project.id)
    job, _ = service.create_job(work_packet=packet)
    db.flush()

    parsed = service.parse_work_packet(job)
    assert parsed.packet_id == packet.packet_id
    assert parsed.task_type == packet.task_type


def test_get_or_404_raises_for_unknown_id(db):
    with pytest.raises(AgentJobError):
        AgentJobService(db).get_or_404(uuid.uuid4())


# --- State transitions -------------------------------------------------------------------


def test_full_happy_path_transition_sequence(db, project):
    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()

    service.mark_preparing(job, dispatcher_backend="inline")
    assert job.status == AgentJobStatus.PREPARING
    assert job.dispatcher_backend == "inline"
    assert job.heartbeat_at is not None

    service.mark_running(job)
    assert job.status == AgentJobStatus.RUNNING

    service.mark_completed(job, result={"summary": "done"})
    assert job.status == AgentJobStatus.COMPLETED
    assert job.result == {"summary": "done"}
    assert job.completed_at is not None


def test_mark_preparing_rejects_a_non_queued_job(db, project):
    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()
    service.mark_preparing(job, dispatcher_backend="inline")

    with pytest.raises(AgentJobError):
        service.mark_preparing(job, dispatcher_backend="inline")


def test_mark_waiting_input_from_running(db, project):
    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()
    service.mark_preparing(job, dispatcher_backend="inline")
    service.mark_running(job)

    service.mark_waiting_input(job)
    assert job.status == AgentJobStatus.WAITING_INPUT
    assert job.completed_at is not None


def test_mark_waiting_approval_from_running(db, project):
    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()
    service.mark_preparing(job, dispatcher_backend="inline")
    service.mark_running(job)

    service.mark_waiting_approval(job)
    assert job.status == AgentJobStatus.WAITING_APPROVAL


def test_mark_failed_records_category_and_message(db, project):
    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()
    service.mark_preparing(job, dispatcher_backend="inline")

    service.mark_failed(job, error_message="boom", failure_category=JobFailureCategory.TRANSIENT)
    assert job.status == AgentJobStatus.FAILED
    assert job.error_message == "boom"
    assert job.failure_category == JobFailureCategory.TRANSIENT


def test_cannot_transition_a_terminal_job(db, project):
    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()
    service.mark_preparing(job, dispatcher_backend="inline")
    service.mark_running(job)
    service.mark_completed(job, result={})

    with pytest.raises(AgentJobError):
        service.mark_running(job)


# --- Cancellation --------------------------------------------------------------------------


def _qa(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Canceller", role=UserRole.QA)
    db.add(user)
    db.flush()
    return user


def test_cancel_a_queued_job_is_immediate(db, project):
    service = AgentJobService(db)
    canceller = _qa(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()

    service.request_cancellation(job, requested_by=canceller)
    assert job.status == AgentJobStatus.CANCELLED
    assert job.cancellation_requested is True


def test_cancel_a_running_job_only_sets_the_flag_cooperatively(db, project):
    service = AgentJobService(db)
    canceller = _qa(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()
    service.mark_preparing(job, dispatcher_backend="inline")
    service.mark_running(job)

    service.request_cancellation(job, requested_by=canceller)
    assert job.status == AgentJobStatus.RUNNING  # not yet cancelled — the handler must notice the flag itself
    assert job.cancellation_requested is True

    service.mark_cancelled(job)
    assert job.status == AgentJobStatus.CANCELLED


def test_cannot_cancel_an_already_terminal_job(db, project):
    service = AgentJobService(db)
    canceller = _qa(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()
    service.mark_preparing(job, dispatcher_backend="inline")
    service.mark_running(job)
    service.mark_completed(job, result={})

    with pytest.raises(AgentJobError):
        service.request_cancellation(job, requested_by=canceller)


# --- Heartbeat / stale detection --------------------------------------------------------


def test_record_heartbeat_updates_timestamp(db, project):
    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()
    service.mark_preparing(job, dispatcher_backend="inline")
    original = job.heartbeat_at

    import time

    time.sleep(0.01)
    service.record_heartbeat(job)
    assert job.heartbeat_at > original


def test_record_heartbeat_rejects_a_non_running_job(db, project):
    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()

    with pytest.raises(AgentJobError):
        service.record_heartbeat(job)  # still QUEUED


def test_find_stale_jobs_detects_an_old_heartbeat(db, project):
    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()
    service.mark_preparing(job, dispatcher_backend="inline")
    job.heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=1000)
    db.flush()

    stale = service.find_stale_jobs(threshold_seconds=300)
    assert job.id in {j.id for j in stale}


def test_find_stale_jobs_ignores_a_recent_heartbeat(db, project):
    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()
    service.mark_preparing(job, dispatcher_backend="inline")

    stale = service.find_stale_jobs(threshold_seconds=300)
    assert job.id not in {j.id for j in stale}


def test_find_stale_jobs_ignores_a_queued_job_with_no_heartbeat_yet(db, project):
    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()  # still QUEUED, heartbeat_at is None

    stale = service.find_stale_jobs(threshold_seconds=0.001)
    assert job.id not in {j.id for j in stale}


def test_sweep_stale_jobs_transitions_them_to_stale(db, project):
    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()
    service.mark_preparing(job, dispatcher_backend="inline")
    job.heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=1000)
    db.flush()

    swept = service.sweep_stale_jobs(threshold_seconds=300)
    assert job.id in {j.id for j in swept}
    assert job.status == AgentJobStatus.STALE
    assert job.failure_category == JobFailureCategory.TRANSIENT


# --- Continuation --------------------------------------------------------------------------


def test_create_continuation_from_waiting_input(db, project, actor):
    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()
    service.mark_preparing(job, dispatcher_backend="inline")
    service.mark_running(job)
    service.mark_waiting_input(job)

    continuation, created = service.create_continuation(original_job=job, updated_work_packet=_work_packet(project.id), created_by=actor)
    assert created is True
    assert continuation.continuation_of_job_id == job.id
    assert continuation.status == AgentJobStatus.QUEUED
    assert continuation.id != job.id


def test_create_continuation_rejects_a_non_waiting_job(db, project):
    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()

    with pytest.raises(AgentJobError):
        service.create_continuation(original_job=job, updated_work_packet=_work_packet(project.id))  # still QUEUED


def test_continuation_respects_idempotency_key_too(db, project):
    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()
    service.mark_preparing(job, dispatcher_backend="inline")
    service.mark_running(job)
    service.mark_waiting_approval(job)

    c1, created1 = service.create_continuation(original_job=job, updated_work_packet=_work_packet(project.id), idempotency_key="resume-1")
    db.flush()
    c2, created2 = service.create_continuation(original_job=job, updated_work_packet=_work_packet(project.id), idempotency_key="resume-1")
    assert created1 is True
    assert created2 is False
    assert c1.id == c2.id


# --- Events --------------------------------------------------------------------------------


def test_record_event_assigns_monotonic_sequence(db, project):
    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))  # sequence 1 already recorded
    db.flush()

    e2 = service.record_event(job, status_event(status="X"))
    e3 = service.record_event(job, status_event(status="Y"))
    assert e2.sequence == 2
    assert e3.sequence == 3


def test_list_events_since_sequence_filters_correctly(db, project):
    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()
    service.record_event(job, status_event(status="A"))
    service.record_event(job, status_event(status="B"))

    events = service.list_events(job.id, since_sequence=1)
    assert [e.payload["status"] for e in events] == ["A", "B"]


def test_events_never_contain_a_chain_of_thought_field(db, project):
    from app.services.agent_jobs.events import NormalizedEvent
    from app.models.enums import AgentJobEventType

    service = AgentJobService(db)
    job, _ = service.create_job(work_packet=_work_packet(project.id))
    db.flush()
    event = service.record_event(job, NormalizedEvent(AgentJobEventType.STATUS, {"status": "RUNNING", "thinking": "secret"}))
    assert "thinking" not in event.payload
