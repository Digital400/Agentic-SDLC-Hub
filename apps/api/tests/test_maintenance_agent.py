"""Unit tests for the Maintenance Agent service — see
app/services/maintenance_agent.py. Mirrors test_pr_review_agent-style
conventions: no network/provider calls, real-AI path exercised via a
monkeypatched generate_raw_text.
"""

import pytest

from app.services import maintenance_agent
from app.services.ai_generation import AIGenerationError
from app.services.maintenance_agent import (
    MAINTENANCE_REPORT_ARTIFACT_TYPE,
    _REQUIRED_SECTIONS,
    run_maintenance_agent,
)

_ARGS = dict(
    released_summary="Deployed v1.2.0 to production.",
    open_bugs=["Login button unresponsive on Safari.", "Login button unresponsive on Safari."],
    pr_history=["PR #12 (MERGED): Add login fix"],
    test_summary="3 completed test run(s): 8 passed, 1 failed in total.",
    error_logs=None,
    user_feedback=None,
)


def test_artifact_type_is_distinct_from_the_generic_nodes_output_type():
    # See app/models/maintenance_run.py's class docstring — deliberately
    # not "maintenance_log", the existing generic node's own output type.
    assert MAINTENANCE_REPORT_ARTIFACT_TYPE == "maintenance_report"


def test_heuristic_report_has_all_8_sections_in_order(monkeypatch):
    monkeypatch.setattr(maintenance_agent, "get_active_provider", lambda: "mock")

    result = run_maintenance_agent(**_ARGS)

    assert result.used_mock is True
    positions = [result.content_markdown.index(f"## {s}") for s in _REQUIRED_SECTIONS]
    assert positions == sorted(positions)


def test_heuristic_never_claims_to_have_changed_production(monkeypatch):
    monkeypatch.setattr(maintenance_agent, "get_active_provider", lambda: "mock")

    result = run_maintenance_agent(**_ARGS)

    assert "advisory only" in result.content_markdown.lower()
    assert "has been deployed" not in result.content_markdown.lower()


def test_heuristic_detects_recurring_bugs(monkeypatch):
    monkeypatch.setattr(maintenance_agent, "get_active_provider", lambda: "mock")

    result = run_maintenance_agent(**_ARGS)  # the sample bug appears twice

    recurring_section = result.content_markdown.split("## Recurring Issues")[1].split("##")[0]
    assert "Login button unresponsive on Safari." in recurring_section


def test_heuristic_is_honest_about_no_data(monkeypatch):
    monkeypatch.setattr(maintenance_agent, "get_active_provider", lambda: "mock")

    result = run_maintenance_agent(
        released_summary="", open_bugs=[], pr_history=[], test_summary="", error_logs=None, user_feedback=None,
    )

    assert "healthy" in result.content_markdown.lower()
    assert "None reported." in result.content_markdown


def test_real_agent_path_returns_raw_markdown(monkeypatch):
    monkeypatch.setattr(maintenance_agent, "get_active_provider", lambda: "anthropic")
    monkeypatch.setattr(maintenance_agent, "generate_raw_text", lambda **kw: "## Health Status\nHealthy.\n")

    result = run_maintenance_agent(**_ARGS)

    assert result.used_mock is False
    assert result.content_markdown == "## Health Status\nHealthy."


def test_real_agent_failure_falls_back_to_heuristic(monkeypatch):
    monkeypatch.setattr(maintenance_agent, "get_active_provider", lambda: "anthropic")

    def _boom(**kw):
        raise AIGenerationError("provider down")

    monkeypatch.setattr(maintenance_agent, "generate_raw_text", _boom)

    result = run_maintenance_agent(**_ARGS)

    assert result.used_mock is True
    assert "## Health Status" in result.content_markdown
