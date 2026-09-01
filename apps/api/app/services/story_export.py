"""Parses a Story Crafting artifact's markdown into structured stories, and
renders those into Markdown / CSV / JSON export files.

Depends entirely on the story-crafting-agent's prompt requiring one exact,
bold-labeled field format per story (see
packages/prompts/agents/story-crafting-agent.md's Output Format, and the
matching AgentPrompt seeded in app/db/seed.py) — this is a text parser, not
a real structured-data model, so it's only as reliable as that format is
followed. A story missing a field gets an empty string for it rather than
failing the whole export; a document with no `## Story:` blocks at all
parses to zero stories rather than raising.

No real Jira (or any other external tracker) connection exists — see
app/services/jira_export.py for the Jira field-mapping *preview* built on
top of this same parser, and docs/architecture.md's MCP integrations
section for the planned real connection.
"""

import csv
import io
import json
import re
from dataclasses import asdict, dataclass, field

# Story Crafting's output_artifact_type — see workflows/sdlc-workflow.json
# and app/models/workflow.py's WorkflowNode.output_artifact_type. Shared
# constant so app/api/routes/artifacts.py's export endpoint and
# app/services/jira_export.py's preview agree on what a "story backlog"
# artifact actually is.
STORY_BACKLOG_ARTIFACT_TYPE = "story_backlog"

_STORY_HEADING_RE = re.compile(r"^##\s*Story:\s*(.+)$", re.MULTILINE)

# Matches "**Label:** value" up to the next "**Label:**" line or end of
# block — covers both a single-line value and a checklist that follows on
# subsequent lines.
_FIELD_RE = re.compile(r"\*\*([^*:]+):\*\*[ \t]*(.*?)(?=\n\*\*[^*:]+:\*\*|\Z)", re.DOTALL)

_CHECKLIST_ITEM_RE = re.compile(r"^\s*-\s*\[[ xX]?\]\s*(.+)$", re.MULTILINE)


@dataclass
class Story:
    title: str
    epic: str = ""
    feature: str = ""
    user_story: str = ""
    priority: str = ""
    dependencies: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    definition_of_done: list[str] = field(default_factory=list)


def _extract_fields(block: str) -> dict[str, str]:
    return {label.strip().lower(): value.strip() for label, value in _FIELD_RE.findall(block)}


def _as_checklist(raw: str) -> list[str]:
    items = _CHECKLIST_ITEM_RE.findall(raw)
    if items:
        return [item.strip() for item in items]
    # Not every draft will use "- [ ]" checkbox syntax — fall back to
    # plain lines so a reasonable-but-not-exact draft still exports cleanly.
    return [line.strip("- ").strip() for line in raw.splitlines() if line.strip()]


def parse_story_backlog(content_markdown: str) -> list[Story]:
    """Splits on `## Story:` headings and extracts each one's fields. See
    module docstring for how a missing/malformed field is handled."""
    headings = list(_STORY_HEADING_RE.finditer(content_markdown))
    stories: list[Story] = []

    for i, heading in enumerate(headings):
        block_start = heading.end()
        block_end = headings[i + 1].start() if i + 1 < len(headings) else len(content_markdown)
        block = content_markdown[block_start:block_end]
        fields = _extract_fields(block)

        stories.append(
            Story(
                title=heading.group(1).strip(),
                epic=fields.get("epic", ""),
                feature=fields.get("feature", ""),
                user_story=fields.get("user story", ""),
                priority=fields.get("priority", ""),
                dependencies=fields.get("dependencies", ""),
                acceptance_criteria=_as_checklist(fields.get("acceptance criteria", "")),
                definition_of_done=_as_checklist(fields.get("definition of done", "")),
            )
        )

    return stories


def find_related_story(story_backlog_content: str, linked_story: str | None) -> Story | None:
    """Matches an ImplementationTask's `linked_story` (a title, not a real
    FK — see app/models/implementation_task.py) against the approved
    story backlog. Shared by app/api/routes/implementation_runs.py and
    app/services/pr_review_agent.py — both need "the story this task
    belongs to" for their own agent's context, and neither should
    re-derive this lookup independently."""
    if not linked_story:
        return None
    for story in parse_story_backlog(story_backlog_content):
        if story.title.strip().lower() == linked_story.strip().lower():
            return story
    return None


def render_markdown(stories: list[Story], backlog_title: str) -> str:
    lines = [f"# {backlog_title}", ""]
    for story in stories:
        lines += [
            f"## {story.title}",
            f"**Epic:** {story.epic or '—'}",
            f"**Feature:** {story.feature or '—'}",
            f"**User Story:** {story.user_story or '—'}",
            f"**Priority:** {story.priority or '—'}",
            f"**Dependencies:** {story.dependencies or 'None.'}",
            "**Acceptance Criteria:**",
        ]
        lines += [f"- [ ] {item}" for item in story.acceptance_criteria] or ["- (none specified)"]
        lines.append("**Definition of Done:**")
        lines += [f"- [ ] {item}" for item in story.definition_of_done] or ["- (none specified)"]
        lines.append("")
    return "\n".join(lines)


_CSV_COLUMNS = [
    ("title", "Title"),
    ("epic", "Epic"),
    ("feature", "Feature"),
    ("user_story", "User Story"),
    ("priority", "Priority"),
    ("dependencies", "Dependencies"),
    ("acceptance_criteria", "Acceptance Criteria"),
    ("definition_of_done", "Definition of Done"),
]


def render_csv(stories: list[Story]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(label for _, label in _CSV_COLUMNS)
    for story in stories:
        row = asdict(story)
        writer.writerow(
            "; ".join(row[key]) if isinstance(row[key], list) else row[key] for key, _ in _CSV_COLUMNS
        )
    return buffer.getvalue()


def render_json(stories: list[Story], backlog_title: str) -> str:
    return json.dumps(
        {
            "backlogTitle": backlog_title,
            "storyCount": len(stories),
            "stories": [
                {
                    "title": s.title,
                    "epic": s.epic,
                    "feature": s.feature,
                    "userStory": s.user_story,
                    "priority": s.priority,
                    "dependencies": s.dependencies,
                    "acceptanceCriteria": s.acceptance_criteria,
                    "definitionOfDone": s.definition_of_done,
                }
                for s in stories
            ],
        },
        indent=2,
    )
