"""Tests for Implementation Agent execution:
  1. Every gate — LLD approved, Implementation Plan approved, GitHub repo
     snapshot exists, and area must be one of the four supported agents.
  3 + 5. Output shape persisted as ImplementationRun.
  6. Review (accept/reject) before anything could ever be "applied" —
     and the explicit rule that neither decision ever touches GitHub.

Follows test_implementation_planner.py's/test_github_integration.py's
conventions: plain functions, direct calls into the real route function
(no TestClient exists in this repo), the db/project/actor fixtures.
"""

import json
import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.implementation_runs import review_implementation_run, start_implementation_run
from app.models import (
    ArtifactStatus,
    AuditLog,
    ImplementationRun,
    ImplementationRunReviewStatus,
    ImplementationRunStatus,
    ImplementationTaskArea,
    ImplementationTaskStatus,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    Repository,
    RepositoryFileEntryType,
    RepositoryFileIndex,
    RepositorySnapshot,
    User,
    UserRole,
    WorkflowStatus,
)
from app.schemas.implementation_run import ReviewImplementationRunRequest, StartImplementationRunRequest
from app.services import implementation_agent
from tests.conftest import make_approved_artifact, make_implementation_task, make_node

SAMPLE_LLD = "## Password Reset Endpoint\n\nAdd a POST /auth/password-reset endpoint.\n"


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(implementation_agent, "get_active_provider", lambda: "mock")


def _developer(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(user)
    db.flush()
    return user


def _chain(db, project, actor):
    lld = make_node(db, project, node_key="lld", order_index=0, output_artifact_type="lld_document")
    story_crafting = make_node(db, project, node_key="story_crafting", order_index=1, output_artifact_type="story_backlog")
    planning = make_node(
        db, project, node_key="implementation_planning", order_index=2,
        required_inputs=["lld_document", "story_backlog"], output_artifact_type="implementation_plan",
    )
    implementation = make_node(
        db, project, node_key="implementation", order_index=3,
        required_inputs=["lld_document", "story_backlog", "implementation_plan"], output_artifact_type="code_change",
        requires_human_approval=False, status=WorkflowStatus.READY,
    )
    make_approved_artifact(db, project, lld, actor, content=SAMPLE_LLD)
    make_approved_artifact(db, project, story_crafting, actor, content="## Story: Password Reset Request\n\n**User Story:** As a user...\n")
    plan_artifact = make_approved_artifact(db, project, planning, actor, content="# Implementation Plan\n")
    task = make_implementation_task(
        db, project, planning, plan_artifact,
        linked_story="Password Reset Request", linked_lld_section="Password Reset Endpoint",
        expected_paths=["apps/api/app/api/routes/auth.py"],
    )
    return implementation, task


def _add_repository(db, project, *, with_snapshot=True):
    integration = Integration(integration_name="GitHub", provider=IntegrationProvider.GITHUB, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(
        integration=integration,
        access_token_encrypted="not-a-real-fernet-token",  # decrypt fails closed — see test_repo_context_builder.py
        token_last_four="7890", github_username="octocat", status=IntegrationStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    repository = Repository(project=project, connection=connection, owner="octocat", name="hello-world", default_branch="main")
    db.add(repository)
    db.flush()
    if not with_snapshot:
        return repository, None
    snapshot = RepositorySnapshot(repository=repository, ref="main", commit_sha="abc123", file_count=1, truncated=False)
    db.add(snapshot)
    db.flush()
    db.add(RepositoryFileIndex(snapshot=snapshot, path="apps/api/app/api/routes/auth.py", entry_type=RepositoryFileEntryType.FILE, size=200, sha="deadbeef"))
    db.flush()
    db.refresh(snapshot)
    return repository, snapshot


# --- Gates (requirement 1) ------------------------------------------------------------


def test_blocked_when_lld_not_approved(db, project, actor):
    lld = make_node(db, project, node_key="lld", order_index=0, output_artifact_type="lld_document")
    del lld  # deliberately not approved
    story_crafting = make_node(db, project, node_key="story_crafting", order_index=1, output_artifact_type="story_backlog")
    planning = make_node(db, project, node_key="implementation_planning", order_index=2, required_inputs=["lld_document", "story_backlog"], output_artifact_type="implementation_plan")
    plan_artifact = make_approved_artifact(db, project, planning, actor, content="Plan")
    implementation = make_node(
        db, project, node_key="implementation", order_index=3,
        required_inputs=["lld_document", "story_backlog", "implementation_plan"], output_artifact_type="code_change",
        requires_human_approval=False, status=WorkflowStatus.READY,
    )
    del implementation
    del story_crafting
    task = make_implementation_task(db, project, planning, plan_artifact)

    with pytest.raises(HTTPException) as exc_info:
        start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "lld_document" in exc_info.value.detail
    assert db.query(ImplementationRun).count() == 0


def test_blocked_when_implementation_plan_not_approved(db, project, actor):
    lld = make_node(db, project, node_key="lld", order_index=0, output_artifact_type="lld_document")
    story_crafting = make_node(db, project, node_key="story_crafting", order_index=1, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, lld, actor, content=SAMPLE_LLD)
    make_approved_artifact(db, project, story_crafting, actor, content="Stories.")
    planning = make_node(db, project, node_key="implementation_planning", order_index=2, required_inputs=["lld_document", "story_backlog"], output_artifact_type="implementation_plan")
    # An artifact exists but is left DRAFT (never approved) — the plan
    # artifact_id ImplementationTask itself references, distinct from
    # required_inputs' own "is there an APPROVED implementation_plan
    # anywhere in this project" check, which this leaves unsatisfied.
    plan_artifact = make_approved_artifact(db, project, planning, actor, content="Plan")
    plan_artifact.status = ArtifactStatus.DRAFT
    db.flush()
    make_node(
        db, project, node_key="implementation", order_index=3,
        required_inputs=["lld_document", "story_backlog", "implementation_plan"], output_artifact_type="code_change",
        requires_human_approval=False, status=WorkflowStatus.READY,
    )
    task = make_implementation_task(db, project, planning, plan_artifact)

    with pytest.raises(HTTPException) as exc_info:
        start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "implementation_plan" in exc_info.value.detail
    assert db.query(ImplementationRun).count() == 0


def test_blocked_when_no_repository_is_configured(db, project, actor):
    implementation, task = _chain(db, project, actor)
    del implementation

    with pytest.raises(HTTPException) as exc_info:
        start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "repository" in exc_info.value.detail.lower()
    assert db.query(ImplementationRun).count() == 0


def test_blocked_when_repository_has_no_snapshot(db, project, actor):
    implementation, task = _chain(db, project, actor)
    del implementation
    _add_repository(db, project, with_snapshot=False)

    with pytest.raises(HTTPException) as exc_info:
        start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "snapshot" in exc_info.value.detail.lower()
    assert db.query(ImplementationRun).count() == 0


def test_blocked_for_an_unsupported_area(db, project, actor):
    implementation, task = _chain(db, project, actor)
    del implementation
    _add_repository(db, project)
    task.area = ImplementationTaskArea.TESTING
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 400
    assert "TESTING" in exc_info.value.detail
    assert db.query(ImplementationRun).count() == 0


# --- Happy path (requirements 2, 3, 5) -------------------------------------------------


def test_successful_run_persists_expected_output_and_updates_task_status(db, project, actor):
    implementation, task = _chain(db, project, actor)
    del implementation
    _add_repository(db, project)
    dev = _developer(db)

    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=dev.id), db)

    assert run.status == ImplementationRunStatus.COMPLETED
    assert run.review_status == ImplementationRunReviewStatus.PENDING_REVIEW
    assert run.proposed_file_changes
    assert run.diff_text.strip()
    assert run.explanation.strip()
    assert run.test_command.strip()
    assert run.risks
    assert run.agent_type == task.assigned_agent_type
    db.refresh(task)
    assert task.status == ImplementationTaskStatus.IN_PROGRESS

    actions = [row.action for row in db.query(AuditLog).filter(AuditLog.entity_id == run.id).all()]
    assert "implementation_run.started" in actions
    assert "implementation_run.completed" in actions


def test_no_github_token_ever_appears_in_the_run_or_its_audit_log(db, project, actor):
    implementation, task = _chain(db, project, actor)
    del implementation
    _add_repository(db, project)

    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)

    dump = json.dumps(
        [run.diff_text, run.explanation, [row.extra_data for row in db.query(AuditLog).filter(AuditLog.entity_id == run.id).all()]]
    )
    assert "not-a-real-fernet-token" not in dump


# --- Review (requirement 6) -------------------------------------------------------------


def test_review_requires_a_completed_run(db, project, actor):
    implementation, task = _chain(db, project, actor)
    del implementation
    _add_repository(db, project)
    run = ImplementationRun(
        project_id=project.id, implementation_task_id=task.id, agent_type="backend-coding-agent",
        status=ImplementationRunStatus.RUNNING,
    )
    db.add(run)
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        review_implementation_run(run.id, ReviewImplementationRunRequest(decision="ACCEPTED", reviewed_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409


def test_accept_and_reject_record_the_decision_and_never_touch_github(db, project, actor):
    implementation, task = _chain(db, project, actor)
    del implementation
    _add_repository(db, project)
    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)

    reviewed = review_implementation_run(
        run.id, ReviewImplementationRunRequest(decision="ACCEPTED", reviewed_by_user_id=actor.id, comment="Looks good."), db
    )

    assert reviewed.review_status == ImplementationRunReviewStatus.ACCEPTED
    assert reviewed.reviewed_by_user_id == actor.id
    assert reviewed.review_comment == "Looks good."

    actions = [row.action for row in db.query(AuditLog).filter(AuditLog.entity_id == run.id).all()]
    assert "implementation_run.accepted" in actions


def test_reject_records_rejected_status(db, project, actor):
    implementation, task = _chain(db, project, actor)
    del implementation
    _add_repository(db, project)
    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)

    reviewed = review_implementation_run(
        run.id, ReviewImplementationRunRequest(decision="REJECTED", reviewed_by_user_id=actor.id, comment=None), db
    )

    assert reviewed.review_status == ImplementationRunReviewStatus.REJECTED
