"""Wraps the Claude Agent SDK (the real Claude Code harness — file
read/write/edit, bash, multi-turn self-correction — packaged as a
library, not a single-shot prompt) as an alternative generation path for
the Implementation Agent.

Returns the exact same app.services.implementation_agent.ImplementationAgentResult
shape run_implementation_agent itself returns, so every downstream
consumer (start_implementation_run, ImplementationRun, PR creation, PR
Review, Testing) needs zero changes to accept this path's output —
see app/services/implementation_agent.py's own module docstring for the
contract this mirrors.

SCOPE (Phase 1): the Implementation Agent (full read/write/bash access,
above), plus — as of this module's second entry point,
run_document_session — EVERY stage reached through
app/services/ai_generation.py's generate(), but ONLY read-only
(Read/Glob/Grep — no Write, Edit, or Bash in allowed_tools, so a document
stage's session cannot modify anything even if it tried), and ONLY when
the project actually has a connected repository to ground it in — a
project with none gets no benefit from a filesystem the session has
nothing to read, so it keeps using the plain single-shot prompt call
unconditionally. That covers both the nine project-level stages
(Requirement Intake, Problem Discovery, Solution Discovery, HLD, Story
Crafting, Infrastructure Planning, Infrastructure, Release, Maintenance)
AND the three per-story stages (Story LLD, Implementation Plan, Test
Scenarios) — CORRECTED from an earlier version of this docstring, which
claimed the per-story three call generate_raw_text directly and are
unaffected: they don't. app/services/story_lld_agent.py,
story_implementation_plan_agent.py, and story_test_scenarios_agent.py all
call generate() too (with a transient, never-persisted WorkflowNode
standing in for the story's own context — see story_lld_agent.py's own
module docstring for why — but the REAL, persisted `project` object is
what's passed in, which is what resolve_project_repository reads), so
every one of them already goes through this same dispatch with zero
extra wiring. Gated entirely off by default — see
get_settings().CLAUDE_AGENT_SDK_ENABLED — existing behavior is completely
unchanged unless a deployment opts in.

WORKSPACE: unlike CodeRunnerService (app/services/code_runner.py), which
only exists once an ImplementationRun is already ACCEPTED and logs onto
a real CodeRun row, this runs BEFORE any ImplementationRun row exists —
there is nothing to log onto yet. So this clones its own throwaway
workspace with the same security posture CodeRunnerService's own clone
uses (an argv list through subprocess, shell=False, the token redacted
out of any error text, the directory deleted unconditionally when done)
rather than reusing CodeRunnerService's instance methods directly, which
are bound to a CodeRun row this stage doesn't have.

HARD RULE (the same boundary implementation_agent.py's own module
docstring states): this module never commits, branches, or pushes
anything. The Claude Agent SDK session writes real files into the
throwaway clone; the ONLY thing persisted out of it is the resulting
diff — read back off disk via `git status`/file reads after the session
ends, never from a commit this module made. A human still reviews before
anything is ever written back to GitHub, exactly like every other path
through run_implementation_agent.

OPERATIONAL REQUIREMENT: the Agent SDK runs the `claude` CLI binary as a
subprocess — it is not a pure HTTP API call. The host running this
backend needs that CLI installed and an authenticated profile (or
ANTHROPIC_API_KEY) it can see; see code.claude.com/docs/en/agent-sdk.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import ResultMessage

from app.core.config import get_settings
from app.core.security import SecretDecryptionError, decrypt_secret
from app.models import ImplementationTask, Repository
from app.services.implementation_agent import ImplementationAgentResult, ProposedFileChange
from app.services.story_export import Story

logger = logging.getLogger(__name__)


class ClaudeAgentHarnessError(Exception):
    """Raised when the harness can't run at all or didn't finish
    successfully — a missing repository token, the `claude` CLI not being
    found, or the SDK session itself erroring/timing out. Callers catch
    this the same way they already catch AIGenerationError from the
    plain real-AI path (see run_implementation_agent) and degrade to the
    heuristic scaffold rather than let it block a run."""


# --- Workspace — a throwaway clone, not a CodeRun's own workspace (see module docstring) --


def _repository_token(repository: Repository) -> str:
    if repository.connection is None:
        raise ClaudeAgentHarnessError(f"Repository {repository.owner}/{repository.name} has no GitHub connection.")
    try:
        return decrypt_secret(repository.connection.access_token_encrypted)
    except SecretDecryptionError as exc:
        raise ClaudeAgentHarnessError("Could not decrypt this repository's GitHub token.") from exc


def _sdk_env() -> dict[str, str]:
    """Extra environment variables merged into the `claude` CLI subprocess
    the Agent SDK launches (ClaudeAgentOptions.env) — on top of whatever
    this backend process's own OS environment already has.

    Without this, apps/api/.env's ANTHROPIC_API_KEY only ever reached
    _generate_with_anthropic (which passes it to the `anthropic` Python
    SDK directly, see ai_generation.py) — it was NEVER visible to this
    harness's own `claude` CLI subprocess, because pydantic-settings reads
    .env into the Settings object only, not into os.environ, and nothing
    in this app calls load_dotenv() to also export it. A deployment that
    set ANTHROPIC_API_KEY in .env and turned on CLAUDE_AGENT_SDK_ENABLED
    still silently fell back to whatever `claude login` session (or lack
    of one) already existed on the host — this is what actually lets the
    .env value authenticate the SDK session too, same as every other
    provider's key already does."""
    settings = get_settings()
    return {"ANTHROPIC_API_KEY": settings.ANTHROPIC_API_KEY} if settings.ANTHROPIC_API_KEY else {}


def _run_git(cwd: Path, args: list[str], *, secret: str | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    """Mirrors CodeRunnerService._run_command's exact security posture
    (argv list, shell=False, PATHEXT-aware executable resolution,
    secret redaction) without needing a CodeRun row to log onto."""
    resolved = [shutil.which("git") or "git", *args]
    try:
        proc = subprocess.run(resolved, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, shell=False)
    except subprocess.TimeoutExpired as exc:
        raise ClaudeAgentHarnessError(f"git {args[0] if args else ''} timed out after {timeout}s.") from exc
    except OSError as exc:
        raise ClaudeAgentHarnessError(f"git was not found or failed to start: {exc}") from exc
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if secret:
            stderr = stderr.replace(secret, "***")
        raise ClaudeAgentHarnessError(f"git {args[0] if args else ''} failed: {stderr[:500]}")
    return proc


def _clone_workspace(repository: Repository, base_branch: str) -> Path:
    settings = get_settings()
    token = _repository_token(repository)
    workspace = settings.CODE_RUNNER_WORKSPACE_ROOT / f"harness-{uuid.uuid4()}"
    workspace.mkdir(parents=True, exist_ok=False)
    clone_url = f"https://x-access-token:{token}@github.com/{repository.owner}/{repository.name}.git"
    _run_git(
        workspace.parent,
        ["clone", "--branch", base_branch, "--single-branch", "--depth", "1", clone_url, str(workspace)],
        secret=token,
    )
    return workspace


def _collect_changes(workspace: Path) -> list[ProposedFileChange]:
    """Reads back what the Claude Agent SDK session actually did to the
    working tree — `git status --porcelain` after the session ends, never
    a commit this module made (see module docstring's HARD RULE)."""
    proc = _run_git(workspace, ["status", "--porcelain"])
    changes: list[ProposedFileChange] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        status, path = line[:2].strip(), line[3:].strip()
        if "->" in path:  # a rename shows as "R  old -> new" — keep the new path
            path = path.split("->")[-1].strip()
        full = (workspace / path).resolve()
        if workspace.resolve() not in full.parents:
            continue  # defense in depth — never report a path outside the workspace
        if status.startswith("D") or not full.exists():
            changes.append(ProposedFileChange(path=path, change_type="delete", summary="Deleted by the Claude Agent SDK session."))
            continue
        change_type = "create" if status in ("??", "A", "AM") else "modify"
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue  # a binary/unreadable file — skip rather than propose garbage content
        changes.append(
            ProposedFileChange(path=path, change_type=change_type, summary="Written by the Claude Agent SDK session.", after_content=content)
        )
    return changes


def _diff_text(changes: list[ProposedFileChange]) -> str:
    parts = []
    for c in changes:
        before = "" if c.change_type == "create" else "(previous content not retained in this workspace)\n"
        after = c.after_content or ""
        parts.append("".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=f"a/{c.path}", tofile=f"b/{c.path}")))
    return "\n".join(parts)


# --- Prompt ------------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are an Implementation Agent for one specific area of a software system, working on exactly ONE task of "
    "exactly ONE story. You have real tools — Read, Glob, Grep, Write, Edit, Bash — against a real clone of the "
    "repository. Explore it yourself; do not assume the summary given to you below is complete. Implement the task "
    "for real: write the code and tests for it, under the files this task names (more files are fine if the change "
    "genuinely needs them, but never outside this task's own story). Every operation that can fail (I/O, network/API "
    "calls, database access, parsing external input) needs real error/exception handling as you write it — use this "
    "project's own declared pattern if its coding standards name one, otherwise a sensible try/catch with a clear, "
    "non-silent error path. Run this project's own test command(s) if you can determine one, and fix failures before "
    "finishing.\n\n"
    "EVERY acceptance criterion gets a real file write — not just the ones that feel like 'real code.' A "
    "placeholder file, an empty directory marker, a short README, a 'not yet implemented' note: if an acceptance "
    "criterion asks for it, create it for real with the Write tool, exactly like you would a source file. Treat a "
    "criterion as unmet until you have actually written the file it describes — never mark something done in your "
    "head without a corresponding real Write/Bash call, however small or 'obvious' it seems.\n\n"
    "RULES:\n"
    "- Do NOT run any git command that commits, branches, or pushes (no `git commit`, `git branch`, `git checkout -b`, "
    "`git push`) — this workspace is a disposable clone; a human reviews your file changes afterward through this "
    "app's own review step, not through git history you create.\n"
    "- Never touch a file unrelated to this task.\n"
    "- Do not invent requirements this task doesn't state. If something is genuinely ambiguous, make the most "
    "reasonable, clearly-noted assumption rather than stopping.\n"
    "- MANDATORY LAST STEP, before writing your final summary: run `git status --porcelain` yourself and check "
    "its output against every acceptance criterion one by one. If a criterion's file isn't listed there, it does "
    "NOT exist yet — go create it now, then re-run `git status --porcelain` and check again. Only write your final "
    "summary once every acceptance criterion has a real, confirmed entry in that output. Your summary must "
    "describe only changes you have just confirmed this way — never a change you intended, planned, or assumed "
    "you made but didn't verify is actually sitting in the working tree.\n"
    "- An earlier sibling task's summary (see 'Earlier sibling tasks' below) describes what THAT task's agent "
    "claimed to do — it is NOT proof those files exist in YOUR clone right now (a human approving that work "
    "does not mean it was ever pushed or merged to the real repository). Never skip an acceptance criterion "
    "because an earlier summary says it's already handled — use Read/Glob/Grep to check for real in your own "
    "clone first, and if the file genuinely isn't there, create it yourself.\n"
    "- When you finish, reply with a short summary: what you changed and why, any risks a reviewer should know "
    "about, and the exact test command you ran (or would run)."
)


def _build_prompt(
    *,
    task: ImplementationTask,
    story: Story | None,
    lld_summary: str,
    jira_issue_key: str | None,
    implementation_plan_summary: str,
    test_scenarios_summary: str,
    engineering_setup_context: str,
    prior_story_task_context: str,
) -> str:
    story_text = f"Title: {story.title}\nUser story: {story.user_story}" if story else "(no related story found)"
    return (
        f"# Task\nTitle: {task.title}\nArea: {task.area.value}\nDescription: {task.description}\n"
        f"Expected files/folders: {', '.join(task.expected_paths) or '(none declared — use your own judgment after exploring the repo)'}\n"
        f"Acceptance criteria: {'; '.join(task.acceptance_criteria) or '(none declared)'}\n\n"
        f"# Approved LLD summary\n{lld_summary or '(not available)'}\n\n"
        f"# Implementation Plan\n{implementation_plan_summary or '(not available)'}\n\n"
        f"# Test Scenarios\n{test_scenarios_summary or '(not available)'}\n\n"
        f"# Earlier sibling tasks for this same story — PROPOSED changes, human-ACCEPTED but NOT confirmed present "
        f"in your own clone\n"
        "A human reviewed and accepted these, but that does not mean they were ever pushed or merged to the real "
        "repository — treat every claim below as unverified until you've checked it yourself with Read/Glob/Grep "
        "in this clone. If an acceptance criterion's file genuinely isn't there, create it — do not skip real "
        "work because an earlier summary says it's done.\n\n"
        f"{prior_story_task_context or '(none — this is the first/only task for this story)'}\n\n"
        f"# Related story\n{story_text}\n\n"
        f"# Jira issue key\n{jira_issue_key or '(not synced to Jira yet)'}\n\n"
        f"# Project engineering setup — coding standards, guardrails, build/test conventions to follow\n"
        f"{engineering_setup_context or '(no engineering setup configured for this project)'}\n\n"
        f"# Test expectation\n{task.test_expectation or '(not specified — determine and run a reasonable test command yourself)'}"
    )


# --- Session -----------------------------------------------------------------------------


async def _run_session(prompt: str, options: ClaudeAgentOptions) -> ResultMessage | None:
    result: ResultMessage | None = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            result = message
    return result


# --- Entry point ---------------------------------------------------------------------------


def run_implementation_agent_via_sdk(
    *,
    task: ImplementationTask,
    repository: Repository,
    base_branch: str,
    story: Story | None,
    lld_summary: str,
    jira_issue_key: str | None = None,
    implementation_plan_summary: str = "",
    test_scenarios_summary: str = "",
    engineering_setup_context: str = "",
    prior_story_task_context: str = "",
    model: str | None = None,
) -> ImplementationAgentResult:
    """Clones `repository`@`base_branch` into a throwaway workspace, runs a
    real Claude Agent SDK session against it with file/bash tools enabled,
    and reads the resulting diff back off disk — never from a commit this
    module made (see module docstring). Raises ClaudeAgentHarnessError for
    any precondition or session failure; callers should catch it and
    degrade to run_implementation_agent's own heuristic scaffold, the same
    resilience contract that function already has for its own real-AI path.

    `model` is this one run's per-run override (see
    app/api/routes/implementation_runs.py's own provider_override/
    model_override on StartImplementationRunRequest) — None leaves
    ClaudeAgentOptions.model unset, falling back to the `claude` CLI's own
    configured/default model, same as run_document_session's own `model`.
    """
    settings = get_settings()
    workspace = _clone_workspace(repository, base_branch)
    try:
        prompt = _build_prompt(
            task=task, story=story, lld_summary=lld_summary, jira_issue_key=jira_issue_key,
            implementation_plan_summary=implementation_plan_summary, test_scenarios_summary=test_scenarios_summary,
            engineering_setup_context=engineering_setup_context, prior_story_task_context=prior_story_task_context,
        )
        options = ClaudeAgentOptions(
            cwd=str(workspace),
            allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
            # A throwaway clone, never the real repository — safe to run
            # without interactive approval prompts, which would otherwise
            # block forever in this server-side, non-interactive context.
            permission_mode="bypassPermissions",
            system_prompt=_SYSTEM_PROMPT,
            max_turns=settings.CLAUDE_AGENT_SDK_MAX_TURNS,
            max_budget_usd=settings.CLAUDE_AGENT_SDK_MAX_BUDGET_USD,
            env=_sdk_env(),
            model=model,
        )
        try:
            result_message = asyncio.run(_run_session(prompt, options))
        except Exception as exc:  # noqa: BLE001 — any SDK/transport failure degrades the same way
            raise ClaudeAgentHarnessError(f"Claude Agent SDK session failed to run: {exc}") from exc

        if result_message is None:
            raise ClaudeAgentHarnessError("Claude Agent SDK session produced no result.")
        if result_message.is_error:
            errors = "; ".join(result_message.errors) if result_message.errors else result_message.subtype
            raise ClaudeAgentHarnessError(f"Claude Agent SDK session did not complete successfully: {errors}")

        changes = _collect_changes(workspace)
        usage = result_message.usage or {}
        prompt_tokens = int(usage.get("input_tokens", 0) or 0)
        completion_tokens = int(usage.get("output_tokens", 0) or 0)
        return ImplementationAgentResult(
            proposed_file_changes=changes,
            diff_text=_diff_text(changes),
            explanation=result_message.result or "(Claude Agent SDK session produced no summary text.)",
            test_command=task.test_expectation.strip() or "N/A — no test command declared; see the session's own summary above.",
            risks=[] if changes else ["The session made no file changes — review carefully before accepting."],
            pr_description=_build_pr_description(task=task, story=story, jira_issue_key=jira_issue_key),
            used_mock=False,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost=float(result_message.total_cost_usd or 0.0),
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _build_pr_description(*, task: ImplementationTask, story: Story | None, jira_issue_key: str | None) -> str:
    """Same minimal, honest shape implementation_agent.py's own
    _build_pr_description produces — duplicated rather than imported
    (that one is module-private) since it's a handful of lines."""
    lines = [f"## {task.title}", "", task.description or "(no description provided)"]
    if story is not None:
        lines += ["", f"**Story:** {story.title}"]
    if jira_issue_key:
        lines += ["", f"**Jira:** {jira_issue_key}"]
    if task.acceptance_criteria:
        lines += ["", "**Acceptance criteria:**"] + [f"- {c}" for c in task.acceptance_criteria]
    return "\n".join(lines)


# --- Document stages (project-level, read-only grounding) --------------------------------


@dataclass
class DocumentSessionResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    cost: float
    truncated: bool


def run_document_session(
    *, system_prompt: str, user_content: str, repository: Repository, base_branch: str, model: str | None = None,
) -> DocumentSessionResult:
    """Read-only grounding for a project-level document stage — see
    app/services/ai_generation.py's _generate_with_claude_agent_sdk, the
    only caller. The SAME session mechanics as
    run_implementation_agent_via_sdk (clone, real Claude Agent SDK
    session, cleanup), but `allowed_tools` is Read/Glob/Grep ONLY — no
    Write, Edit, or Bash — so this session cannot modify the clone even if
    it tried; nothing is ever read back off disk, only the session's own
    final text response. This exists so a document stage (e.g. Problem
    Discovery, HLD) can ground itself in the project's REAL existing
    codebase — most useful for the "Existing Project — Feature" workflow
    template — instead of reasoning from a text summary alone.

    `model` is this one run's per-run override (see
    ai_generation.py's use_model_override) — None (the default) leaves
    ClaudeAgentOptions.model unset, which falls back to the `claude` CLI's
    own configured/default model exactly as before this parameter existed.

    Callers decide whether to use this at all (only when a repository is
    actually connected — see _resolve_project_repository) and how to
    interpret a failure (ai_generation.py's caller falls back to the
    plain single-shot prompt rather than failing the whole stage run, a
    different, more lenient choice than run_implementation_agent_via_sdk's
    callers make deliberately — see that function's own docstring)."""
    workspace = _clone_workspace(repository, base_branch)
    try:
        settings = get_settings()
        options = ClaudeAgentOptions(
            cwd=str(workspace),
            allowed_tools=["Read", "Glob", "Grep"],
            permission_mode="bypassPermissions",
            system_prompt=system_prompt,
            max_turns=settings.CLAUDE_AGENT_SDK_MAX_TURNS,
            max_budget_usd=settings.CLAUDE_AGENT_SDK_MAX_BUDGET_USD,
            model=model,
            env=_sdk_env(),
        )
        try:
            result_message = asyncio.run(_run_session(user_content, options))
        except Exception as exc:  # noqa: BLE001 — any SDK/transport failure is reported the same way
            raise ClaudeAgentHarnessError(f"Claude Agent SDK session failed to run: {exc}") from exc

        if result_message is None:
            raise ClaudeAgentHarnessError("Claude Agent SDK session produced no result.")
        if result_message.is_error:
            errors = "; ".join(result_message.errors) if result_message.errors else result_message.subtype
            raise ClaudeAgentHarnessError(f"Claude Agent SDK session did not complete successfully: {errors}")

        usage = result_message.usage or {}
        return DocumentSessionResult(
            text=result_message.result or "",
            prompt_tokens=int(usage.get("input_tokens", 0) or 0),
            completion_tokens=int(usage.get("output_tokens", 0) or 0),
            cost=float(result_message.total_cost_usd or 0.0),
            truncated=result_message.subtype == "error_max_turns",
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def resolve_project_repository(project) -> tuple[Repository, str] | tuple[None, None]:
    """The project-level counterpart to implementation_runs.py's own
    _resolve_repository_for_task — no per-task override exists at this
    level, so this is just "the primary repo, or the first one" plus its
    latest snapshot's own ref (falling back to its default branch if it's
    never been snapshotted). Reads straight off the already-loaded
    `project` ORM object's relationships — no extra query, no `db` param
    needed — so this is safe to call from generate(), which only ever
    receives the ORM object, never a session."""
    repos = list(project.repositories)
    if not repos:
        return None, None
    repository = next((r for r in repos if r.is_primary), repos[0])
    base_branch = repository.snapshots[0].ref if repository.snapshots else repository.default_branch
    if not base_branch:
        return None, None
    return repository, base_branch
