"""Phase 16: Requirement Intake v2 (PromptCompiler-driven) — see
app/services/requirement_intake_agent.py's module docstring for the full
list of behavioral differences from the legacy path this test file
verifies against.
"""

import pytest

from app.agent_runtime.enums import ExecutionState
from app.services import ai_generation
from app.services.requirement_intake_agent import (
    RequirementIntakeV2Outcome,
    build_work_packet,
    missing_required_fields,
    run_requirement_intake_agent_v2,
)


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_active_provider", lambda: "mock")


FULL_CONTEXT = {
    "business_objective": "Reduce password-reset support tickets by 50%.",
    "users": "End users who forgot their password.",
    "current_problem": "Users must email support to reset their password.",
    "success_measures": "Support ticket volume for password resets drops by half.",
}


class TestMissingRequiredFields:
    def test_returns_empty_when_all_three_required_fields_present(self):
        assert missing_required_fields(FULL_CONTEXT) == []

    def test_names_exactly_the_missing_required_fields(self):
        missing = missing_required_fields({"business_objective": "Grow revenue"})
        assert missing == ["users", "current_problem"]

    def test_treats_whitespace_only_as_missing(self):
        missing = missing_required_fields({**FULL_CONTEXT, "users": "   "})
        assert "users" in missing


class TestBuildWorkPacket:
    def test_uses_business_objective_as_the_goal(self, project):
        packet = build_work_packet(project, FULL_CONTEXT, None)
        assert packet.objective.goal == FULL_CONTEXT["business_objective"]
        assert packet.task_type.value == "REQUIREMENT_ANALYSIS"

    def test_falls_back_to_a_generic_success_definition_when_absent(self, project):
        packet = build_work_packet(project, {"business_objective": "Grow revenue"}, None)
        assert "human reviewer" in packet.objective.success_definition


class TestRunRequirementIntakeAgentV2:
    def test_short_circuits_to_clarification_with_zero_llm_calls_when_fields_missing(self, project):
        outcome: RequirementIntakeV2Outcome = run_requirement_intake_agent_v2(project, {"business_objective": "Grow revenue"}, None)
        assert outcome.needs_clarification is True
        assert outcome.execution_result.state == ExecutionState.CLARIFICATION_REQUIRED
        assert outcome.execution_result.usage.llm_calls_made == 0
        assert any("users" in q for q in outcome.clarification_questions)
        assert any("current problem" in q for q in outcome.clarification_questions)

    def test_completes_with_a_real_draft_when_all_required_fields_present(self, project):
        outcome = run_requirement_intake_agent_v2(project, FULL_CONTEXT, None)
        assert outcome.needs_clarification is False
        assert outcome.execution_result.state == ExecutionState.COMPLETED
        assert outcome.content_markdown
        assert outcome.execution_result.usage.llm_calls_made == 1
        assert outcome.repair_attempted is False

    def test_execution_result_has_a_packet_id_and_zero_repair_when_the_first_draft_is_accepted(self, project):
        outcome = run_requirement_intake_agent_v2(project, FULL_CONTEXT, None)
        assert outcome.execution_result.packet_id is not None
        assert outcome.execution_result.usage.repair_attempts_used == 0

    def test_repairs_exactly_once_on_a_too_short_draft_then_accepts_whatever_comes_back(self, project, monkeypatch):
        calls = {"n": 0}

        def fake_dispatch(system_prompt, user_content, output_token_budget):
            from app.services.ai_generation import AgentGenerationResult

            calls["n"] += 1
            if calls["n"] == 1:
                return AgentGenerationResult(content_markdown="x", needs_clarification=False, prompt_tokens=1, completion_tokens=1, total_tokens=2)
            return AgentGenerationResult(content_markdown="A real, sufficiently long repaired draft of the requirement intake document.", needs_clarification=False, prompt_tokens=1, completion_tokens=1, total_tokens=2)

        import app.services.requirement_intake_agent as ria
        monkeypatch.setattr(ria, "_dispatch", fake_dispatch)

        outcome = run_requirement_intake_agent_v2(project, FULL_CONTEXT, None)
        assert calls["n"] == 2  # exactly one repair, never more
        assert outcome.repair_attempted is True
        assert outcome.execution_result.usage.repair_attempts_used == 1
        assert outcome.execution_result.usage.llm_calls_made == 2
        assert "repaired draft" in outcome.content_markdown

    def test_accepts_a_still_short_draft_after_the_one_bounded_repair_rather_than_looping_further(self, project, monkeypatch):
        calls = {"n": 0}

        def always_short_dispatch(system_prompt, user_content, output_token_budget):
            from app.services.ai_generation import AgentGenerationResult

            calls["n"] += 1
            return AgentGenerationResult(content_markdown="short", needs_clarification=False, prompt_tokens=1, completion_tokens=1, total_tokens=2)

        import app.services.requirement_intake_agent as ria
        monkeypatch.setattr(ria, "_dispatch", always_short_dispatch)

        outcome = run_requirement_intake_agent_v2(project, FULL_CONTEXT, None)
        assert calls["n"] == 2  # bounded: exactly one repair attempt, no infinite loop
        assert outcome.execution_result.state == ExecutionState.COMPLETED  # still accepted, per "accept either way"

    def test_propagates_a_model_requested_clarification_after_dispatch(self, project, monkeypatch):
        def clarifying_dispatch(system_prompt, user_content, output_token_budget):
            from app.services.ai_generation import AgentGenerationResult

            return AgentGenerationResult(content_markdown="", needs_clarification=True, clarification_questions=["What is the target launch date?"])

        import app.services.requirement_intake_agent as ria
        monkeypatch.setattr(ria, "_dispatch", clarifying_dispatch)

        outcome = run_requirement_intake_agent_v2(project, FULL_CONTEXT, None)
        assert outcome.needs_clarification is True
        assert outcome.execution_result.state == ExecutionState.CLARIFICATION_REQUIRED
        assert outcome.clarification_questions == ["What is the target launch date?"]
