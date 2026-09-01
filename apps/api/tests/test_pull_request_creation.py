"""Tests for GitHub branch/PR creation from an accepted Implementation Run
(app/api/routes/implementation_runs.py's create_pull_request):
  1. User must approve (ACCEPT) the patch before PR creation.
  4/Never push to main — create_or_update_file/delete_file are only ever
     called with the new feature branch, never repository.default_branch.
  5. PR links back to project/workflow node/task/run.
  6. PullRequestLink stores repo/branch/PR number/PR URL/status/createdByAgent.
  7. PR title includes the derived project key and the task title.

Follows test_implementation_runs.py's exact setup helpers and conventions
(mocked GitHub via httpx.MockTransport, no TestClient, direct route calls).
"""

import json
import uuid

import httpx
import pytest
from fastapi import HTTPException

from app.api.routes.implementation_runs import create_pull_request, review_implementation_run, start_implementation_run
from app.models import (
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
    User,
    UserRole,
    WorkflowStatus,
)
from app.schemas.implementation_run import CreatePullRequestRequest, ReviewImplementationRunRequest, StartImplementationRunRequest
from app.services import github_integration as github_api
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
    make_node(
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
    return task


def _add_repository(db, project):
    integration = Integration(integration_name="GitHub", provider=IntegrationProvider.GITHUB, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(
        integration=integration,
        # A genuinely valid-looking Fernet ciphertext isn't needed here —
        # decrypt_repository_token is monkeypatched in the PR-creation
        # tests below (a real write cannot silently degrade to "no
        # token" the way the preview/run-generation paths do).
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
    db.add(RepositoryFileIndex(snapshot=snapshot, path="apps/api/app/api/routes/auth.py", entry_type=RepositoryFileEntryType.FILE, size=200, sha="deadbeef"))
    db.flush()
    db.refresh(snapshot)
    return repository


def _accepted_run(db, project, actor):
    task = _chain(db, project, actor)
    _add_repository(db, project)
    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    run = review_implementation_run(run.id, ReviewImplementationRunRequest(decision="ACCEPTED", reviewed_by_user_id=actor.id), db)
    return task, run


def _mock_github(monkeypatch, *, record_calls: list | None = None):
    """Patches decrypt_repository_token (real decryption isn't the point
    of these tests) and every github_api write call with a deterministic,
    call-recording fake — no real network, ever."""
    calls = record_calls if record_calls is not None else []

    import app.api.routes.implementation_runs as routes_module

    monkeypatch.setattr(routes_module, "decrypt_repository_token", lambda repo: "fake-token")

    def _create_branch(token, owner, repo, *, new_branch, base_ref, **kwargs):
        calls.append(("create_branch", new_branch, base_ref))
        return "base-sha"

    def _get_file_sha(token, owner, repo, path, ref, **kwargs):
        calls.append(("get_file_sha", path, ref))
        return "old-sha" if path == "apps/api/app/api/routes/auth.py" else None

    def _create_or_update_file(token, owner, repo, path, *, content, message, branch, sha=None, **kwargs):
        calls.append(("create_or_update_file", path, branch, sha))
        return "commit-sha"

    def _delete_file(token, owner, repo, path, *, message, branch, sha, **kwargs):
        calls.append(("delete_file", path, branch, sha))
        return "commit-sha"

    def _create_pull_request(token, owner, repo, *, title, head, base, body, **kwargs):
        calls.append(("create_pull_request", title, head, base))
        return github_api.GitHubPullRequest(number=7, html_url="https://github.com/octocat/hello-world/pull/7", state="open")

    monkeypatch.setattr(routes_module.github_api, "create_branch", _create_branch)
    monkeypatch.setattr(routes_module.github_api, "get_file_sha", _get_file_sha)
    monkeypatch.setattr(routes_module.github_api, "create_or_update_file", _create_or_update_file)
    monkeypatch.setattr(routes_module.github_api, "delete_file", _delete_file)
    monkeypatch.setattr(routes_module.github_api, "create_pull_request", _create_pull_request)
    return calls


# --- Gating (rule: approve before PR creation) -----------------------------------------


def test_blocked_when_run_is_not_accepted(db, project, actor, monkeypatch):
    task = _chain(db, project, actor)
    _add_repository(db, project)
    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    _mock_github(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        create_pull_request(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert db.query(PullRequestLink).count() == 0


def test_blocked_when_run_is_rejected(db, project, actor, monkeypatch):
    task = _chain(db, project, actor)
    _add_repository(db, project)
    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    run = review_implementation_run(run.id, ReviewImplementationRunRequest(decision="REJECTED", reviewed_by_user_id=actor.id), db)
    _mock_github(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        create_pull_request(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert db.query(PullRequestLink).count() == 0


def test_blocked_when_a_pr_already_exists_for_the_run(db, project, actor, monkeypatch):
    task, run = _accepted_run(db, project, actor)
    _mock_github(monkeypatch)
    create_pull_request(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)

    with pytest.raises(HTTPException) as exc_info:
        create_pull_request(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert db.query(PullRequestLink).count() == 1


# --- Happy path (requirements 2, 3, 5, 6, 7) -------------------------------------------


def test_successful_pr_creation_persists_full_link_metadata(db, project, actor, monkeypatch):
    task, run = _accepted_run(db, project, actor)
    calls = _mock_github(monkeypatch)

    result = create_pull_request(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)

    link = db.query(PullRequestLink).filter(PullRequestLink.implementation_run_id == run.id).first()
    assert link is not None
    assert link.project_id == project.id
    assert link.implementation_task_id == task.id
    assert link.implementation_run_id == run.id
    assert link.repository_id is not None
    assert link.base_branch == "main"
    assert link.branch_name.startswith("agent/")
    assert link.pr_number == 7
    assert link.pr_url == "https://github.com/octocat/hello-world/pull/7"
    assert link.status == PullRequestStatus.OPEN
    assert link.created_by_agent is True
    assert link.triggered_by_user_id == actor.id
    assert result.pull_request is not None
    assert result.pull_request.pr_number == 7

    # requirement 7 — PR title includes the derived project key and the task title
    pr_calls = [c for c in calls if c[0] == "create_pull_request"]
    assert len(pr_calls) == 1
    _, title, head, base = pr_calls[0]
    assert task.title in title
    assert title.startswith("[")
    assert head == link.branch_name
    assert base == "main"


def test_never_writes_to_the_default_branch(db, project, actor, monkeypatch):
    task, run = _accepted_run(db, project, actor)
    calls = _mock_github(monkeypatch)

    create_pull_request(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)

    write_calls = [c for c in calls if c[0] in ("create_or_update_file", "delete_file")]
    assert write_calls  # at least one file was written
    for call in write_calls:
        branch = call[2]
        assert branch != "main"
        assert branch.startswith("agent/")


def test_pr_creation_requires_a_configured_repository(db, project, actor, monkeypatch):
    lld = make_node(db, project, node_key="lld", order_index=0, output_artifact_type="lld_document")
    story_crafting = make_node(db, project, node_key="story_crafting", order_index=1, output_artifact_type="story_backlog")
    make_node(
        db, project, node_key="implementation", order_index=3,
        required_inputs=["lld_document", "story_backlog", "implementation_plan"], output_artifact_type="code_change",
        requires_human_approval=False, status=WorkflowStatus.READY,
    )
    planning = make_node(
        db, project, node_key="implementation_planning", order_index=2,
        required_inputs=["lld_document", "story_backlog"], output_artifact_type="implementation_plan",
    )
    make_approved_artifact(db, project, lld, actor, content=SAMPLE_LLD)
    make_approved_artifact(db, project, story_crafting, actor, content="Stories.")
    plan_artifact = make_approved_artifact(db, project, planning, actor, content="Plan")
    task = make_implementation_task(db, project, planning, plan_artifact)

    from app.models import ImplementationRun, ImplementationRunReviewStatus, ImplementationRunStatus

    run = ImplementationRun(
        project_id=project.id, implementation_task_id=task.id, agent_type="backend-coding-agent",
        status=ImplementationRunStatus.COMPLETED, review_status=ImplementationRunReviewStatus.ACCEPTED,
    )
    db.add(run)
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        create_pull_request(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409


# --- Security regression ---------------------------------------------------------------


def test_token_never_appears_anywhere_after_pr_creation(db, project, actor, monkeypatch):
    task, run = _accepted_run(db, project, actor)
    _mock_github(monkeypatch)

    result = create_pull_request(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)

    dump = json.dumps(
        [
            result.model_dump(mode="json"),
            [row.extra_data for row in db.query(AuditLog).all()],
        ]
    )
    assert "fake-token" not in dump
    assert "not-a-real-fernet-token" not in dump
