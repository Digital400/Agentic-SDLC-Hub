"""Tests for the Story LLD Agent prompt/validator registration — mirrors
tests/test_lld_agent_prompt.py's exact conventions for the project-level
lld-agent, applied to the per-story story-lld-agent:
  - RICH_DEFAULT_PROMPTS["story_lld"]'s content shape (all 15 required
    sections, in order; the 9 system-prompt rules; the checklist).
  - app/db/seed.py's _ensure_story_lld_agent / _ensure_story_lld_validator_definition
    — Add requirements 2 (agent prompt: story-lld-agent) and 3 (validator
    prompt: story-lld-validator).
"""

from app.db.seed import RICH_DEFAULT_PROMPTS, _ensure_story_lld_agent, _ensure_story_lld_validator_definition
from app.models import AgentPromptRole, ValidatorDefinition
from app.services.story_lld_agent import STORY_LLD_AGENT_KEY, STORY_LLD_SECTIONS

# --- RICH_DEFAULT_PROMPTS["story_lld"] content shape --------------------------------


def test_story_lld_prompt_output_format_lists_all_15_sections_in_order():
    output_format = RICH_DEFAULT_PROMPTS["story_lld"]["output_format"]

    assert len(STORY_LLD_SECTIONS) == 15
    positions = [output_format.index(section) for section in STORY_LLD_SECTIONS]

    assert positions == sorted(positions), "sections must appear in the documented order"


def test_story_lld_prompt_encodes_the_stated_rules():
    system_prompt = RICH_DEFAULT_PROMPTS["story_lld"]["system_prompt"].lower()

    assert "use only the approved hld" in system_prompt
    assert "do not invent missing business rules" in system_prompt
    assert "ask clarification questions" in system_prompt
    assert "do not write production code" in system_prompt
    assert "developer-ready" in system_prompt
    assert "tech lead review" in system_prompt
    # Requirement 4 — "Related HLD sections" section is explicitly instructed, not just listed.
    assert "name exactly which section(s) of the approved hld" in system_prompt


def test_story_lld_validation_checklist_covers_the_new_sections():
    checklist = " ".join(RICH_DEFAULT_PROMPTS["story_lld"]["validation_checklist"]).lower()

    assert "15 required sections" in checklist
    assert "related hld sections" in checklist
    assert "does not plan implementation tasks or write test scenarios" in checklist


# --- Add requirement 2: Agent prompt "story-lld-agent" -------------------------------


def test_ensure_story_lld_agent_creates_the_agent_and_an_active_draft_prompt(db):
    agent = _ensure_story_lld_agent(db)

    assert agent.agent_key == STORY_LLD_AGENT_KEY
    draft_prompts = [p for p in agent.prompts if p.role == AgentPromptRole.DRAFT]
    assert len(draft_prompts) == 1
    assert draft_prompts[0].is_active is True


def test_ensure_story_lld_agent_is_idempotent(db):
    first = _ensure_story_lld_agent(db)
    second = _ensure_story_lld_agent(db)

    assert first.id == second.id
    draft_prompts = [p for p in second.prompts if p.role == AgentPromptRole.DRAFT]
    assert len(draft_prompts) == 1  # no duplicate version created when content hasn't changed


# --- Add requirement 3: Validator prompt "story-lld-validator" -----------------------


def test_ensure_story_lld_validator_definition_creates_it(db):
    validator = _ensure_story_lld_validator_definition(db)

    assert validator.validator_key == "story-lld-validator"
    assert validator.stage == "story_lld"
    assert validator.criteria == RICH_DEFAULT_PROMPTS["story_lld"]["validation_checklist"]
    assert db.query(ValidatorDefinition).filter(ValidatorDefinition.validator_key == "story-lld-validator").count() == 1


def test_ensure_story_lld_validator_definition_is_idempotent(db):
    first = _ensure_story_lld_validator_definition(db)
    second = _ensure_story_lld_validator_definition(db)

    assert first.id == second.id
    assert db.query(ValidatorDefinition).filter(ValidatorDefinition.stage == "story_lld").count() == 1
