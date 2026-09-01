"""Route-level tests for Jira integration (app/api/routes/jira_integration.py):
  1/2. Connect/disconnect, project configuration.
  3/5. Preview surfaces validation errors and duplicates before any push.
  6/7. Push only creates exactly what's selected; records Jira issue keys.
  8. Sync status.
  Rules: no auto-create (push requires explicit selections), duplicate
  prevention (unique constraint + preview/push both honor it), validation
  errors block push, token never leaks.

No TestClient exists in this repo — every call is a direct call into the
real route function, mirroring test_github_integration.py's conventions.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.jira_integration import (
    connect_jira,
    create_jira_project_link,
    disconnect_jira,
    get_jira_push_preview,
    push_to_jira,
    sync_jira_status,
)
import app.api.routes.jira_integration as jira_routes
from app.models import (
    AuditLog,
    ArtifactStatus,
    IntegrationStatus,
    JiraIssueLink,
    JiraProjectLink,
    JiraSourceType,
    User,
    UserRole,
)
from app.schemas.jira_integration import (
    ConnectJiraRequest,
    CreateJiraProjectLinkRequest,
    JiraPushRequest,
    JiraPushSelectionItem,
)
from app.services.jira_integration import JiraIntegrationError, JiraProject, JiraUser
from tests.conftest import make_approved_artifact, make_implementation_task, make_node

REAL_TOKEN = "ATATT3xFfGF0ThisIsARealSecretJiraApiToken1234567890"

SAMPLE_STORY_BACKLOG = (
    "## Story: Password Reset Request\n\n"
    "**Epic:** Account Recovery\n"
    "**Feature:** Password Reset\n"
    "**User Story:** As a user I want to request a password reset.\n"
    "**Priority:** High\n"
    "**Dependencies:** None.\n"
    "**Acceptance Criteria:**\n- Returns 202 for a valid email\n"
)


def _developer(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(user)
    db.flush()
    return user


def _mock_jira(monkeypatch, *, project_key="PROJ"):
    monkeypatch.setattr(
        jira_routes.jira_api, "verify_credentials",
        lambda base_url, email, token, **kw: JiraUser(account_id="acc-1", display_name="Suru", email=email),
    )
    monkeypatch.setattr(
        jira_routes.jira_api, "get_project",
        lambda base_url, email, token, key, **kw: JiraProject(key=key, name=f"{key} Project", id="10000"),
    )

    created = []

    def _create_issue(base_url, email, token, *, project_key, issue_type, summary, description, parent_key=None, labels=None, **kw):
        from app.services.jira_integration import JiraIssue

        num = len(created) + 1
        key = f"{project_key}-{num}"
        created.append((issue_type, summary, parent_key))
        return JiraIssue(key=key, url=f"{base_url}/browse/{key}")

    monkeypatch.setattr(jira_routes.jira_api, "create_issue", _create_issue)
    monkeypatch.setattr(jira_routes.jira_api, "get_issue_status", lambda *a, **kw: "In Progress")
    return created


def _connect_and_link(db, actor, project, monkeypatch):
    _mock_jira(monkeypatch)
    connection = connect_jira(
        ConnectJiraRequest(base_url="https://example.atlassian.net", email="suru@example.com", api_token=REAL_TOKEN, connected_by_id=actor.id),
        db,
    )
    link = create_jira_project_link(
        CreateJiraProjectLinkRequest(project_id=project.id, connection_id=connection.id, jira_project_key="PROJ"), db,
    )
    return link


def _story_setup(db, project, actor):
    story_node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, story_node, actor, content=SAMPLE_STORY_BACKLOG)


# --- Connect / disconnect / project link -------------------------------------------------


def test_connect_never_leaks_the_token_in_audit_log(db, actor, monkeypatch):
    _mock_jira(monkeypatch)
    connection = connect_jira(
        ConnectJiraRequest(base_url="https://example.atlassian.net", email="suru@example.com", api_token=REAL_TOKEN, connected_by_id=actor.id),
        db,
    )

    assert connection.status == IntegrationStatus.CONNECTED
    assert connection.token_hint == f"****{REAL_TOKEN[-4:]}"
    dump = str([row.extra_data for row in db.query(AuditLog).all()])
    assert REAL_TOKEN not in dump


def test_disconnect_clears_the_stored_token(db, actor, monkeypatch):
    _mock_jira(monkeypatch)
    connection = connect_jira(
        ConnectJiraRequest(base_url="https://example.atlassian.net", email="suru@example.com", api_token=REAL_TOKEN, connected_by_id=actor.id),
        db,
    )
    disconnect_jira(connection.id, db)

    from app.models import IntegrationConnection

    row = db.get(IntegrationConnection, connection.id)
    assert row.access_token_encrypted == ""
    assert row.status == IntegrationStatus.NOT_CONNECTED


def test_create_project_link_persists_key_and_name(db, project, actor, monkeypatch):
    link = _connect_and_link(db, actor, project, monkeypatch)
    assert link.jira_project_key == "PROJ"
    assert link.jira_project_name == "PROJ Project"


# --- Preview (requirements 3, 5) ----------------------------------------------------------


def test_preview_404s_when_no_jira_project_configured(db, project, actor):
    with pytest.raises(HTTPException) as exc_info:
        get_jira_push_preview(project.id, db)
    assert exc_info.value.status_code == 404


def test_preview_reports_overall_error_when_no_approved_story_backlog(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)

    preview = get_jira_push_preview(project.id, db)

    assert preview.overall_errors
    assert preview.stories == []


def test_preview_shows_epics_stories_and_validation_errors(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    _story_setup(db, project, actor)

    preview = get_jira_push_preview(project.id, db)

    assert any(e.label == "Account Recovery" for e in preview.epics)
    assert any(s.source_key == "Password Reset Request" and s.validation_errors == [] for s in preview.stories)


# --- Push (requirements 6, 7 + rules) ------------------------------------------------------


def test_push_creates_epic_then_story_with_correct_parent_in_one_request(db, project, actor, monkeypatch):
    link = _connect_and_link(db, actor, project, monkeypatch)
    _story_setup(db, project, actor)
    dev = _developer(db)

    response = push_to_jira(
        JiraPushRequest(
            project_id=project.id, triggered_by_user_id=dev.id,
            selections=[
                JiraPushSelectionItem(source_type=JiraSourceType.STORY, source_key="Password Reset Request"),
                JiraPushSelectionItem(source_type=JiraSourceType.EPIC, source_key="Account Recovery"),
            ],
        ),
        db,
    )

    by_type = {r.source_type: r for r in response.results}
    assert by_type[JiraSourceType.EPIC].status == "created"
    assert by_type[JiraSourceType.STORY].status == "created"

    story_link = db.query(JiraIssueLink).filter(JiraIssueLink.source_type == JiraSourceType.STORY).first()
    epic_link = db.query(JiraIssueLink).filter(JiraIssueLink.source_type == JiraSourceType.EPIC).first()
    assert story_link.parent_jira_issue_key == epic_link.jira_issue_key

    actions = [row.action for row in db.query(AuditLog).filter(AuditLog.project_id == project.id).all()]
    assert actions.count("jira_issue.created") == 2


def test_push_skips_an_already_linked_item_as_duplicate(db, project, actor, monkeypatch):
    link = _connect_and_link(db, actor, project, monkeypatch)
    _story_setup(db, project, actor)
    dev = _developer(db)
    db.add(
        JiraIssueLink(
            project_id=project.id, jira_project_link=link, source_type=JiraSourceType.EPIC, source_key="Account Recovery",
            source_label="Account Recovery", jira_issue_key="PROJ-99", jira_issue_type="Epic", jira_issue_url="https://x/PROJ-99",
        )
    )
    db.flush()

    response = push_to_jira(
        JiraPushRequest(project_id=project.id, triggered_by_user_id=dev.id, selections=[JiraPushSelectionItem(source_type=JiraSourceType.EPIC, source_key="Account Recovery")]),
        db,
    )

    assert response.results[0].status == "skipped_duplicate"
    assert response.results[0].jira_issue_key == "PROJ-99"
    assert db.query(JiraIssueLink).filter(JiraIssueLink.source_type == JiraSourceType.EPIC).count() == 1


def test_push_skips_an_invalid_item_and_never_calls_jira(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    _story_setup(db, project, actor)
    dev = _developer(db)
    node = make_node(db, project, node_key="implementation_planning", order_index=1, output_artifact_type="implementation_plan")
    plan_artifact = make_approved_artifact(db, project, node, actor)
    task = make_implementation_task(db, project, node, plan_artifact, linked_story=None)  # invalid: no linked story

    calls = []
    monkeypatch.setattr(jira_routes.jira_api, "create_issue", lambda *a, **kw: calls.append(1) or (_ for _ in ()).throw(AssertionError("should not be called")))

    response = push_to_jira(
        JiraPushRequest(project_id=project.id, triggered_by_user_id=dev.id, selections=[JiraPushSelectionItem(source_type=JiraSourceType.IMPLEMENTATION_TASK, source_key=str(task.id))]),
        db,
    )

    assert response.results[0].status == "skipped_invalid"
    assert response.results[0].errors
    assert calls == []


def test_push_requires_completed_review_of_nothing_but_does_require_connection_token(db, project, actor):
    _story_setup(db, project, actor)
    dev = _developer(db)
    with pytest.raises(HTTPException) as exc_info:
        push_to_jira(JiraPushRequest(project_id=project.id, triggered_by_user_id=dev.id, selections=[JiraPushSelectionItem(source_type=JiraSourceType.EPIC, source_key="X")]), db)
    assert exc_info.value.status_code == 404  # no jira project configured at all


def test_push_one_failure_does_not_abort_the_batch(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    _story_setup(db, project, actor)
    dev = _developer(db)

    def _flaky_create_issue(base_url, email, token, *, project_key, issue_type, **kw):
        if issue_type == "Epic":
            raise JiraIntegrationError("Jira API returned 500: boom", status_code=500)
        from app.services.jira_integration import JiraIssue

        return JiraIssue(key=f"{project_key}-1", url=f"{base_url}/browse/{project_key}-1")

    monkeypatch.setattr(jira_routes.jira_api, "create_issue", _flaky_create_issue)

    response = push_to_jira(
        JiraPushRequest(
            project_id=project.id, triggered_by_user_id=dev.id,
            selections=[
                JiraPushSelectionItem(source_type=JiraSourceType.EPIC, source_key="Account Recovery"),
                JiraPushSelectionItem(source_type=JiraSourceType.STORY, source_key="Password Reset Request"),
            ],
        ),
        db,
    )

    by_type = {r.source_type: r for r in response.results}
    assert by_type[JiraSourceType.EPIC].status == "failed"
    assert by_type[JiraSourceType.STORY].status == "created"  # kept going despite the Epic failure


# --- Sync status (requirement 8) ----------------------------------------------------------


def test_sync_status_updates_every_link(db, project, actor, monkeypatch):
    link = _connect_and_link(db, actor, project, monkeypatch)
    issue_link = JiraIssueLink(
        project_id=project.id, jira_project_link=link, source_type=JiraSourceType.EPIC, source_key="Account Recovery",
        source_label="Account Recovery", jira_issue_key="PROJ-1", jira_issue_type="Epic", jira_issue_url="https://x/PROJ-1",
    )
    db.add(issue_link)
    db.flush()

    response = sync_jira_status(project.id, db)

    assert response.links[0].jira_status == "In Progress"
    assert response.links[0].last_synced_at is not None


# --- Security regression --------------------------------------------------------------------


def test_no_jira_token_ever_appears_in_any_response_or_audit_log(db, project, actor, monkeypatch):
    link = _connect_and_link(db, actor, project, monkeypatch)
    _story_setup(db, project, actor)
    dev = _developer(db)

    preview = get_jira_push_preview(project.id, db)
    push_response = push_to_jira(
        JiraPushRequest(project_id=project.id, triggered_by_user_id=dev.id, selections=[JiraPushSelectionItem(source_type=JiraSourceType.EPIC, source_key="Account Recovery")]),
        db,
    )
    sync_response = sync_jira_status(project.id, db)

    dump = str([preview.model_dump(), push_response.model_dump(), sync_response.model_dump(), [row.extra_data for row in db.query(AuditLog).all()]])
    assert REAL_TOKEN not in dump
