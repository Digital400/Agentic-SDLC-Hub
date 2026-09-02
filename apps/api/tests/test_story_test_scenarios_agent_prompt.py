"""Tests for the Story Test Scenario Agent prompt registration — mirrors
tests/test_story_implementation_plan_agent_prompt.py's exact conventions,
applied to story-test-scenarios-agent.
"""

from app.db.seed import RICH_DEFAULT_PROMPTS, _ensure_story_test_scenarios_agent
from app.models import AgentPromptRole
from app.services.story_test_scenarios_agent import STORY_TEST_SCENARIOS_AGENT_KEY, STORY_TEST_SCENARIOS_SECTIONS


def test_story_test_scenarios_prompt_output_format_lists_all_10_sections_in_order():
    output_format = RICH_DEFAULT_PROMPTS["story_test_scenarios"]["output_format"]

    assert len(STORY_TEST_SCENARIOS_SECTIONS) == 10
    positions = [output_format.index(section) for section in STORY_TEST_SCENARIOS_SECTIONS]

    assert positions == sorted(positions), "sections must appear in the documented order"


def test_story_test_scenarios_prompt_encodes_the_stated_rules():
    system_prompt = RICH_DEFAULT_PROMPTS["story_test_scenarios"]["system_prompt"].lower()

    assert "do not include any other story" in system_prompt
    assert "base every scenario on the story, the story lld, and the implementation plan" in system_prompt
    assert "ask clarification questions" in system_prompt
    assert "qa or tech lead review" in system_prompt


def test_story_test_scenarios_validation_checklist_covers_the_rules():
    checklist = " ".join(RICH_DEFAULT_PROMPTS["story_test_scenarios"]["validation_checklist"]).lower()

    assert "10 required sections" in checklist
    assert "does not include any other story" in checklist
    assert "ui test scenarios present only if frontend is affected" in checklist


def test_ensure_story_test_scenarios_agent_creates_the_agent_and_an_active_draft_prompt(db):
    agent = _ensure_story_test_scenarios_agent(db)

    assert agent.agent_key == STORY_TEST_SCENARIOS_AGENT_KEY
    draft_prompts = [p for p in agent.prompts if p.role == AgentPromptRole.DRAFT]
    assert len(draft_prompts) == 1
    assert draft_prompts[0].is_active is True


def test_ensure_story_test_scenarios_agent_is_idempotent(db):
    first = _ensure_story_test_scenarios_agent(db)
    second = _ensure_story_test_scenarios_agent(db)

    assert first.id == second.id
    draft_prompts = [p for p in second.prompts if p.role == AgentPromptRole.DRAFT]
    assert len(draft_prompts) == 1
