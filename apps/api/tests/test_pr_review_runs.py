"""Tests for the PR Review Agent:
  1. Gate — a PR review can start only once an implementation run is
     ACCEPTED, a PullRequestLink exists (stricter than Testing's
     either/or), and a diff is present.
  3/4/5. Output persisted as PRReviewRun.
  7/"comments must be editable" — post-comments takes the final text the
     client sends, not whatever's stored in suggested_comments.
  Rule: no merge call exists anywhere; a bad token hard-fails posting
  (unlike run generation, which degrades gracefully for reads).

Follows test_pull_request_creation.py's/test_test_runs.py's exact setup
helpers and conventions (no TestClient, direct route calls, mocked GitHub).
"""

import json
import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.implementation_runs import review_implementation_run, start_implementation_run
from app.api.routes.pr_review_runs import post_pr_review_comments, start_pr_review_run
from app.models import (
    AuditLog,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    PRReviewRun,
    PRReviewRunStatus,
    PullRequestLink,
    PullRequestStatus,
    Repository,
    RepositoryFileEntryType,
    RepositoryFileIndex,
    RepositorySnapshot,
    User,
    UserRole,
    WorkflowNode,
    WorkflowStatus,
)
from app.schemas.implementation_run import ReviewImplementationRunRequest, StartImplementationRunRequest
from app.schemas.pr_review_run import PostCommentRequestItem, PostPRReviewCommentsRequest, StartPRReviewRunRequest
from app.services import implementation_agent, pr_review_agent
from tests.conftest import make_approved_artifact, make_implementation_task, make_node

SAMPLE_LLD = "## Password Reset Endpoint\n\nAdd a POST /auth/password-reset endpoint.\n"


@pytest.fixture(autouse=True)
def _force_mock_providers(monkeypatch):
    monkeypatch.setattr(implementation_agent, "get_active_provider", lambda: "mock")
    monkeypatch.setattr(pr_review_agent, "get_active_provider", lambda: "mock")


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
    make_node(
        db, project, node_key="implementation", order_index=3,
        required_inputs=["lld_document", "story_backlog", "implementation_plan"], output_artifact_type="code_change",
        requires_human_approval=False, status=WorkflowStatus.READY,
    )
    make_node(db, project, node_key="pr_review", order_index=4, output_artifact_type="pr_review_report")
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
    return repository


def _accepted_run(db, project, actor):
    task = _chain(db, project, actor)
    _add_repository(db, project)
    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    run = review_implementation_run(run.id, ReviewImplementationRunRequest(decision="ACCEPTED", reviewed_by_user_id=actor.id), db)
    return task, run


def _add_pr_link(db, project, run):
    repository = db.query(Repository).filter(Repository.project_id == project.id).first()
    impl_node = db.query(WorkflowNode).filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == "implementation").first()
    link = PullRequestLink(
        project_id=project.id, workflow_node_id=impl_node.id, implementation_task_id=run.implementation_task_id,
        implementation_run_id=run.id, repository_id=repository.id, branch_name="agent/x", base_branch="main",
        pr_number=1, pr_url="https://github.com/octocat/hello-world/pull/1", status=PullRequestStatus.OPEN,
        created_by_agent=True, commit_message="[ASH] Test PR",
    )
    db.add(link)
    db.flush()
    return link


def _mock_github_reads_and_writes(monkeypatch, *, pr_title="Add password reset", pr_body="Implements the flow."):
    import app.api.routes.pr_review_runs as routes_module

    monkeypatch.setattr(routes_module, "decrypt_repository_token", lambda repo: "fake-token")

    class _PR:
        title = pr_title
        body = pr_body

    posted = []

    def _get_pull_request(token, owner, repo, pr_number, **kwargs):
        return _PR()

    def _create_issue_comment(token, owner, repo, pr_number, *, body, **kwargs):
        from app.services.github_integration import GitHubComment

        posted.append(body)
        return GitHubComment(id=len(posted), html_url=f"https://github.com/octocat/hello-world/pull/1#issuecomment-{len(posted)}", body=body)

    monkeypatch.setattr(routes_module.github_api, "get_pull_request", _get_pull_request)
    monkeypatch.setattr(routes_module.github_api, "create_issue_comment", _create_issue_comment)
    return posted


# --- Gating (requirement 1) --------------------------------------------------------------


def test_blocked_when_no_accepted_implementation_run_exists(db, project, actor, monkeypatch):
    task = _chain(db, project, actor)
    _add_repository(db, project)
    _mock_github_reads_and_writes(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        start_pr_review_run(StartPRReviewRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert db.query(PRReviewRun).count() == 0


def test_blocked_when_accepted_run_has_no_pr(db, project, actor, monkeypatch):
    task, run = _accepted_run(db, project, actor)
    _mock_github_reads_and_writes(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        start_pr_review_run(StartPRReviewRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "pull request" in exc_info.value.detail.lower()
    assert db.query(PRReviewRun).count() == 0


# --- Happy path (requirements 2, 3, 4, 5) ------------------------------------------------


def test_successful_run_persists_full_output(db, project, actor, monkeypatch):
    task, run = _accepted_run(db, project, actor)
    _add_pr_link(db, project, run)
    _mock_github_reads_and_writes(monkeypatch)

    result = start_pr_review_run(StartPRReviewRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)

    assert result.status == PRReviewRunStatus.COMPLETED
    assert result.overall_recommendation is not None
    assert result.overall_recommendation.value != "APPROVE"  # heuristic never approves
    assert result.summary.strip()
    assert result.missing_tests
    assert result.risk_score is not None
    assert result.final_reviewer_note.strip()

    actions = [row.action for row in db.query(AuditLog).filter(AuditLog.entity_id == result.id).all()]
    assert "pr_review_run.started" in actions
    assert "pr_review_run.completed" in actions


def test_no_pr_review_node_means_400_not_a_crash(db, project, actor, monkeypatch):
    # A project without a pr_review node at all — resolved before the gate.
    from app.models import ImplementationTask, Project, ProjectStatus

    other_project = Project(
        name="Other", business_owner="Owner", workflow_template_id="sdlc-workflow",
        workflow_template_version="test", current_stage="node_a", status=ProjectStatus.ACTIVE, created_by_id=actor.id,
    )
    db.add(other_project)
    db.flush()
    node = make_node(db, other_project, node_key="implementation_planning", order_index=0, output_artifact_type="implementation_plan")
    artifact = make_approved_artifact(db, other_project, node, actor)
    task = make_implementation_task(db, other_project, node, artifact)

    with pytest.raises(HTTPException) as exc_info:
        start_pr_review_run(StartPRReviewRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 400


# --- Posting comments (requirements 6, 7) -------------------------------------------------


def test_post_comments_requires_a_completed_run(db, project, actor, monkeypatch):
    task, run = _accepted_run(db, project, actor)
    pr_link = _add_pr_link(db, project, run)
    _mock_github_reads_and_writes(monkeypatch)
    pr_review_run = PRReviewRun(
        project_id=project.id,
        workflow_node_id=db.query(WorkflowNode).filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == "pr_review").first().id,
        implementation_task_id=task.id, implementation_run_id=run.id, pull_request_link_id=pr_link.id,
        status=PRReviewRunStatus.RUNNING,
    )
    db.add(pr_review_run)
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        post_pr_review_comments(
            pr_review_run.id, PostPRReviewCommentsRequest(triggered_by_user_id=actor.id, comments=[PostCommentRequestItem(file="a.py", body="x")]), db,
        )
    assert exc_info.value.status_code == 409


def test_post_comments_sends_the_clients_final_edited_text_and_records_it(db, project, actor, monkeypatch):
    task, run = _accepted_run(db, project, actor)
    _add_pr_link(db, project, run)
    posted = _mock_github_reads_and_writes(monkeypatch)
    result = start_pr_review_run(StartPRReviewRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)

    response = post_pr_review_comments(
        result.id,
        PostPRReviewCommentsRequest(
            triggered_by_user_id=actor.id,
            comments=[PostCommentRequestItem(file="apps/api/app/api/routes/auth.py", body="EDITED: please add a docstring here.")],
        ),
        db,
    )

    assert posted == ["EDITED: please add a docstring here."]
    assert response.results[0].status == "posted"
    assert response.run.posted_comments[0].body == "EDITED: please add a docstring here."

    actions = [row.action for row in db.query(AuditLog).filter(AuditLog.entity_id == result.id).all()]
    assert "pr_review_run.comments_posted" in actions


def test_post_comments_hard_fails_on_undecryptable_token(db, project, actor):
    task, run = _accepted_run(db, project, actor)
    _add_pr_link(db, project, run)
    result = start_pr_review_run(StartPRReviewRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)

    with pytest.raises(HTTPException) as exc_info:
        post_pr_review_comments(
            result.id, PostPRReviewCommentsRequest(triggered_by_user_id=actor.id, comments=[PostCommentRequestItem(file="a.py", body="x")]), db,
        )
    assert exc_info.value.status_code == 409


# --- Security regression -----------------------------------------------------------------


def test_no_github_token_ever_appears_in_run_or_audit_log(db, project, actor, monkeypatch):
    task, run = _accepted_run(db, project, actor)
    _add_pr_link(db, project, run)
    _mock_github_reads_and_writes(monkeypatch)

    result = start_pr_review_run(StartPRReviewRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)

    dump = json.dumps(
        [result.model_dump(mode="json"), [row.extra_data for row in db.query(AuditLog).all()]]
    )
    assert "not-a-real-fernet-token" not in dump
    assert "fake-token" not in dump
