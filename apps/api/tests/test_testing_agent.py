"""Tests for TestingAgentService (app/services/testing_agent.py):
  2. Inputs (task, LLD summary, diff, acceptance criteria, existing test
     patterns) shape the output.
  3. Output shape: test plan, tests to add, tests executed, pass/fail,
     bugs found, coverage impact placeholder.
  Rule: can suggest fixes. Honesty: heuristic path never fabricates a
  pass/fail result. Real-AI path + exception-tuple fallback.
"""

import json

import pytest

from app.models import ImplementationTaskArea, ImplementationTaskRiskLevel, TestAgentType
from app.services import testing_agent
from app.services.testing_agent import COVERAGE_IMPACT_PLACEHOLDER, is_test_path, render_test_report_markdown, run_testing_agent


class _FakeTask:
    def __init__(self, **overrides):
        defaults = dict(
            title="Add password reset endpoint",
            description="Add a POST /auth/password-reset endpoint.",
            area=ImplementationTaskArea.BACKEND,
            acceptance_criteria=["Returns 202 for a valid email", "Returns 404 for an unknown email"],
            risk_level=ImplementationTaskRiskLevel.MEDIUM,
        )
        defaults.update(overrides)
        for k, v in defaults.items():
            setattr(self, k, v)


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(testing_agent, "get_active_provider", lambda: "mock")


# --- is_test_path ---------------------------------------------------------------------


def test_is_test_path_recognizes_common_conventions():
    assert is_test_path("apps/api/tests/test_auth.py")
    assert is_test_path("apps/web/components/foo_test.tsx")
    assert is_test_path("apps/api/app/services/auth_service.py") is False


# --- Heuristic path ---------------------------------------------------------------------


def test_heuristic_result_has_all_required_output_fields():
    task = _FakeTask()
    result = run_testing_agent(
        task=task, agent_type=TestAgentType.UNIT, diff_text="--- a/x\n+++ b/x\n", lld_summary="LLD summary.",
        existing_test_paths=["apps/api/tests/test_auth.py"], pattern_chunks=[],
    )

    assert result.test_plan.strip()
    assert result.tests_to_add
    assert result.used_mock is True


def test_heuristic_never_fabricates_a_pass_fail_result():
    task = _FakeTask()
    result = run_testing_agent(
        task=task, agent_type=TestAgentType.UNIT, diff_text="def real_code(): pass\n", lld_summary="",
        existing_test_paths=[], pattern_chunks=[],
    )

    # Honesty: no execution sandbox exists — an empty list is the truthful
    # heuristic result, never a fabricated PASS.
    assert result.tests_executed == []
    assert result.pass_count == 0
    assert result.fail_count == 0


def test_heuristic_flags_a_todo_scaffold_diff_as_a_bug_and_suggests_a_fix():
    task = _FakeTask()
    result = run_testing_agent(
        task=task, agent_type=TestAgentType.UNIT,
        diff_text='+# TODO(implementation-agent): implement "Add password reset endpoint"\n',
        lld_summary="", existing_test_paths=[], pattern_chunks=[],
    )

    assert result.bugs_found
    assert result.suggested_fixes  # rule: "can suggest fixes"


def test_heuristic_tests_to_add_covers_every_acceptance_criterion():
    task = _FakeTask()
    result = run_testing_agent(
        task=task, agent_type=TestAgentType.API, diff_text="", lld_summary="", existing_test_paths=[], pattern_chunks=[]
    )

    assert len(result.tests_to_add) == len(task.acceptance_criteria)


# --- render_test_report_markdown --------------------------------------------------------


def test_render_test_report_markdown_has_required_headings_and_disclosure():
    task = _FakeTask()
    result = run_testing_agent(
        task=task, agent_type=TestAgentType.SECURITY, diff_text="", lld_summary="", existing_test_paths=[], pattern_chunks=[]
    )
    markdown = render_test_report_markdown(result, task=task, agent_type=TestAgentType.SECURITY)

    for heading in ["## Summary", "## Acceptance Criteria Coverage", "## Tests To Add", "## Test Evidence", "## Defects Found", "## Suggested Fixes", "## Coverage Impact"]:
        assert heading in markdown
    assert "no test-execution sandbox" in markdown
    assert COVERAGE_IMPACT_PLACEHOLDER["note"] in markdown


# --- Real-AI path + fallback -----------------------------------------------------------


def test_real_ai_path_is_used_and_pass_fail_counts_are_derived_not_trusted(monkeypatch):
    monkeypatch.setattr(testing_agent, "get_active_provider", lambda: "anthropic")
    canned = {
        "test_plan": "Verify password reset flow end to end.",
        "tests_to_add": [{"name": "test_valid_email", "description": "Returns 202", "area": "UNIT"}],
        "tests_executed": [
            {"name": "test_valid_email", "result": "pass", "notes": "Assessed from the diff, not actually run."},
            {"name": "test_unknown_email", "result": "FAIL", "notes": "Diff doesn't handle the 404 case yet."},
        ],
        "bugs_found": ["Missing 404 handling for unknown emails."],
        "suggested_fixes": ["Add an explicit lookup + 404 branch before sending the reset email."],
    }
    monkeypatch.setattr(testing_agent, "generate_raw_text", lambda **kwargs: json.dumps(canned))

    result = run_testing_agent(
        task=_FakeTask(), agent_type=TestAgentType.UNIT, diff_text="diff", lld_summary="", existing_test_paths=[], pattern_chunks=[]
    )

    assert result.used_mock is False
    assert result.pass_count == 1  # "pass" lowercased is normalized to PASS
    assert result.fail_count == 1
    assert result.bugs_found == ["Missing 404 handling for unknown emails."]


def test_real_ai_unparseable_result_value_is_treated_as_fail_not_dropped(monkeypatch):
    monkeypatch.setattr(testing_agent, "get_active_provider", lambda: "anthropic")
    canned = {
        "test_plan": "x", "tests_to_add": [],
        "tests_executed": [{"name": "test_x", "result": "MAYBE", "notes": "unclear"}],
        "bugs_found": [], "suggested_fixes": [],
    }
    monkeypatch.setattr(testing_agent, "generate_raw_text", lambda **kwargs: json.dumps(canned))

    result = run_testing_agent(
        task=_FakeTask(), agent_type=TestAgentType.UNIT, diff_text="diff", lld_summary="", existing_test_paths=[], pattern_chunks=[]
    )

    assert len(result.tests_executed) == 1
    assert result.tests_executed[0].result == "FAIL"  # conservative, never silently dropped


def test_real_ai_failure_falls_back_to_heuristic(monkeypatch):
    monkeypatch.setattr(testing_agent, "get_active_provider", lambda: "anthropic")

    def _boom(**kwargs):
        raise testing_agent.AIGenerationError("provider outage")

    monkeypatch.setattr(testing_agent, "generate_raw_text", _boom)

    result = run_testing_agent(
        task=_FakeTask(), agent_type=TestAgentType.UNIT, diff_text="diff", lld_summary="", existing_test_paths=[], pattern_chunks=[]
    )
    assert result.used_mock is True


def test_real_ai_malformed_json_falls_back_to_heuristic(monkeypatch):
    monkeypatch.setattr(testing_agent, "get_active_provider", lambda: "anthropic")
    monkeypatch.setattr(testing_agent, "generate_raw_text", lambda **kwargs: "not json")

    result = run_testing_agent(
        task=_FakeTask(), agent_type=TestAgentType.UNIT, diff_text="diff", lld_summary="", existing_test_paths=[], pattern_chunks=[]
    )
    assert result.used_mock is True
