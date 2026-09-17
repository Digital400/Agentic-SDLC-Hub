<!-- GENERATED FILE — do not edit by hand. Source of truth: .sdlc/skills/ and .sdlc/policies/, rendered by apps/api/app/prompt_compiler/vendor_export.py. Regenerate with: cd apps/api && .venv/Scripts/python.exe -m app.prompt_compiler.vendor_export -->

# CLAUDE.md — Agentic SDLC Hub

Instructions for Claude Code (or any Claude-based runtime) working in this repository. Generated from the same canonical `.sdlc/skills/`/`.sdlc/policies/` catalog as every other vendor instruction file here — see AGENTS.md for the vendor-neutral version of this content.

## Skills

### High-Level Design Skill (`hld`, v1.0.0)

**Applies to:** HLD

Define the system-level architecture for an approved, recommended solution option — major components, their interactions, the high-level data model, and key security considerations.

**Never:**
- Do not produce a full database schema or API contract — that belongs to Low-Level Design.
- Do not invent architectural decisions the recommended option's constraints don't support.
- Do not silently drop a security consideration because it's inconvenient — flag it as an open question instead.

### Secure Implementation Skill (`implement-story`, v1.0.0)

**Applies to:** IMPLEMENT_STORY

Implement one approved implementation task as a proposed diff, honoring the project's ScopePolicy/ToolPolicy/approved security-scan commands at every step, never claiming a change was applied, committed, or pushed.

**Never:**
- Do not touch a path outside the ProjectExecutionProfile's allowed_paths, or inside its denied_paths, under any circumstance.
- Do not run a command outside allowed_command_patterns, or matching denied_command_patterns.
- Do not commit, push, merge, or claim to have done any of those — this skill produces a proposed diff for a human to review, nothing more.
- Do not hardcode a secret, API key, or credential anywhere in proposed content — reference an environment variable NAME instead (see ProjectExecutionProfile.environment_variable_names).
- Do not silently skip a BLOCKING quality gate — if one cannot be satisfied, say so in Risks.

### Implementation Planning Skill (`implementation-plan`, v1.0.0)

**Applies to:** IMPLEMENTATION_PLAN

Break an approved LLD and story backlog into concrete, independently reviewable implementation tasks, each assigned to one area (Backend, Frontend, Database, Docs, Testing, Infra) and one risk level.

**Never:**
- Do not write or propose actual code — this skill plans tasks, it never implements them.
- Do not produce a task with no acceptance criteria or no test expectation.
- Do not assign a task to an area this platform has no agent for without flagging it explicitly.

### Infrastructure Change Skill (`infrastructure-change`, v1.0.0)

**Applies to:** INFRASTRUCTURE_CHANGE

Propose an infrastructure change (provisioning, configuration, CI/CD) needed to support an approved design — as a reviewable proposal, never an applied change to a real environment.

**Never:**
- Do not provision, deploy, or modify a real environment — this skill proposes a change for a human/DevOps to apply.
- Do not propose a change outside the ProjectExecutionProfile's network_policy or allowed_paths.
- Do not hardcode a credential, connection string, or secret value anywhere in the proposal.

### Maintenance Analysis Skill (`maintenance-analysis`, v1.0.0)

**Applies to:** MAINTENANCE_ANALYSIS

Analyze a reported issue or maintenance request against the existing system and propose a scoped remediation — without expanding scope into unrelated improvements ("scope creep").

**Never:**
- Do not introduce sprint-planning scope — this skill analyzes and proposes remediation for one reported issue, not a backlog of future work.
- Do not expand scope into unrelated improvements while investigating.
- Do not claim a root cause without evidence from the actual system/logs described in the input.

### PR Review Skill (`pr-review`, v1.0.0)

**Applies to:** PR_REVIEW

Review a created pull request against the approved LLD, its story, acceptance criteria, coding standards, and security rules — to assist, never replace, human review, and never merge anything.

**Never:**
- Do not approve if a critical test is missing or failing.
- Do not invent an issue not actually present in the diff.
- Do not merge the PR, or imply this skill has the ability to — a human reviewer has final authority.

### Problem Discovery Skill (`problem-discovery`, v1.0.0)

**Applies to:** PROBLEM_DISCOVERY

Investigate the underlying problem behind an approved intake summary — its impact, root cause where knowable, and constraints — and state it clearly enough to design a solution against, without proposing one yet.

**Never:**
- Do not propose or evaluate solution options — that belongs to Solution Discovery.
- Do not restate the intake summary verbatim instead of analyzing it.
- Do not claim a root cause the input doesn't support; state it as a hypothesis instead.

### Requirement Analysis Skill (`requirement-analysis`, v1.0.0)

**Applies to:** REQUIREMENT_ANALYSIS

Capture a stakeholder's initial request as a clear, structured intake summary — desired outcome, known constraints, and stakeholders — without yet analyzing the underlying problem or proposing a solution.

**Never:**
- Do not propose a solution or technical approach — that belongs to Solution Discovery.
- Do not invent constraints, stakeholders, or success criteria the input didn't state.
- Do not draft content for any stage other than Requirement Analysis.

### Solution Discovery Skill (`solution-discovery`, v1.0.0)

**Applies to:** SOLUTION_DISCOVERY

Propose a small set of viable solution options against an approved problem statement, compare their trade-offs, and recommend one — without designing the recommended option's architecture yet.

**Never:**
- Do not design system architecture, components, or a data model — that belongs to HLD.
- Do not present only one option without at least a token alternative and rationale.
- Do not recommend an option the problem statement's constraints rule out.

### Horizontal Story Crafting Skill (`story-crafting-horizontal`, v1.0.0)

**Applies to:** STORY_CRAFTING_HORIZONTAL

Slice an approved HLD into technical-layer stories (frontend, backend, database, integration, infrastructure, testing, documentation) — used when a team needs to plan work by layer of ownership rather than by end-to-end user value.

**Never:**
- Do not slice by user-value end-to-end journey — that is Vertical Story Crafting's job.
- Do not produce a layer story with no clear technical deliverable.
- Do not omit the Mode field — every story block must declare Mode: HORIZONTAL.

### Vertical Story Crafting Skill (`story-crafting-vertical`, v1.0.0)

**Applies to:** STORY_CRAFTING_VERTICAL

Slice an approved HLD (and story backlog context) into end-to-end, user-value stories — each one independently deliverable and testable, cutting through every technical layer it touches.

**Never:**
- Do not slice by technical layer (frontend-only, backend-only) — that is Horizontal Story Crafting's job.
- Do not produce a story with no independently testable acceptance criteria.
- Do not omit the Mode field — every story block must declare Mode: VERTICAL.

### Story Low-Level Design Skill (`story-lld`, v1.0.0)

**Applies to:** STORY_LLD

Design the full implementation detail for exactly ONE approved story — API changes, DB changes, frontend changes, business/validation/ permission rules, error handling, test cases, and the concrete implementation tasks — scoped to that story alone.

**Never:**
- Do not design for the whole story backlog at once — that is the project-level HLD/LLD's job; this skill is scoped to exactly one story.
- Do not invent an API/DB shape the HLD's data model doesn't support without flagging it as a deviation.
- Do not omit Test Cases or Implementation Tasks — both are required sections, not optional.

### Testing Skill (`test-story`, v1.0.0)

**Applies to:** TEST_STORY

Produce a test plan and an honest, reasoning-based assessment of an implemented diff against a story's acceptance criteria — never claiming a real test suite was executed unless it actually was.

**Never:**
- Do not claim a test was executed in a real sandbox/CI environment unless it genuinely was — mark every non-executed result real_execution: false.
- Do not report a PASS for a criterion the diff doesn't actually address.
- Do not fabricate a test name or result beyond what the diff and acceptance criteria actually support.

## Policies

### Coding Standards Policy (`coding-standards`, v1.0.0, BLOCKING)

Baseline expectations for any proposed code change, applied on top of whatever the target repository's own detected lint/format/type-check commands already enforce (see ProjectExecutionProfile).

- Follow the active ProjectExecutionProfile's lint_command/format_check_command/type_check_command as the standard of 'done' — never invent a different one.
- Prefer the smallest change that satisfies the task's acceptance criteria — no unrelated refactoring bundled into the same proposal.
- Never introduce a new third-party dependency without stating it explicitly as a risk.
- Match the target repository's existing naming and structural conventions rather than imposing a different style.

### Data Handling Policy (`data-handling`, v1.0.0, BLOCKING)

How this project's data_classification (see ProjectExecutionProfile) constrains what a skill may propose — a RESTRICTED/CONFIDENTIAL project needs a stricter default than a PUBLIC one.

- Never propose sending project data to a network destination not explicitly allow-listed in the active ProjectExecutionProfile's network_policy.
- For a project classified CONFIDENTIAL or RESTRICTED, never propose logging full request/response bodies — reference shapes/field names only.
- Never include real customer data, real credentials, or real personal data in an example, log excerpt, or explanation — use clearly labeled placeholder data instead.

### Security Baseline Policy (`security-baseline`, v1.0.0, BLOCKING)

The minimum security posture every skill must follow, regardless of task type — mirrors this codebase's own existing discipline (see docs/architecture/universal-agent-runtime-baseline.md section 10: encrypted-at-rest tokens, "never the token" audit logging, no chain-of-thought exposure).

- Never write, echo, or transmit a secret value (API key, token, password, connection string). Reference environment variable NAMES only.
- Never claim a command was executed in a real sandbox/CI environment unless it genuinely was — mark honestly (see RuntimeInstructionPackage's real_execution convention).
- Never touch a path outside the active ProjectExecutionProfile's allowed_paths, or inside its denied_paths.
- Never run a command outside the active ProjectExecutionProfile's allowed_command_patterns, or matching its denied_command_patterns.
- Never merge a pull request, push to a repository's default branch, or imply either happened.
- Never fabricate a test result, coverage number, or execution outcome that did not actually happen.

_Catalog hash: `7b554f80816d86ad`_
