"""Unit tests for ArtifactSummaryService's mock/heuristic path — see
app/services/artifact_summary.py.

Forces the mock provider regardless of the environment's actual
ANTHROPIC_API_KEY/GEMINI_API_KEY (this repo's dev .env has a real Gemini
key configured) — these tests are exercising the deterministic heuristic
specifically, not a real network call, which would be slow, non-
deterministic, and cost real money/quota to run as part of the suite.
"""

import pytest

from app.services import artifact_summary
from app.services.artifact_summary import generate_artifact_summaries


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(artifact_summary, "get_active_provider", lambda: "mock")


def test_empty_content_returns_placeholder_summaries():
    result = generate_artifact_summaries(artifact_type="intake_summary", content_markdown="   ")

    assert result.executive_summary == "(Empty artifact.)"
    assert result.agent_context_summary == "(Empty artifact.)"
    assert result.key_decisions == []
    assert result.open_questions == []
    assert result.risks == []


def test_executive_summary_is_the_first_paragraph():
    content = "# Problem Statement\n\nCustomers cannot track their orders after checkout.\n\nMore detail here."
    result = generate_artifact_summaries(artifact_type="problem_statement", content_markdown=content)

    assert result.executive_summary == "Customers cannot track their orders after checkout."


def test_agent_context_summary_is_not_truncated_for_short_content():
    content = "Short document body."
    result = generate_artifact_summaries(artifact_type="intake_summary", content_markdown=content)

    assert result.agent_context_summary == content


def test_agent_context_summary_is_truncated_for_long_content():
    content = "word " * 1000
    result = generate_artifact_summaries(artifact_type="hld_document", content_markdown=content)

    assert len(result.agent_context_summary) < len(content)
    assert "condensed" in result.agent_context_summary


def test_key_decisions_open_questions_and_risks_are_extracted_by_keyword():
    content = (
        "# Solution Options\n\n"
        "- Decision: We will use PostgreSQL for the primary datastore.\n"
        "- Open question: Should we support multi-region deployment?\n"
        "- Risk: Vendor lock-in with the chosen managed database service.\n"
        "- This line has none of the keywords.\n"
    )
    result = generate_artifact_summaries(artifact_type="solution_options_doc", content_markdown=content)

    assert any("PostgreSQL" in d for d in result.key_decisions)
    assert any("multi-region" in q for q in result.open_questions)
    assert any("Vendor lock-in" in r for r in result.risks)


def test_no_matching_lines_yields_empty_lists():
    content = "# Plain Document\n\nJust some ordinary prose with nothing notable in it."
    result = generate_artifact_summaries(artifact_type="intake_summary", content_markdown=content)

    assert result.key_decisions == []
    assert result.open_questions == []
    assert result.risks == []
