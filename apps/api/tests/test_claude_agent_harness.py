"""Tests for app/services/claude_agent_harness.py — the Claude Agent SDK
wrapper for the Implementation Agent (Phase 1, Implementation Agent only;
see that module's own docstring).

No real network/GitHub call and no real Claude Agent SDK session is ever
made — every git-invoking call is exercised against a monkeypatched
subprocess.run (same convention as test_code_runner.py), and query() is
replaced with a fake async generator yielding a controlled ResultMessage.
"""

import subprocess
import uuid
from types import SimpleNamespace

import pytest

from app.models import (
    ImplementationTaskArea,
    ImplementationTaskRiskLevel,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    Repository,
)
from app.models.implementation_task import ImplementationTask
from app.services import claude_agent_harness as module
from app.services.claude_agent_harness import ClaudeAgentHarnessError, resolve_project_repository, run_document_session, run_implementation_agent_via_sdk
from app.services.story_export import Story

REAL_TOKEN = "ghp_ThisIsARealSecretGitHubTokenAndMustNeverAppearInErrors1234"


def _repository_with_connection(db, project, *, connected: bool = True) -> Repository:
    integration = Integration(integration_name="GitHub", provider=IntegrationProvider.GITHUB, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(
        integration=integration, access_token_encrypted="not-a-real-fernet-ciphertext" if connected else "",
        token_last_four="1234", github_username="octocat", status=IntegrationStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    repository = Repository(project=project, connection=connection, owner="octocat", name="hello-world", default_branch="main")
    db.add(repository)
    db.flush()
    return repository


def _task(project_id) -> ImplementationTask:
    return ImplementationTask(
        id=uuid.uuid4(), project_id=project_id, title="Add health check endpoint",
        description="Add GET /health returning 200.", area=ImplementationTaskArea.BACKEND,
        expected_paths=["apps/api/app/api/routes/health.py"], acceptance_criteria=["GET /health returns 200"],
        test_expectation="pytest tests/test_health.py", risk_level=ImplementationTaskRiskLevel.LOW,
        assigned_agent_type="backend-coding-agent",
    )


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_result_message(*, is_error=False, errors=None, result="Added the health check endpoint and a test for it."):
    """A real ResultMessage (not a lookalike) — _run_session's own
    isinstance(message, ResultMessage) check requires it."""
    from claude_agent_sdk.types import ResultMessage

    return ResultMessage(
        subtype="error_during_execution" if is_error else "success", duration_ms=1000, duration_api_ms=800,
        is_error=is_error, num_turns=3, session_id="test-session", errors=errors,
        usage={"input_tokens": 1000, "output_tokens": 500}, total_cost_usd=0.0456, result=result,
    )


def _fake_query_yielding(result_message):
    async def _fake_query(*, prompt, options, transport=None):
        if result_message is not None:
            yield result_message

    return _fake_query


@pytest.fixture(autouse=True)
def _no_executable_resolution(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda executable: None)


# --- _repository_token -----------------------------------------------------------------


def test_repository_token_raises_when_no_connection(db, project):
    repository = Repository(project=project, owner="octocat", name="hello-world", default_branch="main")  # transient, never flushed
    with pytest.raises(ClaudeAgentHarnessError):
        module._repository_token(repository)


def test_repository_token_raises_when_decrypt_fails(db, project, monkeypatch):
    from app.core.security import SecretDecryptionError

    repository = _repository_with_connection(db, project)

    def _raise(ciphertext):
        raise SecretDecryptionError("bad token")

    monkeypatch.setattr(module, "decrypt_secret", _raise)
    with pytest.raises(ClaudeAgentHarnessError):
        module._repository_token(repository)


# --- _run_git ----------------------------------------------------------------------------


def test_run_git_raises_and_redacts_secret_on_failure(tmp_path, monkeypatch):
    def _fake_run(command, **kwargs):
        return _FakeCompletedProcess(returncode=128, stdout="", stderr=f"fatal: could not read {REAL_TOKEN}")

    monkeypatch.setattr(module.subprocess, "run", _fake_run)
    with pytest.raises(ClaudeAgentHarnessError) as exc_info:
        module._run_git(tmp_path, ["clone", "x"], secret=REAL_TOKEN)
    assert REAL_TOKEN not in str(exc_info.value)


def test_run_git_raises_on_timeout(tmp_path, monkeypatch):
    def _fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=1)

    monkeypatch.setattr(module.subprocess, "run", _fake_run)
    with pytest.raises(ClaudeAgentHarnessError):
        module._run_git(tmp_path, ["clone", "x"], timeout=1)


# --- _clone_workspace ----------------------------------------------------------------------


def test_clone_workspace_builds_the_clone_url_with_the_token_and_never_leaks_it_on_failure(db, project, tmp_path, monkeypatch):
    repository = _repository_with_connection(db, project)
    monkeypatch.setattr(module, "decrypt_secret", lambda ciphertext: REAL_TOKEN)
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(CODE_RUNNER_WORKSPACE_ROOT=tmp_path))

    captured = {}

    def _fake_run(command, **kwargs):
        captured["command"] = command
        return _FakeCompletedProcess(returncode=0, stdout="Cloning...", stderr="")

    monkeypatch.setattr(module.subprocess, "run", _fake_run)
    workspace = module._clone_workspace(repository, "main")

    assert REAL_TOKEN in " ".join(captured["command"])
    assert workspace.exists()


# --- _collect_changes ----------------------------------------------------------------------


def test_collect_changes_reads_created_and_modified_files_and_reports_deletes(tmp_path, monkeypatch):
    (tmp_path / "new_file.py").write_text("print('new')\n")
    (tmp_path / "existing.py").write_text("print('modified')\n")

    porcelain = "?? new_file.py\n M existing.py\n D removed.py\n"

    def _fake_run(command, **kwargs):
        return _FakeCompletedProcess(returncode=0, stdout=porcelain, stderr="")

    monkeypatch.setattr(module.subprocess, "run", _fake_run)
    changes = module._collect_changes(tmp_path)

    by_path = {c.path: c for c in changes}
    assert by_path["new_file.py"].change_type == "create"
    assert by_path["new_file.py"].after_content == "print('new')\n"
    assert by_path["existing.py"].change_type == "modify"
    assert by_path["removed.py"].change_type == "delete"
    assert by_path["removed.py"].after_content is None


# --- run_implementation_agent_via_sdk -------------------------------------------------------


def _patch_clone_and_status(monkeypatch, tmp_path, *, porcelain: str):
    monkeypatch.setattr(module, "_clone_workspace", lambda repository, base_branch: tmp_path)

    def _fake_run_git(cwd, args, *, secret=None, timeout=900):
        if args[0] == "status":
            return _FakeCompletedProcess(returncode=0, stdout=porcelain, stderr="")
        return _FakeCompletedProcess(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module, "_run_git", _fake_run_git)


def test_happy_path_returns_a_real_result_shape(db, project, tmp_path, monkeypatch):
    (tmp_path / "health.py").write_text("def health(): return 200\n")
    _patch_clone_and_status(monkeypatch, tmp_path, porcelain="?? health.py\n")
    monkeypatch.setattr(module, "query", _fake_query_yielding(_fake_result_message()))
    repository = _repository_with_connection(db, project)
    task = _task(project.id)

    result = run_implementation_agent_via_sdk(task=task, repository=repository, base_branch="main", story=None, lld_summary="LLD text")

    assert result.used_mock is False
    assert len(result.proposed_file_changes) == 1
    assert result.proposed_file_changes[0].path == "health.py"
    assert result.explanation == "Added the health check endpoint and a test for it."
    assert result.cost == 0.0456
    assert result.prompt_tokens == 1000
    assert result.completion_tokens == 500
    assert result.total_tokens == 1500
    assert result.diff_text.strip() != ""


def test_raises_when_the_session_errors(db, project, tmp_path, monkeypatch):
    _patch_clone_and_status(monkeypatch, tmp_path, porcelain="")
    monkeypatch.setattr(module, "query", _fake_query_yielding(_fake_result_message(is_error=True, errors=["max turns exceeded"])))
    repository = _repository_with_connection(db, project)
    task = _task(project.id)

    with pytest.raises(ClaudeAgentHarnessError):
        run_implementation_agent_via_sdk(task=task, repository=repository, base_branch="main", story=None, lld_summary="")


def test_raises_when_the_session_yields_no_result(db, project, tmp_path, monkeypatch):
    _patch_clone_and_status(monkeypatch, tmp_path, porcelain="")
    monkeypatch.setattr(module, "query", _fake_query_yielding(None))
    repository = _repository_with_connection(db, project)
    task = _task(project.id)

    with pytest.raises(ClaudeAgentHarnessError):
        run_implementation_agent_via_sdk(task=task, repository=repository, base_branch="main", story=None, lld_summary="")


def test_anthropic_api_key_is_forwarded_to_the_cli_subprocess_env(db, project, tmp_path, monkeypatch):
    """Regression: apps/api/.env's ANTHROPIC_API_KEY used to reach only
    _generate_with_anthropic's direct SDK call — pydantic-settings reads
    .env into Settings only, never into os.environ, so the `claude` CLI
    subprocess this harness launches never saw it, silently falling back
    to whatever `claude login` session (or lack of one) existed on the
    host. See _sdk_env's own docstring."""
    _patch_clone_and_status(monkeypatch, tmp_path, porcelain="")
    captured_options = {}

    async def _capturing_query(*, prompt, options, transport=None):
        captured_options["options"] = options
        yield _fake_result_message()

    monkeypatch.setattr(module, "query", _capturing_query)
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(
        CLAUDE_AGENT_SDK_MAX_TURNS=30, CLAUDE_AGENT_SDK_MAX_BUDGET_USD=3.0, ANTHROPIC_API_KEY="sk-ant-real-test-key",
    ))
    repository = _repository_with_connection(db, project)
    task = _task(project.id)

    run_implementation_agent_via_sdk(task=task, repository=repository, base_branch="main", story=None, lld_summary="")

    assert captured_options["options"].env == {"ANTHROPIC_API_KEY": "sk-ant-real-test-key"}


def test_no_anthropic_api_key_configured_means_no_env_override(db, project, tmp_path, monkeypatch):
    """When no key is configured, this harness adds nothing to the
    subprocess's environment — it still inherits a `claude login` session
    from the host exactly as before this field existed."""
    _patch_clone_and_status(monkeypatch, tmp_path, porcelain="")
    captured_options = {}

    async def _capturing_query(*, prompt, options, transport=None):
        captured_options["options"] = options
        yield _fake_result_message()

    monkeypatch.setattr(module, "query", _capturing_query)
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(
        CLAUDE_AGENT_SDK_MAX_TURNS=30, CLAUDE_AGENT_SDK_MAX_BUDGET_USD=3.0, ANTHROPIC_API_KEY=None,
    ))
    repository = _repository_with_connection(db, project)
    task = _task(project.id)

    run_implementation_agent_via_sdk(task=task, repository=repository, base_branch="main", story=None, lld_summary="")

    assert captured_options["options"].env == {}


def test_raises_when_repository_has_no_connection(db, project, tmp_path):
    repository = Repository(project=project, owner="octocat", name="hello-world", default_branch="main")  # transient, never flushed
    task = _task(project.id)

    with pytest.raises(ClaudeAgentHarnessError):
        run_implementation_agent_via_sdk(task=task, repository=repository, base_branch="main", story=None, lld_summary="")


def test_workspace_is_always_removed_even_when_the_session_errors(db, project, tmp_path, monkeypatch):
    workspace = tmp_path / "harness-workspace"
    workspace.mkdir()
    monkeypatch.setattr(module, "_clone_workspace", lambda repository, base_branch: workspace)
    monkeypatch.setattr(module, "query", _fake_query_yielding(_fake_result_message(is_error=True, errors=["boom"])))
    repository = _repository_with_connection(db, project)
    task = _task(project.id)

    with pytest.raises(ClaudeAgentHarnessError):
        run_implementation_agent_via_sdk(task=task, repository=repository, base_branch="main", story=None, lld_summary="")

    assert not workspace.exists()


def test_no_changes_is_flagged_as_a_risk_not_an_error(db, project, tmp_path, monkeypatch):
    _patch_clone_and_status(monkeypatch, tmp_path, porcelain="")
    monkeypatch.setattr(module, "query", _fake_query_yielding(_fake_result_message()))
    repository = _repository_with_connection(db, project)
    task = _task(project.id)

    result = run_implementation_agent_via_sdk(task=task, repository=repository, base_branch="main", story=None, lld_summary="")

    assert result.proposed_file_changes == []
    assert any("no file changes" in r.lower() for r in result.risks)


def test_story_and_jira_key_flow_into_the_pr_description(db, project, tmp_path, monkeypatch):
    (tmp_path / "health.py").write_text("x = 1\n")
    _patch_clone_and_status(monkeypatch, tmp_path, porcelain="?? health.py\n")
    monkeypatch.setattr(module, "query", _fake_query_yielding(_fake_result_message()))
    repository = _repository_with_connection(db, project)
    task = _task(project.id)
    story = Story(title="Add platform health checks", user_story="As an operator...")

    result = run_implementation_agent_via_sdk(
        task=task, repository=repository, base_branch="main", story=story, lld_summary="", jira_issue_key="PROJ-42",
    )

    assert "Add platform health checks" in result.pr_description
    assert "PROJ-42" in result.pr_description


# --- resolve_project_repository ---------------------------------------------------------


def test_resolve_project_repository_returns_none_when_no_repository(db, project):
    assert resolve_project_repository(project) == (None, None)


def test_resolve_project_repository_prefers_the_primary_repository(db, project):
    _repository_with_connection(db, project)  # not primary
    primary = _repository_with_connection(db, project)
    primary.is_primary = True
    db.flush()
    db.refresh(project)

    repository, base_branch = resolve_project_repository(project)

    assert repository.id == primary.id
    assert base_branch == primary.default_branch


def test_resolve_project_repository_uses_a_snapshots_ref_not_the_default_branch(db, project):
    from app.models import RepositorySnapshot

    repository = _repository_with_connection(db, project)
    assert repository.default_branch == "main"
    db.add(RepositorySnapshot(repository=repository, ref="develop", commit_sha="aaa", file_count=1, truncated=False))
    db.flush()
    db.refresh(project)

    _, base_branch = resolve_project_repository(project)

    assert base_branch == "develop"  # the snapshot's own ref, not the repository's default_branch ("main")


# --- run_document_session ----------------------------------------------------------------


def test_run_document_session_returns_the_sessions_text_and_usage(db, project, tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_clone_workspace", lambda repository, base_branch: tmp_path)
    monkeypatch.setattr(module, "query", _fake_query_yielding(_fake_result_message(result="## Problem\n\nReal grounded analysis.")))
    repository = _repository_with_connection(db, project)

    result = run_document_session(system_prompt="You are...", user_content="Draft it.", repository=repository, base_branch="main")

    assert result.text == "## Problem\n\nReal grounded analysis."
    assert result.prompt_tokens == 1000
    assert result.completion_tokens == 500
    assert result.cost == 0.0456
    assert result.truncated is False


def test_run_document_session_cleans_up_the_workspace(db, project, tmp_path, monkeypatch):
    workspace = tmp_path / "doc-workspace"
    workspace.mkdir()
    monkeypatch.setattr(module, "_clone_workspace", lambda repository, base_branch: workspace)
    monkeypatch.setattr(module, "query", _fake_query_yielding(_fake_result_message()))
    repository = _repository_with_connection(db, project)

    run_document_session(system_prompt="sys", user_content="do it", repository=repository, base_branch="main")

    assert not workspace.exists()


def test_run_document_session_raises_on_session_error(db, project, tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_clone_workspace", lambda repository, base_branch: tmp_path)
    monkeypatch.setattr(module, "query", _fake_query_yielding(_fake_result_message(is_error=True, errors=["boom"])))
    repository = _repository_with_connection(db, project)

    with pytest.raises(ClaudeAgentHarnessError):
        run_document_session(system_prompt="sys", user_content="do it", repository=repository, base_branch="main")


def test_run_document_session_only_allows_read_only_tools(db, project, tmp_path, monkeypatch):
    """The whole point of this entry point: a document stage's session
    must never be able to write code, even if it tried — enforced by
    never granting Write/Edit/Bash in the first place."""
    captured = {}

    def _fake_run_session(prompt, options):
        captured["options"] = options

        async def _inner():
            return _fake_result_message()

        return _inner()

    monkeypatch.setattr(module, "_clone_workspace", lambda repository, base_branch: tmp_path)
    monkeypatch.setattr(module, "_run_session", _fake_run_session)
    repository = _repository_with_connection(db, project)

    run_document_session(system_prompt="sys", user_content="do it", repository=repository, base_branch="main")

    assert set(captured["options"].allowed_tools) == {"Read", "Glob", "Grep"}
