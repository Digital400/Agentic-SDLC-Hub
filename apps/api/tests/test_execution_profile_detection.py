"""Tests for safe repository detection — see
app/services/execution_profile_detection.py.

The single most important test here is test_module_never_shells_out: the
module's own docstring promises no subprocess/exec/os.system call exists
anywhere in it, so this test verifies that promise against the actual
source file rather than trusting the docstring.
"""

import base64
import inspect

import httpx

from app.models import (
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    Repository,
    RepositoryFileEntryType,
    RepositoryFileIndex,
    RepositorySnapshot,
)
from app.services import execution_profile_detection as detection_module
from app.services.execution_profile_detection import detect_execution_profile


def _make_repository_with_snapshot(db, project, *, files: list[tuple[str, int]]):
    integration = Integration(integration_name="GitHub", provider=IntegrationProvider.GITHUB, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(
        integration=integration, access_token_encrypted="not-a-real-fernet-token",
        token_last_four="7890", github_username="octocat", status=IntegrationStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    repository = Repository(project=project, connection=connection, owner="octocat", name="hello-world", default_branch="main")
    db.add(repository)
    db.flush()
    snapshot = RepositorySnapshot(repository=repository, ref="main", commit_sha="abc123", file_count=len(files), truncated=False)
    db.add(snapshot)
    db.flush()
    for path, size in files:
        db.add(RepositoryFileIndex(snapshot=snapshot, path=path, entry_type=RepositoryFileEntryType.FILE, size=size, sha="deadbeef"))
    db.flush()
    db.refresh(snapshot)
    return repository, snapshot


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _github_content_handler(content_by_path: dict[str, str]):
    def handler(request: httpx.Request) -> httpx.Response:
        for path, content in content_by_path.items():
            if request.url.path.endswith(path):
                return httpx.Response(200, json={"sha": "abc", "size": len(content), "content": _b64(content)})
        return httpx.Response(404, json={"message": "Not Found"})

    return handler


# --- The hard rule: no execution anywhere in this module ------------------------------


def test_module_never_shells_out():
    """Verifies execution_profile_detection.py's own module docstring
    promise by grepping its actual CODE (module docstring excluded, since
    the docstring's own prose necessarily names the forbidden tokens it's
    promising not to use)."""
    import ast

    source = inspect.getsource(detection_module)
    tree = ast.parse(source)
    module_docstring = ast.get_docstring(tree) or ""
    code_only = source.replace(module_docstring, "", 1)

    forbidden = ["subprocess", "os.system", "os.popen", "os.exec", "eval(", "shell=True"]
    for token in forbidden:
        assert token not in code_only, f"execution_profile_detection.py's code (outside its module docstring) must never contain '{token}'"


# --- Language / package manager detection ----------------------------------------------


def test_detects_python_from_requirements_txt(db, project):
    _repo, snapshot = _make_repository_with_snapshot(db, project, files=[("requirements.txt", 100)])

    result = detect_execution_profile(snapshot=snapshot, github_token=None)

    assert "Python" in result.detected_languages
    assert result.package_manager == "pip"
    assert result.unit_test_command == "pytest -q"
    assert result.install_command == "pip install -r requirements.txt"


def test_detects_poetry_from_pyproject_toml_content(db, project):
    _repo, snapshot = _make_repository_with_snapshot(db, project, files=[("pyproject.toml", 200)])
    handler = _github_content_handler({"pyproject.toml": "[tool.poetry]\nname = 'x'\ndependencies = { fastapi = '*' }"})

    result = detect_execution_profile(snapshot=snapshot, github_token="ghp_fake", transport=httpx.MockTransport(handler))

    assert result.package_manager == "poetry"
    assert "FastAPI" in result.detected_frameworks
    assert result.install_command == "poetry install"


def test_detects_node_package_manager_from_yarn_lock(db, project):
    _repo, snapshot = _make_repository_with_snapshot(db, project, files=[("package.json", 100), ("yarn.lock", 50)])
    handler = _github_content_handler({"package.json": '{"scripts": {"lint": "eslint .", "test": "jest", "build": "next build"}}'})

    result = detect_execution_profile(snapshot=snapshot, github_token="ghp_fake", transport=httpx.MockTransport(handler))

    assert result.package_manager == "yarn"
    assert result.lint_command == "yarn lint"
    assert result.unit_test_command == "yarn test"
    assert result.build_command == "yarn build"
    assert result.install_command == "yarn install --frozen-lockfile"


def test_package_json_frameworks_detected_from_dependencies(db, project):
    _repo, snapshot = _make_repository_with_snapshot(db, project, files=[("package.json", 100)])
    handler = _github_content_handler({"package.json": '{"dependencies": {"next": "14.0.0", "react": "18.0.0"}}'})

    result = detect_execution_profile(snapshot=snapshot, github_token="ghp_fake", transport=httpx.MockTransport(handler))

    assert "Next.js" in result.detected_frameworks
    assert "React" in result.detected_frameworks


def test_scripts_are_never_executed_only_read_as_a_signal(db, project):
    """The whole point of the module — a package.json script string is a
    SIGNAL, never invoked. This test's handler would only be reachable via
    a real npm/yarn process actually running the script, which never
    happens here; success is simply that no such call occurs and the
    proposed command is the *label* npm run lint, not lint's own body."""
    _repo, snapshot = _make_repository_with_snapshot(db, project, files=[("package.json", 100)])
    handler = _github_content_handler({"package.json": '{"scripts": {"lint": "rm -rf / # this must never run"}}'})

    result = detect_execution_profile(snapshot=snapshot, github_token="ghp_fake", transport=httpx.MockTransport(handler))

    assert result.lint_command == "npm run lint"  # the proposed command references the script BY NAME, never inlines its body
    assert "rm -rf" not in (result.lint_command or "")


# --- Dockerfile base image -------------------------------------------------------------


def test_dockerfile_base_image_detected_without_building_it(db, project):
    _repo, snapshot = _make_repository_with_snapshot(db, project, files=[("Dockerfile", 100)])
    handler = _github_content_handler({"Dockerfile": "FROM python:3.13-slim\nWORKDIR /app\nRUN pip install -r requirements.txt\n"})

    result = detect_execution_profile(snapshot=snapshot, github_token="ghp_fake", transport=httpx.MockTransport(handler))

    assert result.runtime_image == "python:3.13-slim"


# --- Environment variable NAMES only ----------------------------------------------------


def test_env_example_extracts_names_only_never_values(db, project):
    _repo, snapshot = _make_repository_with_snapshot(db, project, files=[(".env.example", 100)])
    handler = _github_content_handler({".env.example": "DATABASE_URL=postgresql://user:supersecretpassword@host/db\nAPI_KEY=sk-should-never-appear\n# a comment\nEMPTY_LINE_ABOVE_OK=1\n"})

    result = detect_execution_profile(snapshot=snapshot, github_token="ghp_fake", transport=httpx.MockTransport(handler))

    assert set(result.environment_variable_names) == {"DATABASE_URL", "API_KEY", "EMPTY_LINE_ABOVE_OK"}
    # HARD RULE: no value/secret substring anywhere in the result.
    joined = " ".join(result.environment_variable_names)
    assert "supersecretpassword" not in joined
    assert "sk-should-never-appear" not in joined
    for name in result.environment_variable_names:
        assert "=" not in name


# --- Working directories / monorepo layout ----------------------------------------------


def test_detects_monorepo_working_directories(db, project):
    _repo, snapshot = _make_repository_with_snapshot(
        db, project, files=[("apps/api/requirements.txt", 50), ("apps/web/package.json", 50)]
    )

    result = detect_execution_profile(snapshot=snapshot, github_token=None)

    assert "apps/api" in result.working_directories
    assert "apps/web" in result.working_directories


def test_defaults_to_root_working_directory_when_no_subproject_matches(db, project):
    _repo, snapshot = _make_repository_with_snapshot(db, project, files=[("requirements.txt", 50)])

    result = detect_execution_profile(snapshot=snapshot, github_token=None)

    assert result.working_directories == ["."]


# --- Safe defaults / resilience --------------------------------------------------------


def test_denied_paths_defaults_include_env_and_secrets(db, project):
    _repo, snapshot = _make_repository_with_snapshot(db, project, files=[("requirements.txt", 50)])
    result = detect_execution_profile(snapshot=snapshot, github_token=None)
    assert ".env" in result.denied_paths
    assert any("secrets" in p for p in result.denied_paths)


def test_network_policy_is_not_this_functions_job_but_defaults_stay_safe_upstream(db, project):
    """detect_execution_profile itself doesn't set network_policy (that's
    composed in execution_profile_service.py); this test just documents
    that DetectionResult carries no network_policy field to accidentally
    default open."""
    _repo, snapshot = _make_repository_with_snapshot(db, project, files=[("requirements.txt", 50)])
    result = detect_execution_profile(snapshot=snapshot, github_token=None)
    assert not hasattr(result, "network_policy")


def test_no_token_degrades_to_presence_only_signals_without_raising(db, project):
    _repo, snapshot = _make_repository_with_snapshot(db, project, files=[("package.json", 100)])

    result = detect_execution_profile(snapshot=snapshot, github_token=None)

    assert "TypeScript/JavaScript" in result.detected_languages
    assert result.lint_command is None  # no scripts could be read without a token


def test_unreadable_file_degrades_gracefully(db, project):
    _repo, snapshot = _make_repository_with_snapshot(db, project, files=[("package.json", 100)])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    result = detect_execution_profile(snapshot=snapshot, github_token="ghp_fake", transport=httpx.MockTransport(handler))

    assert result.lint_command is None
    assert "TypeScript/JavaScript" in result.detected_languages  # presence-only signal still works


def test_detection_metadata_records_what_was_scanned(db, project):
    _repo, snapshot = _make_repository_with_snapshot(db, project, files=[("requirements.txt", 50)])
    result = detect_execution_profile(snapshot=snapshot, github_token=None)
    assert result.detection_metadata["files_scanned"] == 1
    assert result.detection_metadata["commit_sha"] == "abc123"


def test_security_scans_and_skills_derived_from_language_not_repo_content(db, project):
    _repo, snapshot = _make_repository_with_snapshot(db, project, files=[("requirements.txt", 50)])
    result = detect_execution_profile(snapshot=snapshot, github_token=None)
    assert "pip-audit" in result.approved_security_scan_commands
    assert "Python" in result.required_coding_skills
    assert "pytest" in result.required_testing_skills
