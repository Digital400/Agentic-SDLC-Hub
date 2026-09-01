"""Tests for PRReviewAgentService (app/services/pr_review_agent.py):
  2. Inputs (PR title/body, diff, LLD summary, story, standards, test
     results) shape the output.
  3/4. Output shape: recommendation, severity-grouped findings, missing
     tests, suggested comments, risk score.
  Honesty: heuristic never returns APPROVE. Real-AI path + invalid-
  recommendation coercion + exception-tuple fallback.
"""

import json

import pytest

from app.models import ImplementationTaskArea, ImplementationTaskRiskLevel
from app.services import pr_review_agent
from app.services.pr_review_agent import run_pr_review_agent


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
    monkeypatch.setattr(pr_review_agent, "get_active_provider", lambda: "mock")


def _run(**overrides):
    defaults = dict(
        task=_FakeTask(), pr_title="Add password reset", pr_body="Implements the reset flow.",
        diff_text="def reset(): ...\n", lld_summary="Emails a signed reset token.", story=None,
        standards_chunks=[], test_summary="", test_fail_count=None, test_bugs_found=[],
    )
    defaults.update(overrides)
    return run_pr_review_agent(**defaults)


# --- Heuristic path ---------------------------------------------------------------------


def test_heuristic_never_returns_approve():
    result = _run()
    assert result.overall_recommendation != "APPROVE"


def test_heuristic_flags_a_todo_scaffold_as_critical_and_requests_changes():
    result = _run(diff_text='+# TODO(implementation-agent): implement "Add password reset endpoint"\n')

    assert result.overall_recommendation == "REQUEST_CHANGES"
    assert result.critical_findings
    assert result.risk_score > 0


def test_heuristic_flags_failing_tests_as_critical():
    result = _run(test_fail_count=2, test_bugs_found=["404 case not handled"])

    assert result.overall_recommendation == "REQUEST_CHANGES"
    assert any("2 test" in f.detail for f in result.critical_findings)
    assert any("404 case not handled" in f.detail for f in result.major_findings)


def test_heuristic_missing_tests_covers_every_acceptance_criterion():
    task = _FakeTask()
    result = _run(task=task)
    assert len(result.missing_tests) == len(task.acceptance_criteria)


def test_heuristic_output_has_all_required_fields():
    result = _run()
    assert result.summary.strip()
    assert result.final_reviewer_note.strip()
    assert "final approval" in result.final_reviewer_note.lower()
    assert isinstance(result.risk_score, int)


# --- Real-AI path + fallback -----------------------------------------------------------


def test_real_ai_path_is_used_and_parsed(monkeypatch):
    monkeypatch.setattr(pr_review_agent, "get_active_provider", lambda: "anthropic")
    canned = {
        "overall_recommendation": "approve",
        "summary": "Looks solid.",
        "critical_findings": [], "major_findings": [{"file": "auth.py", "detail": "No rate limiting."}], "minor_findings": [],
        "missing_tests": ["404 path"], "suggested_comments": [{"file": "auth.py", "body": "Add rate limiting."}],
        "risk_score": 35, "final_reviewer_note": "Human reviewer has final authority; not merged.",
    }
    monkeypatch.setattr(pr_review_agent, "generate_raw_text", lambda **kwargs: json.dumps(canned))

    result = _run()

    assert result.used_mock is False
    assert result.overall_recommendation == "APPROVE"  # normalized uppercase
    assert result.major_findings[0].file == "auth.py"
    assert result.risk_score == 35
    assert result.suggested_comments[0].body == "Add rate limiting."


def test_real_ai_invalid_recommendation_is_coerced_to_comment_only_not_approve(monkeypatch):
    monkeypatch.setattr(pr_review_agent, "get_active_provider", lambda: "anthropic")
    canned = {"overall_recommendation": "LGTM", "summary": "", "critical_findings": [], "major_findings": [], "minor_findings": [], "missing_tests": [], "suggested_comments": [], "risk_score": 0, "final_reviewer_note": ""}
    monkeypatch.setattr(pr_review_agent, "generate_raw_text", lambda **kwargs: json.dumps(canned))

    result = _run()

    assert result.overall_recommendation == "COMMENT_ONLY"


def test_real_ai_out_of_range_risk_score_is_clamped(monkeypatch):
    monkeypatch.setattr(pr_review_agent, "get_active_provider", lambda: "anthropic")
    canned = {"overall_recommendation": "COMMENT_ONLY", "summary": "", "critical_findings": [], "major_findings": [], "minor_findings": [], "missing_tests": [], "suggested_comments": [], "risk_score": 500, "final_reviewer_note": ""}
    monkeypatch.setattr(pr_review_agent, "generate_raw_text", lambda **kwargs: json.dumps(canned))

    result = _run()

    assert result.risk_score == 100


def test_real_ai_failure_falls_back_to_heuristic(monkeypatch):
    monkeypatch.setattr(pr_review_agent, "get_active_provider", lambda: "anthropic")

    def _boom(**kwargs):
        raise pr_review_agent.AIGenerationError("provider outage")

    monkeypatch.setattr(pr_review_agent, "generate_raw_text", _boom)

    result = _run()
    assert result.used_mock is True


def test_real_ai_malformed_json_falls_back_to_heuristic(monkeypatch):
    monkeypatch.setattr(pr_review_agent, "get_active_provider", lambda: "anthropic")
    monkeypatch.setattr(pr_review_agent, "generate_raw_text", lambda **kwargs: "not json")

    result = _run()
    assert result.used_mock is True
