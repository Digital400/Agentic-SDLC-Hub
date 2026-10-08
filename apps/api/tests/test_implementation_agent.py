"""Tests for ImplementationAgentService (app/services/implementation_agent.py):
  2. Inputs (task, LLD summary, story, repo context, test expectations) are
     actually used to shape the output.
  3. Output shape: proposed_file_changes, diff, explanation, test_command, risks.
  Heuristic (mock provider) path, real-AI path, and the exception-tuple
  fallback — same conventions as test_implementation_planner.py.
"""

import json

import pytest

from app.models import ImplementationTaskArea, ImplementationTaskRiskLevel
from app.services import implementation_agent
from app.services.implementation_agent import SUPPORTED_AREAS, run_implementation_agent
from app.services.repo_context_builder import RelevantFile, RepoContextPreviewResult
from app.services.story_export import Story


class _FakeTask:
    """A plain stand-in — implementation_agent.py only reads attributes off
    ImplementationTask, so a real ORM row isn't needed for these unit tests."""

    def __init__(self, **overrides):
        defaults = dict(
            title="Add password reset endpoint",
            description="Add a POST /auth/password-reset endpoint.",
            area=ImplementationTaskArea.BACKEND,
            expected_paths=["apps/api/app/api/routes/auth.py"],
            acceptance_criteria=["Returns 202 for a valid email", "Returns 404 for an unknown email"],
            test_expectation="",
            risk_level=ImplementationTaskRiskLevel.MEDIUM,
            linked_story="Password Reset Request",
        )
        defaults.update(overrides)
        for k, v in defaults.items():
            setattr(self, k, v)


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(implementation_agent, "get_active_provider", lambda: "mock")


def _repo_context(**overrides) -> RepoContextPreviewResult:
    defaults = dict(
        relevant_folders=["apps/api/app/api/routes"],
        relevant_files=[
            RelevantFile(
                path="apps/api/app/api/routes/auth.py", entry_type="FILE", size=500, score=10,
                reasons=["matches an expected path"], content_mode="full", snippet="def existing(): ...",
            )
        ],
        architecture_summary="Touches apps/api/app/api/routes.",
        dependency_notes=[],
        suggested_edit_scope=[{"path": "apps/api/app/api/routes/auth.py", "status": "existing"}],
        token_budget_report={},
    )
    defaults.update(overrides)
    return RepoContextPreviewResult(**defaults)


# --- Heuristic path --------------------------------------------------------------------


def test_heuristic_result_has_all_four_required_output_fields():
    task = _FakeTask()
    result = run_implementation_agent(task=task, repo_context=_repo_context(), story=None, lld_summary="Some LLD summary.")

    assert result.proposed_file_changes
    assert result.diff_text.strip()
    assert result.explanation.strip()
    assert result.test_command.strip()
    assert result.risks
    assert result.used_mock is True


def test_heuristic_marks_existing_files_as_modify_and_new_files_as_create():
    task = _FakeTask(expected_paths=["apps/api/app/services/new_service.py"])
    repo_context = _repo_context(suggested_edit_scope=[{"path": "apps/api/app/services/new_service.py", "status": "new"}])

    result = run_implementation_agent(task=task, repo_context=repo_context, story=None, lld_summary="")

    assert result.proposed_file_changes[0].change_type == "create"
    assert "+++ b/apps/api/app/services/new_service.py" in result.diff_text
    assert "--- a/apps/api/app/services/new_service.py" in result.diff_text


def test_heuristic_diff_is_a_scaffold_referencing_acceptance_criteria():
    task = _FakeTask()
    result = run_implementation_agent(task=task, repo_context=_repo_context(), story=None, lld_summary="")

    assert "Returns 202 for a valid email" in result.diff_text
    assert "scaffold" in result.explanation.lower()


def test_heuristic_test_command_falls_back_to_area_default_when_task_declares_none():
    backend = run_implementation_agent(task=_FakeTask(area=ImplementationTaskArea.BACKEND), repo_context=_repo_context(), story=None, lld_summary="")
    frontend = run_implementation_agent(
        task=_FakeTask(area=ImplementationTaskArea.FRONTEND, expected_paths=["apps/web/components/foo.tsx"]),
        repo_context=_repo_context(), story=None, lld_summary="",
    )

    assert "pytest" in backend.test_command
    assert "yarn test" in frontend.test_command


def test_heuristic_uses_the_declared_test_expectation_when_present():
    task = _FakeTask(test_expectation="Run the password reset integration test suite.")
    result = run_implementation_agent(task=task, repo_context=_repo_context(), story=None, lld_summary="")

    assert result.test_command == "Run the password reset integration test suite."


def test_heuristic_explanation_mentions_related_story_and_lld_summary():
    story = Story(title="Password Reset Request", user_story="As a user I want to reset my password.")
    result = run_implementation_agent(
        task=_FakeTask(), repo_context=_repo_context(), story=story, lld_summary="Emails a signed reset token."
    )

    assert "Password Reset Request" in result.explanation
    assert "Emails a signed reset token" in result.explanation


def test_supported_areas_is_exactly_the_four_named_agent_types():
    assert SUPPORTED_AREAS == {
        ImplementationTaskArea.BACKEND, ImplementationTaskArea.FRONTEND,
        ImplementationTaskArea.DATABASE, ImplementationTaskArea.DOCS,
    }


# --- Real-AI path + fallback -----------------------------------------------------------


def test_real_ai_path_is_used_and_parsed_when_a_real_provider_is_active(monkeypatch):
    monkeypatch.setattr(implementation_agent, "get_active_provider", lambda: "anthropic")
    canned = {
        "proposed_file_changes": [
            {"path": "apps/api/app/api/routes/auth.py", "change_type": "modify", "summary": "Add endpoint", "content": "def reset(): ...\n"}
        ],
        "explanation": "Adds the password reset endpoint.",
        "test_command": "pytest tests/test_auth.py",
        "risks": ["Token expiry not yet enforced."],
    }
    monkeypatch.setattr(implementation_agent, "generate_raw_text", lambda **kwargs: json.dumps(canned))

    result = run_implementation_agent(task=_FakeTask(), repo_context=_repo_context(), story=None, lld_summary="")

    assert result.used_mock is False
    assert result.proposed_file_changes[0].path == "apps/api/app/api/routes/auth.py"
    assert result.test_command == "pytest tests/test_auth.py"
    assert result.risks == ["Token expiry not yet enforced."]
    # diff_text is no longer asked of the model (see _SYSTEM_PROMPT) — it's
    # now built deterministically from "content" via difflib, so it must
    # still come back populated and consistent, not the old raw "diff" key.
    assert "def reset(): ..." in result.diff_text
    assert "+++ b/apps/api/app/api/routes/auth.py" in result.diff_text


def test_real_ai_failure_falls_back_to_heuristic(monkeypatch):
    monkeypatch.setattr(implementation_agent, "get_active_provider", lambda: "anthropic")

    def _boom(**kwargs):
        raise implementation_agent.AIGenerationError("provider outage")

    monkeypatch.setattr(implementation_agent, "generate_raw_text", _boom)

    result = run_implementation_agent(task=_FakeTask(), repo_context=_repo_context(), story=None, lld_summary="")

    assert result.used_mock is True  # fell back to the heuristic scaffold


def test_real_ai_malformed_json_falls_back_to_heuristic(monkeypatch):
    monkeypatch.setattr(implementation_agent, "get_active_provider", lambda: "anthropic")
    monkeypatch.setattr(implementation_agent, "generate_raw_text", lambda **kwargs: "not json at all")

    result = run_implementation_agent(task=_FakeTask(), repo_context=_repo_context(), story=None, lld_summary="")

    assert result.used_mock is True


# --- Technology stack grounding ----------------------------------------------------------
#
# Regression coverage for a real bug: the real-AI path produced a mix of
# .js and .ts files for a project configured for a TypeScript-only
# backend, because (a) the system prompt never told the model to match
# the configured stack, and (b) app/services/agent_context_builder.py's
# "implementation" agent type used to withhold the Technology Stack
# section entirely (see test_agent_context_builder.py's
# test_implementation_includes_repo_config_commands_and_the_technology_stack).
# This file only covers (a) plus the plumbing that gets (b)'s text to the
# model at all; (b) itself is covered where it's built.


def test_system_prompt_requires_matching_the_configured_technology_stack():
    assert "Technology Stack" in implementation_agent._SYSTEM_PROMPT
    assert "file extensions" in implementation_agent._SYSTEM_PROMPT
    assert "authoritative" in implementation_agent._SYSTEM_PROMPT


def test_real_ai_path_forwards_engineering_setup_context_to_the_model(monkeypatch):
    monkeypatch.setattr(implementation_agent, "get_active_provider", lambda: "anthropic")
    canned = {
        "proposed_file_changes": [{"path": "backend/src/index.ts", "change_type": "create", "summary": "Add entrypoint", "content": "export {};\n"}],
        "explanation": "Adds the entrypoint.", "test_command": "npm test", "risks": [],
    }
    captured: dict = {}

    def _fake_generate(**kwargs):
        captured.update(kwargs)
        return json.dumps(canned)

    monkeypatch.setattr(implementation_agent, "generate_raw_text", _fake_generate)

    run_implementation_agent(
        task=_FakeTask(), repo_context=_repo_context(), story=None, lld_summary="",
        engineering_setup_context="## Technology Stack — every file you create or modify MUST match this exactly\n- Primary language: TypeScript",
    )

    assert "Primary language: TypeScript" in captured["user_content"]
