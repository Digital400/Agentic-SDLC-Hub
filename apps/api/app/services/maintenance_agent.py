"""MaintenanceAgentService — produces a repeatable, manually-triggered
project health report (see app/models/maintenance_run.py and
app/api/routes/maintenance_runs.py).

Mirrors app/services/pr_review_agent.py's exact real-AI/heuristic split
and resilience contract: one call via generate_raw_text when a real
provider is configured, a deterministic heuristic otherwise — or if the
real call errors (never let a provider outage block a run). Unlike
pr_review_agent.py, the output is plain Markdown with fixed `## `
headings (like an HLD/LLD document), not JSON — there's no structured
field to validate, just the raw text.

RULE ("Maintenance Agent cannot change production; it can recommend
actions only"): enforced by construction — no deploy/provisioning
execution capability exists anywhere in this codebase for this agent (or
any agent) to call, and the prompt below explicitly instructs the model
to only ever describe what it observed and recommends, never what it did.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.services.ai_generation import AIGenerationError, generate_raw_text, get_active_provider

logger = logging.getLogger(__name__)

# Deliberately distinct from the `maintenance` WorkflowNode's own
# output_artifact_type ("maintenance_log") — see
# app/models/maintenance_run.py's class docstring for why the two paths
# stay independent.
MAINTENANCE_REPORT_ARTIFACT_TYPE = "maintenance_report"

_REQUIRED_SECTIONS = [
    "Health Status",
    "Recent Changes",
    "Open Bugs",
    "Recurring Issues",
    "Technical Debt",
    "Risks",
    "Suggested Improvements",
    "Next Actions",
]


@dataclass
class MaintenanceAgentResult:
    content_markdown: str
    used_mock: bool = True
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0


# --- Heuristic path: grounded only in real counts/facts it was given ----------------


def _recurring_bugs(open_bugs: list[str]) -> list[str]:
    """A bug text repeated across more than one TestRun — the only
    "recurring" signal available without a real issue tracker (see
    app/models/maintenance_run.py's class docstring)."""
    seen: dict[str, int] = {}
    for bug in open_bugs:
        seen[bug] = seen.get(bug, 0) + 1
    return [bug for bug, count in seen.items() if count > 1]


def _build_heuristic_report(
    *,
    released_summary: str,
    open_bugs: list[str],
    pr_history: list[str],
    test_summary: str,
    error_logs: str | None,
    user_feedback: str | None,
) -> str:
    recurring = _recurring_bugs(open_bugs)
    # HONESTY: never invents a health verdict beyond what the counts
    # actually support — same principle as pr_review_agent.py's heuristic
    # never asserting APPROVE.
    if open_bugs or error_logs:
        health = "At Risk — open bugs and/or reported error logs need attention."
    else:
        health = "Healthy — no open bugs or reported error logs observed."

    lines = [
        "## Health Status",
        health,
        "",
        "## Recent Changes",
        released_summary or "No released project summary is available yet.",
    ]
    lines += [f"- {p}" for p in pr_history] if pr_history else ["No PR history is on record for this project yet."]
    lines += [
        "",
        "## Open Bugs",
    ]
    lines += [f"- {b}" for b in open_bugs] if open_bugs else ["None reported."]
    lines += ["", "## Recurring Issues"]
    lines += [f"- {b}" for b in recurring] if recurring else ["None detected — no bug text repeats across test runs."]
    lines += [
        "",
        "## Technical Debt",
        "No automated technical-debt detection exists in this codebase — this is a placeholder heuristic pass, "
        "not a real static-analysis result. Flag debt manually until a real scan is wired up.",
        "",
        "## Risks",
    ]
    risks = []
    if "fail" in test_summary.lower() and "0 failed" not in test_summary.lower():
        risks.append(f"Test results show failures: {test_summary}")
    if error_logs:
        risks.append("Error logs were supplied for this report — review them for recurring failure patterns.")
    lines += [f"- {r}" for r in risks] if risks else ["No concrete risk signals in the data available to this report."]
    lines += [
        "",
        "## Suggested Improvements",
    ]
    improvements = []
    if open_bugs:
        improvements.append("Triage and prioritize the open bugs listed above.")
    if recurring:
        improvements.append("Investigate the root cause behind the recurring issue(s) rather than re-fixing symptoms each time.")
    if user_feedback:
        improvements.append("Review the supplied user feedback for concrete product improvements.")
    lines += [f"- {i}" for i in improvements] if improvements else ["No specific improvement is indicated by the data available to this report."]
    lines += [
        "",
        "## Next Actions",
        "- This report is advisory only — no action listed here has been taken; a human must review and decide.",
    ]
    if open_bugs:
        lines.append(f"- Assign owners to the {len(open_bugs)} open bug(s) above.")
    if not pr_history:
        lines.append("- No PR history is on record for this project yet — confirm this is expected.")

    return "\n".join(lines)


# --- Real-AI path --------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are the Maintenance Agent for a company-grade software delivery platform. Given a released project's "
    "summary, its open issues, PR history, test results, and (if supplied) error logs and user feedback, produce "
    "a factual post-release health report.\n\n"
    "Rules:\n"
    "1. You cannot change production and have no ability to deploy, provision, roll back, or execute anything — "
    "every item you list under Suggested Improvements or Next Actions is a recommendation for a human to "
    "consider, never something you performed or will perform.\n"
    "2. Be factual — distinguish what was actually observed in the given data from speculation. Do not invent "
    "bugs, incidents, or feedback that weren't in the input.\n"
    "3. If a required input wasn't supplied or is empty, say so plainly in the relevant section rather than "
    "guessing.\n\n"
    "Respond with ONLY Markdown using exactly these `## ` headings, in this order, nothing more, nothing "
    "renamed: Health Status, Recent Changes, Open Bugs, Recurring Issues, Technical Debt, Risks, Suggested "
    "Improvements, Next Actions. Write 'None reported.' for a section that genuinely has nothing to report."
)


def _run_real_agent(
    *,
    released_summary: str,
    open_bugs: list[str],
    pr_history: list[str],
    test_summary: str,
    error_logs: str | None,
    user_feedback: str | None,
) -> MaintenanceAgentResult:
    user_content = (
        f"# Released Project Summary\n{released_summary or '(not available)'}\n\n"
        f"# Open Issues\n{chr(10).join(f'- {b}' for b in open_bugs) or '(none reported)'}\n\n"
        f"# PR History\n{chr(10).join(f'- {p}' for p in pr_history) or '(no PR history on record)'}\n\n"
        f"# Test Results\n{test_summary or '(not available)'}\n\n"
        f"# Error Logs\n{error_logs or '(not available)'}\n\n"
        f"# User Feedback\n{user_feedback or '(not available)'}"
    )

    raw = generate_raw_text(system_prompt=_SYSTEM_PROMPT, user_content=user_content, output_token_budget=6000)
    return MaintenanceAgentResult(content_markdown=raw.strip(), used_mock=False)


# --- Entry point -----------------------------------------------------------------------


def run_maintenance_agent(
    *,
    released_summary: str,
    open_bugs: list[str],
    pr_history: list[str],
    test_summary: str,
    error_logs: str | None,
    user_feedback: str | None,
) -> MaintenanceAgentResult:
    """Real AI when configured; the deterministic heuristic otherwise, or
    if the real call errors (logged, not raised — same contract as every
    other agent in this codebase)."""
    if get_active_provider() == "mock":
        return MaintenanceAgentResult(
            content_markdown=_build_heuristic_report(
                released_summary=released_summary, open_bugs=open_bugs, pr_history=pr_history,
                test_summary=test_summary, error_logs=error_logs, user_feedback=user_feedback,
            ),
            used_mock=True,
        )

    try:
        return _run_real_agent(
            released_summary=released_summary, open_bugs=open_bugs, pr_history=pr_history,
            test_summary=test_summary, error_logs=error_logs, user_feedback=user_feedback,
        )
    except AIGenerationError as exc:
        logger.warning("Real-AI Maintenance agent failed (%s); falling back to heuristic report.", exc)
        return MaintenanceAgentResult(
            content_markdown=_build_heuristic_report(
                released_summary=released_summary, open_bugs=open_bugs, pr_history=pr_history,
                test_summary=test_summary, error_logs=error_logs, user_feedback=user_feedback,
            ),
            used_mock=True,
        )
