"""Tests for the LLD Agent prompt registration:
  - the seeded default content itself (RICH_DEFAULT_PROMPTS["lld"]),
  - the workflow template's required inputs for the `lld` stage,
  - and app/db/seed.py's `_sync_default_prompt` — the mechanism that keeps
    an already-seeded database in sync with a later content edit here,
    without silently losing history.
"""

from app.db.seed import RICH_DEFAULT_PROMPTS, _sync_default_prompt
from app.models import AgentPromptRole
from app.services.workflow_templates import load_workflow_template
from tests.conftest import make_agent_prompt

_REQUIRED_LLD_SECTIONS = [
    "Feature Overview",
    "Stories Covered",
    "API Contracts",
    "Request/Response DTOs",
    "Database Changes",
    "Business Rules",
    "Validation Rules",
    "Permission Rules",
    "Frontend Component Plan",
    "State Management Plan",
    "Error Handling",
    "Audit/Logging Requirements",
    "Test Cases",
    "Implementation Task Breakdown",
    "Risks and Assumptions",
]


# --- RICH_DEFAULT_PROMPTS["lld"] content shape --------------------------------------


def test_lld_prompt_output_format_lists_all_15_sections_in_order():
    output_format = RICH_DEFAULT_PROMPTS["lld"]["output_format"]

    positions = [output_format.index(section) for section in _REQUIRED_LLD_SECTIONS]

    assert positions == sorted(positions), "sections must appear in the documented order"


def test_lld_prompt_encodes_all_8_rules():
    system_prompt = RICH_DEFAULT_PROMPTS["lld"]["system_prompt"].lower()

    assert "use only approved artifacts" in system_prompt
    assert "do not invent missing business rules" in system_prompt
    assert "ask clarification questions" in system_prompt
    assert "do not write production code" in system_prompt
    assert "developer-ready" in system_prompt
    assert "api, database, frontend, validation, permission, error-handling, and test" in system_prompt
    assert "highlight risks and assumptions" in system_prompt
    assert "tech lead review" in system_prompt


def test_lld_validation_checklist_covers_the_new_rules():
    checklist = " ".join(RICH_DEFAULT_PROMPTS["lld"]["validation_checklist"]).lower()

    assert "only the approved" in checklist
    assert "no business rule is invented" in checklist
    assert "developer-ready" in checklist


# --- Workflow template: LLD's required inputs ---------------------------------------


def test_lld_is_not_a_project_level_template_node():
    """v0.3.0 — Low-Level Design is no longer a project-level stage; each
    story drafts its own LLD individually in its own delivery lane (see
    workflows/sdlc-workflow.json's own top-level description and
    app/services/story_lld_agent.py). RICH_DEFAULT_PROMPTS["lld"]'s
    content is still tested above since app/services/story_lld_agent.py's
    own prompt (RICH_DEFAULT_PROMPTS["story_lld"]) was designed to mirror
    its shape, but no template node named "lld" is ever materialized for
    a new project anymore."""
    template = load_workflow_template("sdlc-workflow.json")
    node_ids = {n["id"] for n in template["nodes"]}

    assert "lld" not in node_ids


# --- _sync_default_prompt: content changes create a new version, not a mutation ----


def test_sync_default_prompt_is_a_noop_when_content_already_matches(db):
    prompt = make_agent_prompt(db, stage="node_a", role=AgentPromptRole.DRAFT)
    agent = prompt.agent_definition

    _sync_default_prompt(
        db, agent=agent, role=AgentPromptRole.DRAFT, stage_key="node_a", name=prompt.name,
        system_prompt=prompt.system_prompt, output_format=prompt.output_format,
        validation_checklist=list(prompt.validation_checklist),
    )

    versions = [p.version for p in agent.prompts if p.role == AgentPromptRole.DRAFT]
    assert versions == [1]
    assert prompt.is_active is True


def test_sync_default_prompt_creates_and_activates_a_new_version_when_content_changes(db):
    prompt = make_agent_prompt(db, stage="node_a", role=AgentPromptRole.DRAFT)
    agent = prompt.agent_definition

    _sync_default_prompt(
        db, agent=agent, role=AgentPromptRole.DRAFT, stage_key="node_a", name="node_a — Draft Prompt (v2)",
        system_prompt="A completely rewritten system prompt.", output_format="Markdown, revised.",
        validation_checklist=["A new checklist item."],
    )
    db.flush()

    draft_prompts = sorted((p for p in agent.prompts if p.role == AgentPromptRole.DRAFT), key=lambda p: p.version)
    assert [p.version for p in draft_prompts] == [1, 2]
    assert draft_prompts[0].is_active is False  # history preserved, not mutated
    assert draft_prompts[0].system_prompt == prompt.system_prompt  # original content unchanged
    assert draft_prompts[1].is_active is True
    assert draft_prompts[1].system_prompt == "A completely rewritten system prompt."
