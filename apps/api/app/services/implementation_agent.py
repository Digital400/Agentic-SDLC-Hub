"""ImplementationAgentService — turns one approved ImplementationTask into
a *proposed* diff for a human to review, for the four area agents this
request actually names (Backend/Frontend/Database/Docs — see
SUPPORTED_AREAS below; Testing/Infra stay unimplemented, same as
app/services/implementation_planner.py's own forward-looking, not-yet-real
`AREA_TO_AGENT_TYPE` entries for those two).

Mirrors validator_agent.py's/implementation_planner.py's exact real-AI/
heuristic split and resilience contract: one JSON-structured call via
generate_raw_text when a real provider is configured, a deterministic
heuristic otherwise — or if the real call errors or returns unparseable
JSON (never let a formatting slip or provider outage block a run).

HARD RULE: this module never writes to a real file, branch, or repository.
Its only output is a unified diff (via stdlib `difflib` — no diffing
dependency exists or is needed anywhere else in this codebase) plus
supporting metadata, meant to be read by a human before anything else ever
happens to it (see app/api/routes/implementation_runs.py, which is the
only thing that persists this output, and app/models/implementation_run.py,
whose class docstring repeats this same boundary).
"""

from __future__ import annotations

import difflib
import json
import logging
from dataclasses import dataclass, field

from app.models.enums import ImplementationTaskArea
from app.models.implementation_task import ImplementationTask
from app.services.ai_generation import AIGenerationError, generate_raw_text, get_active_provider
from app.services.repo_context_builder import RepoContextPreviewResult
from app.services.retrieval import RetrievedChunk
from app.services.story_export import Story

logger = logging.getLogger(__name__)

# The four agent types this request actually names — see module docstring.
# Starting a run for a task outside this set is rejected by the route
# before this service is ever called (app/api/routes/implementation_runs.py).
SUPPORTED_AREAS = {
    ImplementationTaskArea.BACKEND,
    ImplementationTaskArea.FRONTEND,
    ImplementationTaskArea.DATABASE,
    ImplementationTaskArea.DOCS,
}

_DEFAULT_TEST_COMMAND_BY_AREA: dict[str, str] = {
    "BACKEND": "cd apps/api && .venv/Scripts/python.exe -m pytest -q",
    "DATABASE": "cd apps/api && .venv/Scripts/python.exe -m pytest -q",
    "FRONTEND": "cd apps/web && yarn test",
    "DOCS": "N/A — documentation change; verify by reading the rendered file.",
}

_VALID_CHANGE_TYPES = {"create", "modify", "delete"}


@dataclass
class ProposedFileChange:
    path: str
    change_type: str  # "create" | "modify" | "delete"
    summary: str
    # The full proposed final content for this file — None for "delete".
    # This, not the diff, is what's actually written to GitHub when a PR
    # is created (see app/services/github_integration.py's
    # create_or_update_file): applying a unified diff textually would need
    # a patch-application library this codebase doesn't have and isn't
    # adding just for this. The diff above remains what a human reads;
    # this is what an agent-run PR would write.
    after_content: str | None = None


@dataclass
class ImplementationAgentResult:
    proposed_file_changes: list[ProposedFileChange] = field(default_factory=list)
    diff_text: str = ""
    explanation: str = ""
    test_command: str = ""
    risks: list[str] = field(default_factory=list)
    # Requirement 5 — a real PR description, drafted alongside the diff
    # rather than only synthesized later at PR-creation time.
    pr_description: str = ""
    used_mock: bool = True
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0


def _placeholder_after_content(task: ImplementationTask) -> str:
    lines = [
        f"# TODO(implementation-agent): implement \"{task.title}\"",
        f"# {task.description}",
        "#",
        "# Acceptance criteria to satisfy:",
    ]
    lines += [f"# - {c}" for c in task.acceptance_criteria] or ["# (none specified)"]
    return "\n".join(lines) + "\n"


def _unified_diff_for_path(path: str, *, before: str, after: str) -> str:
    diff_lines = difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    )
    return "".join(diff_lines)


def _change_type_for_path(path: str, repo_context: RepoContextPreviewResult) -> str:
    for entry in repo_context.suggested_edit_scope:
        if entry["path"] == path:
            return "create" if entry["status"] == "new" else "modify"
    # Not one of the task's own declared expected_paths (e.g. produced by
    # the real-AI path) — default to "modify", the safer assumption.
    return "modify"


def _existing_snippet(path: str, repo_context: RepoContextPreviewResult) -> str:
    for f in repo_context.relevant_files:
        if f.path == path and f.content_mode == "full" and f.snippet:
            return f.snippet
    return ""


# --- Heuristic path: no real code synthesis, a clearly-labeled scaffold -------------


def _build_pr_description(*, task: ImplementationTask, story: Story | None, jira_issue_key: str | None) -> str:
    """A minimal, honest PR description — used as-is by the heuristic
    path, and as the fallback shape the real-AI path's own prompt is
    told to follow (see _SYSTEM_PROMPT)."""
    lines = [f"## {task.title}", "", task.description or "(no description provided)"]
    if story is not None:
        lines += ["", f"**Story:** {story.title}"]
    if jira_issue_key:
        lines += ["", f"**Jira:** {jira_issue_key}"]
    if task.acceptance_criteria:
        lines += ["", "**Acceptance criteria:**"] + [f"- {c}" for c in task.acceptance_criteria]
    return "\n".join(lines)


def _build_heuristic_result(
    *,
    task: ImplementationTask,
    repo_context: RepoContextPreviewResult,
    story: Story | None,
    lld_summary: str,
    standards_chunks: list[RetrievedChunk] | None = None,
    jira_issue_key: str | None = None,
    implementation_plan_summary: str = "",
    test_scenarios_summary: str = "",
    engineering_setup_context: str = "",
) -> ImplementationAgentResult:
    paths = task.expected_paths or [f"(no expected path declared for '{task.title}')"]
    changes: list[ProposedFileChange] = []
    diff_parts: list[str] = []

    for path in paths:
        change_type = _change_type_for_path(path, repo_context)
        before = "" if change_type == "create" else _existing_snippet(path, repo_context)
        after = _placeholder_after_content(task) if change_type != "delete" else ""
        diff_parts.append(_unified_diff_for_path(path, before=before, after=after))
        changes.append(
            ProposedFileChange(
                path=path, change_type=change_type, summary=task.description[:300],
                after_content=after if change_type != "delete" else None,
            )
        )

    test_command = task.test_expectation.strip() or _DEFAULT_TEST_COMMAND_BY_AREA.get(task.area.value, "N/A")

    risks = [f"Risk level: {task.risk_level.value}."]
    if task.acceptance_criteria:
        risks.append(
            "This is a scaffold, not working code — every acceptance criterion below still needs a human "
            "or a real model to actually implement it: " + "; ".join(task.acceptance_criteria)
        )
    else:
        risks.append("No acceptance criteria were declared for this task — scope may be under-specified.")
    if not repo_context.relevant_files:
        risks.append("No relevant repository files were found for this task — the repo snapshot may be stale or empty.")
    if not standards_chunks:
        risks.append("No coding-standards knowledge was retrieved — proceeding on repository context alone.")
    if engineering_setup_context:
        risks.append(
            "This project has configured engineering setup context (coding standards, guardrails, and/or repo "
            "conventions) that a real implementation must follow — this scaffold does not enforce any of it."
        )

    explanation = (
        f"Heuristic scaffold for \"{task.title}\" ({task.area.value}). No real model is configured "
        "(mock provider active), so this is a deterministic placeholder — TODO stubs marking exactly where "
        "each acceptance criterion needs real implementation, not working code. "
        f"{'Related story: ' + story.title + '. ' if story else ''}"
        f"{'LLD context: ' + lld_summary[:200] + '. ' if lld_summary else ''}"
        f"{'Jira: ' + jira_issue_key + '. ' if jira_issue_key else ''}"
        f"{f'{len(standards_chunks)} coding-standards excerpt(s) available. ' if standards_chunks else ''}"
        f"{'Project engineering setup context is available. ' if engineering_setup_context else ''}"
        f"{'An Implementation Plan is available. ' if implementation_plan_summary.strip() else ''}"
        f"{'Test Scenarios are available. ' if test_scenarios_summary.strip() else ''}"
        "No repository, branch, or file was written by generating this — review the diff below before anything else happens to it."
    )

    return ImplementationAgentResult(
        proposed_file_changes=changes,
        diff_text="\n".join(diff_parts),
        explanation=explanation,
        test_command=test_command,
        risks=risks,
        pr_description=_build_pr_description(task=task, story=story, jira_issue_key=jira_issue_key),
        used_mock=True,
    )


# --- Real-AI path --------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are an Implementation Agent for one specific area of a software system, working on exactly ONE story — "
    "never introduce changes for any other story or task. Given an approved task, its Low-Level Design context, "
    "its related user story, relevant existing repository files, coding standards, a Jira issue key (if any), and "
    "test expectations, propose the code changes needed to complete the task.\n"
    "Respond with ONLY a single JSON object (no markdown code fences, no commentary) with exactly these keys:\n"
    '- "proposed_file_changes": array of objects {"path": string, "change_type": one of "create"/"modify"/"delete", '
    '"summary": string, "content": string (the FULL proposed final content of the file after this change; omit or '
    'use null for a "delete")}.\n'
    '- "explanation": string — what the change does and why.\n'
    '- "test_command": string — the exact command a human should run to verify this change.\n'
    '- "risks": array of strings — include any blocker that would stop this from being mergeable as-is.\n'
    '- "pr_description": string — a complete pull request description (what changed, why, and how to verify), '
    "referencing the Jira issue key when one is given.\n"
    # Deliberately NOT asking for a hand-written unified diff here anymore
    # (it used to be a required "diff" key): a real multi-file change made
    # the model restate every file's full content twice — once as "content",
    # once again as diff hunks — which routinely exhausted the output
    # budget before the JSON even finished, silently producing the mock
    # scaffold (this was the root cause behind a real "still mock" bug).
    # The diff is now built deterministically below via difflib from
    # "content" plus the existing repository snippet, so it's always
    # complete and always consistent with "content" by construction,
    # instead of just hoping the model kept the two in sync.
    "RULES: Do NOT claim the change has been applied, committed, or pushed anywhere — you have no ability to do so "
    "and must not imply otherwise. Never reference creating or pushing to a branch, and never reference the "
    "repository's default/main branch as a target. Use only the repository content given to you; do not invent "
    "file contents you weren't shown."
)


def _run_real_agent(
    *,
    task: ImplementationTask,
    repo_context: RepoContextPreviewResult,
    story: Story | None,
    lld_summary: str,
    standards_chunks: list[RetrievedChunk] | None = None,
    jira_issue_key: str | None = None,
    implementation_plan_summary: str = "",
    test_scenarios_summary: str = "",
    engineering_setup_context: str = "",
) -> ImplementationAgentResult:
    file_context_parts = []
    for f in repo_context.relevant_files:
        if f.content_mode == "full" and f.snippet:
            file_context_parts.append(f"### {f.path} (existing content)\n{f.snippet}")
        else:
            file_context_parts.append(f"### {f.path} ({f.content_mode}, size={f.size})")

    story_text = (
        f"Title: {story.title}\nUser story: {story.user_story}\n"
        f"Acceptance criteria: {'; '.join(story.acceptance_criteria)}"
        if story
        else "(no related story found)"
    )

    # RAG-retrieved COMPANY_STANDARD knowledge (semantically filtered) —
    # distinct from engineering_setup_context below, which is the
    # project's own persisted, always-included-in-full setup (coding
    # standards, guardrails, repo/build conventions — see
    # app/services/agent_context_builder.py).
    standards_text = "\n\n".join(f"### {c.source_title}\n{c.content}" for c in standards_chunks) if standards_chunks else "(none retrieved)"

    user_content = (
        f"# Task\nTitle: {task.title}\nArea: {task.area.value}\nDescription: {task.description}\n"
        f"Expected files/folders: {', '.join(task.expected_paths) or '(none declared)'}\n"
        f"Acceptance criteria: {'; '.join(task.acceptance_criteria) or '(none declared)'}\n\n"
        f"# Approved LLD summary\n{lld_summary or '(not available)'}\n\n"
        # Story Code Implementation Agent — Implementation Plan and Test
        # Scenarios are additive context (both may not exist yet for an
        # older/project-level task), same as everything else here.
        f"# Implementation Plan\n{implementation_plan_summary or '(not available)'}\n\n"
        f"# Test Scenarios\n{test_scenarios_summary or '(not available)'}\n\n"
        f"# Related story\n{story_text}\n\n"
        f"# Jira issue key\n{jira_issue_key or '(not synced to Jira yet)'}\n\n"
        f"# Coding standards (retrieved from the Knowledge Base)\n{standards_text}\n\n"
        f"# Project Engineering Setup — coding standards, guardrails, GitHub/build conventions to follow\n"
        f"{engineering_setup_context or '(no engineering setup configured for this project)'}\n\n"
        f"# Repository context\nArchitecture summary: {repo_context.architecture_summary}\n"
        f"Relevant folders: {', '.join(repo_context.relevant_folders) or '(none)'}\n"
        + "\n".join(file_context_parts)
        + "\n\n# Test expectation\n"
        + (task.test_expectation or "(not specified — propose a reasonable test command)")
    )

    # 16000, not 4096 — a real code change routinely proposes multiple
    # full file contents (see ProposedFileChange.after_content), which a
    # single-file placeholder-sized budget silently truncated mid-JSON in
    # practice (confirmed: a real multi-file C# change was cut off at
    # ~4096 tokens, producing invalid JSON that fell back to the mock
    # scaffold with no visible error anywhere).
    raw = generate_raw_text(system_prompt=_SYSTEM_PROMPT, user_content=user_content, output_token_budget=16000)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    parsed = json.loads(cleaned)

    changes = []
    diff_parts: list[str] = []
    for item in parsed.get("proposed_file_changes", []):
        change_type = str(item.get("change_type", "modify")).lower()
        if change_type not in _VALID_CHANGE_TYPES:
            change_type = "modify"
        content = item.get("content")
        path = str(item.get("path", ""))
        after_content = str(content) if content is not None and change_type != "delete" else None
        changes.append(
            ProposedFileChange(
                path=path, change_type=change_type, summary=str(item.get("summary", "")),
                after_content=after_content,
            )
        )
        # Built here, not asked of the model — see _SYSTEM_PROMPT's comment
        # on why: guaranteed consistent with "content" by construction,
        # and it doesn't cost the model any of its own output budget.
        before = "" if change_type == "create" else _existing_snippet(path, repo_context)
        diff_parts.append(_unified_diff_for_path(path, before=before, after=after_content or ""))

    return ImplementationAgentResult(
        proposed_file_changes=changes,
        diff_text="\n".join(diff_parts),
        pr_description=str(parsed.get("pr_description", "")) or _build_pr_description(task=task, story=story, jira_issue_key=jira_issue_key),
        explanation=str(parsed.get("explanation", "")),
        test_command=str(parsed.get("test_command", "")) or _DEFAULT_TEST_COMMAND_BY_AREA.get(task.area.value, "N/A"),
        risks=[str(r) for r in parsed.get("risks", [])],
        used_mock=False,
    )


# --- Entry point -----------------------------------------------------------------------


def run_implementation_agent(
    *,
    task: ImplementationTask,
    repo_context: RepoContextPreviewResult,
    story: Story | None,
    lld_summary: str,
    standards_chunks: list[RetrievedChunk] | None = None,
    jira_issue_key: str | None = None,
    implementation_plan_summary: str = "",
    test_scenarios_summary: str = "",
    engineering_setup_context: str = "",
) -> ImplementationAgentResult:
    """Real AI when configured; the deterministic heuristic otherwise, or if
    the real call errors or returns unparseable JSON (logged, not raised —
    same contract as validator_agent.run_validator and
    implementation_planner.build_implementation_plan).

    `standards_chunks` (RAG, see app/services/retrieval.py) and
    `jira_issue_key` are both additive context — story-level implementation
    workflow requirement 4 — never required for a run to proceed.
    `implementation_plan_summary`/`test_scenarios_summary` (Story Code
    Implementation Agent) are likewise additive. `engineering_setup_context`
    (Agent Context Builder, agent_type="implementation" — see
    app/services/agent_context_builder.py) is the project's own persisted
    coding standards/guardrails/repo/build conventions, pre-assembled and
    budget-fitted by the caller — this function just drops it into the
    prompt verbatim."""
    kwargs = dict(
        task=task, repo_context=repo_context, story=story, lld_summary=lld_summary,
        standards_chunks=standards_chunks, jira_issue_key=jira_issue_key,
        implementation_plan_summary=implementation_plan_summary, test_scenarios_summary=test_scenarios_summary,
        engineering_setup_context=engineering_setup_context,
    )
    if get_active_provider() == "mock":
        return _build_heuristic_result(**kwargs)

    try:
        return _run_real_agent(**kwargs)
    except (AIGenerationError, json.JSONDecodeError, ValueError, TypeError, AttributeError, KeyError) as exc:
        logger.warning("Real-AI implementation agent failed (%s); falling back to heuristic scaffold.", exc)
        return _build_heuristic_result(**kwargs)
