"""Builds the full "what would be pushed to Jira" preview — Epics, Stories,
Implementation Tasks (-> Sub-tasks), and Testing bugs (-> Bugs), each with
validation errors and whether it's already linked to a real Jira issue.

This is the broadened sibling of app/services/jira_export.py's existing
story-only, local preview: reuses that module's field-mapping/validation
helpers (promoted to public names — normalize_jira_priority,
split_jira_dependencies, slugify_jira_label, render_story_jira_description)
rather than duplicating them, but covers all four mapped entity kinds and
cross-references real JiraIssueLink rows so duplicate prevention is
visible here, not just enforced silently at push time (see
app/api/routes/jira_integration.py's /jira/push).

Read-only — never creates anything. See app/models/jira_issue_link.py's
class docstring for why `source_key` takes the shape it does per entity kind.
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import ImplementationTask, JiraIssueLink, JiraSourceType, Project, TestRun, TestRunStatus
from app.services.jira_export import build_jira_export_preview
from app.services.story_export import Story


@dataclass
class JiraPushItem:
    source_type: JiraSourceType
    source_key: str
    label: str
    jira_issue_type: str
    parent_source_key: str | None
    summary: str
    description: str
    validation_errors: list[str] = field(default_factory=list)
    already_linked: JiraIssueLink | None = None

    @property
    def is_valid(self) -> bool:
        return len(self.validation_errors) == 0


@dataclass
class JiraPushPreview:
    epics: list[JiraPushItem] = field(default_factory=list)
    stories: list[JiraPushItem] = field(default_factory=list)
    implementation_tasks: list[JiraPushItem] = field(default_factory=list)
    testing_bugs: list[JiraPushItem] = field(default_factory=list)
    overall_errors: list[str] = field(default_factory=list)


def _existing_links(db: Session, jira_project_link_id) -> dict[tuple[JiraSourceType, str], JiraIssueLink]:
    rows = db.query(JiraIssueLink).filter(JiraIssueLink.jira_project_link_id == jira_project_link_id).all()
    return {(row.source_type, row.source_key): row for row in rows}


def _epic_items(stories: list[Story], links: dict) -> list[JiraPushItem]:
    seen: dict[str, None] = {}
    for story in stories:
        if story.epic and story.epic not in seen:
            seen[story.epic] = None

    items = []
    for epic_name in seen:
        errors = [] if epic_name.strip() else ["Epic name is empty."]
        items.append(
            JiraPushItem(
                source_type=JiraSourceType.EPIC, source_key=epic_name, label=epic_name, jira_issue_type="Epic",
                parent_source_key=None, summary=epic_name, description=f"Epic grouping stories under \"{epic_name}\".",
                validation_errors=errors, already_linked=links.get((JiraSourceType.EPIC, epic_name)),
            )
        )
    return items


def _story_items(stories: list[Story], links: dict) -> list[JiraPushItem]:
    story_preview = build_jira_export_preview(stories)
    items = []
    for story, story_result in zip(stories, story_preview.stories):
        items.append(
            JiraPushItem(
                source_type=JiraSourceType.STORY, source_key=story.title, label=story.title, jira_issue_type="Story",
                parent_source_key=story.epic or None, summary=story_result.mapping.summary,
                description=story_result.mapping.description, validation_errors=list(story_result.validation_errors),
                already_linked=links.get((JiraSourceType.STORY, story.title)),
            )
        )
    return items


def _implementation_task_items(db: Session, project: Project, links: dict) -> list[JiraPushItem]:
    tasks = db.query(ImplementationTask).filter(ImplementationTask.project_id == project.id).order_by(ImplementationTask.order_index).all()
    items = []
    for task in tasks:
        errors = []
        if not task.title.strip():
            errors.append("Title is missing.")
        if not task.description.strip():
            errors.append("Description is missing.")
        if not task.linked_story:
            errors.append("No linked story — a Sub-task needs a parent Story.")

        description = task.description
        if task.acceptance_criteria:
            description += "\n\nAcceptance Criteria:\n" + "\n".join(f"- {c}" for c in task.acceptance_criteria)

        items.append(
            JiraPushItem(
                source_type=JiraSourceType.IMPLEMENTATION_TASK, source_key=str(task.id), label=task.title,
                jira_issue_type="Sub-task", parent_source_key=task.linked_story, summary=task.title,
                description=description, validation_errors=errors,
                already_linked=links.get((JiraSourceType.IMPLEMENTATION_TASK, str(task.id))),
            )
        )
    return items


def _testing_bug_items(db: Session, project: Project, links: dict) -> list[JiraPushItem]:
    test_runs = (
        db.query(TestRun)
        .filter(TestRun.project_id == project.id, TestRun.status == TestRunStatus.COMPLETED)
        .order_by(TestRun.completed_at.asc())
        .all()
    )
    items = []
    for run in test_runs:
        task = run.implementation_task
        for i, bug_text in enumerate(run.bugs_found):
            source_key = f"{run.id}:{i}"
            errors = [] if bug_text.strip() else ["Bug description is empty."]
            items.append(
                JiraPushItem(
                    source_type=JiraSourceType.TESTING_BUG, source_key=source_key, label=bug_text[:120],
                    jira_issue_type="Bug", parent_source_key=(task.linked_story if task else None),
                    summary=bug_text[:255], description=bug_text, validation_errors=errors,
                    already_linked=links.get((JiraSourceType.TESTING_BUG, source_key)),
                )
            )
    return items


def build_jira_push_preview(db: Session, *, project: Project, jira_project_link, stories: list[Story]) -> JiraPushPreview:
    links = _existing_links(db, jira_project_link.id)

    return JiraPushPreview(
        epics=_epic_items(stories, links),
        stories=_story_items(stories, links),
        implementation_tasks=_implementation_task_items(db, project, links),
        testing_bugs=_testing_bug_items(db, project, links),
    )
