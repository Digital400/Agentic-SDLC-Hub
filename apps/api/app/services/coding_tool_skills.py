"""Skill packs for developers who prefer their own AI coding tool (Claude Code,
Codex, OpenCode, Cursor) over this app's built-in agents.

The app generates a small set of files for a chosen tool and stage; they are
committed to the project's own GitHub repository (see
app/api/routes/coding_tools.py). The developer then opens the repo in their
tool and types a slash command (e.g. /requirement-intake). The tool writes
the stage's document to docs/sdlc/<stage>.md; the app pulls it back in
("Sync from repository"), where it goes through the normal review gates.

One tool-neutral definition (the StageSpec + the project's own prompt,
checklist and engineering setup) is rendered into each tool's NATIVE
conventions, so each tool's strengths are used rather than a lowest common
denominator:

  Claude Code  slash command with XML-tagged prompt + a PostToolUse HOOK that
               validates the document after every write (a self-correcting
               loop) + a read-only reviewer SUBAGENT for fresh-eyes review.
  Codex        AGENTS.md guidance + a custom prompt (/prompts:<name>) that
               loops on the validator script itself.
  OpenCode     a command that @-references the context file and runs the
               validator with shell injection, + a subagent reviewer.
  Cursor       a chat command + a scoped rule attached to docs/sdlc/**.

Two shapes of stage exist, both driven by the SAME classification the app's
own GraphEngineService uses (an entry in a node's required_inputs is either
another node's output_artifact_type — an upstream document — or a genuinely
freeform field):

  Freeform intake (Requirement Intake, Feature Intake): the developer types
  the raw request; the command asks up to 5 clarifying questions if it's too
  vague, same as the in-app agent.

  Derived from upstream (Existing System Context Scan, Impact Analysis,
  Feature Solution Discovery, HLD Delta, Story Crafting): there is nothing
  for a human to type. The app snapshots each required upstream artifact's
  current APPROVED content into docs/sdlc/inputs/<type>.md at install time,
  and the command's only job is to read those (never inventing what they
  don't say) and produce this stage's document. Running one of these in your
  own tool mainly helps when you want a bigger context window or your own
  model — the app's own "Draft" button already does the same derivation
  automatically, so this is a convenience, not a necessity, for these five.

Add a StageSpec (and, for a genuinely different document shape like Story
Crafting's repeated `## Story:` blocks, a `kind`) to extend to another stage.

CAVEAT (kept honest): these tools change quickly. File locations and
front-matter follow each tool's documented convention at the time of writing;
docs/sdlc/README.md, which is generated into the repo, tells the developer how
to adjust if their installed version differs.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models import (
    AgentDefinition,
    AgentPrompt,
    AgentPromptRole,
    Artifact,
    ArtifactStatus,
    Project,
    ValidatorDefinition,
    WorkflowNode,
)
from app.services.agent_context_builder import build_engineering_setup_context
from app.services.story_implementation_plan_agent import (
    STORY_IMPLEMENTATION_PLAN_AGENT_KEY,
    STORY_IMPLEMENTATION_PLAN_SECTIONS,
)
from app.services.story_lld_agent import STORY_LLD_AGENT_KEY, STORY_LLD_SECTIONS
from app.services.story_test_scenarios_agent import STORY_TEST_SCENARIOS_AGENT_KEY, STORY_TEST_SCENARIOS_SECTIONS

TOOLS = ("claude_code", "codex", "opencode", "cursor")

TOOL_LABELS = {
    "claude_code": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
    "cursor": "Cursor",
}

SDLC_DIR = "docs/sdlc"
INPUTS_DIR = f"{SDLC_DIR}/inputs"
CONTEXT_PATH = f"{SDLC_DIR}/context.md"
README_PATH = f"{SDLC_DIR}/README.md"
VALIDATOR_PATH = ".sdlc/hooks/validate-doc.mjs"

StageKind = Literal["single_document", "story_backlog"]


class SkillPackError(Exception):
    """A caller error (unknown tool/stage)."""


@dataclass(frozen=True)
class StageSpec:
    node_key: str
    slug: str  # the slash command name
    title: str
    output_path: str
    # "single_document": one document, checked by `required_headings` below.
    # "story_backlog": repeated `## Story: <title>` blocks (Story Crafting's
    # own shape — see app/services/story_export.py, reused as the real
    # validator rather than re-implemented here).
    kind: StageKind
    # Each inner tuple is one required section; any heading in it satisfies
    # it. Unused (empty) for kind="story_backlog".
    required_headings: tuple[tuple[str, ...], ...]
    min_words: int
    # Full sentence describing the freeform input this stage needs from the
    # user, e.g. "the stakeholder's raw request...". None when the stage is
    # instead derived entirely from approved upstream documents (see module
    # docstring) — in that case $ARGUMENTS is optional extra guidance only.
    input_hint: str | None
    # Short noun phrase for what $ARGUMENTS holds, used in the one-line usage
    # bullets (e.g. "stakeholder request"). None when there is no required
    # argument.
    argument_label: str | None
    fallback_checklist: tuple[str, ...]
    # True for the per-story delivery lane's own generic document stages
    # (Story LLD, Implementation Plan, Test Scenarios — see
    # app/models/story_delivery_node.py). These have no real WorkflowNode
    # row (a lane node is a StoryDeliveryNode, a different table), are
    # installed ONCE per repo like every other stage, but are then run
    # against WHICHEVER story the developer names in $ARGUMENTS — see
    # app/services/story_coding_tool_sync.py for the per-story input
    # snapshot and sync-back this needs that no other stage does.
    story_scoped: bool = False
    # AgentDefinition.agent_key to look up the prompt/checklist from directly
    # — required when story_scoped is True, since there is no WorkflowNode
    # row to read agent_key off of.
    agent_key: str | None = None
    # Filenames (under docs/sdlc/stories/<story-slug>/inputs/) this
    # story-scoped stage needs read before writing anything. Every one of
    # these gets "story-context.md" implicitly in addition — see
    # story_coding_tool_sync.py's build_story_input_snapshot.
    story_upstream_input_files: tuple[str, ...] = ()
    # Heading names (must match an entry in required_headings) where a real
    # Mermaid diagram is mandatory — see the matching agent prompt in
    # app/db/seed.py's RICH_DEFAULT_PROMPTS, which instructs embedding one.
    # WITHOUT this, that instruction lived only in prose the model can
    # (and in practice did) skip: the generated validate-doc.mjs hook only
    # ever checked headings/word-count/placeholders, so a document missing
    # every diagram still passed the hook and the self-correction loop had
    # nothing to push back on. _validator_script below turns this into a
    # real, mechanical check: the named section's body must contain a
    # ```mermaid fenced block — including as the explicit "no diagram
    # applies here" opt-out (see seed.py's own instruction to write a
    # one-line ```mermaid %% comment instead of bare prose when opting
    # out), so the check never has to parse natural-language justification.
    required_diagram_sections: tuple[str, ...] = ()


STAGE_SPECS: dict[str, StageSpec] = {
    "requirement_intake": StageSpec(
        node_key="requirement_intake",
        slug="requirement-intake",
        title="Requirement Intake",
        output_path=f"{SDLC_DIR}/requirement-intake.md",
        kind="single_document",
        required_headings=(("Stakeholder & Request",), ("Constraints",), ("Success Metric", "Open Questions")),
        min_words=60,
        input_hint="the stakeholder's raw request (typed after the command, or a path to a file that contains it)",
        argument_label="stakeholder request",
        fallback_checklist=(
            "States who the stakeholder is",
            "Captures the request in one clear sentence",
            "Lists constraints or explicitly says none are known",
            "Includes a success metric or flags it as an open question",
        ),
    ),
    "feature_intake": StageSpec(
        node_key="feature_intake",
        slug="feature-intake",
        title="Feature Intake",
        output_path=f"{SDLC_DIR}/feature-intake.md",
        kind="single_document",
        required_headings=(
            ("Summary",),
            ("Current Behavior",),
            ("Expected Behavior",),
            ("Scope",),
            ("References",),
        ),
        min_words=60,
        input_hint=(
            "the change request in the user's own words — what it is, why it's needed, current vs. expected "
            "behavior, which existing module/product and users it affects, and any known references (GitHub "
            "repository, Jira project key, Confluence link) — typed after the command, or a path to a file that "
            "contains it"
        ),
        argument_label="change request",
        fallback_checklist=(
            "States the change's title and business reason",
            "Current behavior and expected behavior are both stated, and are clearly distinguished from each other",
            "States which existing module/product and which users are affected",
            "Lists every reference field given (repo/Jira/Confluence/attachments) and says 'None given' for any missing",
            "Anything not stated by the user is listed under Open Questions rather than invented",
        ),
    ),
    "problem_discovery": StageSpec(
        node_key="problem_discovery",
        slug="problem-discovery",
        title="Problem Discovery",
        output_path=f"{SDLC_DIR}/problem-discovery.md",
        kind="single_document",
        required_headings=(
            ("Executive Summary",),
            ("Background & Context",),
            ("Target Users & Stakeholders",),
            ("Current-State Journey",),
            ("Problem Statements",),
            ("Evidence Collected",),
            ("Problem Prioritization",),
            ("Success Metrics",),
            ("Assumptions, Risks & Unknowns",),
            ("Recommended Decision",),
        ),
        min_words=120,
        input_hint=None,
        argument_label=None,
        fallback_checklist=(
            "All ten '##' sections are present, in the exact order specified, and none are renamed",
            "Background & Context, Target Users & Stakeholders, Current-State Journey, Evidence Collected, "
            "Problem Prioritization, Success Metrics, and Assumptions/Risks/Unknowns are real Markdown tables, "
            "not prose or bullet lists",
            "Nothing is proposed as a solution — every statement describes a problem, not a fix",
            "Anything not confirmed by the input is captured as an Assumption, Risk, or Unknown rather than "
            "invented as if it were fact",
            "Problem statements trace back to the approved intake summary's request",
        ),
    ),
    "solution_discovery": StageSpec(
        node_key="solution_discovery",
        slug="solution-discovery",
        title="Solution Discovery",
        output_path=f"{SDLC_DIR}/solution-discovery.md",
        kind="single_document",
        required_headings=(("Candidate Options",), ("Trade-offs",), ("Recommendation",)),
        min_words=60,
        input_hint=None,
        argument_label=None,
        fallback_checklist=(
            "At least two real alternatives are considered",
            "Trade-offs reference cost, risk, or timeline",
            "A single option is clearly recommended with rationale",
            "Recommendation directly addresses the problem statement",
        ),
    ),
    "hld": StageSpec(
        node_key="hld",
        slug="hld",
        title="High-Level Design",
        output_path=f"{SDLC_DIR}/hld.md",
        kind="single_document",
        required_headings=(
            ("Overview",),
            ("Architecture",),
            ("Technology Stack",),
            ("Data Model",),
            ("Repository/Folder Structure",),
            ("Deployment Architecture",),
            ("Security Considerations",),
            ("Non-Functional Requirements & Risks",),
            ("Open Questions",),
        ),
        min_words=200,
        input_hint=None,
        argument_label=None,
        fallback_checklist=(
            "Every major component has a stated responsibility",
            "Component interactions are described, not just listed",
            "Architecture includes a real Mermaid C4-style context diagram, or an explicit statement of why "
            "none applies",
            "Architecture includes a real Mermaid container/logical architecture diagram reflecting the actual "
            "components named, or an explicit statement of why none applies",
            "Architecture includes a real Mermaid sequenceDiagram for the primary end-to-end flow, or an "
            "explicit statement of why none applies",
            "Technology Stack is a real table naming actual technologies, not a vague prose paragraph",
            "Data Model includes a real Mermaid erDiagram covering every entity named, or an explicit statement "
            "of why none applies",
            "Repository/Folder Structure is a real directory tree in a code block, not a one-line description",
            "Deployment Architecture includes a real Mermaid diagram of the proposed environments/"
            "infrastructure, or an explicit statement of why none applies",
            "Security considerations are addressed explicitly",
            "Non-Functional Requirements & Risks is a real table, not a bare restatement of Security "
            "Considerations",
            "Unresolved decisions are listed as open questions, not silently assumed",
        ),
        required_diagram_sections=("Architecture", "Data Model", "Deployment Architecture"),
    ),
    "existing_system_context_scan": StageSpec(
        node_key="existing_system_context_scan",
        slug="existing-system-context-scan",
        title="Existing System Context Scan",
        output_path=f"{SDLC_DIR}/existing-system-context-scan.md",
        kind="single_document",
        required_headings=(
            ("Existing System Summary",),
            ("Current Modules",),
            ("Relevant Files/Folders",),
            ("Existing APIs",),
            ("Existing UI Areas",),
            ("Existing Database/Data Model Notes",),
            ("Existing Integrations",),
            ("Current Architecture Assumptions",),
            ("Missing Context/Questions",),
            ("Recommended Next Analysis Step",),
        ),
        min_words=80,
        input_hint=None,
        argument_label=None,
        fallback_checklist=(
            "All ten sections are present, in order, none renamed",
            "Confirmed facts and assumptions are clearly distinguished from each other",
            "No implementation approach, fix, or code is proposed anywhere",
            "No user stories are generated",
            "Missing Context/Questions names what's genuinely still unknown rather than leaving gaps unstated",
        ),
    ),
    "impact_analysis": StageSpec(
        node_key="impact_analysis",
        slug="impact-analysis",
        title="Impact Analysis",
        output_path=f"{SDLC_DIR}/impact-analysis.md",
        kind="single_document",
        required_headings=(
            ("Feature/Change Summary",),
            ("Affected Modules",),
            ("Affected APIs",),
            ("Affected Frontend Screens/Components",),
            ("Affected Database Tables/Entities",),
            ("Affected Integrations",),
            ("Permission/Security Impact",),
            ("Performance Impact",),
            ("Testing Impact",),
            ("Deployment/Configuration Impact",),
            ("Backward Compatibility Risks",),
            ("Data Migration Risks",),
            ("Unknowns/Questions",),
            ("Recommendation",),
        ),
        min_words=80,
        input_hint=None,
        argument_label=None,
        fallback_checklist=(
            "All fourteen sections are present, in order, none renamed",
            "Affected items are named specifically, grounded in the System Context — not generic placeholders",
            "No code is generated and no stories are created",
            "Recommendation states exactly HLD_DELTA or FULL_HLD_UPDATE, with a stated reason",
            "A high-impact change (core/shared modules, breaking changes, major data migration, or cross-cutting "
            "security/performance concerns) is recommended FULL_HLD_UPDATE, not HLD_DELTA",
        ),
    ),
    # Display name only — "Feature Solution Discovery" — matching the
    # renamed workflow node; node_key/agentKey/artifact_type all stay
    # mini_solution_discovery (renaming those would touch the agent
    # definition, seed data, and every other reference to that key).
    "mini_solution_discovery": StageSpec(
        node_key="mini_solution_discovery",
        slug="feature-solution-discovery",
        title="Feature Solution Discovery",
        output_path=f"{SDLC_DIR}/feature-solution-discovery.md",
        kind="single_document",
        required_headings=(
            ("Proposed Feature Solution",),
            ("User Workflow Changes",),
            ("Functional Requirements",),
            ("Non-Functional Requirements",),
            ("Data Requirements",),
            ("Integration Requirements",),
            ("UX Considerations",),
            ("Constraints From Existing System",),
            ("Assumptions",),
            ("Risks",),
            ("Open Questions",),
        ),
        min_words=80,
        input_hint=None,
        argument_label=None,
        fallback_checklist=(
            "All eleven sections are present, in order, none renamed",
            "The proposed solution is scoped to this one feature/change, not a rewrite of the overall product",
            "No LLD-level design decisions or code appear anywhere",
            "Constraints From Existing System references the actual System Context, not generic assumptions",
        ),
    ),
    "hld_delta": StageSpec(
        node_key="hld_delta",
        slug="hld-delta",
        title="HLD Delta",
        output_path=f"{SDLC_DIR}/hld-delta.md",
        kind="single_document",
        required_headings=(
            ("Current Architecture Context",),
            ("Proposed Architecture Change",),
            ("New/Changed Modules",),
            ("New/Changed APIs",),
            ("New/Changed Data Flow",),
            ("New/Changed Database Ownership",),
            ("Security Impact",),
            ("Performance/Scalability Impact",),
            ("Observability/Logging Impact",),
            ("Deployment/Configuration Impact",),
            ("Risks and Trade-offs",),
            ("Architecture Decision Record",),
            ("Approval Checklist",),
        ),
        min_words=100,
        input_hint=None,
        argument_label=None,
        fallback_checklist=(
            "All thirteen sections are present, in order, none renamed",
            "Describes only the architecture DELTA this change causes, not a full HLD rewrite, unless the input "
            "explicitly says the impact analysis recommended FULL_HLD_UPDATE",
            "Includes a real Architecture Decision Record (context, decision, alternatives, consequences), not "
            "just a restatement of the proposed change",
            "New/Changed Data Flow includes a real Mermaid sequenceDiagram for the changed flow, or an explicit "
            "statement that this delta has no data flow change",
            "New/Changed Database Ownership includes a real Mermaid erDiagram when tables/entities change, or "
            "an explicit statement that this delta has no database change",
            "Approval Checklist is an actual checklist, not prose",
            "Risks and Trade-offs section is present and substantive",
        ),
        required_diagram_sections=("New/Changed Data Flow", "New/Changed Database Ownership"),
    ),
    "story_crafting": StageSpec(
        node_key="story_crafting",
        slug="story-crafting",
        title="Story Crafting",
        output_path=f"{SDLC_DIR}/story-crafting.md",
        kind="story_backlog",
        required_headings=(),
        min_words=0,
        input_hint=None,
        argument_label="optional guidance (e.g. a sprint focus, or 'HORIZONTAL' for horizontal-mode stories; default VERTICAL)",
        fallback_checklist=(
            "Every story is independently trackable on its own",
            "Every dependency is stated explicitly in the Dependencies field",
            "No LLD-level design decisions or implementation code appear anywhere",
            "Every story states Epic, Feature, Mode, User Story, Business Value, Acceptance Criteria, Suggested "
            "Owner Role, Technical Areas Involved, Dependencies, Priority, Story Points Estimate, Jira Issue "
            "Type, Estimated PR Review Time, Suggested Subtasks, Release Readiness Criteria, and Definition of Done",
            "Every story's Estimated PR Review Time worst case is 30 minutes or less — split it into smaller "
            "stories instead of exceeding that",
            "Together, the stories cover the full scope of the approved upstream design",
        ),
    ),
    # --- Per-story delivery lane stages — see StageSpec.story_scoped's docstring ---------
    "story_lld": StageSpec(
        node_key="story_lld",
        slug="story-lld",
        title="Story LLD",
        output_path=f"{SDLC_DIR}/stories/<story-slug>/story-lld.md",
        kind="single_document",
        required_headings=tuple((h,) for h in STORY_LLD_SECTIONS),
        min_words=100,
        input_hint=None,
        argument_label="the story's slug or exact title (e.g. export-report-as-csv)",
        fallback_checklist=(
            f"All {len(STORY_LLD_SECTIONS)} sections are present, in order, none renamed",
            "Only references the approved HLD and this one story's own context — nothing else",
            "Does not invent business rules, validation rules, or permission rules the input doesn't state",
            "API Changes includes a real Mermaid sequenceDiagram when this story changes an API, unless the "
            "section is genuinely 'None.'",
            "Database Changes includes a real Mermaid erDiagram when this story changes the database, unless "
            "the section is genuinely 'None.'",
        ),
        story_scoped=True,
        agent_key=STORY_LLD_AGENT_KEY,
        story_upstream_input_files=("hld.md",),
        required_diagram_sections=("API Changes", "Database Changes", "Frontend Changes"),
    ),
    "story_implementation_plan": StageSpec(
        node_key="story_implementation_plan",
        slug="story-implementation-plan",
        title="Implementation Plan",
        output_path=f"{SDLC_DIR}/stories/<story-slug>/implementation-plan.md",
        kind="single_document",
        required_headings=tuple((h,) for h in STORY_IMPLEMENTATION_PLAN_SECTIONS),
        min_words=80,
        input_hint=None,
        argument_label="the story's slug or exact title (e.g. export-report-as-csv)",
        fallback_checklist=(
            f"All {len(STORY_IMPLEMENTATION_PLAN_SECTIONS)} sections are present, in order, none renamed",
            "Every task is grounded in the approved Story LLD — nothing invented beyond it",
            "No actual implementation code — tasks describe what to build, not the code itself",
        ),
        story_scoped=True,
        agent_key=STORY_IMPLEMENTATION_PLAN_AGENT_KEY,
        story_upstream_input_files=("story-lld.md",),
    ),
    "story_test_scenarios": StageSpec(
        node_key="story_test_scenarios",
        slug="story-test-scenarios",
        title="Test Scenarios",
        output_path=f"{SDLC_DIR}/stories/<story-slug>/test-scenarios.md",
        kind="single_document",
        required_headings=tuple((h,) for h in STORY_TEST_SCENARIOS_SECTIONS),
        min_words=80,
        input_hint=None,
        argument_label="the story's slug or exact title (e.g. export-report-as-csv)",
        fallback_checklist=(
            f"All {len(STORY_TEST_SCENARIOS_SECTIONS)} sections are present, in order, none renamed",
            "Every acceptance criterion is mapped to at least one test scenario",
            "No implementation code — scenarios describe what to test, not test code itself",
        ),
        story_scoped=True,
        agent_key=STORY_TEST_SCENARIOS_AGENT_KEY,
        story_upstream_input_files=("story-lld.md", "implementation-plan.md"),
    ),
}


@dataclass
class SkillFile:
    path: str
    content: str
    purpose: str
    # Managed files are owned by the app and safe to refresh on re-install;
    # a non-managed one (a file the team commonly edits, e.g. AGENTS.md) is
    # never overwritten.
    managed: bool = True


@dataclass
class SkillPack:
    tool: str
    tool_label: str
    stage: str
    files: list[SkillFile]
    usage: list[str]
    notes: list[str] = field(default_factory=list)


# --- Inputs gathered from the app -------------------------------------------------------


@dataclass
class ArtifactInput:
    """A required upstream document, snapshotted from its current APPROVED
    version so a coding tool can read it without needing its own connection
    to this app. Refreshed every time skills are (re-)installed."""

    artifact_type: str
    title: str  # the upstream stage's display name, e.g. "Feature Intake"
    path: str
    content: str | None  # None when that stage hasn't been approved yet


@dataclass
class _StageInputs:
    project: Project
    spec: StageSpec
    system_prompt: str
    output_format: str
    checklist: list[str]
    context_text: str
    # Freeform keys in this stage's required_inputs (not another node's
    # output_artifact_type) — see GraphEngineService.resolve_required_inputs,
    # whose exact classification rule this mirrors.
    freeform_keys: list[str]
    artifact_inputs: list[ArtifactInput]


def _gather_inputs(db: Session, project: Project, spec: StageSpec) -> _StageInputs:
    system_prompt, output_format, checklist = "", "", list(spec.fallback_checklist)
    freeform_keys: list[str] = []
    artifact_inputs: list[ArtifactInput] = []

    if spec.story_scoped:
        # No real WorkflowNode row exists for a per-story lane stage (see
        # StageSpec.story_scoped's docstring) — look the agent up directly
        # by its known key instead. Freeform/artifact_inputs stay empty:
        # there is no project-wide "the" story to snapshot inputs for at
        # install time — see app/services/story_coding_tool_sync.py, which
        # does that per real story once one is named.
        agent = db.query(AgentDefinition).filter(AgentDefinition.agent_key == spec.agent_key).first()
        if agent is not None:
            prompt = (
                db.query(AgentPrompt)
                .filter(AgentPrompt.agent_definition_id == agent.id, AgentPrompt.role == AgentPromptRole.DRAFT, AgentPrompt.is_active.is_(True))
                .first()
            )
            if prompt is not None:
                system_prompt = prompt.system_prompt
                output_format = prompt.output_format or ""
                checklist = list(prompt.validation_checklist or checklist)
        validator = (
            db.query(ValidatorDefinition)
            .filter(ValidatorDefinition.stage == spec.node_key, ValidatorDefinition.is_active.is_(True))
            .first()
        )
        if validator is not None and validator.criteria:
            checklist = list(dict.fromkeys([*checklist, *validator.criteria]))
        context = build_engineering_setup_context(db, project=project, agent_type="generic", output_token_budget=1500)
        return _StageInputs(project, spec, system_prompt, output_format, checklist, context.context_text or "", freeform_keys, artifact_inputs)

    node = db.query(WorkflowNode).filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == spec.node_key).first()
    if node is not None:
        agent = db.query(AgentDefinition).filter(AgentDefinition.agent_key == node.agent_key).first()
        if agent is not None:
            prompt = (
                db.query(AgentPrompt)
                .filter(AgentPrompt.agent_definition_id == agent.id, AgentPrompt.role == AgentPromptRole.DRAFT, AgentPrompt.is_active.is_(True))
                .first()
            )
            if prompt is not None:
                system_prompt = prompt.system_prompt
                output_format = prompt.output_format or ""
                checklist = list(prompt.validation_checklist or checklist)
        validator = (
            db.query(ValidatorDefinition)
            .filter(ValidatorDefinition.stage == spec.node_key, ValidatorDefinition.is_active.is_(True))
            .first()
        )
        if validator is not None and validator.criteria:
            checklist = list(dict.fromkeys([*checklist, *validator.criteria]))

        # Same classification GraphEngineService.resolve_required_inputs
        # uses: a required_inputs entry that matches some other node's
        # output_artifact_type is an upstream document; anything else is a
        # freeform field the user must type.
        node_by_artifact_type = {n.output_artifact_type: n for n in project.workflow_nodes}
        for required in node.required_inputs or []:
            upstream_node = node_by_artifact_type.get(required)
            if upstream_node is None:
                freeform_keys.append(required)
                continue
            artifact = (
                db.query(Artifact)
                .filter(Artifact.project_id == project.id, Artifact.artifact_type == required, Artifact.status == ArtifactStatus.APPROVED)
                .order_by(Artifact.updated_at.desc())
                .first()
            )
            content = artifact.current_version.content_markdown if artifact and artifact.current_version else None
            artifact_inputs.append(ArtifactInput(artifact_type=required, title=upstream_node.name, path=f"{INPUTS_DIR}/{required}.md", content=content))

    context = build_engineering_setup_context(db, project=project, agent_type="generic", output_token_budget=1500)
    return _StageInputs(project, spec, system_prompt, output_format, checklist, context.context_text or "", freeform_keys, artifact_inputs)


# --- Shared file content ----------------------------------------------------------------


def _front_matter(inputs: _StageInputs, tool: str) -> str:
    story_line = (
        "story_id: <copy the story_id value from this story's own docs/sdlc/stories/<story-slug>/inputs/story-context.md>\n"
        if inputs.spec.story_scoped
        else ""
    )
    return (
        "---\n"
        f"sdlc_stage: {inputs.spec.node_key}\n"
        f"project_id: {inputs.project.id}\n"
        f"{story_line}"
        f"project: {inputs.project.name}\n"
        "status: draft\n"
        f"generated_by: {tool}\n"
        "---"
    )


_STORY_BLOCK_TEMPLATE = """## Story: <short, specific title>

**Epic:** <the epic/theme this belongs to>
**Feature:** <the feature this belongs to>
**Mode:** VERTICAL
**User Story:** As a <role>, I want <capability>, so that <benefit>.
**Business Value:** <why this matters, in business terms>
**Acceptance Criteria:**
- [ ] <criterion>
**Suggested Owner Role:** <one of: BA, ARCHITECT, TECH_LEAD, DEVELOPER, QA, DEVOPS, PRODUCT_OWNER>
**Technical Areas Involved:** <e.g. Frontend, Backend, Database>
**Dependencies:** None.
**Priority:** <High/Medium/Low>
**Story Points Estimate:** <a plain integer, e.g. 5>
**Jira Issue Type:** <Story, Task, or Sub-task>
**Estimated PR Review Time:** <best/typical/worst-case minutes, e.g. "5 / 15 / 30" — worst case must be 30 or less; split the story instead of exceeding it>
**Suggested Subtasks:**
- [ ] <subtask>
**Release Readiness Criteria:**
- [ ] <criterion>
**Definition of Done:**
- [ ] <criterion>

<!-- Repeat this "## Story: ..." block once per story. Produce as many as the approved upstream design actually needs — don't pad or under-cover it. -->
"""


def _document_template(inputs: _StageInputs, tool: str) -> str:
    spec = inputs.spec
    if spec.kind == "story_backlog":
        return f"{_front_matter(inputs, tool)}\n# {spec.title}\n\n{_STORY_BLOCK_TEMPLATE}"

    def _placeholder(heading: str) -> str:
        # A diagram-required heading gets its own reminder right where the
        # agent is about to write it — not just once in the prose at the
        # top of the command, which in practice got skipped (see
        # required_diagram_sections's own comment on StageSpec).
        if heading in spec.required_diagram_sections:
            return "<write this section — MUST include a ```mermaid fenced diagram per the instructions above (a real one, or a one-line ```mermaid %% comment explaining why none applies)>"
        return "<write this section>"

    sections = "\n\n".join(f"## {group[0]}\n{_placeholder(group[0])}" for group in spec.required_headings)
    return f"{_front_matter(inputs, tool)}\n# {spec.title} Summary\n\n{sections}\n"


def _checklist_md(inputs: _StageInputs) -> str:
    return "\n".join(f"- {item}" for item in inputs.checklist)


def _context_file(inputs: _StageInputs) -> SkillFile:
    p = inputs.project
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    body = [
        f"# Project context — {p.name}",
        "",
        f"_Generated by Agentic SDLC Hub on {stamp}. Re-run “Add skills to repository” in the app to refresh it._",
        "",
        f"- **Business owner:** {p.business_owner}",
        f"- **Work type:** {p.work_type.value if hasattr(p.work_type, 'value') else p.work_type}",
    ]
    if p.description:
        body += [f"- **Description:** {p.description}"]
    body += ["", "## Engineering setup, coding standards and guardrails", "", inputs.context_text.strip() or "_No engineering setup has been configured for this project yet._"]
    return SkillFile(CONTEXT_PATH, "\n".join(body) + "\n", "Project context every skill reads first (stack, coding standards, guardrails).")


def _artifact_input_files(inputs: _StageInputs) -> list[SkillFile]:
    """One managed, always-refreshed snapshot per required upstream
    document — see ArtifactInput and the module docstring's "Derived from
    upstream" section. Always regenerated (never hand-edited), so these are
    `managed=True` even though most files here are the opposite."""
    files = []
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    for a in inputs.artifact_inputs:
        if a.content is not None:
            body = (
                f"---\nsource_stage: {a.artifact_type}\nstatus: approved\nsnapshotted_at: {stamp}\n---\n"
                f"# {a.title} (approved, read-only snapshot)\n\n"
                f"_This is a copy of the current approved **{a.title}** document, taken on {stamp}. It is not the "
                "live document — re-run “Add via pull request” in the app to refresh it. Treat it as ground truth; "
                "never contradict it._\n\n---\n\n{content}"
            ).format(content=a.content.strip())
            purpose = f"Read-only snapshot of the approved {a.title} — required input for this stage."
        else:
            body = (
                f"---\nsource_stage: {a.artifact_type}\nstatus: not_yet_available\nsnapshotted_at: {stamp}\n---\n"
                f"# {a.title} — not yet available\n\n"
                f"**{a.title}** has not been approved in Agentic SDLC Hub yet, so there is nothing to read here. "
                f"Draft and approve it first — in the app, or via its own coding-tool skill if one exists — then "
                "re-run “Add via pull request” here to refresh this file before using this skill."
            )
            purpose = f"Placeholder — {a.title} is not approved yet."
        files.append(SkillFile(a.path, body + "\n", purpose))
    return files


def _validator_script(inputs: _StageInputs) -> SkillFile:
    specs = {
        s.node_key: {
            "path": s.output_path,
            "kind": s.kind,
            "headings": [list(g) for g in s.required_headings],
            "minWords": s.min_words,
            "diagramSections": list(s.required_diagram_sections),
        }
        for s in STAGE_SPECS.values()
    }
    script = _VALIDATOR_TEMPLATE.replace("__SPECS__", json.dumps(specs, indent=2))
    return SkillFile(
        VALIDATOR_PATH, script,
        "Checks a stage document (headings, placeholders, length, and required Mermaid diagrams). Used by hooks and by the loop in every tool.",
    )


# docs/sdlc/README.md is ONE file shared by every stage and tool a team has
# installed — unlike every other file here, its content must never be
# regenerated from scratch for a single (tool, stage) pair, or installing a
# second stage would silently delete the first stage's instructions (a real
# regression: see readme_section_key below). Instead each install owns one
# clearly delimited section, merged into whatever the file already contains —
# see merge_readme(), called from app/api/routes/coding_tools.py, which is the
# only thing that actually knows the file's current content.

README_HEADER = """# Working on SDLC stages with your own coding tool

This repository was set up by **Agentic SDLC Hub**. Each section below covers one stage that's been wired up to a
coding tool — use only the ones your team has actually installed; the rest of this file is unaffected by any of them.

## Shared files
- `{context}` — project context (stack, coding standards, guardrails). Do not hand-edit; re-installing any
  stage's skills refreshes it.
- `{validator}` — the document checker every stage's tool runs (needs Node.js). Shared by every stage.

## What gets synced
Whatever stage document you write, it arrives in Agentic SDLC Hub as a **draft** — the app never trusts a file
blindly, and the normal review and approval steps still apply.
""".format(context=CONTEXT_PATH, validator=VALIDATOR_PATH)

_SECTION_MARKER = "<!-- sdlc-hub:coding-tool-section:{key} {edge} -->"


def readme_section_key(tool: str, node_key: str) -> str:
    """Identifies one (tool, stage) pair's own slice of the shared README, so
    installing another stage — or the same stage for another tool — updates
    only its own section and leaves every other one untouched."""
    return f"{tool}:{node_key}"


def render_readme_section(inputs: _StageInputs, tool: str, label: str, usage: list[str]) -> str:
    spec = inputs.spec
    key = readme_section_key(tool, spec.node_key)
    steps = "\n".join(f"{i}. {u}" for i, u in enumerate(usage, start=1))
    inputs_note = (
        "\n**Upstream inputs:** derived from approved upstream documents, snapshotted into "
        f"`{INPUTS_DIR}/` when these skills were added: "
        + ", ".join(f"`{a.path}`" for a in inputs.artifact_inputs)
        + ". Re-run “Add via pull request” in the app to refresh them if an upstream document changes. Running "
        "this stage in your own tool is a convenience (a bigger context window, your own model) — the app's own "
        "“Draft” button derives the same document automatically.\n"
        if inputs.artifact_inputs
        else "\n**Per-story stage:** this stage is installed once, then run against whichever story you name in "
        "$ARGUMENTS each time — see “Sync story inputs” on that story's lane in Agentic SDLC Hub, which must be "
        f"run once per story before this command can read its inputs from `{SDLC_DIR}/stories/<story-slug>/inputs/`.\n"
        if spec.story_scoped
        else ""
    )
    document_note = (
        "must keep its required sections" if spec.kind == "single_document" else "needs at least one `## Story:` block with every required field"
    )
    body = f"""## {spec.title} — {label}

{steps}
{inputs_note}
**Document:** `{spec.output_path}` — keeps its front matter (`sdlc_stage`, `project_id`) and {document_note};
checked by `node {VALIDATOR_PATH} {spec.output_path}`.

**If your {label} version differs:** these tools change quickly. If a file location or setting name has moved in
your version, keep the *contents* and move the file — the skill text does not depend on where it lives.
"""
    return f"{_SECTION_MARKER.format(key=key, edge='start')}\n{body}{_SECTION_MARKER.format(key=key, edge='end')}\n"


def merge_readme(existing: str | None, section: str, key: str) -> str:
    """Replaces this (tool, stage)'s own section in `existing` if it's there,
    appends it if it's a repo that already has other stages' sections but not
    this one, or starts a fresh file (with the shared header) if there's no
    README yet at all. Every other section is passed through byte-for-byte."""

    pattern = re.compile(
        re.escape(_SECTION_MARKER.format(key=key, edge="start")) + r".*?" + re.escape(_SECTION_MARKER.format(key=key, edge="end")) + r"\n?",
        re.DOTALL,
    )
    if not existing or not existing.strip():
        return f"{README_HEADER}\n{section}"
    if pattern.search(existing):
        return pattern.sub(section, existing)
    return existing.rstrip("\n") + "\n\n" + section


def _readme(inputs: _StageInputs, tool: str, label: str, usage: list[str]) -> SkillFile:
    """The SkillFile's `.content` is this stage's OWN section only — never the
    whole merged file, which app/api/routes/coding_tools.py assembles for real
    against the repository's current README (see merge_readme above). A
    preview (before install) shows just this section for the same reason."""
    section = render_readme_section(inputs, tool, label, usage)
    return SkillFile(README_PATH, section, f"Adds/refreshes this stage's own section of the shared {README_PATH} (other stages' sections are kept).")


def _procedure(inputs: _StageInputs, *, tool: str, loop_line: str, review_line: str) -> str:
    spec = inputs.spec
    steps = [f"Read `{CONTEXT_PATH}` — the project's stack, coding standards and guardrails. Respect them."]

    if spec.story_scoped:
        story_dir = f"{SDLC_DIR}/stories/<story-slug>/"
        input_files = ", ".join(f"`{story_dir}inputs/{f}`" for f in ("story-context.md", *spec.story_upstream_input_files))
        steps.append(
            f"$ARGUMENTS names the story: {spec.argument_label}. If it's missing, ask the user which story before "
            "doing anything else — never guess. Derive `<story-slug>` from it exactly as given if it's already a "
            "slug, or by lowercasing and hyphenating the title otherwise."
        )
        steps.append(
            f"Read every one of these in full before writing anything: {input_files}. Treat them as ground truth "
            "— never invent what they don't say. If any is missing, or is marked 'not yet available', stop and "
            'tell the user to run "Sync story inputs" for this story in Agentic SDLC Hub first (it must complete '
            "and be approved/drafted before this stage can start)."
        )
    elif inputs.artifact_inputs:
        listed = ", ".join(f"`{a.path}`" for a in inputs.artifact_inputs)
        steps.append(
            f"Read these approved upstream documents in full before writing anything: {listed}. Treat them as ground "
            "truth. If any is marked 'not yet available', stop and tell the user that stage must be completed and "
            "approved first."
        )

    if spec.input_hint:
        steps.append(
            f"Get the input: {spec.input_hint}. If it is missing or too vague to fill the document below, ask the "
            'user at most 5 short, specific questions and wait for the answers. Never invent facts, constraints, '
            'dates or metrics — anything not stated goes under "Open Questions".'
        )
    elif inputs.artifact_inputs or spec.story_scoped:
        steps.append(
            "No freeform input is required beyond the story named above — this stage is produced entirely from "
            "the files you just read. $ARGUMENTS's extra text, if any beyond the story itself, is optional "
            "guidance only; never let it override what those files say."
        )

    if spec.kind == "story_backlog":
        steps.append(
            f"Write the stories to `{spec.output_path}`, one `## Story: <title>` block per story, using exactly "
            "the field template below for every block (repeat it — do not invent new field names or drop any). "
            "Use VERTICAL mode unless $ARGUMENTS says HORIZONTAL. Cover the full scope of the upstream design; "
            "don't pad it with trivial or duplicate stories."
        )
    else:
        steps.append(
            f"Write the document to `{spec.output_path}` using exactly the template below (keep the front matter "
            "and the section headings; replace every `<write this section>` placeholder)."
        )

    steps.append(loop_line)
    steps.append(review_line)
    steps.append(
        f'Tell the user the file is ready and how to sync it: commit and push it (a branch is fine), then click '
        f'"Sync from repository" on the {spec.title} stage in Agentic SDLC Hub.'
    )

    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, start=1))
    return f"""{numbered}

Template:

```markdown
{_document_template(inputs, tool)}```
"""


def _argument_hint(inputs: _StageInputs) -> str:
    spec = inputs.spec
    if spec.input_hint:
        return f"[{spec.argument_label}, or a path to a file that contains it]"
    if spec.argument_label:
        return f"[{spec.argument_label}]"
    return "[no argument needed]"


def _usage_argument_phrase(inputs: _StageInputs) -> str:
    spec = inputs.spec
    if spec.argument_label and spec.input_hint:
        return f" <paste the {spec.argument_label}>"
    if spec.argument_label:
        return f" [{spec.argument_label}]"
    return ""


# --- Per-tool renderers -----------------------------------------------------------------


def _claude_code(inputs: _StageInputs) -> SkillPack:
    spec, p = inputs.spec, inputs.project
    label = "Claude Code"
    arg_phrase = _usage_argument_phrase(inputs)
    usage = [
        f"Open this repository in Claude Code and type `/{spec.slug}{arg_phrase}`"
        + (" (or without it, and answer its questions)." if spec.input_hint else "."),
        "Claude drafts the document; the validation hook re-checks it after every write and Claude fixes any problems automatically.",
        "A read-only reviewer subagent gives a fresh-eyes second opinion before Claude finishes.",
        f'Commit and push `{spec.output_path}`, then click "Sync from repository" in the app.',
    ]
    command = f"""---
description: {spec.title} for {p.name} — syncs to Agentic SDLC Hub
argument-hint: {_argument_hint(inputs)}
allowed-tools: Read, Write, Edit, Grep, Glob, Task
---

<role>
You are the {spec.title} agent for the project "{p.name}", working inside this repository.
{inputs.system_prompt.strip()}
</role>

<input>
$ARGUMENTS
</input>

<procedure>
{_procedure(inputs, tool="claude-code",
    loop_line="A hook validates the file every time you write it. If it reports problems, fix exactly those problems and write the file again — repeat until it passes (at most 3 rounds). You can also run `node " + VALIDATOR_PATH + " " + spec.output_path + "` yourself.",
    review_line=f"When the hook passes, delegate to the `sdlc-reviewer-{spec.slug}` subagent for an independent review against the checklist" + (', telling it the exact file path you just wrote' if spec.story_scoped else '') + '. Apply its blocking findings, then finish.')}
</procedure>

<quality_checklist>
{_checklist_md(inputs)}
</quality_checklist>

<rules>
- Think before writing; keep the document concise and factual.
- Do not modify any file other than `{spec.output_path}`.
- Do not commit or push; the user does that.
</rules>
"""
    reviewer = f"""---
name: sdlc-reviewer-{spec.slug}
description: Independent, read-only reviewer for the {spec.title} stage document. Use after drafting {spec.output_path} to check it against the quality checklist before finishing.
tools: Read, Grep, Glob
---

You review one document with fresh eyes. You never edit files.

Read `{spec.output_path}` and `{CONTEXT_PATH}`. Check every item, and report:

{_checklist_md(inputs)}

Also flag: invented facts not supported by the given input, unstated assumptions presented as facts,
contradictions, and vague statements that cannot be tested.

Reply with a short list. Mark each item PASS or FAIL with one line of evidence, then a list of BLOCKING issues (may be empty).
"""
    settings = json.dumps(
        {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Write|Edit|MultiEdit",
                        "hooks": [{"type": "command", "command": f'node "$CLAUDE_PROJECT_DIR/{VALIDATOR_PATH}"'}],
                    }
                ]
            }
        },
        indent=2,
    ) + "\n"
    files = [
        SkillFile(f".claude/commands/{spec.slug}.md", command, f"The /{spec.slug} slash command."),
        # Named per stage (not a shared "sdlc-reviewer.md") — a shared name would have this
        # stage's install silently overwrite an earlier stage's reviewer (and its checklist),
        # a real regression once a second stage's skills are added to the same repo.
        SkillFile(f".claude/agents/sdlc-reviewer-{spec.slug}.md", reviewer, f"Read-only reviewer subagent for {spec.title}."),
        SkillFile(
            ".claude/settings.json", settings, "PostToolUse hook that runs the validator after every write (only added if you have no settings file).",
            managed=False,
        ),
        _context_file(inputs), *_artifact_input_files(inputs), _validator_script(inputs), _readme(inputs, "claude_code", label, usage),
    ]
    notes = [
        "If your repo already has .claude/settings.json it is left alone — add the PostToolUse hook from docs/sdlc/README.md instead.",
        "The same command text also works as a skill: copy it to .claude/skills/<name>/SKILL.md if you prefer skills.",
    ]
    return SkillPack("claude_code", label, spec.node_key, files, usage, notes)


def _codex(inputs: _StageInputs) -> SkillPack:
    spec, p = inputs.spec, inputs.project
    label = "Codex"
    arg_phrase = _usage_argument_phrase(inputs)
    usage = [
        "One time per machine: `mkdir -p ~/.codex/prompts && cp .sdlc/codex/*.md ~/.codex/prompts/` (Codex reads custom prompts from your home folder).",
        f"Open this repository in Codex and type `/prompts:{spec.slug}{arg_phrase}`.",
        "Codex drafts the document and re-runs the validator script until it passes.",
        f'Commit and push `{spec.output_path}`, then click "Sync from repository" in the app.',
    ]
    prompt = f"""---
description: {spec.title} for {p.name}
argument-hint: {_argument_hint(inputs)}
---

You are the {spec.title} agent for the project "{p.name}", working inside this repository.
{inputs.system_prompt.strip()}

Input: $ARGUMENTS

Follow this procedure:

{_procedure(inputs, tool="codex",
    loop_line="Run `node " + VALIDATOR_PATH + " " + spec.output_path + "`. If it reports problems, fix exactly those and run it again — repeat until it exits 0 (at most 3 rounds).",
    review_line="Re-read the finished file once against the checklist below and fix anything that fails.")}

Quality checklist:
{_checklist_md(inputs)}

Only modify `{spec.output_path}`. Do not commit or push.
"""
    agents_md = f"""# Agent instructions

## SDLC workflow (Agentic SDLC Hub)

This project's stage documents live in `{SDLC_DIR}/` and are synced to Agentic SDLC Hub.
Before doing any SDLC stage, read `{CONTEXT_PATH}` (stack, coding standards, guardrails).

To run **{spec.title}**: follow `.sdlc/codex/{spec.slug}.md` (or `/prompts:{spec.slug}` if installed).
Write the result to `{spec.output_path}` and validate it with `node {VALIDATOR_PATH} {spec.output_path}` until it exits 0.
Never invent facts; put unknowns under "Open Questions". Do not commit or push unless asked.
"""
    files = [
        SkillFile(f".sdlc/codex/{spec.slug}.md", prompt, f"The /prompts:{spec.slug} custom prompt (copy to ~/.codex/prompts)."),
        SkillFile("AGENTS.md", agents_md, "Project instructions Codex reads automatically (only added if you have none).", managed=False),
        _context_file(inputs), *_artifact_input_files(inputs), _validator_script(inputs), _readme(inputs, "codex", label, usage),
    ]
    notes = [
        "Codex custom prompts are per-user, so the prompt file is shipped in the repo and copied once with the command above.",
        "If your repo already has AGENTS.md it is left alone — add the SDLC section from docs/sdlc/README.md.",
    ]
    return SkillPack("codex", label, spec.node_key, files, usage, notes)


def _opencode(inputs: _StageInputs) -> SkillPack:
    spec, p = inputs.spec, inputs.project
    label = "OpenCode"
    arg_phrase = _usage_argument_phrase(inputs)
    usage = [
        f"Open this repository in OpenCode and type `/{spec.slug}{arg_phrase}`.",
        "OpenCode loads the project context automatically, drafts the document, and runs the validator in a loop until it passes.",
        f"A read-only reviewer subagent can be invoked for a second opinion (@sdlc-reviewer-{spec.slug}).",
        f'Commit and push `{spec.output_path}`, then click "Sync from repository" in the app.',
    ]
    command = f"""---
description: {spec.title} for {p.name}
agent: build
---

You are the {spec.title} agent for the project "{p.name}".
{inputs.system_prompt.strip()}

Project context (read and respect it): @{CONTEXT_PATH}

Input: $ARGUMENTS

{_procedure(inputs, tool="opencode",
    loop_line="Run the validator with your shell tool: `node " + VALIDATOR_PATH + " " + spec.output_path + "`. If it reports problems, fix exactly those and run it again — repeat until it exits 0 (at most 3 rounds).",
    review_line=f"Ask the `@sdlc-reviewer-{spec.slug}` subagent to review the file against the checklist" + (', telling it the exact file path you just wrote' if spec.story_scoped else '') + ', and apply its blocking findings.')}

Quality checklist:
{_checklist_md(inputs)}

Only modify `{spec.output_path}`. Do not commit or push.
"""
    reviewer = f"""---
description: Independent, read-only reviewer for the {spec.title} stage document
mode: subagent
tools:
  write: false
  edit: false
  bash: false
---

You review one document with fresh eyes and never edit files.
Read `{spec.output_path}` and `{CONTEXT_PATH}`, then mark each checklist item PASS or FAIL with one line of evidence:

{_checklist_md(inputs)}

Also flag invented facts, unstated assumptions, contradictions and untestable statements. End with a BLOCKING list (may be empty).
"""
    agents_md = f"""# Agent instructions

## SDLC workflow (Agentic SDLC Hub)

Stage documents live in `{SDLC_DIR}/` and sync to Agentic SDLC Hub. Read `{CONTEXT_PATH}` before any SDLC stage.
Run **{spec.title}** with `/{spec.slug}`; the result goes to `{spec.output_path}` and must pass
`node {VALIDATOR_PATH} {spec.output_path}`. Never invent facts. Do not commit or push unless asked.
"""
    files = [
        SkillFile(f".opencode/command/{spec.slug}.md", command, f"The /{spec.slug} command."),
        # Named per stage — see the identical comment in _claude_code for why a shared
        # "sdlc-reviewer.md" would break on a second stage's install.
        SkillFile(f".opencode/agent/sdlc-reviewer-{spec.slug}.md", reviewer, f"Read-only reviewer subagent for {spec.title}."),
        SkillFile("AGENTS.md", agents_md, "Project instructions OpenCode reads automatically (only added if you have none).", managed=False),
        _context_file(inputs), *_artifact_input_files(inputs), _validator_script(inputs), _readme(inputs, "opencode", label, usage),
    ]
    notes = [
        "OpenCode's config folders have been renamed between versions (command/agent vs commands/agents). If /"
        + spec.slug
        + " does not appear, move the two files to the folder names your version documents.",
        "If your repo already has AGENTS.md it is left alone — add the SDLC section from docs/sdlc/README.md.",
    ]
    return SkillPack("opencode", label, spec.node_key, files, usage, notes)


def _cursor(inputs: _StageInputs) -> SkillPack:
    spec, p = inputs.spec, inputs.project
    label = "Cursor"
    arg_phrase = _usage_argument_phrase(inputs)
    usage = [
        f"Open this repository in Cursor, start an Agent chat and type `/{spec.slug}{arg_phrase}`.",
        "Cursor drafts the document; the attached rule keeps it in the right format, and the agent runs the validator in the terminal until it passes.",
        f'Commit and push `{spec.output_path}`, then click "Sync from repository" in the app.',
    ]
    command = f"""# {spec.title} — {p.name}

You are the {spec.title} agent for the project "{p.name}".
{inputs.system_prompt.strip()}

Project context (read and respect it): @{CONTEXT_PATH}

Input: {"the text the user typed after the command" if spec.input_hint else "optional extra guidance the user typed after the command"}.

{_procedure(inputs, tool="cursor",
    loop_line="Run `node " + VALIDATOR_PATH + " " + spec.output_path + "` in the terminal. If it reports problems, fix exactly those and run it again — repeat until it exits 0 (at most 3 rounds).",
    review_line="Re-read the finished file once against the checklist below and fix anything that fails.")}

Quality checklist:
{_checklist_md(inputs)}

Only modify `{spec.output_path}`. Do not commit or push.
"""
    rule = f"""---
description: Format rules for SDLC stage documents synced to Agentic SDLC Hub
globs: {SDLC_DIR}/**
alwaysApply: false
---

- Every stage document keeps its front matter (`sdlc_stage`, `project_id`, `status`) and its required structure.
- Never invent facts; unknowns go under "Open Questions".
- Read `{CONTEXT_PATH}` before writing; respect the coding standards and guardrails in it.
- Validate with `node {VALIDATOR_PATH} <file>` until it exits 0.
"""
    files = [
        SkillFile(f".cursor/commands/{spec.slug}.md", command, f"The /{spec.slug} chat command."),
        SkillFile(".cursor/rules/sdlc-stage-documents.mdc", rule, "Rule applied whenever a file under docs/sdlc/ is involved."),
        _context_file(inputs), *_artifact_input_files(inputs), _validator_script(inputs), _readme(inputs, "cursor", label, usage),
    ]
    notes = ["Custom chat commands (/…) need a recent Cursor version; the rule works on older ones."]
    return SkillPack("cursor", label, spec.node_key, files, usage, notes)


_RENDERERS = {"claude_code": _claude_code, "codex": _codex, "opencode": _opencode, "cursor": _cursor}


def build_skill_pack(db: Session, project: Project, *, tool: str, stage: str) -> SkillPack:
    if tool not in _RENDERERS:
        raise SkillPackError(f"Unknown tool '{tool}'. Choose one of: {', '.join(TOOLS)}.")
    # Implementation's own skill (a slash command that opens a real pull
    # request, not a docs/sdlc document) is deliberately not a StageSpec —
    # see app/services/implementation_skill.py's module docstring — but it
    # installs through this same generic route, so it's dispatched here
    # before the STAGE_SPECS lookup below.
    if stage == "implementation":
        from app.services.implementation_skill import build_implementation_skill_pack

        return build_implementation_skill_pack(db, project, tool)
    spec = STAGE_SPECS.get(stage)
    if spec is None:
        raise SkillPackError(f"Stage '{stage}' is not available for coding-tool skills yet. Available: {', '.join(STAGE_SPECS)}.")
    return _RENDERERS[tool](_gather_inputs(db, project, spec))


# --- Document front matter (used when syncing back) -------------------------------------


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Splits a leading `---` YAML-ish block (simple `key: value` lines only)
    from the body. Returns ({}, text) when there is none."""
    stripped = text.lstrip("﻿")
    if not stripped.startswith("---"):
        return {}, text
    lines = stripped.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    meta: dict[str, Any] = {}
    for line in lines[1:end]:
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, "\n".join(lines[end + 1 :]).lstrip("\n")


# --- The validator (Node, dependency-free) ------------------------------------------------

_VALIDATOR_TEMPLATE = r'''#!/usr/bin/env node
// Agentic SDLC Hub — stage document checker (generated; do not edit by hand).
// Use:  node .sdlc/hooks/validate-doc.mjs docs/sdlc/<stage>.md
// It also works as a Claude Code PostToolUse hook (the tool call arrives as JSON on stdin),
// in which case a problem exits 2 so the assistant sees the message and fixes it.
import { readFileSync, existsSync } from "node:fs";
import { resolve, relative, sep, basename } from "node:path";

const SPECS = __SPECS__;

// Mirrors (a simplified version of) app/services/story_export.py's _FIELD_RE —
// good enough for a local, fast check; the app re-validates for real on sync.
const STORY_HEADING_RE = /^##\s*Story:\s*(.+)$/gm;
const FIELD_RE = /\*\*([^*:]+):\*\*[ \t]*([\s\S]*?)(?=\n[ \t]*[-*]?[ \t]*\*\*[^*:]+:\*\*|$)/g;
const REQUIRED_STORY_FIELDS = [
  "Epic", "Feature", "Mode", "User Story", "Business Value", "Acceptance Criteria",
  "Suggested Owner Role", "Technical Areas Involved", "Dependencies", "Priority",
  "Story Points Estimate", "Jira Issue Type", "Estimated PR Review Time", "Suggested Subtasks",
  "Release Readiness Criteria", "Definition of Done",
];
// A story's PR must be reviewable by one human — see app/services/review_time.py,
// the Python original this mirrors (a simplified version; the app re-checks for real on sync).
const MAX_WORST_CASE_REVIEW_MINUTES = 30;
const THREE_NUMBERS_RE = /(\d+)\s*(?:\/|,|-|to)\s*(\d+)\s*(?:\/|,|-|to)\s*(\d+)/;
const ONE_NUMBER_RE = /(\d+)/;

function reviewTimeWorstCaseMinutes(raw) {
  const three = THREE_NUMBERS_RE.exec(raw || "");
  if (three) return Number(three[3]);
  const one = ONE_NUMBER_RE.exec(raw || "");
  return one ? Number(one[1]) : null;
}

function readStdin() {
  try {
    if (process.stdin.isTTY) return "";
    return readFileSync(0, "utf8");
  } catch {
    return "";
  }
}

let target = process.argv[2];
let hookMode = false;
if (!target) {
  const raw = readStdin().trim();
  if (raw) {
    try {
      const input = JSON.parse(raw);
      target = input?.tool_input?.file_path;
      hookMode = true;
    } catch {
      process.exit(0);
    }
  }
}
if (!target) process.exit(0);

const abs = resolve(target);
const rel = relative(process.cwd(), abs).split(sep).join("/");
const name = basename(rel);
// Only stage documents under docs/sdlc/ are checked; context, README and inputs are not.
const applicable = rel.startsWith("docs/sdlc/") && rel.endsWith(".md") && !rel.startsWith("docs/sdlc/inputs/") &&
  name !== "context.md" && name !== "README.md";
if (!applicable) process.exit(0);
if (!existsSync(abs)) process.exit(0);

const text = readFileSync(abs, "utf8");
const problems = [];

let meta = {};
let body = text.replace(/^﻿/, "");
if (body.startsWith("---")) {
  const end = body.indexOf("\n---", 3);
  if (end !== -1) {
    for (const line of body.slice(3, end).split("\n")) {
      const i = line.indexOf(":");
      if (i > 0) meta[line.slice(0, i).trim()] = line.slice(i + 1).trim();
    }
    body = body.slice(end + 4);
  }
}

const spec = SPECS[meta.sdlc_stage] || Object.values(SPECS).find((s) => s.path === rel);
if (!meta.sdlc_stage) problems.push("Missing front matter: the file must start with a --- block containing sdlc_stage and project_id.");
if (!meta.project_id) problems.push("Front matter is missing project_id.");

if (!spec) {
  problems.push(`Unknown sdlc_stage "${meta.sdlc_stage ?? ""}".`);
} else if (spec.kind === "story_backlog") {
  const headings = [...body.matchAll(STORY_HEADING_RE)];
  if (headings.length === 0) {
    problems.push('No "## Story: <title>" blocks found.');
  } else {
    for (let i = 0; i < headings.length; i++) {
      const title = headings[i][1].trim() || `story ${i + 1}`;
      const start = headings[i].index + headings[i][0].length;
      const stop = i + 1 < headings.length ? headings[i + 1].index : body.length;
      const block = body.slice(start, stop);
      const fields = new Map([...block.matchAll(FIELD_RE)].map((m) => [m[1].trim().toLowerCase(), m[2].trim()]));
      for (const label of REQUIRED_STORY_FIELDS) {
        if (!fields.get(label.toLowerCase())) problems.push(`"${title}": missing or empty "${label}"`);
      }
      const reviewTime = fields.get("estimated pr review time");
      if (reviewTime) {
        const worst = reviewTimeWorstCaseMinutes(reviewTime);
        if (worst === null) problems.push(`"${title}": Estimated PR Review Time ("${reviewTime}") is not a recognizable minutes estimate`);
        else if (worst > MAX_WORST_CASE_REVIEW_MINUTES) {
          problems.push(`"${title}": Estimated PR Review Time worst case is ${worst} minutes, over the ${MAX_WORST_CASE_REVIEW_MINUTES}-minute limit — split this story into smaller, independently reviewable stories`);
        }
      }
    }
  }
} else {
  const headings = [...body.matchAll(/^##\s+(.+?)\s*$/gm)].map((m) => ({ title: m[1].trim().toLowerCase(), index: m.index, len: m[0].length }));
  for (const group of spec.headings) {
    const found = headings.findIndex((h) => group.some((g) => h.title === g.toLowerCase()));
    if (found === -1) {
      problems.push(`Missing section "## ${group[0]}"${group.length > 1 ? ` (or ${group.slice(1).map((g) => `"## ${g}"`).join(" / ")})` : ""}.`);
      continue;
    }
    const start = headings[found].index + headings[found].len;
    const stop = headings[found + 1] ? headings[found + 1].index : body.length;
    const sectionText = body.slice(start, stop);
    if (sectionText.replace(/\s+/g, " ").trim().length < 15) problems.push(`Section "## ${group[0]}" is empty or too short.`);
    // required_diagram_sections (see StageSpec in coding_tool_skills.py): this
    // is the actual enforcement — the prose instruction alone was being
    // skipped in practice. A section whose whole content is "None." has
    // opted out entirely (the stage doesn't apply here at all, not just the
    // diagram); otherwise a ```mermaid fenced block is mandatory, even as a
    // one-line %% comment explaining why no real diagram applies, so this
    // check never has to parse free-form justification text.
    const isNoneSection = sectionText.replace(/\s+/g, " ").trim().toLowerCase() === "none.";
    if ((spec.diagramSections || []).includes(group[0]) && !isNoneSection && !/```mermaid/i.test(sectionText)) {
      problems.push(`Section "## ${group[0]}" is missing its required \`\`\`mermaid diagram (a real diagram, or a one-line \`\`\`mermaid %% comment explaining why none applies).`);
    }
  }
  const words = body.split(/\s+/).filter(Boolean).length;
  if (words < spec.minWords) problems.push(`The document is too short (${words} words; at least ${spec.minWords}).`);
}
if (/<write this section>|<[a-z][a-z \/]*>/i.test(body) && /<write this section>/i.test(body)) problems.push("Placeholder text `<write this section>` is still present.");
if (/\b(TODO|TBD|lorem ipsum|\[fill in)/i.test(body)) problems.push("Placeholder text (TODO / TBD / lorem ipsum / [fill in) is still present.");
if (/^#\s*Clarification Needed/im.test(body)) problems.push("This is a clarification request, not a finished document. Ask the user the questions, then write the real document.");

if (problems.length) {
  console.error(`SDLC document check FAILED for ${rel}:\n` + problems.map((p) => `  - ${p}`).join("\n"));
  process.exit(hookMode ? 2 : 1);
}
console.log(`SDLC document check passed for ${rel}.`);
'''
