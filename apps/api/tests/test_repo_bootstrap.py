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
    CodingStandardCategory,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    Project,
    ProjectCodingStandard,
    ProjectEngineeringSetup,
    ProjectGuardrail,
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


def test_coding_standards_and_guardrails_are_carried_into_the_repo_as_a_readable_doc(db, project, actor):
    setup = _setup(db, project, actor)
    db.add(
        ProjectCodingStandard(
            setup_id=setup.id, title="Error Handling", content="Always return typed errors, never raw exceptions.",
            category=CodingStandardCategory.ARCHITECTURE, order_index=0,
        )
    )
    db.add(ProjectGuardrail(setup_id=setup.id, rule_text="Never touch payment code without human review.", order_index=0))
    db.flush()
    db.refresh(setup)

    files = generate_scaffold_files(db, project=project, setup=setup)
    by_path = {f.path: f.content for f in files}

    assert "docs/CODING_STANDARDS.md" in by_path
    assert "Error Handling" in by_path["docs/CODING_STANDARDS.md"]
    assert "Always return typed errors, never raw exceptions." in by_path["docs/CODING_STANDARDS.md"]
    assert "Never touch payment code without human review." in by_path["docs/CODING_STANDARDS.md"]
    assert "docs/CODING_STANDARDS.md" in by_path["README.md"]  # README links to it


def test_no_coding_standards_doc_when_nothing_is_configured(db, project, actor):
    setup = _setup(db, project, actor)
    files = generate_scaffold_files(db, project=project, setup=setup)
    paths = {f.path for f in files}
    assert "docs/CODING_STANDARDS.md" not in paths


def test_root_workspace_package_json_present_for_frontend_scaffold(db, project, actor):
    setup = _setup(db, project, actor, frontend="Next.js", backend="FastAPI")
    files = generate_scaffold_files(db, project=project, setup=setup)
    paths = {f.path for f in files}

    assert "package.json" in paths  # workspace root — distinct from frontend/package.json
    assert "frontend/package.json" in paths
    root_pkg = next(f.content for f in files if f.path == "package.json")
    assert '"workspaces"' in root_pkg
    assert '"frontend"' in root_pkg
    assert '"backend"' not in root_pkg  # FastAPI isn't a JS workspace member


def test_no_root_workspace_package_json_for_backend_only_scaffold(db, project, actor):
    setup = _setup(db, project, actor, backend="FastAPI")
    files = generate_scaffold_files(db, project=project, setup=setup)
    paths = {f.path for f in files}

    assert "package.json" not in paths
    assert "backend/requirements.txt" in paths


# --- Express backend template (registry-based, added alongside FastAPI) -----------------


def test_express_backend_gets_real_typescript_boilerplate_when_primary_language_says_so(db, project, actor):
    setup = _setup(db, project, actor, primary_language="TypeScript", frontend="Next.js", backend="Node.js (Express)")
    files = generate_scaffold_files(db, project=project, setup=setup)
    paths = {f.path for f in files}

    assert "backend/package.json" in paths
    assert "backend/src/index.ts" in paths
    assert "backend/tsconfig.json" in paths
    assert "backend/.env.example" in paths
    assert "src/.gitkeep" not in paths  # Express is a recognized stack now, not the generic fallback

    package_json = next(f.content for f in files if f.path == "backend/package.json")
    assert '"express"' in package_json
    assert '"typescript"' in package_json


def test_express_backend_gets_plain_javascript_when_primary_language_is_not_typescript(db, project, actor):
    setup = _setup(db, project, actor, primary_language="JavaScript", backend="Express")
    files = generate_scaffold_files(db, project=project, setup=setup)
    paths = {f.path for f in files}

    assert "backend/src/index.js" in paths
    assert "backend/tsconfig.json" not in paths
    assert "backend/src/index.ts" not in paths


def test_express_backend_joins_the_root_npm_workspace_alongside_frontend(db, project, actor):
    setup = _setup(db, project, actor, frontend="Next.js", backend="Express")
    files = generate_scaffold_files(db, project=project, setup=setup)
    by_path = {f.path: f.content for f in files}

    assert "package.json" in by_path  # a real single workspace tying both together
    assert '"frontend"' in by_path["package.json"]
    assert '"backend"' in by_path["package.json"]
    assert "frontend/` and `backend/`" in by_path["README.md"] or (
        "frontend/" in by_path["README.md"] and "backend/" in by_path["README.md"]
    )


def test_express_only_backend_gitignore_gets_a_node_section_not_a_python_one(db, project, actor):
    setup = _setup(db, project, actor, backend="Express")
    files = generate_scaffold_files(db, project=project, setup=setup)
    gitignore = next(f.content for f in files if f.path == ".gitignore")

    assert "node_modules/" in gitignore
    assert "__pycache__/" not in gitignore


def test_generated_package_json_files_all_have_a_working_test_script(db, project, actor):
    """Regression test for a real failure: app/services/code_runner.py runs
    a project's configured "npm test" from the workspace root whenever a
    root package.json exists (see _resolve_test_command_cwd there) — so
    every package.json this module writes must define a real "test"
    script, or a freshly bootstrapped repo's default Build/Test Commands
    fail immediately with npm's "Missing script: test" before a human
    ever touches the code."""
    import json

    setup = _setup(db, project, actor, frontend="Next.js", backend="Node.js (Express)", primary_language="TypeScript")
    files = generate_scaffold_files(db, project=project, setup=setup)
    by_path = {f.path: f.content for f in files}

    root_pkg = json.loads(by_path["package.json"])
    frontend_pkg = json.loads(by_path["frontend/package.json"])
    backend_pkg = json.loads(by_path["backend/package.json"])

    assert "test" in root_pkg["scripts"] and root_pkg["scripts"]["test"]
    assert "--workspaces" in root_pkg["scripts"]["test"] and "--if-present" in root_pkg["scripts"]["test"]
    assert "test" in frontend_pkg["scripts"] and frontend_pkg["scripts"]["test"]
    assert "test" in backend_pkg["scripts"] and backend_pkg["scripts"]["test"]


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
