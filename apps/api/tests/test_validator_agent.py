"""Unit tests for ValidatorAgentService's mock/heuristic path — see
app/services/validator_agent.py.

Forces the mock provider regardless of the environment's actual
ANTHROPIC_API_KEY/GEMINI_API_KEY (this repo's dev .env has a real Gemini
key configured) — see tests/test_artifact_summary.py for why.
"""

import pytest

from app.services import validator_agent
from app.services.validator_agent import run_validator


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(validator_agent, "get_active_provider", lambda: "mock")


def test_empty_draft_is_rejected_with_a_critical_issue():
    result = run_validator(validator=None, stage_name="Requirement Intake", content_markdown="   ")

    assert result.quality_score == 0.0
    assert result.approval_recommendation == "REJECT"
    assert result.has_critical_issues is True
    assert result.suggestions  # something actionable, not just a bare rejection


def test_substantial_draft_meeting_criteria_is_approved():
    content = (
        "# Problem Statement\n\n"
        + ("This document explains the customer visibility problem in detail. " * 15)
        + "\n\n## Risks\nA key risk is vendor lock-in; this is a known constraint and assumption.\n"
    )
    result = run_validator(
        validator=None, stage_name="Problem Discovery",
        content_markdown=content,
    )

    assert result.quality_score > 0.5
    assert result.completeness_score > 0.0
    assert result.clarity_score > 0.0
    assert result.risk_coverage_score > 0.0
    assert result.approval_recommendation in ("APPROVE", "REVISE")


def test_missing_criteria_are_reported_as_suggestions_and_reflected_in_completeness():
    class _FakeValidator:
        criteria = ["Includes a rollback plan", "States the deployment window"]

    thin_content = "word " * 130  # long enough to pass the length floor, but says nothing about the criteria

    result = run_validator(validator=_FakeValidator(), stage_name="Release", content_markdown=thin_content)

    assert result.completeness_score < 1.0
    assert any("rollback plan" in s for s in result.suggestions)
    assert any("deployment window" in s for s in result.suggestions)


def test_approval_recommendation_matches_quality_and_critical_issues():
    high_quality = "# Doc\n\n" + ("Solid detailed content covering the requirement fully. " * 20) + "\nRisk: none identified beyond the usual assumption."
    result = run_validator(validator=None, stage_name="Stage", content_markdown=high_quality)
    assert result.approval_recommendation != "REJECT"

    low_quality_but_not_empty = "word " * 20  # above the critical floor, but far short of target length
    result2 = run_validator(validator=None, stage_name="Stage", content_markdown=low_quality_but_not_empty)
    assert result2.approval_recommendation in ("REVISE", "REJECT")


# --- missing_details / risks / recommendation (see the LLD validator's contract in
# packages/prompts/validators/lld-validator.md — these fields are shared platform-wide) ----


def test_missing_details_lists_the_same_unmet_criteria_as_the_suggestions():
    class _FakeValidator:
        criteria = ["Includes a rollback plan", "States the deployment window"]

    thin_content = "word " * 130

    result = run_validator(validator=_FakeValidator(), stage_name="Release", content_markdown=thin_content)

    assert "Includes a rollback plan" in result.missing_details
    assert "States the deployment window" in result.missing_details


def test_missing_details_is_empty_when_every_criterion_is_covered():
    class _FakeValidator:
        criteria = ["Mentions the customer visibility problem"]

    content = "# Problem Statement\n\n" + ("This document explains the customer visibility problem in detail. " * 15)

    result = run_validator(validator=_FakeValidator(), stage_name="Problem Discovery", content_markdown=content)

    assert result.missing_details == []


def test_recommendation_is_ready_for_review_only_when_approval_recommendation_is_approve():
    approved = "# Doc\n\n" + ("Solid detailed content covering the requirement fully. " * 20) + "\nRisk: none identified beyond the usual assumption."
    result = run_validator(validator=None, stage_name="Stage", content_markdown=approved)
    if result.approval_recommendation == "APPROVE":
        assert result.recommendation == "READY_FOR_REVIEW"

    rejected = run_validator(validator=None, stage_name="Stage", content_markdown="")
    assert rejected.approval_recommendation == "REJECT"
    assert rejected.recommendation == "NEEDS_IMPROVEMENT"


def test_to_dict_includes_the_new_fields():
    result = run_validator(validator=None, stage_name="Stage", content_markdown="")

    payload = result.to_dict()

    assert "missing_details" in payload
    assert "risks" in payload
    assert payload["recommendation"] in ("READY_FOR_REVIEW", "NEEDS_IMPROVEMENT")


# --- Deterministic review-time check (story backlogs only) -----------------------------
#
# See app/services/review_time.py — this closes the real gap that let an
# oversized story through the in-app Draft/Improve loop with nothing
# checking its own self-reported "5 / 15 / 30" against anything: before
# this, review_time_problems() was only ever called for a repository-
# synced backlog (stage_document_sync.py), never for a draft produced by
# this app's own loop_engine.py.


def _story_block(title: str, review_time: str) -> str:
    return f"## Story: {title}\n\n**Estimated PR Review Time:** {review_time}\n**Priority:** High\n"


def test_an_oversized_story_becomes_a_critical_issue_even_on_an_otherwise_clean_draft():
    content = (
        "# Backlog\n\n"
        + _story_block("Small story", "5 / 15 / 30")
        + _story_block("Huge story", "10 / 30 / 90")
    )
    result = run_validator(validator=None, stage_name="Story Crafting", content_markdown=content)

    assert any("Huge story" in issue and "90 minutes" in issue for issue in result.critical_issues)
    assert not any("Small story" in issue for issue in result.critical_issues)


def test_an_oversized_story_forces_revise_even_when_the_rest_of_the_draft_would_approve():
    content = (
        "# Backlog\n\n" + ("Solid, thorough backlog content describing the work clearly. " * 20) + "\n\n"
        + _story_block("Oversized story", "15 / 45 / 90")
    )
    result = run_validator(validator=None, stage_name="Story Crafting", content_markdown=content)

    assert result.approval_recommendation != "APPROVE"
    assert result.has_critical_issues is True


def test_every_story_within_budget_adds_no_review_time_issues():
    content = "# Backlog\n\n" + _story_block("Story A", "5 / 15 / 30") + _story_block("Story B", "5 / 10 / 20")
    result = run_validator(validator=None, stage_name="Story Crafting", content_markdown=content)

    assert not any("minute" in issue for issue in result.critical_issues)


def test_non_backlog_content_is_never_affected_by_the_review_time_check():
    """A document with no '## Story:' headings at all (every other stage)
    must never be touched by this — confirmed by using the word "Story"
    in prose, which a naive check might mis-trigger on."""
    content = "# HLD\n\n" + ("This document is the story of how we designed the system. " * 10)
    result = run_validator(validator=None, stage_name="HLD", content_markdown=content)

    assert not any("minute" in issue for issue in result.critical_issues)
