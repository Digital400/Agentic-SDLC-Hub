"""Tests for app/services/agent_context_builder.py — the Agent Context
Builder's five rules:
  1. Do not send unnecessary setup context to every agent.
  2. Use only relevant fields per agent type.
  3. Respect token budget.
  4. Summarize long coding standard documents.
  5. Store a context snapshot with the run for audit/debugging (covered
     by the callers in test_project_engineering_setup.py and
     test_implementation_runs.py — this file only tests the builder
     itself producing a correct snapshot to hand back).
"""

from app.models import (
    CodingStandardCategory,
    DocumentationTarget,
    GithubSetupOption,
    JiraSetupOption,
    ProjectCodingStandard,
    ProjectCommandConfig,
    ProjectDocumentationConfig,
    ProjectEngineeringSetup,
    ProjectGuardrail,
    ProjectJiraConfig,
    ProjectRepositoryConfig,
)
from app.services import agent_context_builder
from app.services.agent_context_builder import build_engineering_setup_context


def _setup(db, project, actor, **overrides) -> ProjectEngineeringSetup:
    defaults = dict(
        application_type="Web Application", primary_language="TypeScript",
        frontend_framework="Next.js", backend_framework="FastAPI", database="PostgreSQL", cloud_provider="AWS",
    )
    defaults.update(overrides)
    setup = ProjectEngineeringSetup(project_id=project.id, created_by_id=actor.id, **defaults)
    db.add(setup)
    db.flush()
    return setup


def test_no_setup_returns_an_empty_context(db, project):
    result = build_engineering_setup_context(db, project=project, agent_type="hld")
    assert result.context_text == ""
    assert result.snapshot == {}


def test_generic_agent_type_never_gets_stack_fields(db, project, actor):
    setup = _setup(db, project, actor)
    db.add(ProjectCodingStandard(setup_id=setup.id, title="Naming", content="Use camelCase."))
    db.flush()

    result = build_engineering_setup_context(db, project=project, agent_type="generic")

    assert "TypeScript" not in result.context_text
    assert "Next.js" not in result.context_text
    assert "Use camelCase." in result.context_text
    assert "technology_stack" not in result.snapshot


def test_hld_agent_type_includes_the_technology_stack(db, project, actor):
    _setup(db, project, actor)

    result = build_engineering_setup_context(db, project=project, agent_type="hld")

    assert "TypeScript" in result.context_text
    assert "Next.js" in result.context_text
    assert "FastAPI" in result.context_text
    assert "PostgreSQL" in result.context_text
    assert "AWS" in result.context_text
    assert result.snapshot["technology_stack"]["primary_language"] == "TypeScript"


def test_story_crafting_includes_jira_and_documentation_but_not_stack(db, project, actor):
    setup = _setup(db, project, actor)
    db.add(ProjectJiraConfig(setup_id=setup.id, option=JiraSetupOption.CONNECT_EXISTING_PROJECT))
    db.add(ProjectDocumentationConfig(setup_id=setup.id, target=DocumentationTarget.CONFLUENCE))
    db.flush()

    result = build_engineering_setup_context(db, project=project, agent_type="story_crafting")

    assert "Connect Existing Project" in result.context_text
    assert "Confluence" in result.context_text
    assert "TypeScript" not in result.context_text
    assert result.snapshot["jira"]["option"] == "CONNECT_EXISTING_PROJECT"
    assert result.snapshot["documentation_target"] == "CONFLUENCE"


def test_implementation_includes_repo_config_and_commands_but_not_stack(db, project, actor):
    setup = _setup(db, project, actor)
    db.add(
        ProjectRepositoryConfig(
            setup_id=setup.id, option=GithubSetupOption.CONNECT_EXISTING_REPO,
            branch_naming_pattern="feature/{task}", target_branch="develop",
        )
    )
    db.add(ProjectCommandConfig(setup_id=setup.id, build_command="npm run build", test_commands=["npm test"], lint_command="npm run lint"))
    db.flush()

    result = build_engineering_setup_context(db, project=project, agent_type="implementation")

    assert "feature/{task}" in result.context_text
    assert "develop" in result.context_text
    assert "npm run build" in result.context_text
    assert "npm test" in result.context_text
    assert "Code Runner Restrictions" in result.context_text
    assert "TypeScript" not in result.context_text
    assert result.snapshot["repository_config"]["target_branch"] == "develop"
    assert result.snapshot["command_config"]["build_command"] == "npm run build"
    assert "code_runner_allowed_executables" in result.snapshot


def test_coding_standards_are_grouped_by_category(db, project, actor):
    setup = _setup(db, project, actor)
    db.add(ProjectCodingStandard(setup_id=setup.id, title="Layering", content="Keep controllers thin.", category=CodingStandardCategory.ARCHITECTURE))
    db.add(ProjectCodingStandard(setup_id=setup.id, title="Secrets", content="Never log secrets.", category=CodingStandardCategory.SECURITY))
    db.add(ProjectCodingStandard(setup_id=setup.id, title="Coverage", content="90% minimum.", category=CodingStandardCategory.TESTING))
    db.add(ProjectCodingStandard(setup_id=setup.id, title="Commits", content="Conventional commits.", category=CodingStandardCategory.GIT))
    db.add(ProjectCodingStandard(setup_id=setup.id, title="Docstrings", content="Every public function.", category=CodingStandardCategory.DOCUMENTATION))
    db.flush()

    result = build_engineering_setup_context(db, project=project, agent_type="generic")

    assert "## Architecture Rules" in result.context_text
    assert "## Security Rules" in result.context_text
    assert "## Testing Rules" in result.context_text
    assert "## Git Rules" in result.context_text
    assert "## Documentation Rules" in result.context_text
    assert len(result.snapshot["coding_standards"]) == 5


def test_ai_guardrails_are_included_for_every_agent_type(db, project, actor):
    setup = _setup(db, project, actor)
    db.add(ProjectGuardrail(setup_id=setup.id, rule_text="Never touch payment code without human review."))
    db.flush()

    for agent_type in ("generic", "hld", "story_crafting", "implementation"):
        result = build_engineering_setup_context(db, project=project, agent_type=agent_type)
        assert "Never touch payment code without human review." in result.context_text, agent_type
        assert result.snapshot["guardrails"] == ["Never touch payment code without human review."]


def test_a_long_coding_standard_is_summarized_via_heuristic_when_mock_is_active(db, project, actor, monkeypatch):
    monkeypatch.setattr(agent_context_builder, "get_active_provider", lambda: "mock")
    setup = _setup(db, project, actor)
    long_content = "This rule has a lot of detail. " * 40  # well past the summarize threshold
    db.add(ProjectCodingStandard(setup_id=setup.id, title="Verbose Rule", content=long_content))
    db.flush()

    result = build_engineering_setup_context(db, project=project, agent_type="generic")

    assert len(result.context_text) < len(long_content)
    assert result.snapshot["coding_standards"][0]["summarized"] is True


def test_a_short_coding_standard_is_never_summarized(db, project, actor, monkeypatch):
    monkeypatch.setattr(agent_context_builder, "get_active_provider", lambda: "mock")
    setup = _setup(db, project, actor)
    db.add(ProjectCodingStandard(setup_id=setup.id, title="Short Rule", content="Use camelCase."))
    db.flush()

    result = build_engineering_setup_context(db, project=project, agent_type="generic")

    assert "Use camelCase." in result.context_text
    assert result.snapshot["coding_standards"][0]["summarized"] is False


def test_real_ai_summarization_is_used_when_a_real_provider_is_active(db, project, actor, monkeypatch):
    monkeypatch.setattr(agent_context_builder, "get_active_provider", lambda: "anthropic")
    monkeypatch.setattr(agent_context_builder, "generate_raw_text", lambda **kwargs: "A short real summary.")
    setup = _setup(db, project, actor)
    long_content = "This rule has a lot of detail. " * 40
    db.add(ProjectCodingStandard(setup_id=setup.id, title="Verbose Rule", content=long_content))
    db.flush()

    result = build_engineering_setup_context(db, project=project, agent_type="generic")

    assert "A short real summary." in result.context_text
    assert "This rule has a lot of detail." not in result.context_text


def test_real_ai_summarization_failure_falls_back_to_truncation(db, project, actor, monkeypatch):
    from app.services.ai_generation import AIGenerationError

    monkeypatch.setattr(agent_context_builder, "get_active_provider", lambda: "anthropic")

    def _boom(**kwargs):
        raise AIGenerationError("provider outage")

    monkeypatch.setattr(agent_context_builder, "generate_raw_text", _boom)
    setup = _setup(db, project, actor)
    long_content = "This rule has a lot of detail. " * 40
    db.add(ProjectCodingStandard(setup_id=setup.id, title="Verbose Rule", content=long_content))
    db.flush()

    result = build_engineering_setup_context(db, project=project, agent_type="generic")

    assert len(result.context_text) < len(long_content)
    assert result.snapshot["coding_standards"][0]["summarized"] is True


def test_token_budget_is_respected(db, project, actor, monkeypatch):
    monkeypatch.setattr(agent_context_builder, "get_active_provider", lambda: "mock")
    setup = _setup(db, project, actor)
    for i in range(20):
        db.add(ProjectGuardrail(setup_id=setup.id, rule_text=f"Guardrail number {i} with some real substance to it so it isn't trivially short."))
    db.flush()

    result = build_engineering_setup_context(db, project=project, agent_type="generic", output_token_budget=50)

    # ~4 chars/token (see token_budget.py) + the truncation notice appended.
    assert len(result.context_text) <= 50 * 4 + 200
    assert result.snapshot["truncated"] is True


def test_output_within_budget_is_not_marked_truncated(db, project, actor):
    setup = _setup(db, project, actor)
    db.add(ProjectGuardrail(setup_id=setup.id, rule_text="A short guardrail."))
    db.flush()

    result = build_engineering_setup_context(db, project=project, agent_type="generic", output_token_budget=1500)

    assert "truncated" not in result.snapshot
