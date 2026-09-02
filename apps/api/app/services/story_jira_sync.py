"""Jira sync for exactly one Story — a dedicated, per-story sibling of
app/services/jira_push_preview.py's whole-project push. That module
re-parses `Story` (the app/services/story_export.py dataclass) fresh from
a story_backlog Markdown document on every call; this module works
directly off the real, persisted `Story` ORM row (app/models/story.py) —
the same row Sprint Planning/the delivery lane already use — so it can see
fields the dataclass never carries at all: `story_points`, `sprint_id`,
`jira_issue_key`, and a real `ImplementationTask.story_id` scope for
subtasks (rather than fuzzy `linked_story` title matching).

HONESTY, matching this codebase's established Jira-integration
disclosures (see app/services/jira_export.py's PUSH_TO_JIRA_ENABLED note
and app/services/jira_integration.py's "no update call exists" HARD
RULE): "Story Points" and "Sprint" have no universal standard Jira field
— which custom field (if any) an instance uses for these is
board/project-specific configuration this app has no way to discover
generically. Rather than guess a customfield id and risk silently writing
to the wrong field (or failing), both are rendered as plainly-labeled
lines in the issue description instead — visible in the preview exactly
as they'll be sent, never silently dropped. Priority IS a real, standard
Jira field and is sent as such.

DUPLICATE PREVENTION (requirement 6): reuses the exact same
JiraIssueLink unique constraint (`jira_project_link_id`, `source_type`,
`source_key`) every other Jira push in this codebase already relies on —
a story (or one of its subtasks) already linked is reported, never
re-created.

NO UPDATE: consistent with jira_integration.py's disclosed HARD RULE,
"syncing" an already-linked story never edits the existing Jira issue —
only app/api/routes/jira_integration.py's sync_jira_status route ever
pulls anything back (a read), and only Story.status, only for a
terminal/"done"-like Jira status (see that route for the narrow mapping).
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import ImplementationTask, JiraIssueLink, JiraProjectLink, JiraSourceType, Sprint, Story, User
from app.services import jira_integration as jira_api
from app.services.audit import record_audit_log
from app.services.jira_export import normalize_jira_priority
from app.services.jira_integration import JiraIntegrationError

STORY_POINTS_LABEL = "Story Points Estimate"
SPRINT_LABEL = "Sprint"


@dataclass
class StorySubtaskPreview:
    implementation_task_id: str
    title: str
    description: str
    validation_errors: list[str] = field(default_factory=list)
    already_linked: JiraIssueLink | None = None

    @property
    def is_valid(self) -> bool:
        return len(self.validation_errors) == 0


@dataclass
class StoryJiraPreview:
    story_id: uuid.UUID
    summary: str
    description: str
    priority: str | None
    story_points: int | None
    sprint_name: str | None
    subtasks: list[StorySubtaskPreview] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    already_linked: JiraIssueLink | None = None

    @property
    def is_valid(self) -> bool:
        return len(self.validation_errors) == 0


def render_story_description(story: Story) -> str:
    """Requirement 3 — "User story + acceptance criteria to Jira
    description", plus the disclosed Story Points/Sprint lines (see
    module docstring)."""
    lines = []
    if story.user_story:
        lines.append(story.user_story)
        lines.append("")
    lines.append("Acceptance Criteria:")
    if story.acceptance_criteria:
        lines += [f"- {item}" for item in story.acceptance_criteria]
    else:
        lines.append("- (none specified)")
    if story.story_points is not None:
        lines += ["", f"{STORY_POINTS_LABEL}: {story.story_points}"]
    return "\n".join(lines)


def _existing_link(db: Session, jira_project_link_id: uuid.UUID, source_type: JiraSourceType, source_key: str) -> JiraIssueLink | None:
    return (
        db.query(JiraIssueLink)
        .filter(
            JiraIssueLink.jira_project_link_id == jira_project_link_id,
            JiraIssueLink.source_type == source_type,
            JiraIssueLink.source_key == source_key,
        )
        .first()
    )


def _story_tasks(db: Session, story: Story) -> list[ImplementationTask]:
    """Requirement 3 — "Implementation tasks to Jira subtasks". Prefers
    the real story_id scope (see app/models/implementation_task.py); a
    story with no delivery lane yet simply has none, which is correct —
    there's nothing to map."""
    return (
        db.query(ImplementationTask)
        .filter(ImplementationTask.story_id == story.id)
        .order_by(ImplementationTask.order_index)
        .all()
    )


def build_story_jira_preview(db: Session, *, story: Story, jira_project_link: JiraProjectLink) -> StoryJiraPreview:
    """Requirement 2 — the exact payload a sync would send, built without
    ever calling Jira."""
    errors: list[str] = []
    if not story.title.strip():
        errors.append("Story title is missing.")

    priority, priority_error = normalize_jira_priority(story.priority)
    if priority_error:
        errors.append(priority_error)

    sprint_name: str | None = None
    if story.sprint_id is not None:
        sprint = db.get(Sprint, story.sprint_id)
        sprint_name = sprint.name if sprint is not None else None

    subtasks = []
    for task in _story_tasks(db, story):
        task_errors = []
        if not task.title.strip():
            task_errors.append("Title is missing.")
        subtasks.append(
            StorySubtaskPreview(
                implementation_task_id=str(task.id), title=task.title,
                description=task.description + (
                    "\n\nAcceptance Criteria:\n" + "\n".join(f"- {c}" for c in task.acceptance_criteria)
                    if task.acceptance_criteria else ""
                ),
                validation_errors=task_errors,
                already_linked=_existing_link(db, jira_project_link.id, JiraSourceType.IMPLEMENTATION_TASK, str(task.id)),
            )
        )

    description = render_story_description(story)
    if sprint_name:
        description += f"\n{SPRINT_LABEL}: {sprint_name}"

    return StoryJiraPreview(
        story_id=story.id,
        summary=story.title,
        description=description,
        priority=priority,
        story_points=story.story_points,
        sprint_name=sprint_name,
        subtasks=subtasks,
        validation_errors=errors,
        already_linked=_existing_link(db, jira_project_link.id, JiraSourceType.STORY, story.title),
    )


@dataclass
class SubtaskSyncResult:
    implementation_task_id: str
    status: str  # "created" | "skipped_duplicate" | "skipped_invalid" | "failed"
    jira_issue_key: str | None = None
    jira_issue_url: str | None = None
    errors: list[str] = field(default_factory=list)


@dataclass
class StoryJiraSyncResult:
    status: str  # "created" | "skipped_duplicate" | "skipped_invalid" | "failed"
    jira_issue_key: str | None = None
    jira_issue_url: str | None = None
    errors: list[str] = field(default_factory=list)
    subtasks: list[SubtaskSyncResult] = field(default_factory=list)


def sync_story_to_jira(
    db: Session, *, story: Story, jira_project_link: JiraProjectLink, base_url: str, email: str, token: str, triggered_by: User,
) -> StoryJiraSyncResult:
    """Requirements 1/4/6 — creates this one story's Jira issue (and any
    of its not-yet-linked subtasks), writes `Story.jira_issue_key`, and
    audits every issue created. Re-uses the exact same preview this
    story's own /preview endpoint returns, so what a human confirmed is
    exactly what gets sent — never re-derived differently at sync time.

    NO UPDATE (see module docstring): a story (or subtask) already linked
    is reported "skipped_duplicate", never edited."""
    preview = build_story_jira_preview(db, story=story, jira_project_link=jira_project_link)

    if preview.already_linked is not None:
        story_result = StoryJiraSyncResult(
            status="skipped_duplicate", jira_issue_key=preview.already_linked.jira_issue_key,
            jira_issue_url=preview.already_linked.jira_issue_url,
        )
        story_jira_key = preview.already_linked.jira_issue_key
    elif not preview.is_valid:
        return StoryJiraSyncResult(status="skipped_invalid", errors=preview.validation_errors)
    else:
        try:
            issue = jira_api.create_issue(
                base_url, email, token, project_key=jira_project_link.jira_project_key, issue_type="Story",
                summary=preview.summary, description=preview.description, priority=preview.priority,
            )
        except JiraIntegrationError as exc:
            return StoryJiraSyncResult(status="failed", errors=[str(exc)])

        link = JiraIssueLink(
            project_id=story.project_id, jira_project_link=jira_project_link, source_type=JiraSourceType.STORY,
            source_key=story.title, source_label=story.title, jira_issue_key=issue.key, jira_issue_type="Story",
            jira_issue_url=issue.url, triggered_by_user_id=triggered_by.id,
        )
        db.add(link)
        story.jira_issue_key = issue.key
        db.flush()

        record_audit_log(
            db, project_id=story.project_id, actor_user_id=triggered_by.id, action="jira_issue.created",
            entity_type="JiraIssueLink", entity_id=link.id,
            extra_data={"source_type": "STORY", "source_key": story.title, "jira_issue_key": issue.key, "story_id": str(story.id)},
        )
        story_result = StoryJiraSyncResult(status="created", jira_issue_key=issue.key, jira_issue_url=issue.url)
        story_jira_key = issue.key

    # Subtasks (requirement 3) — only possible once the story itself has
    # a real Jira key, whether from this call or an earlier one.
    for subtask in preview.subtasks:
        if subtask.already_linked is not None:
            story_result.subtasks.append(
                SubtaskSyncResult(
                    implementation_task_id=subtask.implementation_task_id, status="skipped_duplicate",
                    jira_issue_key=subtask.already_linked.jira_issue_key, jira_issue_url=subtask.already_linked.jira_issue_url,
                )
            )
            continue
        if not subtask.is_valid:
            story_result.subtasks.append(
                SubtaskSyncResult(implementation_task_id=subtask.implementation_task_id, status="skipped_invalid", errors=subtask.validation_errors)
            )
            continue
        if story_jira_key is None:
            story_result.subtasks.append(
                SubtaskSyncResult(implementation_task_id=subtask.implementation_task_id, status="skipped_invalid", errors=["Story has no Jira key yet."])
            )
            continue

        try:
            sub_issue = jira_api.create_issue(
                base_url, email, token, project_key=jira_project_link.jira_project_key, issue_type="Sub-task",
                summary=subtask.title, description=subtask.description, parent_key=story_jira_key,
            )
        except JiraIntegrationError as exc:
            story_result.subtasks.append(SubtaskSyncResult(implementation_task_id=subtask.implementation_task_id, status="failed", errors=[str(exc)]))
            continue

        sub_link = JiraIssueLink(
            project_id=story.project_id, jira_project_link=jira_project_link, source_type=JiraSourceType.IMPLEMENTATION_TASK,
            source_key=subtask.implementation_task_id, source_label=subtask.title, jira_issue_key=sub_issue.key,
            jira_issue_type="Sub-task", jira_issue_url=sub_issue.url, parent_jira_issue_key=story_jira_key,
            triggered_by_user_id=triggered_by.id,
        )
        db.add(sub_link)
        db.flush()

        record_audit_log(
            db, project_id=story.project_id, actor_user_id=triggered_by.id, action="jira_issue.created",
            entity_type="JiraIssueLink", entity_id=sub_link.id,
            extra_data={
                "source_type": "IMPLEMENTATION_TASK", "source_key": subtask.implementation_task_id,
                "jira_issue_key": sub_issue.key, "story_id": str(story.id),
            },
        )
        story_result.subtasks.append(
            SubtaskSyncResult(implementation_task_id=subtask.implementation_task_id, status="created", jira_issue_key=sub_issue.key, jira_issue_url=sub_issue.url)
        )

    return story_result
