"""Tests for RepoBootstrapService (app/services/repo_bootstrap.py) and its
one endpoint (app/api/routes/repo_bootstrap.py's bootstrap_repository):
  1. generate_scaffold_files is pure/deterministic — known stacks get real
     boilerplate, unknown stacks get the generic fallback, approved
     HLD/Solution Discovery content is copied into docs/ when present.
  2. push_scaffold writes every file directly to the repository's default
     branch (the one deliberate exception to github_integration.py's
     "never write to default branch" rule — see that module's docstring),
     never with a `sha` (every file is a genuine create), and stops at the
     first failure while reporting exactly what already landed.
  3. The route: blocked without a primary repository, without an
     engineering setup, and — most importantly — without a genuinely
     empty repository (never overwrites real work); a successful bootstrap
     records repository.bootstrapped with the file list.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.repo_bootstrap import bootstrap_repository
from app.models import (
    ArtifactStatus,
    AuditLog,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    Project,
    ProjectEngineeringSetup,
    Repository,
    User,
)
from app.schemas.repo_bootstrap import BootstrapRepositoryRequest
from app.services import github_integration as github_api
from app.services.repo_bootstrap import RepoBootstrapError, generate_scaffold_files, push_scaffold
from tests.conftest import make_approved_artifact, make_node

pytestmark = pytest.mark.usefixtures("db")


def _setup(db, project, actor, *, application_type="Web Application", primary_language="TypeScript", frontend=None, backend=None):
    setup = ProjectEngineeringSetup(
        project_id=project.id, created_by_id=actor.id, application_type=application_type, primary_language=primary_language,
        frontend_framework=frontend, backend_framework=backend,
    )
    db.add(setup)
    db.flush()
    return setup


def _repository(db, project, *, is_primary=True, decryptable=True):
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
        project=project, connection=connection, owner="octocat", name="empty-repo", default_branch="main", is_primary=is_primary,
    )
    db.add(repository)
    db.flush()
    return repository


# --- generate_scaffold_files ------------------------------------------------------------


def test_known_stack_gets_real_boilerplate(db, project, actor):
    setup = _setup(db, project, actor, frontend="Next.js", backend="FastAPI")
    files = generate_scaffold_files(db, project=project, setup=setup)
    paths = {f.path for f in files}

    assert "README.md" in paths
    assert ".gitignore" in paths
    assert "frontend/package.json" in paths
    assert "frontend/app/page.tsx" in paths
    assert "backend/requirements.txt" in paths
    assert "backend/app/main.py" in paths
    assert "src/.gitkeep" not in paths  # a known stack never gets the generic fallback


def test_unknown_stack_falls_back_to_generic_skeleton(db, project, actor):
    setup = _setup(db, project, actor, frontend="Some Bespoke Framework", backend=None)
    files = generate_scaffold_files(db, project=project, setup=setup)
    paths = {f.path for f in files}

    assert "README.md" in paths
    assert ".gitignore" in paths
    assert "src/.gitkeep" in paths
    assert "frontend/package.json" not in paths
    assert "backend/requirements.txt" not in paths
    readme = next(f.content for f in files if f.path == "README.md")
    assert "doesn't match a framework this scaffold generates real boilerplate for yet" in readme


def test_approved_hld_and_solution_discovery_are_copied_into_docs(db, project, actor):
    setup = _setup(db, project, actor)
    hld_node = make_node(db, project, node_key="hld", order_index=0, output_artifact_type="hld_document")
    solution_node = make_node(db, project, node_key="solution_discovery", order_index=1, output_artifact_type="solution_options_doc")
    make_approved_artifact(db, project, hld_node, actor, content="## Architecture\n\nMicroservices.\n")
    make_approved_artifact(db, project, solution_node, actor, content="## Recommendation\n\nOption 2.\n")

    files = generate_scaffold_files(db, project=project, setup=setup)
    by_path = {f.path: f.content for f in files}

    assert "docs/HLD.md" in by_path
    assert "Microservices." in by_path["docs/HLD.md"]
    assert "docs/SOLUTION_DISCOVERY.md" in by_path
    assert "Option 2." in by_path["docs/SOLUTION_DISCOVERY.md"]
    assert "docs/SOLUTION_DISCOVERY.md" in by_path["README.md"]


def test_no_docs_section_when_nothing_is_approved_yet(db, project, actor):
    setup = _setup(db, project, actor)
    files = generate_scaffold_files(db, project=project, setup=setup)
    paths = {f.path for f in files}
    assert "docs/HLD.md" not in paths
    assert "docs/SOLUTION_DISCOVERY.md" not in paths


# --- push_scaffold -----------------------------------------------------------------------


def test_push_scaffold_writes_every_file_to_the_default_branch_as_a_create(db, project, monkeypatch):
    repository = _repository(db, project)
    calls = []

    def _fake_create_or_update_file(token, owner, repo, path, *, content, message, branch, sha=None, **kwargs):
        calls.append((path, branch, sha))
        return f"sha-for-{path}"

    monkeypatch.setattr(github_api, "create_or_update_file", _fake_create_or_update_file)

    from app.services.repo_bootstrap import ScaffoldFile

    files = [ScaffoldFile("README.md", "# Hi\n"), ScaffoldFile(".gitignore", "node_modules/\n")]
    result = push_scaffold("fake-token", repository, files)

    assert result.branch == "main"
    assert [c.path for c in result.commits] == ["README.md", ".gitignore"]
    assert calls == [("README.md", "main", None), (".gitignore", "main", None)]  # never a sha — always a genuine create


def test_push_scaffold_stops_at_first_failure_and_reports_partial_commits(db, project, monkeypatch):
    repository = _repository(db, project)

    def _fake_create_or_update_file(token, owner, repo, path, *, content, message, branch, sha=None, **kwargs):
        if path == ".gitignore":
            raise github_api.GitHubIntegrationError("boom", status_code=502)
        return f"sha-for-{path}"

    monkeypatch.setattr(github_api, "create_or_update_file", _fake_create_or_update_file)

    from app.services.repo_bootstrap import ScaffoldFile

    files = [ScaffoldFile("README.md", "# Hi\n"), ScaffoldFile(".gitignore", "node_modules/\n"), ScaffoldFile("src/.gitkeep", "")]

    with pytest.raises(RepoBootstrapError) as exc_info:
        push_scaffold("fake-token", repository, files)
    assert [c.path for c in exc_info.value.commits] == ["README.md"]  # only the first file actually landed


# --- route: bootstrap_repository ----------------------------------------------------------


def test_route_blocked_without_primary_repository(db, project, actor):
    _setup(db, project, actor, frontend="Next.js")
    with pytest.raises(HTTPException) as exc_info:
        bootstrap_repository(project.id, BootstrapRepositoryRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "no primary GitHub repository" in exc_info.value.detail


def test_route_blocked_without_engineering_setup(db, project, actor):
    _repository(db, project)
    with pytest.raises(HTTPException) as exc_info:
        bootstrap_repository(project.id, BootstrapRepositoryRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "Project Engineering Setup" in exc_info.value.detail


def test_route_refuses_a_non_empty_repository(db, project, actor, monkeypatch):
    _setup(db, project, actor, frontend="Next.js")
    _repository(db, project)

    import app.api.routes.repo_bootstrap as routes_module

    monkeypatch.setattr(routes_module, "decrypt_repository_token", lambda repo: "fake-token")
    monkeypatch.setattr(routes_module.github_api, "list_branches", lambda token, owner, repo, **kw: ["main"])

    with pytest.raises(HTTPException) as exc_info:
        bootstrap_repository(project.id, BootstrapRepositoryRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "already has" in exc_info.value.detail


def test_route_bootstraps_a_genuinely_empty_repository(db, project, actor, monkeypatch):
    setup = _setup(db, project, actor, frontend="Next.js", backend="FastAPI")
    repository = _repository(db, project)

    import app.api.routes.repo_bootstrap as routes_module

    monkeypatch.setattr(routes_module, "decrypt_repository_token", lambda repo: "fake-token")
    monkeypatch.setattr(routes_module.github_api, "list_branches", lambda token, owner, repo, **kw: [])

    def _fake_create_or_update_file(token, owner, repo, path, *, content, message, branch, sha=None, **kwargs):
        return f"sha-for-{path}"

    monkeypatch.setattr(github_api, "create_or_update_file", _fake_create_or_update_file)

    expected_files = generate_scaffold_files(db, project=project, setup=setup)

    response = bootstrap_repository(project.id, BootstrapRepositoryRequest(triggered_by_user_id=actor.id), db)

    assert response.repository_id == repository.id
    assert response.branch == "main"
    assert {c.path for c in response.commits} == {f.path for f in expected_files}

    audit = db.query(AuditLog).filter(AuditLog.action == "repository.bootstrapped").first()
    assert audit is not None
    assert audit.extra_data["file_count"] == len(expected_files)
