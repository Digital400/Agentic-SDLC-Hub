"""Tests for the Story Implementation Plan Agent prompt registration —
mirrors tests/test_story_lld_agent_prompt.py's exact conventions for
story-lld-agent, applied to story-implementation-plan-agent.
"""

from app.db.seed import RICH_DEFAULT_PROMPTS, _ensure_story_implementation_plan_agent
from app.models import AgentPromptRole
from app.services.story_implementation_plan_agent import (
    STORY_IMPLEMENTATION_PLAN_AGENT_KEY,
    STORY_IMPLEMENTATION_PLAN_SECTIONS,
)


def test_story_implementation_plan_prompt_output_format_lists_all_12_sections_in_order():
    output_format = RICH_DEFAULT_PROMPTS["story_implementation_plan"]["output_format"]

    assert len(STORY_IMPLEMENTATION_PLAN_SECTIONS) == 12
    positions = [output_format.index(section) for section in STORY_IMPLEMENTATION_PLAN_SECTIONS]

    assert positions == sorted(positions), "sections must appear in the documented order"


def test_story_implementation_plan_prompt_encodes_the_stated_rules():
    system_prompt = RICH_DEFAULT_PROMPTS["story_implementation_plan"]["system_prompt"].lower()

    assert "do not include any other story" in system_prompt
    assert "do not generate code" in system_prompt
    assert "ask clarification questions" in system_prompt
    assert "either approval or" in system_prompt  # rule 4 — approved OR accepted by assignee


def test_story_implementation_plan_validation_checklist_covers_the_rules():
    checklist = " ".join(RICH_DEFAULT_PROMPTS["story_implementation_plan"]["validation_checklist"]).lower()

    assert "12 required sections" in checklist
    assert "does not include any other story" in checklist
    assert "contains no real code" in checklist


def test_ensure_story_implementation_plan_agent_creates_the_agent_and_an_active_draft_prompt(db):
    agent = _ensure_story_implementation_plan_agent(db)

    assert agent.agent_key == STORY_IMPLEMENTATION_PLAN_AGENT_KEY
    draft_prompts = [p for p in agent.prompts if p.role == AgentPromptRole.DRAFT]
    assert len(draft_prompts) == 1
    assert draft_prompts[0].is_active is True


def test_ensure_story_implementation_plan_agent_is_idempotent(db):
    first = _ensure_story_implementation_plan_agent(db)
    second = _ensure_story_implementation_plan_agent(db)

    assert first.id == second.id
    draft_prompts = [p for p in second.prompts if p.role == AgentPromptRole.DRAFT]
    assert len(draft_prompts) == 1
