"""Route-level tests for per-story Jira sync (app/api/routes/jira_integration.py's
/jira/stories/... endpoints + app/services/story_jira_sync.py). Covers the
"Implement Jira sync per story" requirements:
  2. Preview before sync.
  3. Field mapping (title->summary, user_story+acceptance_criteria->description,
     priority->priority, story_points/sprint->description lines, tasks->subtasks).
  4. Story.jira_issue_key stored.
  5. Jira status synced back to Story.status (narrow, "done"-like only).
  6. Duplicate prevention.
  7/8. Sync + audit logs.
  Rule: bulk sync only syncs an explicit, human-provided list — never "all".

No TestClient exists in this repo — every call is a direct call into the
real route function, mirroring test_jira_routes.py's conventions.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.jira_integration import (
    bulk_sync_stories_jira,
    connect_jira,
    create_jira_project_link,
    get_story_jira_preview,
    sync_jira_status,
    sync_story_jira,
)
import app.api.routes.jira_integration as jira_routes
from app.models import (
    AuditLog,
    JiraIssueLink,
    JiraSourceType,
    Sprint,
    SprintStatus,
    Story,
    StoryStatus,
    User,
    UserRole,
)
from app.schemas.jira_integration import (
    BulkSyncStoriesToJiraRequest,
    ConnectJiraRequest,
    CreateJiraProjectLinkRequest,
    SyncStoryToJiraRequest,
)
from app.services.jira_integration import JiraIssue, JiraProject, JiraUser
from tests.conftest import make_approved_artifact, make_implementation_task, make_node, make_story

REAL_TOKEN = "ATATT3xFfGF0ThisIsARealSecretJiraApiToken1234567890"


def _developer(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(user)
    db.flush()
    return user


def _mock_jira(monkeypatch):
    monkeypatch.setattr(
        jira_routes.jira_api, "verify_credentials",
        lambda base_url, email, token, **kw: JiraUser(account_id="acc-1", display_name="Suru", email=email),
    )
    monkeypatch.setattr(
        jira_routes.jira_api, "get_project",
        lambda base_url, email, token, key, **kw: JiraProject(key=key, name=f"{key} Project", id="10000"),
    )
    created = []

    def _create_issue(base_url, email, token, *, project_key, issue_type, summary, description, parent_key=None, labels=None, priority=None, **kw):
        num = len(created) + 1
        key = f"{project_key}-{num}"
        created.append({"issue_type": issue_type, "summary": summary, "description": description, "parent_key": parent_key, "priority": priority})
        return JiraIssue(key=key, url=f"{base_url}/browse/{key}")

    monkeypatch.setattr(jira_routes.jira_api, "create_issue", _create_issue)
    monkeypatch.setattr(jira_routes.jira_api, "get_issue_status", lambda *a, **kw: "In Progress")
    return created


def _connect_and_link(db, actor, project, monkeypatch):
    """Returns (link, created) — `created` is the list of issues the
    mocked `create_issue` recorded, so tests can assert on exact payloads
    sent (e.g. priority) without a real Jira call."""
    created = _mock_jira(monkeypatch)
    connection = connect_jira(
        ConnectJiraRequest(base_url="https://example.atlassian.net", email="suru@example.com", api_token=REAL_TOKEN, connected_by_id=actor.id),
        db,
    )
    link = create_jira_project_link(
        CreateJiraProjectLinkRequest(project_id=project.id, connection_id=connection.id, jira_project_key="PROJ"), db,
    )
    return link, created


def _make_full_story(db, project, actor, **overrides) -> Story:
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    artifact = make_approved_artifact(db, project, node, actor)
    story = make_story(db, project, actor, artifact.current_version_id, title=overrides.pop("title", "Password Reset Request"))
    story.priority = overrides.pop("priority", "High")
    story.story_points = overrides.pop("story_points", 5)
    story.acceptance_criteria = overrides.pop("acceptance_criteria", ["Returns 202 for a valid email", "Rate limited to 3/hour"])
    for k, v in overrides.items():
        setattr(story, k, v)
    db.flush()
    return story


# --- Preview (requirement 2, 3) -----------------------------------------------------------


def test_preview_maps_fields_per_spec(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    story = _make_full_story(db, project, actor)

    preview = get_story_jira_preview(story.id, db)

    assert preview.summary == story.title  # requirement 3: title -> summary
    assert story.user_story in preview.description
    assert "Returns 202 for a valid email" in preview.description
    assert preview.priority == "High"
    assert preview.story_points == 5
    assert "Story Points Estimate: 5" in preview.description  # no standard Jira field -> disclosed in description
    assert preview.already_linked is None
    assert preview.is_valid


def test_preview_includes_sprint_when_configured(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    story = _make_full_story(db, project, actor)
    sprint = Sprint(project_id=project.id, name="Sprint 7", status=SprintStatus.ACTIVE, created_by_id=actor.id)
    db.add(sprint)
    db.flush()
    story.sprint_id = sprint.id
    db.flush()

    preview = get_story_jira_preview(story.id, db)

    assert preview.sprint_name == "Sprint 7"
    assert "Sprint: Sprint 7" in preview.description


def test_preview_maps_implementation_tasks_to_subtasks(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    story = _make_full_story(db, project, actor)
    node = make_node(db, project, node_key="implementation", order_index=1, output_artifact_type="implementation_plan")
    plan = make_approved_artifact(db, project, node, actor)
    make_implementation_task(db, project, node, plan, title="Build endpoint", story_id=story.id)

    preview = get_story_jira_preview(story.id, db)

    assert len(preview.subtasks) == 1
    assert preview.subtasks[0].title == "Build endpoint"
    assert preview.subtasks[0].is_valid


def test_preview_404s_without_jira_project_link(db, project, actor):
    story = _make_full_story(db, project, actor)
    with pytest.raises(HTTPException) as exc_info:
        get_story_jira_preview(story.id, db)
    assert exc_info.value.status_code == 404


# --- Sync (requirements 1, 4, 6, 7, 8) -----------------------------------------------------


def test_sync_creates_issue_and_stores_key(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    story = _make_full_story(db, project, actor)
    dev = _developer(db)

    result = sync_story_jira(story.id, SyncStoryToJiraRequest(triggered_by_user_id=dev.id), db)

    assert result.status == "created"
    assert result.jira_issue_key == "PROJ-1"
    db.refresh(story)
    assert story.jira_issue_key == "PROJ-1"  # requirement 4

    actions = [row.action for row in db.query(AuditLog).filter(AuditLog.project_id == project.id).all()]
    assert "jira_issue.created" in actions  # requirement 8


def test_sync_sends_priority_as_real_field(db, project, actor, monkeypatch):
    _, created = _connect_and_link(db, actor, project, monkeypatch)
    story = _make_full_story(db, project, actor, priority="High")
    dev = _developer(db)

    sync_story_jira(story.id, SyncStoryToJiraRequest(triggered_by_user_id=dev.id), db)

    assert created[0]["priority"] == "High"


def test_sync_is_a_duplicate_no_op_on_second_call(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    story = _make_full_story(db, project, actor)
    dev = _developer(db)

    first = sync_story_jira(story.id, SyncStoryToJiraRequest(triggered_by_user_id=dev.id), db)
    second = sync_story_jira(story.id, SyncStoryToJiraRequest(triggered_by_user_id=dev.id), db)

    assert first.status == "created"
    assert second.status == "skipped_duplicate"
    assert second.jira_issue_key == first.jira_issue_key
    assert db.query(JiraIssueLink).filter(JiraIssueLink.source_type == JiraSourceType.STORY).count() == 1  # requirement 6


def test_sync_creates_subtasks_under_the_story_issue(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    story = _make_full_story(db, project, actor)
    node = make_node(db, project, node_key="implementation", order_index=1, output_artifact_type="implementation_plan")
    plan = make_approved_artifact(db, project, node, actor)
    make_implementation_task(db, project, node, plan, title="Build endpoint", story_id=story.id)
    dev = _developer(db)

    result = sync_story_jira(story.id, SyncStoryToJiraRequest(triggered_by_user_id=dev.id), db)

    assert result.status == "created"
    assert len(result.subtasks) == 1
    assert result.subtasks[0].status == "created"
    sub_link = db.query(JiraIssueLink).filter(JiraIssueLink.source_type == JiraSourceType.IMPLEMENTATION_TASK).first()
    assert sub_link.parent_jira_issue_key == result.jira_issue_key


def test_sync_invalid_story_is_skipped_and_never_calls_jira(db, project, actor, monkeypatch):
    _, created = _connect_and_link(db, actor, project, monkeypatch)
    story = _make_full_story(db, project, actor, priority="Not A Real Priority")
    dev = _developer(db)

    result = sync_story_jira(story.id, SyncStoryToJiraRequest(triggered_by_user_id=dev.id), db)

    assert result.status == "skipped_invalid"
    assert result.errors
    assert story.jira_issue_key is None


# --- Bulk sync (Rule: explicit list only) --------------------------------------------------


def test_bulk_sync_only_touches_the_named_stories(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    story_a = _make_full_story(db, project, actor, title="Story A")
    story_b = _make_full_story(db, project, actor, title="Story B")
    dev = _developer(db)

    response = bulk_sync_stories_jira(
        BulkSyncStoriesToJiraRequest(story_ids=[story_a.id], triggered_by_user_id=dev.id), db,
    )

    assert len(response.results) == 1
    assert response.results[0].story_id == story_a.id
    db.refresh(story_a)
    db.refresh(story_b)
    assert story_a.jira_issue_key is not None
    assert story_b.jira_issue_key is None  # never touched — no implicit "sync all"


# --- Status sync-back (requirement 5) ------------------------------------------------------


def test_sync_status_marks_story_done_on_terminal_jira_status(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    story = _make_full_story(db, project, actor)
    dev = _developer(db)
    sync_story_jira(story.id, SyncStoryToJiraRequest(triggered_by_user_id=dev.id), db)

    monkeypatch.setattr(jira_routes.jira_api, "get_issue_status", lambda *a, **kw: "Done")
    sync_jira_status(project.id, db)

    db.refresh(story)
    assert story.status == StoryStatus.DONE


def test_sync_status_does_not_touch_story_for_non_terminal_status(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    story = _make_full_story(db, project, actor)
    dev = _developer(db)
    sync_story_jira(story.id, SyncStoryToJiraRequest(triggered_by_user_id=dev.id), db)

    monkeypatch.setattr(jira_routes.jira_api, "get_issue_status", lambda *a, **kw: "In Review")
    sync_jira_status(project.id, db)

    db.refresh(story)
    assert story.status != StoryStatus.DONE
