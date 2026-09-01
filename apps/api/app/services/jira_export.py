"""Jira field-mapping preview for Story Crafting — foundation only.

No real Jira connection exists (no MCP tool, no API client, no auth) — see
docs/architecture.md's MCP integrations section for the planned real
connection and app/models/integration.py for the Integration row Jira will
eventually use. This module only maps already-parsed stories (see
app/services/story_export.py) onto the shape a real Jira push would need,
and validates that mapping, so a human can review it before anything ever
actually reaches Jira. `PUSH_TO_JIRA_ENABLED = False` is checked by the
route so this stays true even if someone forgets to check the docstring.

Mapping (see docs/architecture.md for the rationale):
    Epic               -> Jira Epic          (epic name; not yet a real Epic Link/key)
    Feature             -> Jira label         (slugified; Jira labels can't contain spaces)
    User Story          -> Jira Story summary (the "As a..., I want..., so that..." sentence)
    Acceptance Criteria -> Jira description   (rendered as a checklist)
    Priority             -> Jira priority      (normalized to Jira's standard 5-point scale)
    Dependencies         -> linked issues      (placeholder — story titles, not real Jira keys,
                                                 since no Jira issue exists yet to link to)
"""

import re
from dataclasses import dataclass, field

from app.services.story_export import Story

# Real Jira pushes are out of scope for this feature — see module
# docstring. Kept as an explicit constant (not just an absent code path)
# so it's a one-line, greppable answer to "can this push to Jira yet?"
PUSH_TO_JIRA_ENABLED = False

# Jira's standard priority scale — our stories only ever state one of the
# first three (see packages/prompts/agents/story-crafting-agent.md's
# Output Format), but the mapping is written against the full scale so it
# doesn't need to change if that ever expands.
_JIRA_PRIORITIES = ("Highest", "High", "Medium", "Low", "Lowest")

_DEPENDENCY_SPLIT_RE = re.compile(r"\s*[,;\n]\s*")
_NONE_DEPENDENCY_VALUES = {"", "none", "none.", "n/a", "-"}


def slugify_jira_label(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "unlabeled"


def normalize_jira_priority(priority: str) -> tuple[str | None, str | None]:
    """Returns (normalized_value, error) — error is None when the input
    matched (case-insensitively) one of Jira's standard priorities."""
    if not priority.strip():
        return None, "Priority is missing."
    for candidate in _JIRA_PRIORITIES:
        if priority.strip().lower() == candidate.lower():
            return candidate, None
    return None, f"Priority '{priority}' doesn't match Jira's standard scale ({', '.join(_JIRA_PRIORITIES)})."


def split_jira_dependencies(raw: str) -> list[str]:
    if raw.strip().lower() in _NONE_DEPENDENCY_VALUES:
        return []
    return [d.strip().rstrip(".") for d in _DEPENDENCY_SPLIT_RE.split(raw) if d.strip()]


def render_story_jira_description(story: Story) -> str:
    """Acceptance Criteria -> Jira description. Includes the user story
    line for context, since a Jira Story's description is where a reader
    actually looks for the full picture — the checklist alone would be
    acceptance criteria with no story to accept."""
    lines = []
    if story.user_story:
        lines.append(story.user_story)
        lines.append("")
    lines.append("Acceptance Criteria:")
    if story.acceptance_criteria:
        lines += [f"- {item}" for item in story.acceptance_criteria]
    else:
        lines.append("- (none specified)")
    return "\n".join(lines)


@dataclass
class JiraFieldMapping:
    epic: str | None
    labels: list[str]
    summary: str
    description: str
    priority: str | None
    # Dependency story titles this issue would link to — never real Jira
    # keys, since no Jira issue exists yet for any of them. See module
    # docstring's "linked issues" row.
    linked_issues_placeholder: list[str]


@dataclass
class StoryJiraPreview:
    story_title: str
    jira_issue_type: str
    mapping: JiraFieldMapping
    validation_errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.validation_errors) == 0


@dataclass
class JiraExportPreview:
    stories: list[StoryJiraPreview] = field(default_factory=list)
    # Backlog-level problems that aren't any single story's fault (e.g. two
    # stories sharing a title, so a dependency reference is ambiguous).
    overall_errors: list[str] = field(default_factory=list)

    @property
    def story_count(self) -> int:
        return len(self.stories)

    @property
    def valid_story_count(self) -> int:
        return sum(1 for s in self.stories if s.is_valid)

    @property
    def has_errors(self) -> bool:
        return bool(self.overall_errors) or any(not s.is_valid for s in self.stories)


def build_jira_export_preview(stories: list[Story]) -> JiraExportPreview:
    preview = JiraExportPreview()

    titles_seen: dict[str, int] = {}
    for story in stories:
        titles_seen[story.title] = titles_seen.get(story.title, 0) + 1
    duplicate_titles = {title for title, count in titles_seen.items() if count > 1}
    if duplicate_titles:
        preview.overall_errors.append(
            "Duplicate story title(s) found — dependency links can't be resolved unambiguously: "
            + ", ".join(sorted(duplicate_titles))
        )

    known_titles = set(titles_seen.keys())

    for story in stories:
        errors: list[str] = []

        if not story.epic:
            errors.append("Epic is missing.")
        if not story.feature:
            errors.append("Feature is missing.")
        if not story.user_story:
            errors.append("User Story is missing.")
        if not story.acceptance_criteria:
            errors.append("Acceptance Criteria is missing.")

        priority, priority_error = normalize_jira_priority(story.priority)
        if priority_error:
            errors.append(priority_error)

        dependency_titles = split_jira_dependencies(story.dependencies)
        for dep_title in dependency_titles:
            if dep_title not in known_titles:
                errors.append(f"Dependency '{dep_title}' doesn't match any story title in this backlog.")

        mapping = JiraFieldMapping(
            epic=story.epic or None,
            labels=[slugify_jira_label(story.feature)] if story.feature else [],
            summary=story.user_story or story.title,
            description=render_story_jira_description(story),
            priority=priority,
            linked_issues_placeholder=dependency_titles,
        )

        preview.stories.append(
            StoryJiraPreview(
                story_title=story.title,
                jira_issue_type="Story",
                mapping=mapping,
                validation_errors=errors,
            )
        )

    return preview
