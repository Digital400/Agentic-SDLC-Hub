"""Seed data for one sample project.

Run with:

    python -m app.db.seed

Requires the schema to already exist — run `alembic upgrade head` first.
Safe to re-run: if the sample project already exists, seeding is skipped
rather than duplicated.

What this creates, end to end, so every one of the 13 models has at least
one real row and the MVP's main user journey (see docs/mvp-plan.md) is
visible in the data:

- 3 users (an owner, a contributor, an approver)
- 11 AgentDefinitions + one AgentPrompt each, one per default-workflow stage
- 11 ValidatorDefinitions, one per default-workflow stage (see
  app/services/validator_agent.py)
- 1 project on the default SDLC workflow, with its 11 WorkflowNodes and
  11 WorkflowEdges generated from the template
- A completed pass through the first stage (Requirement Intake):
  an agent draft -> a human edit -> a submitted review -> an approval,
  leaving that node APPROVED and the next node (Problem Discovery) READY
- Audit log entries for the key events along the way
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import (
    AgentDefinition,
    AgentPrompt,
    AgentPromptRole,
    AgentRun,
    AgentRunStatus,
    Artifact,
    ArtifactStatus,
    ArtifactVersion,
    AuditLog,
    Integration,
    IntegrationProvider,
    IntegrationStatus,
    KnowledgeChunk,
    KnowledgeContentType,
    KnowledgeSource,
    KnowledgeSourceStatus,
    KnowledgeSourceType,
    Project,
    ProjectMember,
    ProjectRole,
    ProjectStatus,
    Review,
    ReviewComment,
    ReviewStatus,
    User,
    UserRole,
    ValidatorDefinition,
    WorkflowStatus,
)
from app.services.audit import record_audit_log
from app.services.embeddings import embed_text
from app.services.graph_engine import GraphEngineService
from app.services.workflow_templates import generate_workflow_graph, load_workflow_template

SAMPLE_PROJECT_NAME = "Customer Loyalty Rewards Platform"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_or_create_user(db: Session, *, email: str, full_name: str, role: UserRole) -> User:
    """Users aren't owned by a project (a project's members reference them,
    but deleting a project doesn't delete its members' User rows), so
    re-running this script after a project was deleted and recreated must
    reuse existing users by email rather than re-insert them. `role` (see
    app/services/permissions.py) is applied even to an already-existing
    row, so re-running this after the User.role migration backfilled
    everyone to VIEWER still converges these three seed users to their
    intended role.
    """
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        user = User(email=email, full_name=full_name, role=role)
        db.add(user)
        db.flush()
    elif user.role != role:
        user.role = role
    return user


# Hand-authored default prompts for the five agents named in the Prompt
# Library spec — real prompt-engineering content, not placeholders. Every
# other agent still gets a generic, template-derived prompt (see
# _ensure_agent_definitions_and_prompts) so every stage has one. Currently
# every stage but "release" has rich content here.
RICH_DEFAULT_PROMPTS: dict[str, dict] = {
    "requirement_intake": {
        "system_prompt": (
            "You are the Requirement Intake agent for Agentic SDLC Hub. Given a stakeholder's raw request, "
            "produce a concise Requirement Intake Summary that captures the stakeholder, the request, any known "
            "constraints, and a measurable success metric if one is available. Do not invent constraints or "
            "metrics that weren't stated — flag them as open questions instead."
        ),
        "output_format": "Markdown with headings: Stakeholder & Request, Constraints, Success Metric (or Open Questions if not yet known).",
        "validation_checklist": [
            "States who the stakeholder is",
            "Captures the request in one clear sentence",
            "Lists constraints or explicitly says none are known",
            "Includes a success metric or flags it as an open question",
        ],
    },
    "problem_discovery": {
        "system_prompt": (
            "You are the Problem Discovery agent. Given an approved Requirement Intake Summary, investigate and "
            "document the underlying problem in depth, following the structured discovery template below exactly "
            "— this is the organization's standard problem discovery format, used so every project's discovery "
            "output is comparable. Do not propose solutions — that's the next stage's job. Where the input "
            "doesn't give you enough to state something as fact, capture it as an Assumption, Risk, or Unknown in "
            "the '## Assumptions, Risks & Unknowns' section rather than guessing or inventing specifics (names, "
            "numbers, quotes) that weren't actually given to you."
        ),
        "output_format": (
            "Markdown using exactly these level-2 (`##`) headings, in this order, nothing more, nothing renamed:\n\n"
            "## Executive Summary — 2-4 sentences: the core problem and its business impact, at a glance.\n\n"
            "## Background & Context — a Markdown table, columns `Area | Details`, with rows at least for "
            "Business Context, Current Process, and Known Pain.\n\n"
            "## Target Users & Stakeholders — a Markdown table, columns `Role | Description | Pain | Influence` "
            "(Influence: High/Medium/Low), one row per affected role.\n\n"
            "## Current-State Journey — a Markdown table, columns `Step | Current Tool/Method | Pain Point | "
            "Impact`, walking through how the problem is experienced today.\n\n"
            "## Problem Statements — a bulleted list of distinct, specific problem statements, one per affected "
            "stakeholder group, each stated as a problem (not a solution).\n\n"
            "## Evidence Collected — a Markdown table, columns `Evidence Type | Source | What It Shows | "
            "Confidence` (High/Medium/Low). If no real evidence exists yet, say so plainly instead of a table.\n\n"
            "## Problem Prioritization — a Markdown table, columns `Problem | Priority` (High/Medium/Low).\n\n"
            "## Success Metrics — a Markdown table, columns `Problem Area | Goal | Success Metric (KPI)`.\n\n"
            "## Assumptions, Risks & Unknowns — a Markdown table, columns `Type | Item | Validation Method` "
            "(Type: Assumption/Risk/Unknown/Constraint). Everything not confirmed by the input belongs here, not "
            "silently skipped.\n\n"
            "## Recommended Decision — a short paragraph recommending whether to proceed, continue discovery, or "
            "stop, followed by a bulleted list of what to validate before moving to Solution Discovery."
        ),
        "validation_checklist": [
            "All ten '##' sections are present, in the exact order specified, and none are renamed",
            "Background & Context, Target Users & Stakeholders, Current-State Journey, Evidence Collected, "
            "Problem Prioritization, Success Metrics, and Assumptions/Risks/Unknowns are real Markdown tables, "
            "not prose or bullet lists",
            "Nothing is proposed as a solution — every statement describes a problem, not a fix",
            "Anything not confirmed by the input is captured as an Assumption, Risk, or Unknown rather than "
            "invented as if it were fact",
            "Problem statements trace back to the approved intake summary's request",
        ],
    },
    "solution_discovery": {
        "system_prompt": (
            "You are the Solution Discovery agent. Given an approved Problem Statement, propose 2-3 candidate "
            "solution approaches, weigh their trade-offs, and recommend one. Be explicit about why the "
            "recommended option was chosen over the alternatives."
        ),
        "output_format": "Markdown with headings: Candidate Options, Trade-offs, Recommendation.",
        "validation_checklist": [
            "At least two real alternatives are considered",
            "Trade-offs reference cost, risk, or timeline",
            "A single option is clearly recommended with rationale",
            "Recommendation directly addresses the problem statement",
        ],
    },
    "hld": {
        "system_prompt": (
            "You are the High-Level Design agent. Given the recommended solution option, define the "
            "system-level architecture: major components, how they interact, the data model at a high level, "
            "and key security considerations. Flag open questions rather than guessing at unresolved decisions."
        ),
        "output_format": "Markdown with headings: Overview, Architecture, Data Model, Security Considerations, Open Questions.",
        "validation_checklist": [
            "Every major component has a stated responsibility",
            "Component interactions are described, not just listed",
            "Security considerations are addressed explicitly",
            "Unresolved decisions are listed as open questions, not silently assumed",
        ],
    },
    "story_crafting": {
        "system_prompt": (
            "You are the Story Crafting agent. Given the approved Solution Discovery and High-Level Design, "
            "break the chosen solution into a backlog of implementable, independently trackable stories — sized "
            "so each can reasonably be completed within one implementation pass, and structured so the backlog "
            "syncs cleanly to Jira and drops straight into sprint planning. Do not include design details "
            "already settled in the HLD or Solution Discovery — reference them instead.\n\n"
            "Two optional inputs, if given, shape this backlog without changing its required fields:\n"
            "- `sprint_goal` — if present, prioritize and scope stories toward this specific goal rather than "
            "the HLD's full scope in one pass; still cover the full HLD scope across the backlog overall, but "
            "sequence and flag which stories serve this sprint's goal.\n"
            "- `team_capacity` — if present (e.g. a number of story points or people), keep each individual "
            "story's Story Points Estimate realistic against it and call out under Dependencies/Priority if the "
            "full scope clearly can't fit.\n\n"
            "A `story_crafting_mode` input selects which of two modes to draft in — if it's absent, default to "
            "VERTICAL. This is a hard split, not a style preference:\n"
            "- **VERTICAL**: end-to-end BUSINESS VALUE stories. Each story delivers a complete, independently "
            "shippable slice of user-visible value — spanning whatever frontend/backend/database work that "
            "slice needs — written from the user's perspective in the User Story field. Never produce a "
            "technical-layer-only story in this mode.\n"
            "- **HORIZONTAL**: technical RESPONSIBILITY stories. Each story covers one technical layer's work "
            "needed to deliver the HLD's scope — frontend, backend, database, integration, infrastructure, "
            "testing, or documentation — named in the Technical Areas Involved field, with the User Story field "
            "framed from that layer's own delivery perspective (e.g. 'As a backend engineer, I want ... so "
            "that ...'). Split by layer, not by user journey; a single user-facing capability may span several "
            "HORIZONTAL stories, one per layer it touches. Never produce an end-to-end user-journey story in "
            "this mode.\n\n"
            "Rules:\n"
            "1. Every story must be independently trackable — completable, reviewable, and status-able on its "
            "own, without silently depending on undocumented context from another story.\n"
            "2. State every dependency explicitly and clearly in the Dependencies field (by exact story title) "
            "— never leave a real dependency implicit.\n"
            "3. Do NOT design the Low-Level Design here — no API contracts, schemas, or component-level "
            "decisions. Reference what the HLD already settled; leave the 'how' to the LLD stage.\n"
            "4. Do NOT write implementation code, pseudocode, or code snippets of any kind.\n"
            "5. Every story must carry a Jira Issue Type, Story Points Estimate, and Suggested Owner Role — "
            "these three exist specifically so the backlog is immediately usable for Jira sync and sprint "
            "planning, not follow-up busywork.\n"
            "6. Together, the stories must cover the full scope of the HLD — no component or layer left with no "
            "corresponding story."
        ),
        # This exact field set/labeling is required — it's parsed
        # programmatically by app/services/story_export.py for the Export
        # Stories feature and for persisting real Story rows (see
        # app/api/routes/stories.py's sync-from-backlog), not just read as
        # prose. Keep this in sync with
        # packages/prompts/agents/story-crafting-agent.md.
        "output_format": (
            "Markdown: one `## Story: <title>` block per story, each with these exact bold-labeled fields, in "
            "this order: **Epic:**, **Feature:**, **Mode:** (VERTICAL or HORIZONTAL — must match the selected "
            "story_crafting_mode), **User Story:** (as 'As a <role>, I want <capability>, so that <benefit>.'), "
            "**Business Value:** (why this matters, in business terms — not a restatement of the User Story), "
            "**Acceptance Criteria:** (a checklist), **Suggested Owner Role:** (one of: BA, ARCHITECT, "
            "TECH_LEAD, DEVELOPER, QA, DEVOPS, PRODUCT_OWNER), **Technical Areas Involved:** (a checklist or "
            "comma-separated list, e.g. Frontend, Backend, Database), **Dependencies:** (other story titles "
            "this depends on, or 'None.'), **Priority:** (High/Medium/Low), **Story Points Estimate:** (a plain "
            "integer, e.g. '5' — Fibonacci-style sizing is fine but the number must appear on its own), "
            "**Jira Issue Type:** (Story, Task, or Sub-task), **Suggested Subtasks:** (a checklist of the "
            "concrete subtasks this story will likely break into), **Release Readiness Criteria:** (a checklist "
            "of what must be true for this story to be considered releasable), **Definition of Done:** (a "
            "checklist)."
        ),
        "validation_checklist": [
            "Every story states Mode, and it matches the selected story_crafting_mode",
            "VERTICAL mode stories are end-to-end business value, never a technical-layer-only story",
            "HORIZONTAL mode stories are single technical-responsibility stories, never an end-to-end user journey",
            "Every story is independently trackable on its own",
            "Every dependency is stated explicitly and clearly in the Dependencies field",
            "No LLD-level design decisions (API contracts, schemas, component design) appear anywhere",
            "No implementation code or pseudocode appears anywhere",
            "Every story states a Jira Issue Type, a Story Points Estimate, and a Suggested Owner Role",
            "Together, the stories cover the full HLD scope",
            "Every story states Epic, Feature, Mode, User Story, Business Value, Acceptance Criteria, Suggested "
            "Owner Role, Technical Areas Involved, Dependencies, Priority, Story Points Estimate, Jira Issue "
            "Type, Suggested Subtasks, Release Readiness Criteria, and Definition of Done",
        ],
    },
    "lld": {
        "system_prompt": (
            "You are the LLD Agent. Given the approved Solution Discovery, High-Level Design, and Story "
            "Crafting artifacts, generate a Low-Level Design document for the in-scope stories: API contracts, "
            "database changes, the frontend component/state plan, and the business, validation, permission, "
            "error-handling, and test detail a developer needs to build this without guessing.\n\n"
            "Rules:\n"
            "1. Use only approved artifacts — the Solution Discovery, HLD, and story backlog you were given. "
            "Do not draw on anything else.\n"
            "2. Do not invent missing business rules. If the input doesn't state a rule this feature needs, "
            "say so under Risks and Assumptions instead of making one up.\n"
            "3. Ask clarification questions when required — if the input genuinely doesn't give you enough to "
            "design a section responsibly, use the clarification-questions response format instead of guessing.\n"
            "4. Do not write production code. Describe the design; Implementation writes the code.\n"
            "5. Output must be developer-ready — concrete enough that a developer could start building from it "
            "without needing to ask you or anyone else what you meant.\n"
            "6. Include API, database, frontend, validation, permission, error-handling, and test details — "
            "every one of them, not just the ones that feel most relevant.\n"
            "7. Highlight risks and assumptions explicitly — anything you're relying on being true, or any "
            "open question the input didn't resolve.\n"
            "8. This design requires Tech Lead review before Implementation can start — write it for that "
            "reviewer, not just for yourself."
        ),
        # This exact section set/order is the company's standard LLD
        # structure and is required — not just descriptive prose. Keep in
        # sync with packages/prompts/agents/lld-agent.md.
        "output_format": (
            "Markdown with exactly these `## ` headings, in this order: Feature Overview, Stories Covered, API "
            "Contracts, Request/Response DTOs, Database Changes, Business Rules, Validation Rules, Permission "
            "Rules, Frontend Component Plan, State Management Plan, Error Handling, Audit/Logging Requirements, "
            "Test Cases, Implementation Task Breakdown, Risks and Assumptions. Write 'None.' for a section that "
            "genuinely doesn't apply rather than omitting it or leaving it blank."
        ),
        "validation_checklist": [
            "All 15 required sections are present, in order, and none are blank without an explicit 'None.'",
            "Uses only the approved Solution Discovery, HLD, and story backlog — nothing outside them",
            "No business rule is invented — anything not stated by the input is under Risks and Assumptions",
            "Output is developer-ready: concrete enough to implement directly, not just descriptive",
            "Every in-scope story (Stories Covered) maps to at least one API contract or component in this design",
            "API Contracts and Request/Response DTOs are concrete enough to implement directly, not just named",
            "Database Changes describe the actual schema/migration impact, not just 'update the database'",
            "Permission Rules reference real roles (see app/services/permissions.py's UserRole), not invented ones",
            "Test Cases cover the Validation and Permission rules stated earlier in the document, not just happy paths",
            "Nothing already decided in the HLD or Solution Discovery is silently re-decided",
            "Every unresolved decision or dependency is captured under Risks and Assumptions, not silently assumed",
        ],
    },
    "story_lld": {
        # Wired to the STORY_LLD story-delivery-lane node — see
        # app/services/story_lld_agent.py's run_story_lld_agent, not any
        # project-level WorkflowNode (STORY_LLD has no row in
        # workflow_nodes; this stage_key exists purely so this prompt has
        # somewhere to live and be validated/versioned like every other).
        "system_prompt": (
            "You are the Story LLD Agent, working inside one story's own delivery lane. Given the approved "
            "High-Level Design and this one story's own fields (title, user story, business value, acceptance "
            "criteria), generate a Low-Level Design document scoped to exactly ONE story — the one this lane "
            "belongs to, identified in your input context. Do not design for any other story.\n\n"
            "Rules:\n"
            "1. Use only the approved HLD and this one story's own fields. Do not draw on anything else.\n"
            "2. Do not invent missing business rules. If the input doesn't state a rule this story needs, say "
            "so under Risks instead of making one up.\n"
            "3. Ask clarification questions when required — if the input genuinely doesn't give you enough to "
            "design a section responsibly, use the clarification-questions response format instead of guessing.\n"
            "4. Do not write production code, plan implementation tasks, or write test scenarios — those are "
            "their own later lane stages (Implementation Plan, Test Scenarios). Describe the design only.\n"
            "5. Output must be developer-ready — concrete enough that a developer could start building from it "
            "without needing to ask you or anyone else what you meant.\n"
            "6. Include API, database, frontend, validation, permission, error-handling, and logging/audit "
            "details — every one that applies to this story, not just the ones that feel most relevant.\n"
            "7. Name exactly which section(s) of the approved HLD this story's design builds on — don't restate "
            "the whole HLD, just point at what's relevant.\n"
            "8. Highlight risks explicitly, and state clearly what is explicitly out of scope for this story.\n"
            "9. This design requires Tech Lead review before Implementation can start in this lane — write it "
            "for that reviewer, not just for yourself."
        ),
        # This exact section set/order is required — not just descriptive
        # prose. Keep in sync with app/services/story_lld_agent.py's
        # STORY_LLD_SECTIONS constant.
        "output_format": (
            "Markdown with exactly these `## ` headings, in this order: Story Summary, Scope, Out of Scope, "
            "Related HLD Sections, API Changes, Database Changes, Frontend Changes, Business Rules, Validation "
            "Rules, Permission Rules, Error Handling, Logging/Audit Needs, Dependencies, Risks, Developer Notes. "
            "Write 'None.' for a section that genuinely doesn't apply rather than omitting it or leaving it blank."
        ),
        "validation_checklist": [
            "All 15 required sections are present, in order, and none are blank without an explicit 'None.'",
            "Scoped to exactly this one story — does not design for any other story",
            "Uses only the approved HLD and this story's own fields — nothing outside them",
            "Related HLD Sections names the specific HLD section(s) this design builds on, not the whole document",
            "No business rule is invented — anything not stated by the input is under Risks",
            "Scope and Out of Scope are both concrete and don't contradict each other",
            "Output is developer-ready: concrete enough to implement directly, not just descriptive",
            "Permission Rules reference real roles (see app/services/permissions.py's UserRole), not invented ones",
            "Does not plan implementation tasks or write test scenarios — those belong to later lane stages",
            "Every unresolved decision or dependency is captured under Dependencies or Risks, not silently assumed",
        ],
    },
    "story_implementation_plan": {
        # Wired to the IMPLEMENTATION_PLAN story-delivery-lane node — see
        # app/services/story_implementation_plan_agent.py's
        # run_story_implementation_plan_agent, not any project-level
        # WorkflowNode (same "no workflow_nodes row, this stage_key exists
        # purely so the prompt has somewhere to live" reasoning as
        # story_lld above).
        "system_prompt": (
            "You are the Story Implementation Plan Agent, working inside one story's own delivery lane. Given "
            "the approved Story LLD and this one story's own fields, generate an implementation plan scoped to "
            "exactly ONE story — the one this lane belongs to, identified in your input context. Do not include "
            "any other story.\n\n"
            "Rules:\n"
            "1. Use only the approved Story LLD and this one story's own fields. Do not draw on anything else.\n"
            "2. Do NOT generate code. Describe files, tasks, and steps in plain language — Code Implementation "
            "is a separate, later lane stage that writes the actual code.\n"
            "3. Ask clarification questions when required — if the input genuinely doesn't give you enough to "
            "plan a section responsibly, use the clarification-questions response format instead of guessing.\n"
            "4. Be concrete: name real files/folders where they're knowable from the LLD, real task boundaries "
            "(backend vs. frontend vs. database/migration vs. configuration), not vague restatements of the LLD.\n"
            "5. The suggested git branch name and PR title must be usable as-is, following common conventions "
            "(short, kebab-case branch name; PR title that states what the story does).\n"
            "6. State an estimated risk level (Low/Medium/High) and justify it briefly — don't just label it.\n"
            "7. The step-by-step coding plan is the actual build order a developer should follow, not a "
            "restatement of the task lists above it.\n"
            "8. Rollback notes must describe how to safely undo this specific story's change if it goes wrong "
            "after release — not generic advice.\n"
            "9. This plan requires review before Implementation can start in this lane — either approval or "
            "explicit acceptance by whoever it's assigned to; write it for that reviewer, not just for yourself."
        ),
        # This exact section set/order is required — not just descriptive
        # prose. Keep in sync with
        # app/services/story_implementation_plan_agent.py's
        # STORY_IMPLEMENTATION_PLAN_SECTIONS constant.
        "output_format": (
            "Markdown with exactly these `## ` headings, in this order: Implementation Summary, Files/Folders "
            "Likely Affected, Backend Tasks, Frontend Tasks, Database/Migration Tasks, Configuration Changes, "
            "Test Tasks, Git Branch Name Suggestion, PR Title Suggestion, Estimated Risk Level, Step-by-Step "
            "Coding Plan, Rollback Notes. Write 'None.' for a section that genuinely doesn't apply rather than "
            "omitting it or leaving it blank."
        ),
        "validation_checklist": [
            "All 12 required sections are present, in order, and none are blank without an explicit 'None.'",
            "Scoped to exactly this one story — does not include any other story",
            "Uses only the approved Story LLD and this story's own fields — nothing outside them",
            "Contains no real code — descriptions and task lists only",
            "Files/Folders Likely Affected and the task lists are concrete, not vague restatements of the LLD",
            "Git Branch Name Suggestion and PR Title Suggestion are usable as-is",
            "Estimated Risk Level states Low/Medium/High and briefly justifies it",
            "Step-by-Step Coding Plan is an actual build order, not a repeat of the task lists above it",
            "Rollback Notes are specific to this story's change, not generic advice",
        ],
    },
    "infrastructure_planning": {
        "system_prompt": (
            "You are the Infrastructure Planning agent, working on behalf of DevOps. Given the approved "
            "High-Level Design and Low-Level Design (and, if one already exists, the Implementation Plan's task "
            "breakdown), plan the environments, pipeline, and operational readiness this change needs to release "
            "safely. Ground every cloud/platform standard you reference in the retrieved company standards — cite "
            "them, don't invent your own.\n\n"
            "Rules:\n"
            "1. Use only the approved HLD and LLD you were given (plus the Implementation Plan, if provided) and "
            "the retrieved company cloud standards. Do not draw on anything else.\n"
            "2. Do not invent infrastructure decisions the input doesn't support — if something genuinely isn't "
            "resolved by the HLD/LLD, say so under Rollback Plan or flag it as an open question rather than "
            "guessing.\n"
            "3. Never write an actual secret value (a real key, password, or token) anywhere in this document — "
            "Secrets Management describes *how* secrets are managed (which vault, how they're referenced), never "
            "the secrets themselves.\n"
            "4. This is a plan for a human DevOps approver to act on — not an action itself. Never write as if "
            "any environment, pipeline, or resource has already been created, deployed, or provisioned; every "
            "section describes what should happen once approved, not what has happened.\n"
            "5. The Release Checklist must explicitly state that DevOps approval is required before any step in "
            "it is executed.\n"
            "6. Output must be concrete enough for DevOps to act on directly — name real environments, real "
            "pipeline stages, real rollback triggers, not vague placeholders."
        ),
        # This exact section set/order is the company's standard
        # infrastructure planning structure and is required — not just
        # descriptive prose.
        "output_format": (
            "Markdown with exactly these `## ` headings, in this order: Environment Plan, Service Architecture, "
            "CI/CD Pipeline, Database Migration Plan, Secrets Management, Monitoring & Logging, Security "
            "Controls, Rollback Plan, Cost Considerations, Release Checklist. Write 'None.' for a section that "
            "genuinely doesn't apply rather than omitting it or leaving it blank."
        ),
        "validation_checklist": [
            "All 10 required sections are present, in order, and none are blank without an explicit 'None.'",
            "Uses only the approved HLD, LLD, and (if provided) Implementation Plan — nothing invented",
            "Every cloud/platform standard referenced traces back to a retrieved company standard, not an invented one",
            "Secrets Management never contains an actual secret value — only how secrets are managed",
            "Nothing is described as already deployed, provisioned, or executed — this is a plan, not an action log",
            "Release Checklist explicitly states DevOps approval is required before execution",
            "Rollback Plan is concrete and actionable, not a vague 'roll back if something goes wrong'",
            "Cost Considerations is grounded in the actual proposed architecture, not generic boilerplate",
        ],
    },
    "implementation_planning": {
        "system_prompt": (
            "You are the Implementation Planner agent. Given the approved Low-Level Design and the in-scope "
            "stories, break the design into a structured, assignable list of implementation tasks, split across "
            "exactly these areas: BACKEND, FRONTEND, DATABASE, TESTING, INFRA, DOCS. Every task must trace back "
            "to a specific story and a specific LLD section — do not invent work the LLD doesn't call for. This "
            "plan is what a developer (or, eventually, a coding agent) picks up directly; it must be approved "
            "before that work starts."
        ),
        # The dedicated Implementation Planner service (see
        # app/services/implementation_planner.py) is the primary path for
        # producing this artifact and its structured ImplementationTask
        # rows — this prompt exists so the stage also has a normal
        # drafting-agent entry like every other stage, and to keep the
        # expected shape documented in one place.
        "output_format": (
            "Markdown with one `## <Area>` heading per area that has tasks (BACKEND, FRONTEND, DATABASE, "
            "TESTING, INFRA, DOCS, in that order — omit an area with no tasks), and under each, one `### <task "
            "title>` block per task with these exact bold-labeled fields: **Description:**, **Linked Story:**, "
            "**Linked LLD Section:**, **Expected Files/Folders:** (a list), **Dependencies:** (other task "
            "titles, or 'None.'), **Acceptance Criteria:** (a checklist), **Test Expectation:**, **Risk Level:** "
            "(Low/Medium/High)."
        ),
        "validation_checklist": [
            "Every task names a real area: BACKEND, FRONTEND, DATABASE, TESTING, INFRA, or DOCS",
            "Every task traces back to a specific story and a specific LLD section — nothing invented",
            "Expected Files/Folders is concrete enough to start from, not a vague description",
            "Acceptance Criteria and Test Expectation are both present and specific per task",
            "Dependencies reference other task titles in this same plan, not vague prose",
            "Risk Level is stated for every task",
        ],
    },
    "implementation": {
        "system_prompt": (
            "You are the Implementation agent. Given the approved Low-Level Design and the in-scope stories, "
            "describe the code change that satisfies them: which files/modules change and why, the key logic "
            "introduced, and how it maps back to the LLD's interfaces. This is a change description for review, "
            "not literal source code — be concrete about what changes and why, not just what the outcome is."
        ),
        "output_format": "Markdown with headings: Summary, Files/Modules Changed, Key Logic, Mapping to LLD, Risks.",
        "validation_checklist": [
            "Every changed file/module has a stated reason for changing",
            "Key logic decisions are explained, not just asserted to work",
            "Explicitly maps back to the LLD's interfaces/contracts",
            "Risks or edge cases the change doesn't handle are called out, not hidden",
        ],
    },
    "pr_review": {
        "system_prompt": (
            "You are a senior software engineer performing a pull request review for a company-grade software "
            "delivery platform.\n\n"
            "Your job is to review the PR against:\n"
            "1. Approved LLD\n"
            "2. Related user stories\n"
            "3. Acceptance criteria\n"
            "4. Company coding standards\n"
            "5. Security rules\n"
            "6. Existing architecture\n"
            "7. Test expectations\n\n"
            "Review categories:\n"
            "- Functional correctness\n"
            "- Requirement alignment\n"
            "- Architecture alignment\n"
            "- Code quality\n"
            "- Security\n"
            "- Performance\n"
            "- Error handling\n"
            "- Observability\n"
            "- Test coverage\n"
            "- Maintainability\n\n"
            "Rules:\n"
            "- Be specific.\n"
            "- Reference changed files when possible.\n"
            "- Do not invent issues.\n"
            "- Do not approve if critical tests are missing.\n"
            "- Do not merge the PR.\n"
            "- Human reviewer has final authority."
        ),
        "output_format": (
            "Markdown with these headings, in this order:\n"
            "1. Overall Recommendation — exactly one of APPROVE, REQUEST_CHANGES, COMMENT_ONLY\n"
            "2. Summary\n"
            "3. Critical Findings (or 'None.')\n"
            "4. Major Findings (or 'None.')\n"
            "5. Minor Findings (or 'None.')\n"
            "6. Missing Tests (or 'None.')\n"
            "7. Suggested GitHub Comments — one per finding worth raising inline, each naming a file/line where "
            "possible\n"
            "8. Risk Score — an integer from 0 to 100\n"
            "9. Final Reviewer Note — a closing note that states plainly the human reviewer has final authority "
            "and this PR has not been merged"
        ),
        "validation_checklist": [
            "Overall Recommendation is exactly one of APPROVE, REQUEST_CHANGES, COMMENT_ONLY — never any other wording",
            "Never recommends APPROVE when Missing Tests names a critical gap",
            "Every finding (Critical/Major/Minor) references a specific changed file or behavior, not a generic complaint",
            "Suggested GitHub Comments are concrete enough to post as-is, each tied to a specific file where possible",
            "Risk Score is a single integer between 0 and 100, consistent with the findings' severity",
            "Never claims to have merged, or offers to merge, the PR — the Final Reviewer Note states the human reviewer has final authority",
            "Does not invent a finding that isn't actually supported by the diff, LLD, stories, acceptance criteria, or standards it was given",
        ],
    },
    "testing": {
        "system_prompt": (
            "You are the Testing agent. Given the reviewed code change, validate it against the in-scope stories' "
            "acceptance criteria and produce a test report. You must document real evidence of what was actually "
            "run — test counts, pass/fail results, and links to logs or CI runs where available — not just a "
            "claim that testing occurred."
        ),
        # The exact "## Test Evidence" heading is required — it's checked
        # programmatically by GraphEngineService.validate_evidence_requirement
        # (see WorkflowNode.required_evidence_section) before this stage's
        # review can be approved, not just read as prose.
        "output_format": (
            "Markdown with headings: Summary, Acceptance Criteria Coverage, Test Evidence (test counts, "
            "pass/fail results, and links to logs/CI runs — required, never leave this empty), Defects Found "
            "(or 'None.')."
        ),
        "validation_checklist": [
            "Every in-scope acceptance criterion is explicitly addressed",
            "Includes a non-empty Test Evidence section with concrete results, not a bare assertion",
            "Defects found are described specifically enough to act on",
            "Doesn't claim coverage the evidence doesn't actually support",
        ],
    },
    "infrastructure": {
        "system_prompt": (
            "You are the Infrastructure Provisioning agent. Given a tested change ready to release, define the "
            "infrastructure and environment configuration required to deploy it safely: what's provisioned or "
            "changed, required environment variables/secrets (names only, never values), and a rollback approach."
        ),
        "output_format": "Markdown with headings: Infrastructure Changes, Environment Configuration, Rollback Plan, Risks.",
        "validation_checklist": [
            "States exactly what infrastructure is provisioned or changed",
            "Never includes an actual secret value, only names/placeholders",
            "Includes a concrete rollback plan, not just 'roll back if needed'",
            "Risks specific to this deployment are named",
        ],
    },
    "maintenance": {
        "system_prompt": (
            "You are the Maintenance agent. Given a released change, summarize its post-release status: any "
            "incidents or issues observed, feedback received, and learnings worth feeding back into a future "
            "intake. Be factual about what's been observed — don't speculate about issues that haven't occurred."
        ),
        "output_format": "Markdown with headings: Post-Release Status, Incidents & Feedback (or 'None reported.'), Learnings for Future Intake.",
        "validation_checklist": [
            "Distinguishes observed facts from speculation",
            "Any incident/feedback entry is specific enough to act on",
            "Learnings are phrased so a future intake could actually use them",
        ],
    },
}


def _build_improve_system_prompt(node_data: dict) -> str:
    """The IMPROVE role's system prompt (see AgentPromptRole) — revising
    an existing draft against specific feedback, not drafting from
    scratch. build_prioritized_context (see app/services/ai_generation.py)
    already injects the prior draft/validation feedback as context for an
    IMPROVE-action run; this just sets the revision framing the model
    needs on top of that."""
    return (
        f"You are the {node_data['name']} agent, now revising your own previous draft of the "
        f"{node_data['outputArtifactType']}. You will be given the prior draft plus specific feedback "
        "(validation issues and/or reviewer comments) to address. Produce a complete, revised version of "
        "the full document — do not just describe the changes. Preserve everything that already satisfies "
        "the feedback or was never called into question; only change what the feedback actually raises. "
        "Do not introduce new problems, invent unrelated content, or remove material the feedback didn't "
        "ask you to remove."
    )


def _sync_default_prompt(
    db: Session,
    *,
    agent: AgentDefinition,
    role: AgentPromptRole,
    stage_key: str,
    name: str,
    system_prompt: str,
    output_format: str,
    validation_checklist: list[str],
) -> None:
    """Ensure this (agent, role) lineage's active version matches the
    current default content above — the same way ValidatorDefinition's
    criteria are kept in sync on every reseed (see
    _ensure_validator_definitions), but respecting AgentPrompt's own
    versioning: a change here creates a new version and activates it
    (exactly what editing a prompt through the Prompt Library does via
    POST /prompts/{id}/versions + /activate), rather than mutating an
    existing row's content in place. Without this, a RICH_DEFAULT_PROMPTS
    edit for a stage that was already seeded (e.g. from an earlier
    project's workflow generation) would silently never reach the
    database — the stage would keep serving its original content forever."""
    active = next((p for p in agent.prompts if p.role == role and p.is_active), None)
    if (
        active is not None
        and active.system_prompt == system_prompt
        and active.output_format == output_format
        and active.validation_checklist == validation_checklist
    ):
        return  # already in sync — nothing to do

    next_version = max((p.version for p in agent.prompts if p.role == role), default=0) + 1
    if active is not None:
        active.is_active = False
    db.add(
        AgentPrompt(
            agent_definition=agent,
            role=role,
            version=next_version,
            name=name,
            stage=stage_key,
            system_prompt=system_prompt,
            output_format=output_format,
            validation_checklist=validation_checklist,
            is_active=True,
        )
    )
    db.flush()


def _ensure_agent_definitions_and_prompts(db: Session, template: dict) -> dict[str, AgentDefinition]:
    """Ensure every template node's AgentDefinition + an active DRAFT and
    IMPROVE AgentPrompt each exist, with content matching this file's
    current RICH_DEFAULT_PROMPTS (VALIDATE is a separate concept — see
    app/services/validator_agent.py's ValidatorDefinition, not an
    AgentPrompt role).

    Not project-owned data, so this runs unconditionally (unlike the rest
    of seed(), which is skipped once the sample project exists). Creating
    the AgentDefinition/first version is idempotent by agent_key; keeping
    an existing lineage's *content* in sync with a later
    RICH_DEFAULT_PROMPTS edit is handled by _sync_default_prompt, which
    creates and activates a new version rather than mutating history.
    """
    agent_definitions: dict[str, AgentDefinition] = {}
    for node_data in template["nodes"]:
        agent_key = node_data["agentKey"]
        agent = db.query(AgentDefinition).filter(AgentDefinition.agent_key == agent_key).first()
        if agent is None:
            agent = AgentDefinition(
                agent_key=agent_key,
                name=node_data["name"] + " Agent",
                description=f"Drafts, improves, and validates the {node_data['outputArtifactType']} "
                f"produced by the {node_data['name']} stage.",
                model_name="stub-no-model-configured",  # no real AI calls yet
            )
            db.add(agent)
            db.flush()
        agent_definitions[agent_key] = agent

    for node_data in template["nodes"]:
        stage_key = node_data["id"]
        agent = agent_definitions[node_data["agentKey"]]
        rich = RICH_DEFAULT_PROMPTS.get(stage_key)
        default_system_prompt = rich["system_prompt"] if rich else (
            f"You are the {node_data['name']} agent. Given the following inputs: "
            f"{', '.join(node_data['requiredInputs']) or 'none'}, draft a "
            f"{node_data['outputArtifactType']}. {node_data['description']}"
        )
        default_output_format = rich["output_format"] if rich else "Markdown document."
        default_checklist = rich["validation_checklist"] if rich else [
            "Addresses all of the stage's required inputs",
            "Uses valid Markdown formatting",
        ]
        _sync_default_prompt(
            db, agent=agent, role=AgentPromptRole.DRAFT, stage_key=stage_key,
            name=f"{node_data['name']} — Draft Prompt",
            system_prompt=default_system_prompt, output_format=default_output_format, validation_checklist=default_checklist,
        )

    for node_data in template["nodes"]:
        stage_key = node_data["id"]
        agent = agent_definitions[node_data["agentKey"]]
        # Same output_format/validation_checklist as the DRAFT prompt — an
        # improve pass produces the same kind of document, just revised;
        # only the system_prompt's framing (draft vs. revise) differs.
        rich = RICH_DEFAULT_PROMPTS.get(stage_key)
        default_output_format = rich["output_format"] if rich else "Markdown document."
        default_checklist = rich["validation_checklist"] if rich else [
            "Addresses all of the stage's required inputs",
            "Uses valid Markdown formatting",
        ]
        _sync_default_prompt(
            db, agent=agent, role=AgentPromptRole.IMPROVE, stage_key=stage_key,
            name=f"{node_data['name']} — Improve Prompt",
            system_prompt=_build_improve_system_prompt(node_data),
            output_format=default_output_format, validation_checklist=default_checklist,
        )

    db.flush()
    return agent_definitions


# Generic fallback criteria for a stage with no entry in RICH_DEFAULT_PROMPTS
# above — a validator's own rubric is deliberately not identical to the
# drafting agent's validation_checklist (see app/models/validator.py), so
# this adds a risk/clarity angle the agent's own self-check doesn't cover.
_GENERIC_VALIDATOR_CRITERIA = [
    "Covers the stage's required inputs and output artifact type",
    "Calls out relevant risks, constraints, or assumptions explicitly",
    "Is clearly structured (headings/sections), not one unbroken block of text",
]


def _ensure_validator_definitions(db: Session, template: dict) -> dict[str, ValidatorDefinition]:
    """Ensure one ValidatorDefinition exists per default-workflow stage
    (see app/services/validator_agent.py) — idempotent by stage, like
    _ensure_agent_definitions_and_prompts above, and likewise runs
    unconditionally rather than being gated by the sample project check.

    Unlike AgentPrompt (versioned — editing an active version is refused,
    see app/api/routes/prompts.py), a validator has no version history, so
    its `criteria` is kept in sync with RICH_DEFAULT_PROMPTS on every run
    rather than only set at creation — otherwise a stage's rubric silently
    goes stale the moment its DRAFT prompt's output_format changes (a real
    bug: a validator kept scoring against the *old* template's checklist
    after the prompt moved to a new one, tanking every real draft's
    completeness score for no good reason).
    """
    validators: dict[str, ValidatorDefinition] = {}
    for node_data in template["nodes"]:
        stage_key = node_data["id"]
        rich = RICH_DEFAULT_PROMPTS.get(stage_key)
        criteria = rich["validation_checklist"] if rich else list(_GENERIC_VALIDATOR_CRITERIA)

        validator = db.query(ValidatorDefinition).filter(ValidatorDefinition.stage == stage_key).first()
        if validator is None:
            validator = ValidatorDefinition(
                validator_key=f"{stage_key}-validator",
                name=f"{node_data['name']} Validator",
                stage=stage_key,
                description=f"Independently scores a drafted {node_data['outputArtifactType']} for the "
                f"{node_data['name']} stage before it's shown to a human reviewer.",
                model_name="stub-no-model-configured",  # no real AI call required — see get_active_provider
                criteria=criteria,
            )
            db.add(validator)
            db.flush()
        elif validator.criteria != criteria:
            validator.criteria = criteria
        validators[stage_key] = validator
    return validators


def _ensure_story_lld_agent(db: Session) -> AgentDefinition:
    """Ensures the story-lld-agent AgentDefinition + an active DRAFT
    AgentPrompt exist — the STORY_LLD story-delivery-lane node's real
    agent (see app/services/story_lld_agent.py). Not part of
    _ensure_agent_definitions_and_prompts above because STORY_LLD has no
    corresponding row in any project-level workflow template's `nodes`
    list — it's a per-story StoryDeliveryNode, a genuinely different
    concept (see app/models/story_delivery_node.py)."""
    agent_key = "story-lld-agent"
    agent = db.query(AgentDefinition).filter(AgentDefinition.agent_key == agent_key).first()
    if agent is None:
        agent = AgentDefinition(
            agent_key=agent_key,
            name="Story LLD Agent",
            description="Drafts a Low-Level Design scoped to exactly one story, for that story's own delivery lane.",
            model_name="stub-no-model-configured",
        )
        db.add(agent)
        db.flush()

    rich = RICH_DEFAULT_PROMPTS["story_lld"]
    _sync_default_prompt(
        db, agent=agent, role=AgentPromptRole.DRAFT, stage_key="story_lld",
        name="Story LLD — Draft Prompt",
        system_prompt=rich["system_prompt"], output_format=rich["output_format"], validation_checklist=rich["validation_checklist"],
    )
    db.flush()
    return agent


def _ensure_story_implementation_plan_agent(db: Session) -> AgentDefinition:
    """Ensures the story-implementation-plan-agent AgentDefinition + an
    active DRAFT AgentPrompt exist — the IMPLEMENTATION_PLAN
    story-delivery-lane node's real agent (see
    app/services/story_implementation_plan_agent.py). Same reasoning as
    _ensure_story_lld_agent above for why this is seeded on its own."""
    agent_key = "story-implementation-plan-agent"
    agent = db.query(AgentDefinition).filter(AgentDefinition.agent_key == agent_key).first()
    if agent is None:
        agent = AgentDefinition(
            agent_key=agent_key,
            name="Story Implementation Plan Agent",
            description="Drafts an implementation plan scoped to exactly one story, for that story's own delivery lane.",
            model_name="stub-no-model-configured",
        )
        db.add(agent)
        db.flush()

    rich = RICH_DEFAULT_PROMPTS["story_implementation_plan"]
    _sync_default_prompt(
        db, agent=agent, role=AgentPromptRole.DRAFT, stage_key="story_implementation_plan",
        name="Story Implementation Plan — Draft Prompt",
        system_prompt=rich["system_prompt"], output_format=rich["output_format"], validation_checklist=rich["validation_checklist"],
    )
    db.flush()
    return agent


def _ensure_story_lld_validator_definition(db: Session) -> ValidatorDefinition:
    """Ensures a story-lld-validator ValidatorDefinition exists — same
    validator_key convention as _ensure_validator_definitions above
    (f"{stage_key}-validator"), added on its own here for the same reason
    _ensure_story_lld_agent is: STORY_LLD has no row in any project-level
    workflow template's `nodes` list, so the loop that seeds one
    ValidatorDefinition per template stage never reaches it.

    DISCLOSED SCOPE: unlike a project-level stage's validator (run
    automatically by app/services/loop_engine.py's LoopEngineService
    after every GENERATE_DRAFT/IMPROVE step), nothing yet calls this one
    automatically — app/services/story_lld_agent.py's run_story_lld_agent
    is a single generate() call, not routed through the loop engine (a
    story-delivery-lane node isn't a WorkflowNode). This row exists so
    the stage has a real, discoverable rubric (same criteria shape as
    every other stage's validator, listed the same way in the UI) ready
    for whenever a story-lane loop/validation flow is built."""
    rich = RICH_DEFAULT_PROMPTS["story_lld"]
    criteria = rich["validation_checklist"]

    validator = db.query(ValidatorDefinition).filter(ValidatorDefinition.stage == "story_lld").first()
    if validator is None:
        validator = ValidatorDefinition(
            validator_key="story-lld-validator",
            name="Story LLD Validator",
            stage="story_lld",
            description="Independently scores a drafted Story LLD for one story's own delivery lane before it's shown to the Tech Lead reviewer.",
            model_name="stub-no-model-configured",  # no real AI call required — see get_active_provider
            criteria=criteria,
        )
        db.add(validator)
        db.flush()
    elif validator.criteria != criteria:
        validator.criteria = criteria
    return validator


def _kb_chunk(
    content: str,
    *,
    content_type: KnowledgeContentType = KnowledgeContentType.OTHER,
    stage: str | None = None,
    domain: str | None = None,
    project_type: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    return {
        "content": content,
        "content_type": content_type,
        "stage": stage,
        "domain": domain,
        "project_type": project_type,
        "tags": tags or [],
    }


# Deliberately covers all five KnowledgeContentType values RAG is required
# to support (see app/services/retrieval.py's module docstring) —
# company standards and architecture rules are stage-agnostic (no `stage`,
# so every stage can retrieve them); past artifacts/UI guidelines/testing
# standards are tagged to the stage(s) that would actually use them, to
# demonstrate stage-aware retrieval filtering them out elsewhere.
KNOWLEDGE_BASE_SAMPLES: list[dict] = [
    {
        "title": "Company Engineering Handbook",
        "category": "Company Policy",
        "source_type": KnowledgeSourceType.UPLOADED_DOCUMENT,
        "status": KnowledgeSourceStatus.INDEXED,
        "chunks": [
            _kb_chunk(
                "All new services must expose a /health endpoint returning 200 when ready to serve traffic.",
                content_type=KnowledgeContentType.COMPANY_STANDARD,
                domain="engineering",
                tags=["health-check", "service-standards"],
            ),
            _kb_chunk(
                "Database migrations are reviewed the same way as code — no direct schema changes in production.",
                content_type=KnowledgeContentType.COMPANY_STANDARD,
                domain="engineering",
                tags=["database", "change-management"],
            ),
        ],
    },
    {
        "title": "Testing Standards",
        "category": "Engineering Standards",
        "source_type": KnowledgeSourceType.UPLOADED_DOCUMENT,
        "status": KnowledgeSourceStatus.INDEXED,
        "chunks": [
            _kb_chunk(
                "Every pull request must include automated tests for new behavior; a PR that only adds code "
                "without tests is not merged.",
                content_type=KnowledgeContentType.TESTING_STANDARD,
                domain="engineering",
                stage="testing",
                tags=["tests", "pull-requests"],
            ),
            _kb_chunk(
                "A test report must state pass/fail counts, list any skipped tests with a reason, and call out "
                "coverage gaps explicitly rather than silently omitting untested paths.",
                content_type=KnowledgeContentType.TESTING_STANDARD,
                domain="quality",
                stage="testing",
                tags=["test-report", "coverage"],
            ),
        ],
    },
    {
        "title": "Architecture Decision Standards",
        "category": "Engineering Standards",
        "source_type": KnowledgeSourceType.UPLOADED_DOCUMENT,
        "status": KnowledgeSourceStatus.INDEXED,
        "chunks": [
            _kb_chunk(
                "Services communicate asynchronously via events for cross-boundary side effects; synchronous "
                "calls are reserved for request/response reads within a single bounded context.",
                content_type=KnowledgeContentType.ARCHITECTURE_RULE,
                domain="architecture",
                stage="hld",
                tags=["event-driven", "service-boundaries"],
            ),
            _kb_chunk(
                "Every new external integration goes through the shared API gateway — application code never "
                "calls a third-party service directly.",
                content_type=KnowledgeContentType.ARCHITECTURE_RULE,
                domain="architecture",
                stage="hld",
                tags=["integrations", "api-gateway"],
            ),
        ],
    },
    {
        "title": "Cloud Infrastructure Standards",
        "category": "Engineering Standards",
        "source_type": KnowledgeSourceType.UPLOADED_DOCUMENT,
        "status": KnowledgeSourceStatus.INDEXED,
        "chunks": [
            _kb_chunk(
                "All production environments are provisioned via Infrastructure-as-Code (Terraform); no manual "
                "console changes are permitted against a production account.",
                content_type=KnowledgeContentType.COMPANY_STANDARD,
                domain="cloud-infrastructure",
                stage="infrastructure_planning",
                tags=["iac", "provisioning"],
            ),
            _kb_chunk(
                "Secrets are never stored in application config or source control — only in the organization's "
                "secrets manager, referenced by name at deploy time.",
                content_type=KnowledgeContentType.COMPANY_STANDARD,
                domain="cloud-infrastructure",
                stage="infrastructure_planning",
                tags=["secrets", "security"],
            ),
            _kb_chunk(
                "Every service ships structured logs and at least one health/liveness metric to the central "
                "observability stack before it can go to production.",
                content_type=KnowledgeContentType.COMPANY_STANDARD,
                domain="cloud-infrastructure",
                stage="infrastructure_planning",
                tags=["monitoring", "observability"],
            ),
        ],
    },
    {
        "title": "UI Design System Guidelines",
        "category": "Design",
        "source_type": KnowledgeSourceType.UPLOADED_DOCUMENT,
        "status": KnowledgeSourceStatus.INDEXED,
        "chunks": [
            _kb_chunk(
                "Primary actions use the filled button style; secondary or destructive actions use outline or "
                "text buttons — never two filled buttons side by side.",
                content_type=KnowledgeContentType.UI_GUIDELINE,
                domain="ux",
                project_type="web-app",
                stage="story_crafting",
                tags=["buttons", "visual-hierarchy"],
            ),
            _kb_chunk(
                "All interactive elements must meet WCAG 2.1 AA contrast ratios (4.5:1 for normal text) and be "
                "reachable via keyboard alone.",
                content_type=KnowledgeContentType.UI_GUIDELINE,
                domain="accessibility",
                stage="story_crafting",
                tags=["accessibility", "wcag"],
            ),
        ],
    },
    {
        "title": "Past Project Retrospective — Fraud Detection Overhaul",
        "category": "Project Artifact",
        "source_type": KnowledgeSourceType.PROJECT_ARTIFACT,
        "status": KnowledgeSourceStatus.INDEXED,
        "chunks": [
            _kb_chunk(
                "The Fraud Detection Overhaul project underestimated data migration effort by roughly 3x; "
                "projects touching the same legacy ledger tables should budget migration as its own phase, not "
                "a sub-task of implementation.",
                content_type=KnowledgeContentType.PAST_ARTIFACT,
                domain="delivery",
                stage="lld",
                tags=["retrospective", "estimation"],
            ),
        ],
    },
    {
        "title": "Loyalty Program Design Guidelines",
        "category": "Market Research",
        "source_type": KnowledgeSourceType.EXTERNAL_LINK,
        "file_url": "https://example.com/research/loyalty-programs-2026",
        "status": KnowledgeSourceStatus.INDEXED,
        "chunks": [
            _kb_chunk(
                "Points-based loyalty programs see the strongest repeat purchase impact when redemption is "
                "simple: customers should be able to see their points balance and redeem it in two taps or fewer."
            ),
            _kb_chunk(
                "Competitor loyalty programs in retail typically award 1 point per $1 spent and set redemption "
                "thresholds low enough (around 100-200 points) that a customer's first reward feels achievable "
                "within a few purchases, which drives early engagement."
            ),
            _kb_chunk(
                "Loyalty programs that expire points aggressively see higher churn; a rolling 12-month expiry "
                "window is a common balance between encouraging return visits and not feeling punitive."
            ),
        ],
    },
    {
        "title": "Requirement Intake Summary — Customer Loyalty Rewards Platform",
        "category": "Project Artifact",
        "source_type": KnowledgeSourceType.PROJECT_ARTIFACT,
        "status": KnowledgeSourceStatus.FAILED,
        "chunks": [],
    },
]


def _ensure_knowledge_base_samples(db: Session, uploaded_by: User) -> int:
    """Ensure a few sample KnowledgeSource rows exist, spanning every
    KnowledgeContentType RAG is required to support (see
    KNOWLEDGE_BASE_SAMPLES's own comment), so the Knowledge Base UI has
    something to show, and so agent runs against the sample project have
    something real, stage-tagged, and content-type-tagged to retrieve (see
    app/services/retrieval.py). Idempotent by title. Chunks are embedded
    immediately with the same app/services/embeddings.py function real
    ingestion would use — these aren't placeholder vectors.
    """
    created = 0
    for sample in KNOWLEDGE_BASE_SAMPLES:
        existing_source = db.query(KnowledgeSource).filter(KnowledgeSource.title == sample["title"]).first()
        if existing_source is not None:
            continue

        source = KnowledgeSource(
            title=sample["title"],
            category=sample["category"],
            source_type=sample["source_type"],
            file_url=sample.get("file_url"),
            status=sample["status"],
            uploaded_by=uploaded_by,
        )
        db.add(source)
        db.flush()

        for i, chunk_data in enumerate(sample["chunks"]):
            db.add(
                KnowledgeChunk(
                    source=source,
                    chunk_index=i,
                    content=chunk_data["content"],
                    content_type=chunk_data["content_type"],
                    stage=chunk_data["stage"],
                    domain=chunk_data["domain"],
                    project_type=chunk_data["project_type"],
                    tags=chunk_data["tags"],
                    embedding=embed_text(chunk_data["content"]),
                )
            )

        created += 1
    db.flush()
    return created


STORY_BACKLOG_DEMO_TITLE = f"Story Backlog — {SAMPLE_PROJECT_NAME}"

# Demo content for the Export Stories feature (see
# app/services/story_export.py) — hand-authored in the exact format the
# story-crafting-agent's prompt requires, so the parser has something real
# to work against without needing a live agent run first. Note this
# artifact is seeded APPROVED independent of the sample project's actual
# workflow progress (which stops at Problem Discovery) — it exists purely
# to demonstrate/exercise the export feature, not to claim the project has
# really reached Story Crafting.
STORY_BACKLOG_DEMO_CONTENT = """# Story Backlog — Customer Loyalty Rewards Platform

## Story: Display points balance on account home
**Epic:** Loyalty Points Program
**Feature:** Points Balance Display
**User Story:** As a customer, I want to see my current points balance on my account home screen, so that I always know how many points I have without digging through menus.
**Priority:** High
**Dependencies:** None.
**Acceptance Criteria:**
- [ ] Points balance is visible on the account home screen within one screen load, no extra taps.
- [ ] Balance updates within 5 minutes of a qualifying purchase.
- [ ] Balance displays "0 points" rather than a blank/error state for a new customer.
**Definition of Done:**
- [ ] Code reviewed and merged.
- [ ] Unit tests cover the zero-balance and stale-cache cases.
- [ ] Verified against the points ledger service in staging.

## Story: Redeem points for a discount at checkout
**Epic:** Loyalty Points Program
**Feature:** Points Redemption
**User Story:** As a customer, I want to redeem my points for a discount at checkout, so that I get value from my accumulated points in two taps or fewer.
**Priority:** High
**Dependencies:** Display points balance on account home.
**Acceptance Criteria:**
- [ ] Customer can apply available points as a discount in at most two taps from the cart screen.
- [ ] Partial redemption is supported when the order total is less than the full points value.
- [ ] Points balance and order total both reflect the redemption before payment is confirmed.
**Definition of Done:**
- [ ] Code reviewed and merged.
- [ ] Integration test covers full and partial redemption.
- [ ] Reviewed by Payments for correct ledger reconciliation.

## Story: Expire unused points after a rolling 12-month window
**Epic:** Loyalty Points Program
**Feature:** Points Expiry
**User Story:** As the business, I want unused points to expire on a rolling 12-month window, so that the points liability doesn't grow unbounded while still giving customers a fair amount of time to redeem.
**Priority:** Medium
**Dependencies:** Display points balance on account home.
**Acceptance Criteria:**
- [ ] Points earned more than 12 months ago are automatically excluded from the redeemable balance.
- [ ] Customer receives a notification 30 days before their oldest points expire.
- [ ] Expired points are recorded in the ledger for audit purposes, not silently dropped.
**Definition of Done:**
- [ ] Code reviewed and merged.
- [ ] Scheduled job tested against a seeded ledger spanning more than 12 months.
- [ ] Notification copy approved by Marketing.
"""


def _ensure_story_export_demo(db: Session, project: Project, author: User) -> bool:
    """Idempotent by title. Returns True if it created the demo artifact."""
    existing_artifact = db.query(Artifact).filter(Artifact.title == STORY_BACKLOG_DEMO_TITLE).first()
    if existing_artifact is not None:
        return False

    story_crafting_node = next((n for n in project.workflow_nodes if n.node_key == "story_crafting"), None)
    if story_crafting_node is None:
        return False

    artifact = Artifact(
        project=project,
        workflow_node=story_crafting_node,
        artifact_type=story_crafting_node.output_artifact_type,
        title=STORY_BACKLOG_DEMO_TITLE,
        status=ArtifactStatus.APPROVED,
        created_by=author,
    )
    db.add(artifact)
    db.flush()

    version = ArtifactVersion(
        artifact=artifact,
        version_number=1,
        content_markdown=STORY_BACKLOG_DEMO_CONTENT,
        created_by=author,
        change_summary="Demo backlog for the Export Stories feature.",
    )
    db.add(version)
    db.flush()
    artifact.current_version = version

    record_audit_log(
        db,
        project_id=project.id,
        actor_user_id=author.id,
        action="artifact.created",
        entity_type="Artifact",
        entity_id=artifact.id,
        extra_data={"workflow_node": story_crafting_node.node_key, "artifact_type": artifact.artifact_type, "seeded_demo": True},
    )
    db.flush()
    return True


INTEGRATION_PLACEHOLDERS: list[dict] = [
    {"integration_name": "Jira", "provider": IntegrationProvider.JIRA},
    {"integration_name": "Confluence", "provider": IntegrationProvider.CONFLUENCE},
    {"integration_name": "GitHub", "provider": IntegrationProvider.GITHUB},
    {"integration_name": "Slack", "provider": IntegrationProvider.SLACK},
    {"integration_name": "Azure DevOps", "provider": IntegrationProvider.AZURE_DEVOPS},
]


def _ensure_integration_placeholders(db: Session) -> int:
    """Ensures one placeholder Integration row per planned provider exists
    (see docs/architecture.md's MCP integrations section) — idempotent by
    provider. Every row is created NOT_CONNECTED with no config; nothing
    here ever actually connects (see app/models/integration.py)."""
    created = 0
    for placeholder in INTEGRATION_PLACEHOLDERS:
        existing = db.query(Integration).filter(Integration.provider == placeholder["provider"]).first()
        if existing is not None:
            continue
        db.add(
            Integration(
                integration_name=placeholder["integration_name"],
                provider=placeholder["provider"],
                status=IntegrationStatus.NOT_CONNECTED,
            )
        )
        created += 1
    db.flush()
    return created


def seed(db: Session) -> None:
    template = load_workflow_template()
    agent_definitions = _ensure_agent_definitions_and_prompts(db, template)
    validator_definitions = _ensure_validator_definitions(db, template)

    # Scrum story lanes — a second, opt-in project-level template (see
    # workflows/scrum-story-lanes-workflow.json). Ensured unconditionally,
    # same as the default template above. A story's own delivery lane
    # (see app/services/story_delivery.py's StoryDeliveryLane/Node) is a
    # separate, dedicated model — not a WorkflowNode-based template — so
    # there's no second template file to ensure here for it.
    for extra_template_file in ("scrum-story-lanes-workflow.json",):
        extra_template = load_workflow_template(extra_template_file)
        extra_agents = _ensure_agent_definitions_and_prompts(db, extra_template)
        extra_validators = _ensure_validator_definitions(db, extra_template)
        agent_definitions.update(extra_agents)
        validator_definitions.update(extra_validators)

    # STORY_LLD — the one story-delivery-lane node with real AI drafting
    # attached (see app/services/story_lld_agent.py). No WorkflowNode
    # template lists it, so it's ensured on its own.
    agent_definitions["story-lld-agent"] = _ensure_story_lld_agent(db)
    validator_definitions["story_lld"] = _ensure_story_lld_validator_definition(db)
    agent_definitions["story-implementation-plan-agent"] = _ensure_story_implementation_plan_agent(db)

    # Not project-owned, so — like agent definitions/prompts above — this
    # runs unconditionally rather than being gated by the sample project
    # existence check below.
    kb_owner = _get_or_create_user(db, email="sampathisuru516@gmail.com", full_name="Suru Sampathi", role=UserRole.ADMIN)
    knowledge_sources_created = _ensure_knowledge_base_samples(db, kb_owner)
    integrations_created = _ensure_integration_placeholders(db)

    # Also not project-owned — ensured unconditionally so re-running this
    # script converges these three seed users' roles even when the sample
    # project already exists and the rest of this function is skipped below
    # (e.g. after the User.role migration backfilled everyone to VIEWER).
    contributor = _get_or_create_user(db, email="priya.dev@agentic-sdlc-hub.local", full_name="Priya Dev", role=UserRole.BA)
    approver = _get_or_create_user(
        db, email="alex.reviewer@agentic-sdlc-hub.local", full_name="Alex Reviewer", role=UserRole.PRODUCT_OWNER
    )

    existing = db.query(Project).filter(Project.name == SAMPLE_PROJECT_NAME).first()
    if existing is not None:
        story_demo_created = _ensure_story_export_demo(db, existing, kb_owner)
        db.commit()
        print(f"Sample project '{SAMPLE_PROJECT_NAME}' already exists (id={existing.id}); skipping project seed.")
        print(f"Agent definitions ensured: {len(agent_definitions)}.")
        print(f"Validator definitions ensured: {len(validator_definitions)}.")
        print(f"Knowledge sources created: {knowledge_sources_created}.")
        print(f"Story export demo artifact created: {story_demo_created}.")
        print(f"Integration placeholders created: {integrations_created}.")
        return

    now = _now()

    # --- Users -----------------------------------------------------------
    owner = kb_owner

    # --- Project + membership + generated workflow graph ------------------
    project = Project(
        name=SAMPLE_PROJECT_NAME,
        description=(
            "Add a points-based loyalty rewards program to the customer mobile app: "
            "customers earn points on purchases and redeem them for discounts."
        ),
        business_owner="Marketing — Jordan Lee",
        workflow_template_id=template["id"],
        workflow_template_version=template["version"],
        current_stage=template["startNode"],
        status=ProjectStatus.ACTIVE,
        created_by=owner,
    )
    db.add(project)
    db.flush()

    db.add_all(
        [
            ProjectMember(project=project, user=owner, role=ProjectRole.OWNER),
            ProjectMember(project=project, user=contributor, role=ProjectRole.CONTRIBUTOR),
            ProjectMember(project=project, user=approver, role=ProjectRole.APPROVER),
        ]
    )

    generate_workflow_graph(db, project, template)
    db.flush()

    def log(**kwargs) -> AuditLog:
        """Create + immediately register an AuditLog row.

        Added to the session right away (rather than batched into a list
        for a later add_all) so the Project.audit_logs backref population
        that happens on construction doesn't warn about an object that
        isn't in the session yet.
        """
        entry = AuditLog(**kwargs)
        db.add(entry)
        return entry

    log(
        project=project,
        actor_user=owner,
        action="project.created",
        entity_type="Project",
        entity_id=project.id,
        extra_data={"workflow_template_id": template["id"], "workflow_template_version": template["version"]},
        created_at=now - timedelta(days=2, hours=1),
    )

    # --- Walk the first stage (Requirement Intake) through to approval ----
    intake_node = next(n for n in project.workflow_nodes if n.node_key == "requirement_intake")
    problem_discovery_node = next(n for n in project.workflow_nodes if n.node_key == "problem_discovery")
    intake_agent = agent_definitions["requirement-intake-agent"]
    intake_prompt = intake_agent.prompts[0]

    drafted_at = now - timedelta(days=2)
    agent_run = AgentRun(
        project=project,
        workflow_node=intake_node,
        agent_definition=intake_agent,
        agent_prompt=intake_prompt,
        triggered_by_user=contributor,
        action=AgentPromptRole.DRAFT,
        status=AgentRunStatus.COMPLETED,
        input_context={
            "stakeholder_request": (
                "Marketing wants a loyalty program to improve repeat purchase rate; "
                "no fixed launch date yet, but it should ship before the holiday season."
            )
        },
        output_text=(
            "# Requirement Intake Summary\n\n"
            "**Stakeholder:** Marketing\n\n"
            "**Request:** Introduce a points-based loyalty program in the customer "
            "mobile app so customers earn points on purchases and redeem them for "
            "discounts, to improve repeat purchase rate.\n\n"
            "**Constraints:** No fixed launch date; target shipping ahead of the "
            "holiday season.\n"
        ),
        started_at=drafted_at,
        completed_at=drafted_at,
    )
    db.add(agent_run)
    db.flush()

    log(
        project=project,
        actor_agent_run=agent_run,
        action="agent_run.completed",
        entity_type="AgentRun",
        entity_id=agent_run.id,
        extra_data={"workflow_node": intake_node.node_key, "action": "draft"},
        created_at=drafted_at,
    )

    artifact = Artifact(
        project=project,
        workflow_node=intake_node,
        artifact_type=intake_node.output_artifact_type,
        title=f"Requirement Intake Summary — {SAMPLE_PROJECT_NAME}",
        status=ArtifactStatus.READY_FOR_REVIEW,
        created_by=contributor,
    )
    db.add(artifact)
    db.flush()

    v1 = ArtifactVersion(
        artifact=artifact,
        version_number=1,
        content_markdown=agent_run.output_text,
        # No agent-authorship link yet (AI drafting isn't wired up — see
        # docs/mvp-plan.md); recorded as created by whoever triggered the
        # draft request.
        created_by=contributor,
        change_summary="Initial AI-drafted intake summary.",
        created_at=drafted_at,
    )
    db.add(v1)
    db.flush()
    artifact.current_version = v1

    edited_at = now - timedelta(days=1, hours=6)
    v2 = ArtifactVersion(
        artifact=artifact,
        version_number=2,
        content_markdown=v1.content_markdown
        + "\n**Success metric:** +15% repeat purchase rate within 2 quarters of launch.\n",
        created_by=contributor,
        change_summary="Clarified success metrics after stakeholder sync.",
        created_at=edited_at,
    )
    db.add(v2)
    db.flush()
    artifact.current_version = v2

    log(
        project=project,
        actor_user=contributor,
        action="artifact_version.created",
        entity_type="ArtifactVersion",
        entity_id=v2.id,
        extra_data={"artifact_id": str(artifact.id), "version_number": 2},
        created_at=edited_at,
    )

    submitted_at = now - timedelta(days=1, hours=5)
    review = Review(
        artifact_version=v2,
        workflow_node=intake_node,
        reviewer=approver,
        status=ReviewStatus.PENDING,
        created_at=submitted_at,
    )
    db.add(review)
    db.flush()

    decided_at = now - timedelta(days=1)
    review.status = ReviewStatus.APPROVED
    review.decided_at = decided_at

    db.add(
        ReviewComment(
            review=review,
            author=approver,
            body=(
                "Looks good — clear problem framing and a measurable success "
                "metric. Approved to proceed to Problem Discovery."
            ),
            created_at=decided_at,
        )
    )

    artifact.status = ArtifactStatus.APPROVED
    # Real engine, not hand-rolled status assignments — see
    # app/services/graph_engine.py. mark_approved() is what a real
    # POST /reviews/{id}/approve call does; unlock_next_nodes() is what
    # actually decides problem_discovery_node should become READY (rather
    # than assuming it here), so this stays correct if the template ever
    # changes.
    graph_engine = GraphEngineService(db)
    graph_engine.mark_approved(intake_node)
    unlocked = graph_engine.unlock_next_nodes(intake_node)
    project.current_stage = problem_discovery_node.node_key

    log(
        project=project,
        actor_user=approver,
        action="review.approved",
        entity_type="Review",
        entity_id=review.id,
        extra_data={"artifact_id": str(artifact.id), "workflow_node": intake_node.node_key},
        created_at=decided_at,
    )
    log(
        project=project,
        actor_user=approver,
        action="workflow_node.status_changed",
        entity_type="WorkflowNode",
        entity_id=intake_node.id,
        extra_data={"from": "WAITING_FOR_REVIEW", "to": WorkflowStatus.APPROVED.value},
        created_at=decided_at,
    )
    for node in unlocked:
        log(
            project=project,
            actor_user=approver,
            action="workflow_node.unlocked",
            entity_type="WorkflowNode",
            entity_id=node.id,
            extra_data={"from": "LOCKED", "to": WorkflowStatus.READY.value},
            created_at=decided_at,
        )

    story_demo_created = _ensure_story_export_demo(db, project, contributor)
    db.commit()

    print(f"Seeded sample project '{SAMPLE_PROJECT_NAME}' (id={project.id}).")
    print(f"  Users: owner={owner.email}, contributor={contributor.email}, approver={approver.email}")
    print(f"  Workflow nodes: {len(project.workflow_nodes)}, edges: {len(project.workflow_edges)}")
    print(f"  {intake_node.name}: {intake_node.status.value} -> {problem_discovery_node.name}: {problem_discovery_node.status.value}")
    print(f"  Validator definitions ensured: {len(validator_definitions)}.")
    print(f"  Knowledge sources created: {knowledge_sources_created}.")
    print(f"  Story export demo artifact created: {story_demo_created}.")


def main() -> None:
    db = SessionLocal()
    try:
        seed(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
