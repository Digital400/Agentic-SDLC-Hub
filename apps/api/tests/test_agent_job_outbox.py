"""Tests for app/services/agent_jobs/outbox.py — the write-ahead pattern
for external writes."""

import uuid
from datetime import datetime, timezone

import pytest

from app.agent_runtime import WorkObjective, WorkPacket, WorkPacketTaskType
from app.models import OutboxEntryStatus
from app.services.agent_jobs.job_service import AgentJobService
from app.services.agent_jobs.outbox import OutboxConflictError, OutboxService


def _make_job(db, project):
    packet = WorkPacket(
        packet_id=uuid.uuid4(), task_type=WorkPacketTaskType.IMPLEMENT_STORY, project_id=project.id,
        objective=WorkObjective(goal="g", success_definition="s"), created_at=datetime.now(timezone.utc),
    )
    job, _ = AgentJobService(db).create_job(work_packet=packet)
    db.flush()
    return job


def test_enqueue_creates_a_pending_entry(db, project):
    job = _make_job(db, project)
    entry = OutboxService(db).enqueue(job_id=job.id, idempotency_key=f"{job.id}:create_pr", external_target="github_pull_request", payload={"branch": "feature/x"})
    assert entry.status == OutboxEntryStatus.PENDING
    assert entry.attempt_count == 0


def test_enqueue_with_a_duplicate_idempotency_key_raises_conflict(db, project):
    job = _make_job(db, project)
    service = OutboxService(db)
    service.enqueue(job_id=job.id, idempotency_key="dup-key", external_target="github_pull_request", payload={})
    db.flush()

    with pytest.raises(OutboxConflictError):
        service.enqueue(job_id=job.id, idempotency_key="dup-key", external_target="github_pull_request", payload={})


def test_get_by_idempotency_key_finds_the_entry(db, project):
    job = _make_job(db, project)
    service = OutboxService(db)
    entry = service.enqueue(job_id=job.id, idempotency_key="findme", external_target="jira_issue", payload={})
    db.flush()

    found = service.get_by_idempotency_key("findme")
    assert found.id == entry.id


def test_list_pending_only_returns_pending_entries(db, project):
    job = _make_job(db, project)
    service = OutboxService(db)
    e1 = service.enqueue(job_id=job.id, idempotency_key="k1", external_target="github_pull_request", payload={})
    e2 = service.enqueue(job_id=job.id, idempotency_key="k2", external_target="github_pull_request", payload={})
    db.flush()
    service.mark_sent(e1)
    db.flush()

    pending = service.list_pending()
    assert {e.id for e in pending} == {e2.id}


def test_list_pending_filters_by_external_target(db, project):
    job = _make_job(db, project)
    service = OutboxService(db)
    service.enqueue(job_id=job.id, idempotency_key="k1", external_target="github_pull_request", payload={})
    service.enqueue(job_id=job.id, idempotency_key="k2", external_target="jira_issue", payload={})
    db.flush()

    pending = service.list_pending(external_target="jira_issue")
    assert len(pending) == 1
    assert pending[0].external_target == "jira_issue"


def test_mark_sent_sets_status_and_timestamp(db, project):
    job = _make_job(db, project)
    service = OutboxService(db)
    entry = service.enqueue(job_id=job.id, idempotency_key="k1", external_target="github_pull_request", payload={})
    db.flush()

    service.mark_sent(entry)
    assert entry.status == OutboxEntryStatus.SENT
    assert entry.sent_at is not None
    assert entry.attempt_count == 1


def test_mark_failed_stays_pending_for_retry(db, project):
    job = _make_job(db, project)
    service = OutboxService(db)
    entry = service.enqueue(job_id=job.id, idempotency_key="k1", external_target="github_pull_request", payload={})
    db.flush()

    service.mark_failed(entry, error="rate limited")
    assert entry.status == OutboxEntryStatus.PENDING
    assert entry.attempt_count == 1
    assert entry.last_error == "rate limited"
    assert entry in service.list_pending()


def test_mark_permanently_failed_removes_it_from_pending(db, project):
    job = _make_job(db, project)
    service = OutboxService(db)
    entry = service.enqueue(job_id=job.id, idempotency_key="k1", external_target="github_pull_request", payload={})
    db.flush()

    service.mark_permanently_failed(entry, error="invalid repository")
    db.flush()
    assert entry.status == OutboxEntryStatus.FAILED
    assert entry not in service.list_pending()
