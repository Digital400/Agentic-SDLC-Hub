"""Long freeform input is condensed (never silently cut) — see
app/services/intake_text_condenser.py."""

import pytest
from pydantic import ValidationError

from app.schemas.agent_run import MAX_INPUT_CHARS, AgentRunCreate
from app.services.intake_text_condenser import (
    condense_freeform_context,
    extractive_summary,
    split_into_parts,
)
from app.services.token_budget import estimate_tokens


def _big_request(paragraphs: int = 400) -> str:
    return "\n\n".join(
        f"Section {i}. The marketing team wants feature {i} delivered. It must launch by Q3 2026 with a budget of $50,000. "
        "Some background filler that adds little value follows here and repeats itself again and again."
        for i in range(paragraphs)
    )


def test_short_input_is_returned_unchanged():
    context = {"stakeholder_request": "We need a loyalty program by Q3."}
    result, report = condense_freeform_context(context, context_token_budget=8000)
    assert result is context and report.condensed is False


def test_long_input_is_condensed_within_budget_and_reported(monkeypatch):
    monkeypatch.setattr("app.services.intake_text_condenser.get_active_provider", lambda: "mock")
    text = _big_request()
    assert estimate_tokens(text) > 4000

    result, report = condense_freeform_context({"stakeholder_request": text}, context_token_budget=8000)

    assert report.condensed and report.fields == ["stakeholder_request"]
    assert report.final_tokens <= report.budget_tokens
    assert report.parts >= 2 and report.extractive_summaries >= 2
    assert "[Part 1 of" in result["stakeholder_request"]
    # requirement signals survive
    assert "Q3 2026" in result["stakeholder_request"] and "$50,000" in result["stakeholder_request"]


def test_input_dict_is_not_mutated(monkeypatch):
    monkeypatch.setattr("app.services.intake_text_condenser.get_active_provider", lambda: "mock")
    original = {"stakeholder_request": _big_request(), "mode": "VERTICAL"}
    snapshot = dict(original)
    result, _ = condense_freeform_context(original, context_token_budget=8000)
    assert original == snapshot and result["mode"] == "VERTICAL"


def test_provider_failure_falls_back_to_extractive_per_part(monkeypatch):
    from app.services import intake_text_condenser as mod
    from app.services.ai_generation import AIGenerationError

    monkeypatch.setattr(mod, "get_active_provider", lambda: "anthropic")

    def boom(**kwargs):
        raise AIGenerationError("down")

    monkeypatch.setattr(mod, "generate_raw_text", boom)
    _, report = condense_freeform_context({"r": _big_request()}, context_token_budget=8000)
    assert report.condensed and report.provider_summaries == 0 and report.extractive_summaries >= 2


def test_provider_summaries_are_used_and_counted(monkeypatch):
    from app.services import intake_text_condenser as mod

    monkeypatch.setattr(mod, "get_active_provider", lambda: "anthropic")
    monkeypatch.setattr(mod, "generate_raw_text", lambda **kw: "- key fact")
    result, report = condense_freeform_context({"r": _big_request()}, context_token_budget=8000)
    assert report.provider_summaries >= 2 and report.extractive_summaries == 0
    assert "- key fact" in result["r"]


def test_absurdly_large_input_is_flagged_as_hard_truncated():
    huge = "word " * 100_000
    result, report = condense_freeform_context(
        {"r": huge}, context_token_budget=200, summarize=lambda text, target: (text, False)
    )
    assert report.hard_truncated and "truncated" in result["r"]


def test_split_never_exceeds_max_and_keeps_all_text():
    text = _big_request(50)
    parts = split_into_parts(text, max_chars=3000)
    assert all(len(p) <= 3000 for p in parts)
    assert "".join(p.replace("\n", "").replace(" ", "") for p in parts) == text.replace("\n", "").replace(" ", "")


def test_extractive_summary_keeps_signal_sentences():
    text = "Intro line here.\nFiller filler filler.\nThe deadline is 30 June.\nMore filler."
    summary = extractive_summary(text, 100)
    assert "deadline is 30 June" in summary


def test_request_over_hard_limit_is_rejected_with_clear_message():
    kwargs = dict(project_id="00000000-0000-0000-0000-000000000001", workflow_node_id="00000000-0000-0000-0000-000000000002",
                  triggered_by_user_id="00000000-0000-0000-0000-000000000003")
    AgentRunCreate(**kwargs, input_context={"r": "x" * MAX_INPUT_CHARS})
    with pytest.raises(ValidationError) as exc:
        AgentRunCreate(**kwargs, input_context={"r": "x" * (MAX_INPUT_CHARS + 1)})
    assert "too long" in str(exc.value)


# --- end to end through start_agent_run ---------------------------------------------------


def test_run_with_long_input_generates_from_condensed_text_and_records_report(db, project, actor, monkeypatch):
    import app.api.routes.agent_runs as agent_runs
    from app.models import AgentPromptRole, WorkflowStatus
    from app.services.ai_generation import AgentGenerationResult
    from tests.conftest import make_agent_prompt, make_node

    monkeypatch.setattr("app.services.intake_text_condenser.get_active_provider", lambda: "mock")
    from tests.test_agent_run_improve_current_draft import _setup

    node, _artifact = _setup(db, project, actor)  # IMPROVE goes straight to generate() (DRAFT uses the loop engine)
    original = _big_request()
    seen = {}

    def fake_generate(**kwargs):
        seen.update(kwargs)
        return AgentGenerationResult(
            content_markdown="## Summary", needs_clarification=False, prompt_tokens=1, completion_tokens=1,
            total_tokens=2, cost=0.0, used_mock=True, estimated_context_tokens=10, token_budget_report={"x": 1},
        )

    monkeypatch.setattr(agent_runs, "generate", fake_generate)
    monkeypatch.setattr(agent_runs, "retrieve_relevant_chunks", lambda *a, **kw: [])

    payload = AgentRunCreate(
        project_id=project.id, workflow_node_id=node.id, action=AgentPromptRole.IMPROVE, triggered_by_user_id=actor.id,
        input_context={"stakeholder_request": original},
    )
    run = agent_runs.start_agent_run(payload, db)

    assert run.status.value == "COMPLETED", run.error_message
    sent = seen["freeform_context"]["stakeholder_request"]
    assert sent != original and estimate_tokens(sent) < estimate_tokens(original)
    assert run.input_context["stakeholder_request"] == original  # the original is kept on the run
    assert run.token_budget_report["input_condensation"]["condensed"] is True
    assert run.token_budget_report["x"] == 1
