"""TestingAgentService — turns an accepted ImplementationRun's diff into a
test plan, proposed tests, a reasoning-based test-execution assessment,
and a rendered `test_report` Artifact for QA review.

Mirrors validator_agent.py's/implementation_planner.py's/implementation_agent.py's
exact real-AI/heuristic split and resilience contract: one JSON-structured
call via generate_raw_text when a real provider is configured, a
deterministic heuristic otherwise — or if the real call errors or returns
unparseable JSON (never let a formatting slip or provider outage block a
run).

HONESTY, repeated from app/models/test_run.py: this codebase has no
test-execution sandbox. `tests_executed` and the pass/fail counts derived
from it are the agent's own reasoning-based assessment from the diff and
acceptance criteria — never a real CI result. Every rendering of this
data (the dataclass, the persisted TestRun, and render_test_report_markdown's
own Test Evidence section) says so plainly.

QA GATE: nothing in this module ever approves anything. Its only output
is a draft Markdown report and structured fields for a route to persist
as a READY_FOR_REVIEW Artifact — the existing Review mechanism
(app/api/routes/reviews.py) is what a human QA reviewer actually approves
through.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.models.enums import TestAgentType
from app.models.implementation_task import ImplementationTask
from app.services.ai_generation import AIGenerationError, generate_raw_text, get_active_provider
from app.services.retrieval import RetrievedChunk
from app.services.story_export import Story

logger = logging.getLogger(__name__)

# Requirement 7 — the new artifact type a story-scoped test run produces
# (see app/models/story_artifact.py). Distinct from the project-level
# `test_report` Artifact type (workflows/sdlc-workflow.json's `testing`
# node) — same reasoning as story_lld_agent.STORY_LLD_ARTIFACT_TYPE.
STORY_TEST_REPORT_ARTIFACT_TYPE = "story_test_report"

_VALID_RESULTS = {"PASS", "FAIL"}

_AGENT_TYPE_FRAMING: dict[TestAgentType, str] = {
    TestAgentType.UNIT: "a Unit Test Agent, focused on isolated function/module-level correctness",
    TestAgentType.API: "an API Test Agent, focused on request/response contracts, status codes, and error handling",
    TestAgentType.UI: "a UI Test Agent, focused on user-facing behavior and rendered output",
    TestAgentType.REGRESSION: "a Regression Test Agent, focused on whether this change could break existing behavior",
    TestAgentType.SECURITY: "a Security Test Agent, focused on injection, auth/permission bypass, and data exposure risks",
}

# Always the same placeholder shape — requirement 3 explicitly calls this
# a placeholder; no coverage tool exists anywhere in this codebase.
COVERAGE_IMPACT_PLACEHOLDER = {
    "estimated_change": "unknown",
    "note": "Coverage measurement is not wired up in this codebase yet — this is a fixed placeholder, not a real measurement.",
}


@dataclass
class TestToAdd:
    name: str
    description: str
    area: str = ""


@dataclass
class TestExecuted:
    name: str
    result: str  # "PASS" | "FAIL"
    notes: str = ""


@dataclass
class TestingAgentResult:
    test_plan: str = ""
    tests_to_add: list[TestToAdd] = field(default_factory=list)
    tests_executed: list[TestExecuted] = field(default_factory=list)
    bugs_found: list[str] = field(default_factory=list)
    suggested_fixes: list[str] = field(default_factory=list)  # rule: "can suggest fixes"
    # Requirement 5 — evidence attachments (a CI run URL, a screenshot
    # description, etc.), additive to the Test Evidence section's own
    # disclosure text.
    evidence_attachments: list[str] = field(default_factory=list)
    used_mock: bool = True
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0

    @property
    def pass_count(self) -> int:
        return sum(1 for t in self.tests_executed if t.result == "PASS")

    @property
    def fail_count(self) -> int:
        return sum(1 for t in self.tests_executed if t.result == "FAIL")


def is_test_path(path: str) -> bool:
    lowered = path.lower()
    return "tests/" in lowered or lowered.rsplit("/", 1)[-1].startswith("test_") or "_test." in lowered


# --- Heuristic path: an honest scaffold, no fabricated results ----------------------


def _build_heuristic_result(
    *,
    task: ImplementationTask,
    agent_type: TestAgentType,
    diff_text: str,
    existing_test_paths: list[str],
    story: Story | None = None,
    test_scenarios: str = "",
) -> TestingAgentResult:
    criteria = task.acceptance_criteria or [f"{task.title} behaves as described."]
    test_plan_lines = [f"- Verify: {c}" for c in criteria]
    if existing_test_paths:
        test_plan_lines.append(f"- Extend existing coverage in: {', '.join(existing_test_paths[:5])}")
    if test_scenarios.strip():
        # Rule 4 — "Testing stage should use these scenarios later." Not
        # re-parsed/re-derived, just surfaced as-is so a human reviewing
        # the test plan can see the scenarios it was built alongside.
        test_plan_lines.append("- See the story's own Test Scenarios document for the full scenario set this plan is based on.")

    tests_to_add = [
        TestToAdd(name=f"test_{i}_{_slug(c)}", description=f"Verify: {c}", area=agent_type.value)
        for i, c in enumerate(criteria, start=1)
    ]

    # HONESTY: no real execution capability exists — an empty list with an
    # explanatory bug/risk note is the truthful result, not a fabricated pass.
    bugs_found: list[str] = []
    suggested_fixes: list[str] = []
    if "TODO(implementation-agent)" in diff_text:
        bugs_found.append(
            "The diff contains an implementation-agent TODO scaffold, not working code — "
            "acceptance criteria cannot actually be verified yet."
        )
        suggested_fixes.append("Replace the TODO scaffold with a real implementation before testing can produce a real result.")

    return TestingAgentResult(
        test_plan="\n".join(test_plan_lines),
        tests_to_add=tests_to_add,
        tests_executed=[],  # honest — see module docstring
        bugs_found=bugs_found,
        suggested_fixes=suggested_fixes,
        used_mock=True,
    )


def _slug(text: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in text)[:40].strip("_") or "case"


# --- Real-AI path ----------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = (
    "You are {framing}. Given an approved task, its Low-Level Design summary, the PR diff that claims to "
    "implement it, its acceptance criteria, and existing test patterns in the repository, produce a test plan and "
    "assessment.\n"
    "You are reasoning about the diff, not executing it — report pass/fail as your best assessment from reading "
    "the code, and you MUST make clear this is an assessment, never a claim that you ran a real test suite.\n"
    "Respond with ONLY a single JSON object (no markdown code fences, no commentary) with exactly these keys:\n"
    '- "test_plan": string.\n'
    '- "tests_to_add": array of objects {"name": string, "description": string, "area": string}.\n'
    '- "tests_executed": array of objects {"name": string, "result": "PASS" or "FAIL", "notes": string — must '
    "explain this is a reasoning-based assessment, not a real run}.\n"
    '- "bugs_found": array of strings.\n'
    '- "suggested_fixes": array of strings — concrete, actionable suggestions; empty array if none.\n'
    "Do not fabricate test names or results beyond what the diff and acceptance criteria actually support."
)


def _run_real_agent(
    *,
    task: ImplementationTask,
    agent_type: TestAgentType,
    diff_text: str,
    lld_summary: str,
    existing_test_paths: list[str],
    pattern_chunks: list[RetrievedChunk],
    story: Story | None = None,
    test_scenarios: str = "",
) -> TestingAgentResult:
    # Plain substring replace, not str.format — the template's JSON-shape
    # examples contain literal `{`/`}` that .format() would misparse as
    # placeholders.
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.replace("{framing}", _AGENT_TYPE_FRAMING[agent_type])

    patterns_text = "\n".join(f"## Pattern: {c.source_title}\n{c.content}" for c in pattern_chunks) or "(none found)"
    story_text = (
        f"Title: {story.title}\nUser story: {story.user_story}\n"
        f"Acceptance criteria: {'; '.join(story.acceptance_criteria)}"
        if story
        else "(no related story found)"
    )
    user_content = (
        f"# Task\nTitle: {task.title}\nDescription: {task.description}\n"
        f"Acceptance criteria: {'; '.join(task.acceptance_criteria) or '(none declared)'}\n\n"
        f"# Related story\n{story_text}\n\n"
        f"# Approved LLD summary\n{lld_summary or '(not available)'}\n\n"
        # Rule 4 — "Testing stage should use these scenarios later." The
        # story's own Test Scenarios document (see
        # app/services/story_test_scenarios_agent.py), if one has been
        # approved — additive context, same as everything else here.
        f"# Approved Test Scenarios\n{test_scenarios or '(not available)'}\n\n"
        f"# PR diff\n{diff_text or '(no diff available)'}\n\n"
        f"# Existing test files in the repository\n{', '.join(existing_test_paths) or '(none found)'}\n\n"
        f"# Existing test patterns / standards\n{patterns_text}"
    )

    raw = generate_raw_text(system_prompt=system_prompt, user_content=user_content, output_token_budget=8192)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    parsed = json.loads(cleaned)

    tests_to_add = [
        TestToAdd(name=str(i.get("name", "")), description=str(i.get("description", "")), area=str(i.get("area", "")))
        for i in parsed.get("tests_to_add", [])
    ]
    tests_executed = []
    for i in parsed.get("tests_executed", []):
        result = str(i.get("result", "")).upper()
        if result not in _VALID_RESULTS:
            result = "FAIL"  # an unparseable result is treated conservatively, never silently dropped
        tests_executed.append(TestExecuted(name=str(i.get("name", "")), result=result, notes=str(i.get("notes", ""))))

    return TestingAgentResult(
        test_plan=str(parsed.get("test_plan", "")),
        tests_to_add=tests_to_add,
        tests_executed=tests_executed,
        bugs_found=[str(b) for b in parsed.get("bugs_found", [])],
        suggested_fixes=[str(f) for f in parsed.get("suggested_fixes", [])],
        used_mock=False,
    )


# --- Entry point -----------------------------------------------------------------------


def run_testing_agent(
    *,
    task: ImplementationTask,
    agent_type: TestAgentType,
    diff_text: str,
    lld_summary: str,
    existing_test_paths: list[str],
    pattern_chunks: list[RetrievedChunk],
    story: Story | None = None,
    test_scenarios: str = "",
) -> TestingAgentResult:
    """Real AI when configured; the deterministic heuristic otherwise, or if
    the real call errors or returns unparseable JSON (logged, not raised —
    same contract as every other agent in this codebase). `story`
    (requirement 4) is additive context — a story-scoped run's task
    already carries the same acceptance criteria either way. `test_scenarios`
    is likewise additive (Story Test Scenario Agent rule 4 — "Testing
    stage should use these scenarios later")."""
    kwargs = dict(
        task=task, agent_type=agent_type, diff_text=diff_text, existing_test_paths=existing_test_paths, story=story,
        test_scenarios=test_scenarios,
    )
    if get_active_provider() == "mock":
        return _build_heuristic_result(**kwargs)

    try:
        return _run_real_agent(lld_summary=lld_summary, pattern_chunks=pattern_chunks, **kwargs)
    except (AIGenerationError, json.JSONDecodeError, ValueError, TypeError, AttributeError, KeyError) as exc:
        logger.warning("Real-AI testing agent failed (%s); falling back to heuristic scaffold.", exc)
        return _build_heuristic_result(**kwargs)


# --- Rendered artifact content -----------------------------------------------------------


def render_test_report_markdown(result: TestingAgentResult, *, task: ImplementationTask, agent_type: TestAgentType) -> str:
    """The `test_report` Artifact's actual content — headings match the
    seeded testing-agent AgentPrompt's own Output Format (Summary,
    Acceptance Criteria Coverage, Test Evidence, Defects Found — see
    app/db/seed.py's RICH_DEFAULT_PROMPTS["testing"]) plus two sections
    this service adds (Suggested Fixes, Coverage Impact), so a report
    generated here and one drafted by a human running the existing generic
    agent loop read the same way."""
    parts = [
        f"# Test Report — {task.title}\n",
        f"**Agent type:** {agent_type.value} Test Agent\n",
        "## Summary\n",
        result.test_plan or "(no test plan produced)",
        "\n## Acceptance Criteria Coverage\n",
    ]
    for c in task.acceptance_criteria or ["(none declared)"]:
        parts.append(f"- {c}")

    parts.append("\n## Tests To Add\n")
    if result.tests_to_add:
        for t in result.tests_to_add:
            parts.append(f"- **{t.name}** ({t.area}): {t.description}")
    else:
        parts.append("(none)")

    parts.append(
        "\n## Test Evidence\n"
        "**Disclosure:** this codebase has no test-execution sandbox — the results below are the agent's "
        "reasoning-based assessment from the diff and acceptance criteria, not a real test-suite run.\n"
    )
    if result.tests_executed:
        parts.append(f"Pass: {result.pass_count} · Fail: {result.fail_count}\n")
        for t in result.tests_executed:
            parts.append(f"- [{t.result}] {t.name} — {t.notes}")
    else:
        parts.append("No test execution assessment was produced.")
    if result.evidence_attachments:
        parts.append("\nAttachments:")
        parts += [f"- {a}" for a in result.evidence_attachments]

    parts.append("\n## Defects Found\n")
    parts.append("\n".join(f"- {b}" for b in result.bugs_found) if result.bugs_found else "None.")

    parts.append("\n## Suggested Fixes\n")
    parts.append("\n".join(f"- {f}" for f in result.suggested_fixes) if result.suggested_fixes else "None.")

    parts.append(
        "\n## Coverage Impact\n"
        f"{COVERAGE_IMPACT_PLACEHOLDER['note']}\n"
    )

    return "\n".join(parts)
