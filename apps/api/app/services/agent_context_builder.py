"""Agent Context Builder — assembles the subset of a project's Project
Engineering Setup (see app/models/project_engineering_setup.py) relevant
to one specific agent call, so "every agent gets everything" never
happens by accident.

Rules this module exists to satisfy:
  1. Do not send unnecessary setup context to every agent.
  2. Use only relevant fields per agent type.
  3. Respect token budget.
  4. Summarize long coding standard documents.
  5. Store a context snapshot with the run for audit/debugging (see
     AgentRun.engineering_setup_context_snapshot and
     ImplementationRun.engineering_setup_context_snapshot — this module
     only builds the snapshot; the callers in app/api/routes/agent_runs.py
     and app/api/routes/implementation_runs.py persist it).

Agent types (rules 1/2 — deliberately not "send the whole setup to
everyone"):
  - "hld"            — technology stack, cloud provider, database,
                        architecture standards.
  - "story_crafting"  — Jira configuration, documentation rules, story
                        format rules (i.e. coding standards generally —
                        this stage has no stack-specific need).
  - "implementation"  — technology stack (same as "hld" — a real coding
                        agent proposing file content has to match the
                        project's configured language/framework exactly,
                        not guess file extensions; this used to be
                        withheld here on the theory that "implementation"
                        only needed GitHub/build-command specifics, but in
                        practice that left the one agent actually writing
                        code with zero grounding in what language to
                        write it in), GitHub repository config (branch
                        naming pattern, PR target branch), build/test
                        commands, code runner restrictions.
  - anything else ("generic") — no stack/GitHub/Jira specifics; still
                        gets coding standards + guardrails, since those
                        are genuinely useful to any drafting agent.
Coding standards (grouped by category) and AI guardrails are included for
every agent type — the one part of the setup general enough to always be
relevant (still counted against the token budget below, and still
summarized when long).

A project with no ProjectEngineeringSetup row returns an empty context —
the exact "don't break existing projects" behavior every other
engineering-setup gate in this codebase already follows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Project, ProjectEngineeringSetup
from app.models.enums import CodingStandardCategory
from app.services.ai_generation import AIGenerationError, generate_raw_text, get_active_provider
from app.services.token_budget import estimate_tokens

logger = logging.getLogger(__name__)

AgentContextType = Literal["generic", "hld", "story_crafting", "implementation"]

# Rule 4 — a standard's content longer than this is summarized rather
# than injected verbatim. Short enough that most real standards ("use
# camelCase for variables") never trigger it; long enough that a genuine
# multi-paragraph policy document does.
_SUMMARIZE_THRESHOLD_CHARS = 600
_SUMMARY_OUTPUT_TOKEN_BUDGET = 200

_CATEGORY_HEADING: dict[CodingStandardCategory, str] = {
    CodingStandardCategory.GENERAL: "Coding Standards",
    CodingStandardCategory.ARCHITECTURE: "Architecture Rules",
    CodingStandardCategory.SECURITY: "Security Rules",
    CodingStandardCategory.TESTING: "Testing Rules",
    CodingStandardCategory.GIT: "Git Rules",
    CodingStandardCategory.DOCUMENTATION: "Documentation Rules",
}
# Stable output order — a project's own row insertion order shouldn't
# determine which rule category a model sees first.
_CATEGORY_ORDER = [
    CodingStandardCategory.ARCHITECTURE,
    CodingStandardCategory.SECURITY,
    CodingStandardCategory.TESTING,
    CodingStandardCategory.GIT,
    CodingStandardCategory.DOCUMENTATION,
    CodingStandardCategory.GENERAL,
]


@dataclass
class EngineeringSetupContext:
    """`context_text` is ready to inject as a single freeform_context
    value (or appended to a bespoke agent's own prompt); `snapshot` is
    exactly what got included, in a shape safe to persist verbatim on
    AgentRun/ImplementationRun.engineering_setup_context_snapshot for
    audit/debugging (rule 5) — never the raw setup, so a later change to
    the project's setup doesn't retroactively change what an old run
    appears to have seen."""

    context_text: str = ""
    snapshot: dict[str, Any] = field(default_factory=dict)


def _summarize_if_long(text: str) -> str:
    """Rule 4. Real-AI summary when a provider is configured; a plain
    truncation otherwise or if the real call fails — same resilience
    contract as every other real-AI/heuristic split in this codebase
    (never let a provider hiccup block a run)."""
    if len(text) <= _SUMMARIZE_THRESHOLD_CHARS:
        return text
    if get_active_provider() == "mock":
        return text[:_SUMMARIZE_THRESHOLD_CHARS].rsplit(" ", 1)[0] + "… (truncated — configure a real AI provider for an actual summary)"
    try:
        return generate_raw_text(
            system_prompt=(
                "Summarize the following coding standard in 2-4 concise sentences for inclusion in another "
                "agent's prompt context. Preserve every concrete, actionable rule (specific naming conventions, "
                "thresholds, tool/library names, commands) — do not soften or genericize a rule just to shorten "
                "it. Respond with ONLY the summary, no preamble."
            ),
            user_content=text,
            output_token_budget=_SUMMARY_OUTPUT_TOKEN_BUDGET,
        ).strip()
    except AIGenerationError as exc:
        logger.warning("Coding standard summarization failed (%s); falling back to truncation.", exc)
        return text[:_SUMMARIZE_THRESHOLD_CHARS].rsplit(" ", 1)[0] + "…"


def _coding_standards_and_guardrails_sections(setup: ProjectEngineeringSetup, snapshot: dict[str, Any]) -> list[str]:
    sections: list[str] = []

    if setup.coding_standards:
        grouped: dict[CodingStandardCategory, list[str]] = {}
        standards_snapshot = []
        for standard in setup.coding_standards:
            summarized = _summarize_if_long(standard.content)
            grouped.setdefault(standard.category, []).append(f"**{standard.title}:** {summarized}")
            standards_snapshot.append({
                "title": standard.title,
                "category": standard.category.value,
                "summarized": summarized != standard.content,
            })
        for category in _CATEGORY_ORDER:
            items = grouped.get(category)
            if items:
                sections.append(f"## {_CATEGORY_HEADING[category]}\n" + "\n".join(f"- {i}" for i in items))
        snapshot["coding_standards"] = standards_snapshot

    if setup.guardrails:
        sections.append(
            "## AI Guardrails — rules you must follow, even if they narrow what you'd otherwise do\n"
            + "\n".join(f"- {g.rule_text}" for g in setup.guardrails)
        )
        snapshot["guardrails"] = [g.rule_text for g in setup.guardrails]

    return sections


def _stack_lines(setup: ProjectEngineeringSetup) -> list[str]:
    """Shared by `_hld_sections` and `_implementation_sections` — both need
    the exact same technology-stack facts; only the heading/framing text
    around them differs per agent type (see each caller)."""
    lines = []
    if setup.application_type:
        lines.append(f"Application type: {setup.application_type}")
    if setup.primary_language:
        lines.append(f"Primary language: {setup.primary_language}")
    if setup.frontend_framework:
        lines.append(f"Frontend framework: {setup.frontend_framework}")
    if setup.backend_framework:
        lines.append(f"Backend framework: {setup.backend_framework}")
    if setup.database:
        lines.append(f"Database: {setup.database}")
    if setup.cloud_provider:
        lines.append(f"Cloud provider: {setup.cloud_provider}")
    return lines


def _stack_snapshot(setup: ProjectEngineeringSetup) -> dict[str, str | None]:
    return {
        "application_type": setup.application_type, "primary_language": setup.primary_language,
        "frontend_framework": setup.frontend_framework, "backend_framework": setup.backend_framework,
        "database": setup.database, "cloud_provider": setup.cloud_provider,
    }


def _hld_sections(setup: ProjectEngineeringSetup, snapshot: dict[str, Any]) -> list[str]:
    stack_lines = _stack_lines(setup)
    if not stack_lines:
        return []
    snapshot["technology_stack"] = _stack_snapshot(setup)
    return ["## Technology Stack — use this, don't invent a different one\n" + "\n".join(f"- {l}" for l in stack_lines)]


def _story_crafting_sections(setup: ProjectEngineeringSetup, snapshot: dict[str, Any]) -> list[str]:
    sections: list[str] = []
    if setup.jira_config is not None:
        jira_lines = [f"Setup option: {setup.jira_config.option.value.replace('_', ' ').title()}"]
        jira_snapshot: dict[str, Any] = {"option": setup.jira_config.option.value}
        if setup.jira_config.jira_project_link is not None:
            link = setup.jira_config.jira_project_link
            jira_lines.append(f"Connected Jira project: {link.jira_project_key} ({link.jira_project_name or 'name unknown'})")
            jira_snapshot["jira_project_key"] = link.jira_project_key
        sections.append(
            "## Jira Configuration — reflect this in each story's Jira Issue Type field where relevant\n"
            + "\n".join(f"- {l}" for l in jira_lines)
        )
        snapshot["jira"] = jira_snapshot
    if setup.documentation_config is not None:
        target_label = setup.documentation_config.target.value.replace("_", " ").title()
        sections.append(f"## Documentation Rules\n- This project's documentation target is: {target_label}.")
        snapshot["documentation_target"] = setup.documentation_config.target.value
    return sections


def _implementation_sections(setup: ProjectEngineeringSetup, snapshot: dict[str, Any]) -> list[str]:
    sections: list[str] = []
    stack_lines = _stack_lines(setup)
    if stack_lines:
        snapshot["technology_stack"] = _stack_snapshot(setup)
        sections.append(
            "## Technology Stack — every file you create or modify MUST match this exactly: same language, same "
            "file extensions (e.g. .ts/.tsx, not .js/.jsx, when Primary language is TypeScript), and the same "
            "framework/library conventions. Never introduce a different language, runtime, or framework than what's "
            "configured here, even for a brand-new file\n" + "\n".join(f"- {l}" for l in stack_lines)
        )
    if setup.repository_config is not None:
        rc = setup.repository_config
        sections.append(
            "## GitHub Repository Configuration\n"
            f"- Branch naming pattern to follow: `{rc.branch_naming_pattern}`\n"
            f"- Pull requests target branch: `{rc.target_branch}`"
        )
        snapshot["repository_config"] = {
            "option": rc.option.value, "branch_naming_pattern": rc.branch_naming_pattern, "target_branch": rc.target_branch,
        }
    if setup.command_config is not None:
        cc = setup.command_config
        command_lines = []
        if cc.build_command:
            command_lines.append(f"Build: `{cc.build_command}`")
        if cc.test_commands:
            command_lines.append("Test: " + ", ".join(f"`{c}`" for c in cc.test_commands))
        if cc.lint_command:
            command_lines.append(f"Lint: `{cc.lint_command}`")
        if command_lines:
            sections.append("## Build/Test Commands\n" + "\n".join(f"- {l}" for l in command_lines))
        snapshot["command_config"] = {
            "build_command": cc.build_command, "test_commands": cc.test_commands, "lint_command": cc.lint_command,
        }
    allowed = ", ".join(get_settings().CODE_RUNNER_ALLOWED_TEST_EXECUTABLES)
    sections.append(
        "## Code Runner Restrictions\n"
        f"- Only these test-command executables may run: {allowed}. Any other command is refused outright — "
        "do not propose a test_command using anything else."
    )
    snapshot["code_runner_allowed_executables"] = get_settings().CODE_RUNNER_ALLOWED_TEST_EXECUTABLES
    return sections


# node_key -> agent_type — the one place this mapping lives, shared by
# every call site that needs to turn a WorkflowNode into an AgentContextType
# (app/api/routes/agent_runs.py's generic Draft/Improve/Validate action,
# and the revision-style paths — app/services/revision_agent.py's
# reviewer "Request changes" cycle and app/services/section_improve_agent.py's
# "Improve section" — which otherwise had no engineering-setup context at
# all despite being real IMPROVE runs). Anything not named here is
# "generic": coding standards + guardrails only (rules 1/2).
_AGENT_CONTEXT_TYPE_BY_NODE_KEY: dict[str, AgentContextType] = {
    "hld": "hld",
    "story_crafting": "story_crafting",
}


def infer_agent_context_type(node_key: str) -> AgentContextType:
    return _AGENT_CONTEXT_TYPE_BY_NODE_KEY.get(node_key, "generic")


def build_engineering_setup_context(
    db: Session, *, project: Project, agent_type: AgentContextType, output_token_budget: int = 1500
) -> EngineeringSetupContext:
    """The single entry point. `agent_type` decides which stack/GitHub/
    Jira-specific sections are even considered (rules 1/2); coding
    standards and guardrails are always considered regardless of type.
    The assembled text is capped to `output_token_budget` (rule 3) —
    truncated as a last resort, never silently left over budget."""
    setup = db.query(ProjectEngineeringSetup).filter(ProjectEngineeringSetup.project_id == project.id).first()
    if setup is None:
        return EngineeringSetupContext()

    snapshot: dict[str, Any] = {"agent_type": agent_type}
    sections: list[str] = []

    if agent_type == "hld":
        sections += _hld_sections(setup, snapshot)
    elif agent_type == "story_crafting":
        sections += _story_crafting_sections(setup, snapshot)
    elif agent_type == "implementation":
        sections += _implementation_sections(setup, snapshot)

    sections += _coding_standards_and_guardrails_sections(setup, snapshot)

    if not sections:
        return EngineeringSetupContext()

    context_text = "\n\n".join(sections)
    if estimate_tokens(context_text) > output_token_budget:
        max_chars = max(0, output_token_budget * 4)  # same chars-per-token ratio as token_budget.py
        context_text = context_text[:max_chars].rstrip() + "\n\n_(engineering setup context truncated to fit the token budget)_"
        snapshot["truncated"] = True

    return EngineeringSetupContext(context_text=context_text, snapshot=snapshot)
