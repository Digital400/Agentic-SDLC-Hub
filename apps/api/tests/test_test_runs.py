"""Tests for the Testing Agent system:
  1. Gate — testing can start only once an implementation run is ACCEPTED
     and either a PR or a non-empty diff exists.
  3/4. Output persisted as TestRun.
  5/6. A real test_report Artifact is created and opens a QA Review
     through the existing generic mechanism — approvable via the
     unmodified approve_review route function.

Follows test_pull_request_creation.py's exact setup helpers and
conventions (no TestClient, direct route calls, mocked GitHub).
"""

import json
import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.implementation_runs import review_implementation_run, start_implementation_run
from app.api.routes.reviews import approve_review
from app.api.routes.test_runs import start_test_run
from app.models import (
    ArtifactStatus,
    AuditLog,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    PullRequestLink,
    PullRequestStatus,
    Repository,
    RepositoryFileEntryType,
    RepositoryFileIndex,
    RepositorySnapshot,
    Review,
    ReviewStatus,
    TestAgentType,
    TestRun,
    TestRunStatus,
    User,
    UserRole,
    WorkflowStatus,
)
from app.schemas.implementation_run import ReviewImplementationRunRequest, StartImplementationRunRequest
from app.schemas.review import ReviewApproveRequest
from app.schemas.test_run import StartTestRunRequest
from app.services import artifact_summary, implementation_agent, testing_agent
from tests.conftest import make_approved_artifact, make_implementation_task, make_node

SAMPLE_LLD = "## Password Reset Endpoint\n\nAdd a POST /auth/password-reset endpoint.\n"


@pytest.fixture(autouse=True)
def _force_mock_providers(monkeypatch):
    monkeypatch.setattr(implementation_agent, "get_active_provider", lambda: "mock")
    monkeypatch.setattr(testing_agent, "get_active_provider", lambda: "mock")
    # approve_review calls apply_summaries_to_version, which has its own
    # get_active_provider reference — see test_lld_workflow.py's identical
    # fixture for why this needs its own patch, not just the two above.
    monkeypatch.setattr(artifact_summary, "get_active_provider", lambda: "mock")


def _qa(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="QA", role=UserRole.QA)
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
    make_node(
        db, project, node_key="implementation", order_index=3,
        required_inputs=["lld_document", "story_backlog", "implementation_plan"], output_artifact_type="code_change",
        requires_human_approval=False, status=WorkflowStatus.READY,
    )
    make_node(
        db, project, node_key="testing", order_index=4, output_artifact_type="test_report",
        required_evidence_section="Test Evidence",
    )
    make_approved_artifact(db, project, lld, actor, content=SAMPLE_LLD)
    make_approved_artifact(db, project, story_crafting, actor, content="## Story: Password Reset Request\n\n**User Story:** As a user...\n")
    plan_artifact = make_approved_artifact(db, project, planning, actor, content="# Implementation Plan\n")
    task = make_implementation_task(
        db, project, planning, plan_artifact,
        linked_story="Password Reset Request", linked_lld_section="Password Reset Endpoint",
        expected_paths=["apps/api/app/api/routes/auth.py"],
        acceptance_criteria=["Returns 202 for a valid email", "Returns 404 for an unknown email"],
    )
    return task


def _add_repository(db, project):
    integration = Integration(integration_name="GitHub", provider=IntegrationProvider.GITHUB, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(
        integration=integration,
        access_token_encrypted="not-a-real-fernet-token", token_last_four="7890", github_username="octocat",
        status=IntegrationStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    repository = Repository(project=project, connection=connection, owner="octocat", name="hello-world", default_branch="main")
    db.add(repository)
    db.flush()
    snapshot = RepositorySnapshot(repository=repository, ref="main", commit_sha="abc123", file_count=1, truncated=False)
    db.add(snapshot)
    db.flush()
    db.add(RepositoryFileIndex(snapshot=snapshot, path="apps/api/tests/test_auth.py", entry_type=RepositoryFileEntryType.FILE, size=200, sha="deadbeef"))
    db.flush()
    db.refresh(snapshot)
    return repository, snapshot


def _accepted_run(db, project, actor, *, with_repository=True):
    task = _chain(db, project, actor)
    if with_repository:
        _add_repository(db, project)
    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    run = review_implementation_run(run.id, ReviewImplementationRunRequest(decision="ACCEPTED", reviewed_by_user_id=actor.id), db)
    return task, run


def _add_pr_link(db, project, run):
    repository = db.query(Repository).filter(Repository.project_id == project.id).first()
    link = PullRequestLink(
        project_id=project.id, workflow_node_id=run.implementation_task_id,  # placeholder — overwritten below
        implementation_task_id=run.implementation_task_id, implementation_run_id=run.id, repository_id=repository.id,
        branch_name="agent/x", base_branch="main", pr_number=1, pr_url="https://github.com/octocat/hello-world/pull/1",
        status=PullRequestStatus.OPEN, created_by_agent=True, commit_message="[ASH] Test PR",
    )
    # workflow_node_id must be a real WorkflowNode id — use the project's implementation node.
    from app.models import WorkflowNode

    impl_node = db.query(WorkflowNode).filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == "implementation").first()
    link.workflow_node_id = impl_node.id
    db.add(link)
    db.flush()
    return link


# --- Gate (requirement 1) ---------------------------------------------------------------


def test_blocked_when_no_accepted_implementation_run_exists(db, project, actor):
    task = _chain(db, project, actor)
    _add_repository(db, project)
    qa = _qa(db)

    with pytest.raises(HTTPException) as exc_info:
        start_test_run(StartTestRunRequest(implementation_task_id=task.id, agent_type=TestAgentType.UNIT, triggered_by_user_id=qa.id, reviewer_id=qa.id), db)
    assert exc_info.value.status_code == 409
    assert db.query(TestRun).count() == 0


def test_blocked_when_accepted_run_has_no_pr_and_an_empty_diff(db, project, actor, monkeypatch):
    from app.models import ImplementationRun

    task, run = _accepted_run(db, project, actor)
    # `run` is the Pydantic ImplementationRunRead the route returned —
    # mutate the real ORM row instead, which is what the route re-queries.
    run_row = db.get(ImplementationRun, run.id)
    run_row.diff_text = ""
    db.flush()
    qa = _qa(db)

    with pytest.raises(HTTPException) as exc_info:
        start_test_run(StartTestRunRequest(implementation_task_id=task.id, agent_type=TestAgentType.UNIT, triggered_by_user_id=qa.id, reviewer_id=qa.id), db)
    assert exc_info.value.status_code == 409
    assert db.query(TestRun).count() == 0


def test_succeeds_with_only_a_diff_and_no_pr(db, project, actor):
    task, run = _accepted_run(db, project, actor)
    assert run.diff_text.strip()  # the implementation agent always produces one
    qa = _qa(db)

    result = start_test_run(StartTestRunRequest(implementation_task_id=task.id, agent_type=TestAgentType.UNIT, triggered_by_user_id=qa.id, reviewer_id=qa.id), db)

    assert result.status == TestRunStatus.COMPLETED
    assert result.pull_request_link_id is None


def test_succeeds_with_a_pr_and_links_it(db, project, actor):
    task, run = _accepted_run(db, project, actor)
    link = _add_pr_link(db, project, run)
    qa = _qa(db)

    result = start_test_run(StartTestRunRequest(implementation_task_id=task.id, agent_type=TestAgentType.API, triggered_by_user_id=qa.id, reviewer_id=qa.id), db)

    assert result.pull_request_link_id == link.id


# --- Happy path (requirements 2, 3, 4, 5, 6) --------------------------------------------


def test_successful_run_persists_output_and_opens_a_qa_review(db, project, actor):
    task, run = _accepted_run(db, project, actor)
    qa = _qa(db)

    result = start_test_run(StartTestRunRequest(implementation_task_id=task.id, agent_type=TestAgentType.REGRESSION, triggered_by_user_id=qa.id, reviewer_id=qa.id), db)

    assert result.status == TestRunStatus.COMPLETED
    assert result.test_plan.strip()
    assert result.tests_to_add
    assert result.coverage_impact  # placeholder dict, always present
    assert result.review_id is not None

    test_run_row = db.get(TestRun, result.id)
    artifact = test_run_row.artifact
    assert artifact is not None
    assert artifact.artifact_type == "test_report"
    assert artifact.status == ArtifactStatus.READY_FOR_REVIEW

    review = db.get(Review, result.review_id)
    assert review is not None
    assert review.reviewer_id == qa.id
    assert review.status == ReviewStatus.PENDING

    actions = [row.action for row in db.query(AuditLog).filter(AuditLog.entity_id == test_run_row.id).all()]
    assert "test_run.started" in actions
    assert "test_run.completed" in actions


def test_the_opened_review_is_qa_approvable_through_the_unmodified_generic_endpoint(db, project, actor):
    task, run = _accepted_run(db, project, actor)
    qa = _qa(db)

    result = start_test_run(StartTestRunRequest(implementation_task_id=task.id, agent_type=TestAgentType.UNIT, triggered_by_user_id=qa.id, reviewer_id=qa.id), db)

    approved = approve_review(result.review_id, ReviewApproveRequest(comment="Looks good."), db)

    assert approved.status == "APPROVED"
    artifact = db.get(TestRun, result.id).artifact
    assert artifact.status == ArtifactStatus.APPROVED


def test_agent_never_sets_approved_status_itself(db, project, actor):
    task, run = _accepted_run(db, project, actor)
    qa = _qa(db)

    result = start_test_run(StartTestRunRequest(implementation_task_id=task.id, agent_type=TestAgentType.UI, triggered_by_user_id=qa.id, reviewer_id=qa.id), db)

    # Rule: "should not silently approve its own result" — right after
    # generation, nothing is APPROVED yet; only a human's approve_review
    # call (exercised in the test above) can make that transition.
    artifact = db.get(TestRun, result.id).artifact
    assert artifact.status == ArtifactStatus.READY_FOR_REVIEW
    review = db.get(Review, result.review_id)
    assert review.status == ReviewStatus.PENDING


# --- Security regression ---------------------------------------------------------------


def test_no_github_token_ever_appears_in_the_test_run_or_its_audit_log(db, project, actor):
    task, run = _accepted_run(db, project, actor)
    qa = _qa(db)

    result = start_test_run(StartTestRunRequest(implementation_task_id=task.id, agent_type=TestAgentType.SECURITY, triggered_by_user_id=qa.id, reviewer_id=qa.id), db)

    dump = json.dumps(
        [
            result.model_dump(mode="json"),
            [row.extra_data for row in db.query(AuditLog).all()],
        ]
    )
    assert "not-a-real-fernet-token" not in dump
