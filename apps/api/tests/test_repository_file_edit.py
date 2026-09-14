"""Tests for the repository file edit endpoint
(app/api/routes/repository_file_edit.py's commit_file_edit) — the second
deliberate exception to github_integration.py's read-only contract.

Covers:
  1. RBAC: only roles allowed to edit "implementation" (Developer, Tech
     Lead, Admin) may commit a file edit; anyone else gets 403.
  2. It never writes to the repository's default branch — a caller-given
     branch_name equal to base_branch is refused outright.
  3. A normal commit creates the branch (tolerating "already exists"),
     writes the file with the correct existing sha (update, not a stray
     create), and opens a real pull request whose head/base match.
  4. open_pull_request=False skips PR creation entirely.
  5. A failed commit is audited and surfaced as an HTTP error; a commit
     that lands but whose PR creation fails is still audited and reports
     the branch it landed on (no silent data loss for a human to recover).
"""

import uuid

import pytest
from fastapi import HTTPException

import app.api.routes.repository_file_edit as routes_module
from app.api.routes.repository_file_edit import commit_file_edit
from app.models import (
    AuditLog,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    Repository,
    User,
    UserRole,
)
from app.schemas.repository_file_edit import CommitFileEditRequest
from app.services import github_integration as github_api

pytestmark = pytest.mark.usefixtures("db")


def _repository(db, project, *, default_branch="main"):
    integration = Integration(integration_name="GitHub", provider=IntegrationProvider.GITHUB, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(
        integration=integration, access_token_encrypted="not-a-real-fernet-token", token_last_four="7890",
        github_username="octocat", status=IntegrationStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    repository = Repository(
        project=project, connection=connection, owner="octocat", name="a-repo", default_branch=default_branch, is_primary=True,
    )
    db.add(repository)
    db.flush()
    return repository


def _user(db, *, role: UserRole) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Test User", role=role)
    db.add(user)
    db.flush()
    return user


def _request(path="backend/package.json", content='{"scripts": {"test": "echo ok"}}', **kw) -> CommitFileEditRequest:
    return CommitFileEditRequest(path=path, content=content, **kw)


def _patch_github(monkeypatch, *, create_branch_error=None, existing_sha="old-sha", pr_error=None):
    # Real decryption isn't the point of these tests (see
    # test_pull_request_creation.py's/test_story_code_implementation.py's
    # own identical pattern) — the fixture's access_token_encrypted below
    # is deliberately not a real Fernet token, so decrypt_repository_token
    # must be patched directly rather than relied on to succeed.
    monkeypatch.setattr(routes_module, "decrypt_repository_token", lambda repo: "fake-token")
    calls: dict[str, list] = {"create_branch": [], "get_file_sha": [], "create_or_update_file": [], "create_pull_request": []}

    def _create_branch(token, owner, repo, *, new_branch, base_ref, **kw):
        calls["create_branch"].append((new_branch, base_ref))
        if create_branch_error is not None:
            raise create_branch_error
        return "new-branch-sha"

    def _get_file_sha(token, owner, repo, path, ref, **kw):
        calls["get_file_sha"].append((path, ref))
        return existing_sha

    def _create_or_update_file(token, owner, repo, path, *, content, message, branch, sha=None, **kw):
        calls["create_or_update_file"].append((path, branch, sha))
        return "new-commit-sha"

    def _create_pull_request(token, owner, repo, *, title, head, base, body, **kw):
        calls["create_pull_request"].append((title, head, base))
        if pr_error is not None:
            raise pr_error

        class _PR:
            html_url = "https://github.com/octocat/a-repo/pull/1"

        return _PR()

    monkeypatch.setattr(github_api, "create_branch", _create_branch)
    monkeypatch.setattr(github_api, "get_file_sha", _get_file_sha)
    monkeypatch.setattr(github_api, "create_or_update_file", _create_or_update_file)
    monkeypatch.setattr(github_api, "create_pull_request", _create_pull_request)
    return calls


# --- RBAC ------------------------------------------------------------------------------


def test_developer_may_commit_a_file_edit(db, project, monkeypatch):
    repository = _repository(db, project)
    developer = _user(db, role=UserRole.DEVELOPER)
    _patch_github(monkeypatch)

    response = commit_file_edit(
        project.id, repository.id, _request(triggered_by_user_id=developer.id), db
    )
    assert response.commit_sha == "new-commit-sha"


def test_qa_may_not_commit_a_file_edit(db, project, monkeypatch):
    repository = _repository(db, project)
    qa = _user(db, role=UserRole.QA)
    _patch_github(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        commit_file_edit(project.id, repository.id, _request(triggered_by_user_id=qa.id), db)
    assert exc_info.value.status_code == 403


def test_admin_may_always_commit_a_file_edit(db, project, monkeypatch):
    repository = _repository(db, project)
    admin = _user(db, role=UserRole.ADMIN)
    _patch_github(monkeypatch)

    response = commit_file_edit(project.id, repository.id, _request(triggered_by_user_id=admin.id), db)
    assert response.commit_sha == "new-commit-sha"


# --- Never the default branch -----------------------------------------------------------


def test_refuses_to_use_the_default_branch_as_the_edit_branch(db, project, monkeypatch):
    repository = _repository(db, project, default_branch="main")
    developer = _user(db, role=UserRole.DEVELOPER)
    _patch_github(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        commit_file_edit(
            project.id, repository.id,
            _request(triggered_by_user_id=developer.id, branch_name="main"),
            db,
        )
    assert exc_info.value.status_code == 400
    assert "default branch" in exc_info.value.detail


# --- Normal commit + PR -----------------------------------------------------------------


def test_commits_the_edit_and_opens_a_pull_request(db, project, monkeypatch):
    repository = _repository(db, project, default_branch="main")
    developer = _user(db, role=UserRole.DEVELOPER)
    calls = _patch_github(monkeypatch)

    response = commit_file_edit(
        project.id, repository.id,
        _request(triggered_by_user_id=developer.id, branch_name="fix/test-script"),
        db,
    )

    assert response.branch_name == "fix/test-script"
    assert response.base_branch == "main"
    assert response.commit_sha == "new-commit-sha"
    assert response.pull_request_url == "https://github.com/octocat/a-repo/pull/1"

    assert calls["create_branch"] == [("fix/test-script", "main")]
    assert calls["get_file_sha"] == [("backend/package.json", "fix/test-script")]
    assert calls["create_or_update_file"] == [("backend/package.json", "fix/test-script", "old-sha")]  # a real update, not a blind create
    assert calls["create_pull_request"] == [("Fix: backend/package.json", "fix/test-script", "main")]

    audit = db.query(AuditLog).filter(AuditLog.action == "repository.file_committed").first()
    assert audit is not None
    assert audit.extra_data["pull_request_url"] == response.pull_request_url


def test_reuses_an_already_existing_branch_instead_of_failing(db, project, monkeypatch):
    repository = _repository(db, project)
    developer = _user(db, role=UserRole.DEVELOPER)
    already_exists = github_api.GitHubIntegrationError("Reference already exists", status_code=422)
    _patch_github(monkeypatch, create_branch_error=already_exists)

    response = commit_file_edit(
        project.id, repository.id,
        _request(triggered_by_user_id=developer.id, branch_name="fix/already-there"),
        db,
    )
    assert response.commit_sha == "new-commit-sha"  # the branch conflict never stopped the commit


def test_open_pull_request_false_skips_pr_creation(db, project, monkeypatch):
    repository = _repository(db, project)
    developer = _user(db, role=UserRole.DEVELOPER)
    calls = _patch_github(monkeypatch)

    response = commit_file_edit(
        project.id, repository.id,
        _request(triggered_by_user_id=developer.id, open_pull_request=False),
        db,
    )
    assert response.pull_request_url is None
    assert calls["create_pull_request"] == []


# --- Failure paths are audited -----------------------------------------------------------


def test_failed_commit_is_audited_and_surfaced_as_an_http_error(db, project, monkeypatch):
    repository = _repository(db, project)
    developer = _user(db, role=UserRole.DEVELOPER)

    def _failing_create_or_update_file(token, owner, repo, path, *, content, message, branch, sha=None, **kw):
        raise github_api.GitHubIntegrationError("boom", status_code=502)

    monkeypatch.setattr(routes_module, "decrypt_repository_token", lambda repo: "fake-token")
    monkeypatch.setattr(github_api, "create_branch", lambda *a, **kw: "sha")
    monkeypatch.setattr(github_api, "get_file_sha", lambda *a, **kw: None)
    monkeypatch.setattr(github_api, "create_or_update_file", _failing_create_or_update_file)

    with pytest.raises(HTTPException) as exc_info:
        commit_file_edit(project.id, repository.id, _request(triggered_by_user_id=developer.id), db)
    assert exc_info.value.status_code == 502

    audit = db.query(AuditLog).filter(AuditLog.action == "repository.file_commit_failed").first()
    assert audit is not None


def test_commit_that_lands_but_pr_creation_fails_still_reports_the_branch(db, project, monkeypatch):
    repository = _repository(db, project)
    developer = _user(db, role=UserRole.DEVELOPER)
    pr_error = github_api.GitHubIntegrationError("rate limited", status_code=429)
    _patch_github(monkeypatch, pr_error=pr_error)

    with pytest.raises(HTTPException) as exc_info:
        commit_file_edit(
            project.id, repository.id,
            _request(triggered_by_user_id=developer.id, branch_name="fix/pr-fails"),
            db,
        )
    assert exc_info.value.status_code == 502
    assert "fix/pr-fails" in exc_info.value.detail  # the branch name a human needs to find the orphaned commit

    audit = db.query(AuditLog).filter(AuditLog.action == "repository.file_commit_pr_failed").first()
    assert audit is not None
    assert audit.extra_data["commit_sha"] == "new-commit-sha"
