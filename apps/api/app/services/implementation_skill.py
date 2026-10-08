"""The coding-tool skill for the Implementation stage — deliberately separate
from app/services/coding_tool_skills.py's StageSpec-driven pack builder,
because Implementation's output is a real GitHub pull request, not a
docs/sdlc/<stage>.md document the validator script can check headings/word
count on (see that module's own docstring, and
app/services/story_coding_tool_sync.py's docstring, which both explicitly
disclose this stage as out of scope for that machinery).

Still follows the SAME per-tool conventions the document stages use (see
coding_tool_skills.py's module docstring) — just applied to a task-that-
becomes-a-PR instead of a document-that-gets-synced-back:

  Claude Code  XML-tagged slash command + a PreToolUse HOOK that blocks a
               `git push` to the repository's own default branch (never
               trust the model alone for "never push to main") + a
               read-only reviewer SUBAGENT for a fresh-eyes check before
               the PR opens.
  Codex        AGENTS.md guidance + a custom prompt (/prompts:implementation)
               with an explicit self-review step (Codex has no subagent
               concept in this pattern).
  OpenCode     a command that @-references the per-task input file, + a
               subagent reviewer (@sdlc-reviewer-implementation).
  Cursor       a chat command + a rule scoped to the project's own source
               globs (never docs/sdlc/**, unlike the document stages).

What this gives a developer:
  1. One slash command per tool (installed once per repo, via the same
     generic POST /coding-tools/skills/install route — build_skill_pack
     dispatches here for stage="implementation") that tells them to read a
     provisioned task-context file and implement it for real: write code
     and tests, commit, push a new branch, and open a PR.
  2. A per-task input snapshot (one call per ImplementationTask, analogous
     to build_story_input_snapshot but keyed by task, not by stage) with
     everything that task needs: its own description/expected files/
     acceptance criteria/test expectation, the story's Story LLD and
     Implementation Plan content, and this project's engineering setup
     (coding standards, guardrails, build/test commands, branch naming).

The command never pushes a file back through this app (there is no document
to sync) — the loop closes instead through
POST /implementation-runs/register-pull-request, once the developer has a
real PR number to hand the app.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import ImplementationTask, Project, PullRequestLink, PullRequestStatus, Repository, Story
from app.services.agent_context_builder import build_engineering_setup_context
from app.services.coding_tool_skills import SDLC_DIR, SkillFile, SkillPack, TOOL_LABELS
from app.services.story_coding_tool_sync import story_slug
from app.services.story_implementation_plan_agent import STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE
from app.services.story_lld_agent import STORY_LLD_ARTIFACT_TYPE
from app.services.story_test_scenarios_agent import STORY_TEST_SCENARIOS_ARTIFACT_TYPE

_SLUG = "implementation"
GUARD_HOOK_PATH = ".sdlc/hooks/guard-implementation.mjs"

_REVIEWER_CHECKLIST = (
    "Only files the task's input file lists (or genuinely necessary companions, e.g. a new test file) were touched",
    "Every stated acceptance criterion is actually satisfied by the diff",
    "Real tests were added or updated for the change, not just production code",
    "The project's coding standards and guardrails (see the input file's own engineering-setup section) were followed",
    "Every operation that can fail — I/O, network/API calls, database access, parsing, external input — has real "
    "error/exception handling (the project's own declared pattern if its standards name one, otherwise a sensible "
    "try/catch with a clear, non-silent error path); nothing is left to crash uncaught or fail silently",
    "No TODO/FIXME/debug prints/commented-out code was left behind",
    "Nothing outside this task's own story was modified",
)


def _default_branch(db: Session, project: Project) -> str:
    repo = (
        db.query(Repository)
        .filter(Repository.project_id == project.id)
        .order_by(Repository.is_primary.desc(), Repository.created_at.desc())
        .first()
    )
    return (repo.default_branch if repo and repo.default_branch else "main")


def _procedure_steps(*, review_line: str) -> str:
    return f"""1. Find this task's input file under `{SDLC_DIR}/stories/<story-slug>/inputs/implementation/` (one file per
   task, named `<area>-task-context.md`) matching the input above. If more than one matches, ask which. If none
   exists, tell the user to run "Sync this task's inputs" in the app first.
2. Read that file in full: the task's description, expected files, acceptance criteria and test expectation, the
   story's approved Story LLD and Implementation Plan, and this project's coding standards/guardrails/build & test
   commands. Never invent requirements it doesn't state — unknowns are a question for the user, not a guess.
3. Implement the change for real: write the code, and tests for it, under the expected files listed (more files
   are fine if the change genuinely needs them; nothing outside this task's own story). This is not optional or a
   follow-up: every operation that can fail (I/O, network/API calls, database access, parsing external input) gets
   real error/exception handling as you write it, not added later. Use this project's own declared error-handling
   pattern if its coding standards name one; otherwise a sensible try/catch (or this language's equivalent) with a
   clear, non-silent error path — logged or surfaced, never swallowed, never left to crash uncaught.
4. Run this project's configured test command(s) from the input file. Fix failures before continuing — never
   commit code with failing tests. Include at least one test that exercises a failure path (an invalid input, a
   failed call) for anything you added error handling to, not just the happy path.
5. {review_line}
6. Check the input file's own "Existing pull request for this story" section:
   - If it names one (a sibling task — e.g. DATABASE — already has an open PR for this story), check out that
     SAME branch, commit your changes onto it, and push. Do NOT create a new branch or open a second pull
     request — one story's areas land on ONE shared pull request, never a separate PR per area (a separate PR
     per area means separate, stacked, dependent reviews for what is really one change).
   - If it says none exists yet, create a new branch (never commit directly to the repository's default/base
     branch — a hook enforces this for Claude Code; do it anyway, deliberately, for every tool), commit, and
     push it.
7. Only if you pushed a brand-new branch in step 6 (no existing PR named in the input file): open a pull request
   from that branch (the GitHub CLI `gh pr create` if available, otherwise github.com) targeting this project's
   configured target branch. Title it after the story (not just this one area, since later areas will land on
   it too); its body should summarize what changed and reference the acceptance criteria you satisfied. If you
   instead pushed onto an existing PR's branch, there is nothing to open — that PR's diff already grew to
   include your commits.
8. Tell the user the pull request's number (the existing one if you reused it, the new one if you just opened
   it). They register it in Agentic SDLC Hub's Implementation tab ("Register pull request") — that is how this
   app learns this area's work is attached to that PR; nothing here does it for you."""


_GUARDRAILS_MD = "\n".join(f"- {c}" for c in _REVIEWER_CHECKLIST)

_RULES_MD = """- Do not touch any story/task this input file doesn't belong to.
- Do not merge the pull request — a human reviews it, in GitHub and in the app's PR Review stage.
- Exactly two sections in the input file are REQUIRED before you implement: "Story LLD" and "Implementation Plan".
  If EITHER of those two specifically is marked "not yet available", stop and tell the user to complete that
  stage first instead of guessing at it.
- The "Test Scenarios" section is NOT required and is expected to say "not yet available" — in this project's
  own delivery lane, Test Scenarios is drafted AFTER Implementation, not before (see the lane order: Story LLD,
  Implementation Plan, Implementation, THEN Test Scenarios). Seeing it marked "not yet available" here is the
  normal, by-design state, not a missing input — implement the task using Story LLD and Implementation Plan only,
  and do NOT stop or ask the user about it.
- Never commit or push directly to the repository's default/base branch, even to "fix something quick".
- Never write code that can throw or fail with no error handling around it — this is checked by the reviewer
  below and is a BLOCKING finding, not a style nitpick."""


def _reviewer_body(output_description: str) -> str:
    return f"""You review one implementation with fresh eyes, against its own task-context file. You never edit files.

Read the task's input file under `{SDLC_DIR}/stories/<story-slug>/inputs/implementation/`, then run `git diff` against
the base branch to see {output_description}. Check every item below and reply PASS or FAIL with one line of evidence:

{_GUARDRAILS_MD}

End with a BLOCKING list (may be empty). The calling agent must fix every blocking item before opening the pull
request."""


def _claude_code_pack(default_branch: str) -> SkillPack:
    label = TOOL_LABELS["claude_code"]
    command = f"""---
description: Implement an Implementation Task for real — write code, tests, commit, push, open a PR.
argument-hint: <task title, area, or story title>
allowed-tools: Read, Write, Edit, Grep, Glob, Bash, Task
---

<role>
You implement ONE Implementation Task for real, in this repository — not a document.
</role>

<input>
$ARGUMENTS — the task's title, its area (DATABASE/BACKEND/FRONTEND/...), or the story's title if there is only one task.
</input>

<procedure>
{_procedure_steps(review_line="Delegate to the `sdlc-reviewer-implementation` subagent for an independent review of your diff against the guardrails below, telling it which task's input file you used. Apply every blocking finding before continuing.")}
</procedure>

<guardrails>
{_GUARDRAILS_MD}
</guardrails>

<rules>
{_RULES_MD}
- A PreToolUse hook blocks any `git push` targeting `{default_branch}` directly — expect it to refuse and push a
  feature branch instead, never work around it.
</rules>
"""
    reviewer = f"""---
name: sdlc-reviewer-implementation
description: Independent, read-only reviewer for an Implementation Task's diff. Use before opening a pull request to check it against the guardrails below.
tools: Read, Grep, Glob, Bash
---

{_reviewer_body("exactly what changed")}
"""
    guard_hook = _guard_hook_script(default_branch)
    settings = _claude_settings_snippet()
    files = [
        SkillFile(".claude/commands/implementation.md", command, "The /implementation slash command."),
        SkillFile(".claude/agents/sdlc-reviewer-implementation.md", reviewer, "Read-only reviewer subagent for Implementation."),
        SkillFile(GUARD_HOOK_PATH, guard_hook, f"PreToolUse hook — blocks a `git push` straight to '{default_branch}'."),
        SkillFile(".claude/settings.json", settings, "PreToolUse hook wiring (only added if you have no settings file).", managed=False),
    ]
    usage = [
        "Open this repository in Claude Code and type `/implementation <task title, area, or story title>`.",
        "Claude Code implements the task for real: code, tests, a new branch, and a pull request — a hook refuses any attempt to push straight to "
        + default_branch
        + ", and a reviewer subagent checks the diff before the PR opens.",
        "Copy the pull request's number and click \"Register pull request\" on that task's Implementation tab in the app.",
    ]
    notes = [
        f"If your repo already has .claude/settings.json (e.g. from another stage's skill) it is left alone — add this "
        f"file's own PreToolUse hook block (matcher \"Bash\", command `node \"$CLAUDE_PROJECT_DIR/{GUARD_HOOK_PATH}\"`) to it by hand.",
    ]
    return SkillPack("claude_code", label, _SLUG, files, usage, notes)


def _guard_hook_script(default_branch: str) -> str:
    return f"""#!/usr/bin/env node
// Agentic SDLC Hub — Implementation guardrail (generated; do not edit by hand).
// A Claude Code PreToolUse hook (matcher "Bash"): blocks any `git push` whose
// current branch, or explicit target, is this repository's own default
// branch ({default_branch!r}) — "never push to main" enforced structurally,
// not just asked for in the prompt. The tool call arrives as JSON on stdin;
// a blocked command exits 2 so Claude sees the message and uses a feature
// branch instead.
import {{ readFileSync }} from "node:fs";
import {{ execSync }} from "node:child_process";

const DEFAULT_BRANCH = {default_branch!r};

function readStdin() {{
  try {{
    if (process.stdin.isTTY) return "";
    return readFileSync(0, "utf8");
  }} catch {{
    return "";
  }}
}}

const raw = readStdin().trim();
if (!raw) process.exit(0);
let input;
try {{
  input = JSON.parse(raw);
}} catch {{
  process.exit(0);
}}

const command = input?.tool_input?.command;
if (typeof command !== "string" || !/\\bgit\\s+push\\b/.test(command)) process.exit(0);

// Pushing an explicit ref to the default branch, however it's spelled.
const explicitTarget = new RegExp(`\\\\bgit\\\\s+push\\\\b[^|;&]*\\\\b${{DEFAULT_BRANCH}}\\\\b`).test(command);

let currentBranch = "";
try {{
  currentBranch = execSync("git rev-parse --abbrev-ref HEAD", {{ encoding: "utf8" }}).trim();
}} catch {{
  // Not a git repo / detached HEAD — nothing more this hook can check.
  process.exit(0);
}}

// A plain `git push` (no explicit branch) pushes the CURRENT branch.
const pushesCurrentBranch = !/\\bgit\\s+push\\b[^|;&]*\\S+\\s+\\S+/.test(command);

if (explicitTarget || (pushesCurrentBranch && currentBranch === DEFAULT_BRANCH)) {{
  console.error(
    `Blocked: this command would push to '${{DEFAULT_BRANCH}}' directly. Create a feature branch first ` +
    `(see the /implementation command's own rules) — never commit or push straight to the default branch.`
  );
  process.exit(2);
}}
process.exit(0);
"""


def _claude_settings_snippet() -> str:
    import json

    return (
        json.dumps(
            {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": f'node "$CLAUDE_PROJECT_DIR/{GUARD_HOOK_PATH}"'}]}]}},
            indent=2,
        )
        + "\n"
    )


def _codex_pack(default_branch: str) -> SkillPack:
    label = TOOL_LABELS["codex"]
    prompt = f"""---
description: Implement an Implementation Task for real
argument-hint: <task title, area, or story title>
---

You implement ONE Implementation Task for real, in this repository — not a document.

Input: $ARGUMENTS — the task's title, its area, or the story's title if there is only one task.

{_procedure_steps(review_line="Re-read your own diff once against the guardrails below before continuing — a second pass, not a rubber stamp.")}

Guardrails:
{_GUARDRAILS_MD}

Rules:
{_RULES_MD}
- Never run `git push` with `{default_branch}` as the target, or while `{default_branch}` is checked out.
"""
    agents_md = f"""# Agent instructions

## Implementation (Agentic SDLC Hub)

This project's implementation tasks are described under `{SDLC_DIR}/stories/<story-slug>/inputs/implementation/`.
Before implementing any task, read `{SDLC_DIR}/context.md` (stack, coding standards, guardrails) and that task's own
input file. Never invent requirements; unknowns are a question for the user.

To implement a task: follow `.sdlc/codex/implementation.md` (or `/prompts:implementation` if installed). Write code
and tests for real, run the configured test command(s), then commit on a NEW branch, push it, and open a pull
request — never commit or push to `{default_branch}` directly.
"""
    files = [
        SkillFile(".sdlc/codex/implementation.md", prompt, "The /prompts:implementation custom prompt (copy to ~/.codex/prompts)."),
        SkillFile("AGENTS.md", agents_md, "Project instructions Codex reads automatically (only added if you have none).", managed=False),
    ]
    usage = [
        "One time per machine: `mkdir -p ~/.codex/prompts && cp .sdlc/codex/implementation.md ~/.codex/prompts/`.",
        "Open this repository in Codex and type `/prompts:implementation <task title, area, or story title>`.",
        "Copy the pull request's number and click \"Register pull request\" on that task's Implementation tab in the app.",
    ]
    notes = ["If your repo already has AGENTS.md it is left alone — add this file's own Implementation section to it by hand."]
    return SkillPack("codex", label, _SLUG, files, usage, notes)


def _opencode_pack(default_branch: str) -> SkillPack:
    label = TOOL_LABELS["opencode"]
    command = f"""---
description: Implement an Implementation Task for real
agent: build
---

You implement ONE Implementation Task for real, in this repository — not a document.

Input: $ARGUMENTS — the task's title, its area, or the story's title if there is only one task.

{_procedure_steps(review_line='Ask the `@sdlc-reviewer-implementation` subagent to review your diff against the guardrails below, and apply every blocking finding.')}

Guardrails:
{_GUARDRAILS_MD}

Rules:
{_RULES_MD}
- Never run `git push` with `{default_branch}` as the target, or while `{default_branch}` is checked out.
"""
    reviewer = f"""---
description: Independent, read-only reviewer for an Implementation Task's diff
mode: subagent
tools:
  write: false
  edit: false
---

{_reviewer_body("exactly what changed")}
"""
    agents_md = f"""# Agent instructions

## Implementation (Agentic SDLC Hub)

Implementation tasks are described under `{SDLC_DIR}/stories/<story-slug>/inputs/implementation/`. Read
`{SDLC_DIR}/context.md` and that task's own input file before implementing. Run `/implementation` to do it; it
writes code and tests, runs the configured test command(s), and must push a NEW branch — never `{default_branch}`
directly — before opening a pull request.
"""
    files = [
        SkillFile(".opencode/command/implementation.md", command, "The /implementation command."),
        SkillFile(".opencode/agent/sdlc-reviewer-implementation.md", reviewer, "Read-only reviewer subagent for Implementation."),
        SkillFile("AGENTS.md", agents_md, "Project instructions OpenCode reads automatically (only added if you have none).", managed=False),
    ]
    usage = [
        "Open this repository in OpenCode and type `/implementation <task title, area, or story title>`.",
        "OpenCode implements the task for real: code, tests, a new branch, and a pull request, with a reviewer subagent checking the diff first.",
        "Copy the pull request's number and click \"Register pull request\" on that task's Implementation tab in the app.",
    ]
    notes = [
        "OpenCode's config folders have been renamed between versions (command/agent vs commands/agents) — move the files if /implementation does not appear.",
        "If your repo already has AGENTS.md it is left alone — add this file's own Implementation section to it by hand.",
    ]
    return SkillPack("opencode", label, _SLUG, files, usage, notes)


def _cursor_pack(default_branch: str) -> SkillPack:
    label = TOOL_LABELS["cursor"]
    command = f"""# Implementation

You implement ONE Implementation Task for real, in this repository — not a document.

Input: the task title, area, or story title the user typed after the command.

{_procedure_steps(review_line="Re-read your own diff once against the guardrails below before continuing — a second pass, not a rubber stamp.")}

Guardrails:
{_GUARDRAILS_MD}

Rules:
{_RULES_MD}
- Never run `git push` with `{default_branch}` as the target, or while `{default_branch}` is checked out.
"""
    rule = f"""---
description: Guardrails for implementing an Implementation Task from Agentic SDLC Hub
globs: **/*
alwaysApply: false
---

- Read the task's own input file under `{SDLC_DIR}/stories/<story-slug>/inputs/implementation/` before writing code.
- Never invent requirements it doesn't state; unknowns are a question for the user.
- Write real tests for the change, not just production code, and run this project's configured test command(s).
- Never commit or push directly to `{default_branch}` — always a new branch, always a pull request.
"""
    files = [
        SkillFile(".cursor/commands/implementation.md", command, "The /implementation chat command."),
        SkillFile(".cursor/rules/sdlc-implementation.mdc", rule, "Guardrails applied whenever implementing a task from the app."),
    ]
    usage = [
        "Open this repository in Cursor, start an Agent chat and type `/implementation <task title, area, or story title>`.",
        "Cursor implements the task for real: code, tests, a new branch, and a pull request.",
        "Copy the pull request's number and click \"Register pull request\" on that task's Implementation tab in the app.",
    ]
    return SkillPack("cursor", label, _SLUG, files, usage)


_RENDERERS = {"claude_code": _claude_code_pack, "codex": _codex_pack, "opencode": _opencode_pack, "cursor": _cursor_pack}


def build_implementation_skill_pack(db: Session, project: Project, tool: str) -> SkillPack:
    return _RENDERERS[tool](_default_branch(db, project))


# --- Per-task input snapshot -------------------------------------------------------------


def _snapshot_section(title: str, content: str | None, *, required: bool = True) -> str:
    if content is not None and content.strip():
        return f"## {title} (approved)\n\n{content.strip()}\n"
    if required:
        return f'## {title}\n\n_Not yet available — complete this stage first, then re-run "Sync this task\'s inputs"._\n'
    # Test Scenarios is the one non-required section here: this project's
    # lane drafts it AFTER Implementation (see STORY_DELIVERY_NODE_KEYS),
    # so it's always "not yet available" at this point — by design, not a
    # missing input. A distinct message (not the same "complete this stage
    # first" wording the two genuinely-required sections use) stops a
    # coding agent from pattern-matching on the shared "not yet available"
    # phrase and blocking on it — see _RULES_MD's own matching note.
    return (
        f"## {title}\n\n_Not written yet — by design. This project drafts Test Scenarios AFTER Implementation, "
        "not before, so this section is expected to be empty right now. This is not a missing input; proceed "
        "without it._\n"
    )


def _existing_pr_section(db: Session, story: Story) -> str:
    """Surfaces whether an earlier-ordered sibling task (e.g. DATABASE,
    when this file is for BACKEND) already registered an open pull request
    for this story — see app/api/routes/implementation_runs.py's own
    _get_open_task_pull_request, the identical "one story, one shared PR"
    lookup the in-app Implementation Agent already relies on. Without this,
    a developer running /implementation for each area in turn had no way
    to know a PR already existed and would open a new one every time —
    three separate, stacked, dependent pull requests needing three separate
    reviews for what is really one change."""
    link = (
        db.query(PullRequestLink)
        .filter(PullRequestLink.story_id == story.id, PullRequestLink.status == PullRequestStatus.OPEN)
        .order_by(PullRequestLink.created_at.desc())
        .first()
    )
    if link is None:
        return "## Existing pull request for this story\n\nNone yet — this is the first area implemented. Create a new branch and open a new pull request."
    return (
        "## Existing pull request for this story\n\n"
        f"**#{link.pr_number}** — branch `{link.branch_name}` ({link.pr_url}).\n\n"
        "An earlier area of this same story already opened this pull request. Check out THIS branch, commit your "
        "changes onto it, and push — do NOT create a new branch or open a second pull request. One story's areas "
        "share ONE pull request."
    )


def _latest_story_artifact(db: Session, story: Story, artifact_type: str) -> str | None:
    from app.models import StoryArtifact

    row = (
        db.query(StoryArtifact)
        .filter(StoryArtifact.story_id == story.id, StoryArtifact.artifact_type == artifact_type)
        .order_by(StoryArtifact.version_number.desc())
        .first()
    )
    return row.content_markdown if row else None


@dataclass
class TaskInputSnapshot:
    file: SkillFile
    not_ready: list[str]


def build_implementation_task_input_snapshot(
    db: Session, *, project: Project, story: Story, task: ImplementationTask
) -> TaskInputSnapshot:
    """One file per ImplementationTask — docs/sdlc/stories/<slug>/inputs/implementation/<area>-task-context.md
    — everything the /implementation command needs for that one task. Re-run
    any time the task, its story's LLD/Implementation Plan, or engineering
    setup changes; the file is always fully regenerated, never partially
    patched."""
    slug = story_slug(story.title)
    area = task.area.value.lower()
    lld_content = _latest_story_artifact(db, story, STORY_LLD_ARTIFACT_TYPE)
    plan_content = _latest_story_artifact(db, story, STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE)
    scenarios_content = _latest_story_artifact(db, story, STORY_TEST_SCENARIOS_ARTIFACT_TYPE)
    not_ready = [name for name, content in (("Story LLD", lld_content), ("Implementation Plan", plan_content)) if content is None]

    engineering_context = build_engineering_setup_context(db, project=project, agent_type="implementation", output_token_budget=2000)

    lines = [
        "---", "source: implementation_task", f"task_id: {task.id}", f"story_id: {story.id}", f"story_slug: {slug}",
        f"area: {task.area.value}", "---",
        f"# Implementation Task: {task.title}", "",
        f"**Area:** {task.area.value}", f"**Risk level:** {task.risk_level.value}", "",
        "## What to build", "", task.description or "(no description recorded)", "",
    ]
    if task.linked_lld_section:
        lines += [f"**LLD section:** {task.linked_lld_section}", ""]
    if task.expected_paths:
        lines += ["## Expected files", "", *[f"- `{p}`" for p in task.expected_paths], ""]
    if task.acceptance_criteria:
        lines += ["## Acceptance criteria", "", *[f"- {c}" for c in task.acceptance_criteria], ""]
    if task.test_expectation:
        lines += ["## Testing", "", task.test_expectation, ""]
    lines += [_existing_pr_section(db, story), ""]
    lines += [
        _snapshot_section("Story LLD", lld_content), "",
        _snapshot_section("Implementation Plan", plan_content), "",
        _snapshot_section("Test Scenarios", scenarios_content, required=False), "",
        "## Project engineering setup", "", engineering_context.context_text or "(none configured)", "",
        '_Re-run "Sync this task\'s inputs" in the app to refresh this file if the task, its story\'s documents, or engineering setup change._',
    ]
    path = f"{SDLC_DIR}/stories/{slug}/inputs/implementation/{area}-task-context.md"
    file = SkillFile(path, "\n".join(lines) + "\n", f"Implementation input snapshot for task '{task.title}'.")
    return TaskInputSnapshot(file=file, not_ready=not_ready)
