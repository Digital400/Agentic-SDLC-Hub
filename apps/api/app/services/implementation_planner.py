"""ImplementationPlannerService — turns an approved LLD (plus its story
backlog) into a structured, persisted list of implementation tasks split
by area, for the `implementation_planning` workflow stage (see
`workflows/sdlc-workflow.json` and `app/models/implementation_task.py`).

Mirrors `app/services/validator_agent.py`'s exact real-AI/heuristic split
and resilience contract: one JSON-structured call via `generate_raw_text`
when a real provider is configured, a deterministic heuristic otherwise —
or if the real call errors or returns unparseable JSON (never let a
formatting slip or provider outage block planning).

No coding agent is built or wired to these tasks yet — `assigned_agent_type`
and `status` are forward-looking fields for when one is (see
app/models/enums.py's ImplementationTaskStatus docstring). This module
only plans work; it never writes or touches source code.
"""

import json
import logging
import re
from dataclasses import dataclass, field

from app.models.enums import ImplementationTaskArea, ImplementationTaskRiskLevel
from app.services.ai_generation import AIGenerationError, generate_raw_text, get_active_provider
from app.services.markdown_sections import find_section, split_into_sections

logger = logging.getLogger(__name__)

_VALID_AREAS = {a.value for a in ImplementationTaskArea}
_VALID_RISK_LEVELS = {r.value for r in ImplementationTaskRiskLevel}

# Forward-looking classification only — no real per-area coding agent
# exists yet (see module docstring). Deliberately namespaced
# "...-coding-agent" rather than e.g. "testing-agent", which already
# names a real, registered stage AgentDefinition (see app/db/seed.py) —
# this is a different, not-yet-built concept and the strings must not
# collide with it.
AREA_TO_AGENT_TYPE: dict[str, str] = {
    "BACKEND": "backend-coding-agent",
    "FRONTEND": "frontend-coding-agent",
    "DATABASE": "database-coding-agent",
    "TESTING": "testing-coding-agent",
    "INFRA": "infra-coding-agent",
    "DOCS": "docs-coding-agent",
}

_IMPLEMENTATION_TASK_BREAKDOWN_SECTION = "Implementation Task Breakdown"
_STORIES_COVERED_SECTION = "Stories Covered"

# Keyword -> area, checked in order (first match wins) against each
# candidate task line's own text — a loose heuristic, same spirit as
# validator_agent.py's keyword-based criteria coverage check.
_AREA_KEYWORDS: list[tuple[str, str]] = [
    ("migration", "DATABASE"),
    ("schema", "DATABASE"),
    ("table", "DATABASE"),
    ("database", "DATABASE"),
    ("endpoint", "BACKEND"),
    ("api", "BACKEND"),
    ("service", "BACKEND"),
    ("component", "FRONTEND"),
    ("ui", "FRONTEND"),
    ("frontend", "FRONTEND"),
    ("page", "FRONTEND"),
    ("test", "TESTING"),
    ("deploy", "INFRA"),
    ("infra", "INFRA"),
    ("provision", "INFRA"),
    ("doc", "DOCS"),
    ("readme", "DOCS"),
]

_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")


@dataclass
class TaskDraft:
    title: str
    description: str
    area: str  # one of ImplementationTaskArea's values
    risk_level: str = "MEDIUM"  # one of ImplementationTaskRiskLevel's values
    linked_story: str | None = None
    linked_lld_section: str | None = None
    expected_paths: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    test_expectation: str = ""

    @property
    def assigned_agent_type(self) -> str:
        return AREA_TO_AGENT_TYPE.get(self.area, AREA_TO_AGENT_TYPE["BACKEND"])


def _infer_area(text: str) -> str:
    lowered = text.lower()
    for keyword, area in _AREA_KEYWORDS:
        if keyword in lowered:
            return area
    return "BACKEND"  # a reasonable default — most breakdown items skew backend/API work


# Which of a Story Implementation Plan's own sections (see
# app/services/story_implementation_plan_agent.py's
# STORY_IMPLEMENTATION_PLAN_SECTIONS — that exact heading text) implies a
# real ImplementationTask for a story, and in what order those tasks
# should run: DATABASE before BACKEND before FRONTEND, so a later area's
# agent can build against the earlier area's already-committed code
# (a migration exists before the API queries the column it added; the
# API endpoint exists before the UI calls it). Only areas with a real
# working coding agent today (see app/services/implementation_agent.py's
# SUPPORTED_AREAS) are considered here — TESTING/INFRA/DOCS tasks aren't
# generated from the plan by this function.
_PLAN_SECTION_TO_AREA: tuple[tuple[str, ImplementationTaskArea], ...] = (
    ("Database/Migration Tasks", ImplementationTaskArea.DATABASE),
    ("Backend Tasks", ImplementationTaskArea.BACKEND),
    ("Frontend Tasks", ImplementationTaskArea.FRONTEND),
)

# A section that just says "None"/"N/A"/etc. is the agent honestly
# reporting "this area needs no work" (see that agent's own prompt) —
# not a real task to create. Matched against the section's full stripped
# content (lowercased), not a substring, so a genuine one-line task that
# happens to contain the word "none" is never mistaken for an empty one.
_EMPTY_PLAN_SECTION_MARKERS = {
    "none", "n/a", "na", "not applicable", "not needed", "not required",
    "no changes needed", "no changes required", "no backend changes needed",
    "no frontend changes needed", "no database changes needed", "-",
}


def _plan_section_has_real_work(content: str | None) -> bool:
    if not content:
        return False
    stripped = content.strip().strip("-*•.! \n\t")
    return bool(stripped) and stripped.lower() not in _EMPTY_PLAN_SECTION_MARKERS


def infer_story_task_areas(plan_markdown: str) -> list[ImplementationTaskArea]:
    """Full-stack-per-story: rather than guessing a single area for the
    whole story (the old _infer_area heuristic, now only a fallback — see
    its caller), read what the story's own, already-approved
    Implementation Plan actually says is needed per area and generate one
    ImplementationTask per area with real content, in DATABASE -> BACKEND
    -> FRONTEND order. Returns an empty list if the plan has no
    identifiable section content at all (e.g. it doesn't follow the
    expected heading structure) — the caller falls back to the
    single-heuristic-task behavior in that case, never leaving a story
    with zero tasks."""
    areas = []
    for heading, area in _PLAN_SECTION_TO_AREA:
        section = find_section(plan_markdown, heading)
        if section is not None and _plan_section_has_real_work(section.get("content")):
            areas.append(area)
    return areas


def _extract_bullet_lines(section_content: str) -> list[str]:
    lines: list[str] = []
    for raw_line in section_content.splitlines():
        match = _BULLET_RE.match(raw_line) or _NUMBERED_RE.match(raw_line)
        if match:
            text = match.group(1).strip()
            if text:
                lines.append(text)
    # A breakdown section written as plain sentences (no bullets/numbers)
    # still shouldn't come back empty — fall back to non-blank lines.
    if not lines:
        lines = [line.strip() for line in section_content.splitlines() if line.strip()]
    return lines


def _first_story_title(lld_content: str) -> str | None:
    """Best-effort only — the heuristic path has no real judgment to match
    a specific task to a specific story, so every heuristic task is linked
    to the first story listed under Stories Covered (or none, if that
    section is missing/empty). The real-AI path does this properly."""
    section = find_section(lld_content, _STORIES_COVERED_SECTION)
    if section is None:
        return None
    for line in _extract_bullet_lines(section["content"]):
        # "Story: <title> — <note>" or just "<title>" — take the part
        # before a colon/dash if present, else the whole line.
        title = re.split(r"[:—-]", line, maxsplit=1)[0].strip()
        if title:
            return title
    return None


# --- Heuristic path: parse the LLD's own breakdown section, no model call ---------


def _build_heuristic_plan(*, lld_content: str) -> list[TaskDraft]:
    section = find_section(lld_content, _IMPLEMENTATION_TASK_BREAKDOWN_SECTION)
    if section is None or not section["content"].strip():
        return []

    story = _first_story_title(lld_content)
    tasks: list[TaskDraft] = []
    for line in _extract_bullet_lines(section["content"]):
        area = _infer_area(line)
        tasks.append(
            TaskDraft(
                title=line[:255],
                description=line,
                area=area,
                risk_level="MEDIUM",
                linked_story=story,
                linked_lld_section=_IMPLEMENTATION_TASK_BREAKDOWN_SECTION,
                test_expectation="Add a test covering this task's acceptance criteria before marking it done.",
            )
        )
    return tasks


# --- Real-AI path ------------------------------------------------------------------

_PLANNER_SYSTEM_PROMPT = (
    "You are an implementation planner. Given an approved Low-Level Design document and its story backlog, "
    "break the design into a structured list of implementation tasks — real, assignable units of work, not a "
    "restatement of the design.\n"
    "Respond with ONLY a single JSON array (no markdown code fences, no commentary). Each element is an object "
    "with exactly these keys:\n"
    '- "title": short string.\n'
    '- "description": what this task actually involves.\n'
    '- "area": one of "BACKEND", "FRONTEND", "DATABASE", "TESTING", "INFRA", "DOCS".\n'
    '- "linked_story": the exact title of the story (from Stories Covered) this task serves, or null.\n'
    '- "linked_lld_section": the exact LLD section heading this task derives from, or null.\n'
    '- "expected_paths": array of strings — files/folders likely touched.\n'
    '- "dependencies": array of strings — titles of OTHER tasks in this same array that must land first; '
    "empty array if none.\n"
    '- "acceptance_criteria": array of strings.\n'
    '- "test_expectation": string — what must be tested before this task counts as done.\n'
    '- "risk_level": one of "LOW", "MEDIUM", "HIGH".\n'
    "Do not invent stories or LLD sections that aren't in the input — use null for linked_story/"
    "linked_lld_section if you genuinely can't tie the task to one. Cover every area the design actually needs; "
    "do not force tasks into areas the design doesn't touch."
)


def _run_real_planner(*, lld_content: str, story_backlog_content: str) -> list[TaskDraft]:
    user_content = (
        f"# Approved Low-Level Design\n{lld_content}\n\n"
        f"# Approved Story Backlog\n{story_backlog_content or '(not provided)'}"
    )
    raw = generate_raw_text(system_prompt=_PLANNER_SYSTEM_PROMPT, user_content=user_content, output_token_budget=8192)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, list):
        raise ValueError("Expected a JSON array of tasks.")

    tasks: list[TaskDraft] = []
    for item in parsed:
        area = str(item.get("area", "BACKEND")).upper()
        if area not in _VALID_AREAS:
            area = "BACKEND"
        risk_level = str(item.get("risk_level", "MEDIUM")).upper()
        if risk_level not in _VALID_RISK_LEVELS:
            risk_level = "MEDIUM"
        tasks.append(
            TaskDraft(
                title=str(item.get("title", "Untitled task"))[:255],
                description=str(item.get("description", "")),
                area=area,
                risk_level=risk_level,
                linked_story=(str(item["linked_story"]) if item.get("linked_story") else None),
                linked_lld_section=(str(item["linked_lld_section"]) if item.get("linked_lld_section") else None),
                expected_paths=[str(p) for p in item.get("expected_paths", [])],
                dependencies=[str(d) for d in item.get("dependencies", [])],
                acceptance_criteria=[str(c) for c in item.get("acceptance_criteria", [])],
                test_expectation=str(item.get("test_expectation", "")),
            )
        )
    return tasks


# --- Entry point ---------------------------------------------------------------------


def build_implementation_plan(*, lld_content: str, story_backlog_content: str) -> list[TaskDraft]:
    """The single entry point. Real AI when configured; the deterministic
    heuristic otherwise, or if the real call errors or returns unparseable
    JSON (logged, not raised — planning must never fail the run over a
    provider outage or a formatting slip, same contract as
    validator_agent.run_validator)."""
    if get_active_provider() == "mock":
        return _build_heuristic_plan(lld_content=lld_content)

    try:
        return _run_real_planner(lld_content=lld_content, story_backlog_content=story_backlog_content)
    except (AIGenerationError, json.JSONDecodeError, ValueError, TypeError, AttributeError, KeyError) as exc:
        logger.warning("Real-AI implementation planner failed (%s); falling back to heuristic planning.", exc)
        return _build_heuristic_plan(lld_content=lld_content)


def render_plan_markdown(tasks: list[TaskDraft]) -> str:
    """The Implementation Plan artifact's own reviewable content — a
    Markdown document grouping tasks by area, mirroring the drafting
    agent's own documented Output Format (see app/db/seed.py's
    RICH_DEFAULT_PROMPTS["implementation_planning"]) so a plan generated
    by this service and one drafted by a human running the agent manually
    look the same."""
    if not tasks:
        return "# Implementation Plan\n\nNo tasks were generated.\n"

    parts = ["# Implementation Plan\n"]
    for area in [a.value for a in ImplementationTaskArea]:
        area_tasks = [t for t in tasks if t.area == area]
        if not area_tasks:
            continue
        parts.append(f"## {area}\n")
        for t in area_tasks:
            parts.append(f"### {t.title}\n")
            parts.append(f"**Description:** {t.description}\n")
            parts.append(f"**Linked Story:** {t.linked_story or 'None.'}\n")
            parts.append(f"**Linked LLD Section:** {t.linked_lld_section or 'None.'}\n")
            paths = ", ".join(t.expected_paths) or "None."
            parts.append(f"**Expected Files/Folders:** {paths}\n")
            deps = ", ".join(t.dependencies) or "None."
            parts.append(f"**Dependencies:** {deps}\n")
            criteria = "\n".join(f"- [ ] {c}" for c in t.acceptance_criteria) or "- [ ] (none specified)"
            parts.append(f"**Acceptance Criteria:**\n{criteria}\n")
            parts.append(f"**Test Expectation:** {t.test_expectation or 'None specified.'}\n")
            parts.append(f"**Risk Level:** {t.risk_level.title()}\n")
    return "\n".join(parts)
