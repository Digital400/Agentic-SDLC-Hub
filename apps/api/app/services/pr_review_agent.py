"""PRReviewAgentService — reviews an already-created GitHub pull request
against the approved LLD, its related story, acceptance criteria, coding
standards, security rules, and (if available) test results.

Mirrors implementation_agent.py's/testing_agent.py's exact real-AI/
heuristic split and resilience contract: one JSON-structured call via
generate_raw_text when a real provider is configured, a deterministic
heuristic otherwise — or if the real call errors or returns unparseable
JSON (never let a formatting slip or provider outage block a run).

This is the structured, JSON-output sibling of the Markdown-drafting
`pr-review-agent` AgentPrompt registered in app/db/seed.py's
RICH_DEFAULT_PROMPTS["pr_review"] — same persona, same 7 inputs, same 10
review categories, same rules, adapted to a real service with a fixed
output schema instead of free-form Markdown.

HONESTY: the heuristic path never returns APPROVE — it has no real
reviewing judgment, so asserting approval would be dishonest, the same
principle TestingAgentService's heuristic already applies to fabricating
a pass. The real-AI path's `overall_recommendation` is validated against
the three allowed values; anything else is coerced to COMMENT_ONLY, never
silently to APPROVE.

SCOPE: "human reviewer decides final approval" is the real GitHub PR
review, external to this app — nothing here approves anything on GitHub's
behalf, and nothing here (or anywhere in app/services/github_integration.py)
ever merges a PR.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.models.implementation_task import ImplementationTask
from app.services.ai_generation import AIGenerationError, generate_raw_text, get_active_provider
from app.services.retrieval import RetrievedChunk
from app.services.story_export import Story

logger = logging.getLogger(__name__)

_VALID_RECOMMENDATIONS = {"APPROVE", "REQUEST_CHANGES", "COMMENT_ONLY"}


@dataclass
class Finding:
    file: str
    detail: str


@dataclass
class SuggestedComment:
    file: str
    body: str


@dataclass
class PRReviewAgentResult:
    overall_recommendation: str = "COMMENT_ONLY"
    summary: str = ""
    critical_findings: list[Finding] = field(default_factory=list)
    major_findings: list[Finding] = field(default_factory=list)
    minor_findings: list[Finding] = field(default_factory=list)
    missing_tests: list[str] = field(default_factory=list)
    suggested_comments: list[SuggestedComment] = field(default_factory=list)
    risk_score: int = 0
    final_reviewer_note: str = (
        "This is an AI-generated review to assist, not replace, human judgment. The human reviewer on GitHub "
        "decides final approval; this PR has not been merged."
    )
    used_mock: bool = True
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0


# --- Heuristic path: never approves, never invents an issue -------------------------


def _build_heuristic_result(
    *,
    task: ImplementationTask,
    diff_text: str,
    test_fail_count: int | None,
    test_bugs_found: list[str],
) -> PRReviewAgentResult:
    critical: list[Finding] = []
    major: list[Finding] = []
    missing_tests: list[str] = []

    if "TODO(implementation-agent)" in diff_text:
        critical.append(
            Finding(file="(diff)", detail="The diff contains an implementation-agent TODO scaffold, not working code.")
        )
    if test_fail_count is not None and test_fail_count > 0:
        critical.append(Finding(file="(test run)", detail=f"{test_fail_count} test(s) failed in the latest test run."))
    for bug in test_bugs_found:
        major.append(Finding(file="(test run)", detail=bug))
    for criterion in task.acceptance_criteria:
        missing_tests.append(f"No confirmed test coverage for: {criterion}")

    risk_score = min(100, 30 * len(critical) + 15 * len(major))

    # HONESTY: never asserts APPROVE — the heuristic has no real judgment.
    if critical:
        recommendation = "REQUEST_CHANGES"
    else:
        recommendation = "COMMENT_ONLY"

    summary = (
        f"Heuristic review of \"{task.title}\" — no real model is configured, so this is a deterministic pass over "
        "concrete, checkable facts only (a TODO scaffold, failing tests, unconfirmed acceptance-criteria coverage), "
        "not a substantive code review. Treat this as a placeholder, not a real review."
    )

    return PRReviewAgentResult(
        overall_recommendation=recommendation,
        summary=summary,
        critical_findings=critical,
        major_findings=major,
        minor_findings=[],
        missing_tests=missing_tests,
        suggested_comments=[SuggestedComment(file=f.file, body=f.detail) for f in critical + major],
        risk_score=risk_score,
        used_mock=True,
    )


# --- Real-AI path --------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a senior software engineer performing a pull request review for a company-grade software delivery "
    "platform.\n\n"
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
    "- Do not merge the PR — you have no ability to do so and must not imply otherwise.\n"
    "- Human reviewer has final authority.\n\n"
    "Respond with ONLY a single JSON object (no markdown code fences, no commentary) with exactly these keys:\n"
    '- "overall_recommendation": one of "APPROVE", "REQUEST_CHANGES", "COMMENT_ONLY".\n'
    '- "summary": string.\n'
    '- "critical_findings", "major_findings", "minor_findings": arrays of objects {"file": string, "detail": string}.\n'
    '- "missing_tests": array of strings.\n'
    '- "suggested_comments": array of objects {"file": string, "body": string} — comments a human could post as-is.\n'
    '- "risk_score": integer from 0 to 100.\n'
    '- "final_reviewer_note": string — must state the human reviewer has final authority and this PR has not been merged.'
)


def _findings_from(items: list[dict]) -> list[Finding]:
    return [Finding(file=str(i.get("file", "")), detail=str(i.get("detail", ""))) for i in items]


def _run_real_agent(
    *,
    task: ImplementationTask,
    pr_title: str,
    pr_body: str,
    diff_text: str,
    lld_summary: str,
    story: Story | None,
    standards_chunks: list[RetrievedChunk],
    test_summary: str,
) -> PRReviewAgentResult:
    story_text = (
        f"Title: {story.title}\nUser story: {story.user_story}\nAcceptance criteria: {'; '.join(story.acceptance_criteria)}"
        if story
        else "(no related story found)"
    )
    standards_text = "\n".join(f"## {c.source_title}\n{c.content}" for c in standards_chunks) or "(none found)"

    user_content = (
        f"# PR\nTitle: {pr_title}\nDescription: {pr_body or '(none)'}\n\n"
        f"# Diff\n{diff_text or '(no diff available)'}\n\n"
        f"# Task\nTitle: {task.title}\nDescription: {task.description}\n"
        f"Acceptance criteria: {'; '.join(task.acceptance_criteria) or '(none declared)'}\n\n"
        f"# Approved LLD summary\n{lld_summary or '(not available)'}\n\n"
        f"# Related story\n{story_text}\n\n"
        f"# Coding standards / security rules\n{standards_text}\n\n"
        f"# Test results\n{test_summary or '(not available — this task has not been tested yet)'}"
    )

    raw = generate_raw_text(system_prompt=_SYSTEM_PROMPT, user_content=user_content, output_token_budget=4096)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    parsed = json.loads(cleaned)

    recommendation = str(parsed.get("overall_recommendation", "")).upper()
    if recommendation not in _VALID_RECOMMENDATIONS:
        # Never trust the model into an invalid enum, and never silently
        # default an unparseable value to APPROVE.
        recommendation = "COMMENT_ONLY"

    risk_score = parsed.get("risk_score", 0)
    try:
        risk_score = max(0, min(100, int(risk_score)))
    except (TypeError, ValueError):
        risk_score = 0

    return PRReviewAgentResult(
        overall_recommendation=recommendation,
        summary=str(parsed.get("summary", "")),
        critical_findings=_findings_from(parsed.get("critical_findings", [])),
        major_findings=_findings_from(parsed.get("major_findings", [])),
        minor_findings=_findings_from(parsed.get("minor_findings", [])),
        missing_tests=[str(m) for m in parsed.get("missing_tests", [])],
        suggested_comments=[
            SuggestedComment(file=str(c.get("file", "")), body=str(c.get("body", ""))) for c in parsed.get("suggested_comments", [])
        ],
        risk_score=risk_score,
        final_reviewer_note=str(parsed.get("final_reviewer_note", "")) or PRReviewAgentResult().final_reviewer_note,
        used_mock=False,
    )


# --- Entry point -----------------------------------------------------------------------


def run_pr_review_agent(
    *,
    task: ImplementationTask,
    pr_title: str,
    pr_body: str,
    diff_text: str,
    lld_summary: str,
    story: Story | None,
    standards_chunks: list[RetrievedChunk],
    test_summary: str,
    test_fail_count: int | None,
    test_bugs_found: list[str],
) -> PRReviewAgentResult:
    """Real AI when configured; the deterministic heuristic otherwise, or
    if the real call errors or returns unparseable JSON (logged, not
    raised — same contract as every other agent in this codebase)."""
    if get_active_provider() == "mock":
        return _build_heuristic_result(task=task, diff_text=diff_text, test_fail_count=test_fail_count, test_bugs_found=test_bugs_found)

    try:
        return _run_real_agent(
            task=task, pr_title=pr_title, pr_body=pr_body, diff_text=diff_text, lld_summary=lld_summary,
            story=story, standards_chunks=standards_chunks, test_summary=test_summary,
        )
    except (AIGenerationError, json.JSONDecodeError, ValueError, TypeError, AttributeError, KeyError) as exc:
        logger.warning("Real-AI PR review agent failed (%s); falling back to heuristic scaffold.", exc)
        return _build_heuristic_result(task=task, diff_text=diff_text, test_fail_count=test_fail_count, test_bugs_found=test_bugs_found)
