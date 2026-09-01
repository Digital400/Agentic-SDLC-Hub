"""Tests for app/services/jira_push_preview.py — the broadened preview
covering Epics (grouped from Story.epic), Stories, Implementation Tasks,
and Testing bugs, each with validation errors and already-linked
detection (requirement 5's "preview before push" + duplicate visibility).
"""

import uuid

from app.models import (
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    JiraIssueLink,
    JiraProjectLink,
    JiraSourceType,
    TestRun,
    TestRunStatus,
    User,
    UserRole,
)
from app.services.jira_push_preview import build_jira_push_preview
from app.services.story_export import Story
from tests.conftest import make_approved_artifact, make_implementation_task, make_node


def _developer(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(user)
    db.flush()
    return user


def _jira_project_link(db, project) -> JiraProjectLink:
    integration = Integration(integration_name="Jira", provider=IntegrationProvider.JIRA, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(
        integration=integration, access_token_encrypted="not-a-real-fernet-token", token_last_four="7890",
        status=IntegrationStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    link = JiraProjectLink(project=project, connection=connection, jira_project_key="PROJ", jira_project_name="My Project")
    db.add(link)
    db.flush()
    return link


SAMPLE_STORIES = [
    Story(
        title="Password Reset Request", epic="Account Recovery", feature="Password Reset",
        user_story="As a user I want to request a password reset.", priority="High", dependencies="",
        acceptance_criteria=["Returns 202 for a valid email"],
    ),
    Story(
        title="Password Reset Confirmation", epic="Account Recovery", feature="Password Reset",
        user_story="As a user I want to set a new password.", priority="High", dependencies="Password Reset Request",
        acceptance_criteria=["Returns 200 on success"],
    ),
    Story(title="Broken Story", epic="", feature="", user_story="", priority="", dependencies="", acceptance_criteria=[]),
]


def test_epics_are_grouped_by_distinct_epic_value(db, project, actor):
    link = _jira_project_link(db, project)
    preview = build_jira_push_preview(db, project=project, jira_project_link=link, stories=SAMPLE_STORIES)

    assert [e.label for e in preview.epics] == ["Account Recovery"]  # empty epic excluded
    assert preview.epics[0].is_valid


def test_story_items_carry_over_validation_errors_from_jira_export(db, project, actor):
    link = _jira_project_link(db, project)
    preview = build_jira_push_preview(db, project=project, jira_project_link=link, stories=SAMPLE_STORIES)

    valid_story = next(s for s in preview.stories if s.source_key == "Password Reset Request")
    broken_story = next(s for s in preview.stories if s.source_key == "Broken Story")

    assert valid_story.is_valid
    assert valid_story.parent_source_key == "Account Recovery"
    assert not broken_story.is_valid
    assert broken_story.validation_errors  # Epic/Feature/User Story/Acceptance Criteria all missing


def test_implementation_task_requires_a_linked_story(db, project, actor):
    link = _jira_project_link(db, project)
    node = make_node(db, project, node_key="implementation_planning", order_index=0, output_artifact_type="implementation_plan")
    artifact = make_approved_artifact(db, project, node, actor)
    task_with_story = make_implementation_task(db, project, node, artifact, title="Add endpoint", linked_story="Password Reset Request")
    task_without_story = make_implementation_task(db, project, node, artifact, title="Fix bug", linked_story=None, order_index=1)

    preview = build_jira_push_preview(db, project=project, jira_project_link=link, stories=SAMPLE_STORIES)

    with_story = next(i for i in preview.implementation_tasks if i.source_key == str(task_with_story.id))
    without_story = next(i for i in preview.implementation_tasks if i.source_key == str(task_without_story.id))

    assert with_story.is_valid
    assert with_story.parent_source_key == "Password Reset Request"
    assert not without_story.is_valid
    assert any("linked story" in e.lower() for e in without_story.validation_errors)


def test_testing_bugs_are_derived_from_completed_test_runs(db, project, actor):
    link = _jira_project_link(db, project)
    node = make_node(db, project, node_key="implementation_planning", order_index=0, output_artifact_type="implementation_plan")
    artifact = make_approved_artifact(db, project, node, actor)
    task = make_implementation_task(db, project, node, artifact, linked_story="Password Reset Request")
    run = TestRun(
        project_id=project.id, workflow_node_id=node.id, implementation_task_id=task.id, implementation_run_id=uuid.uuid4(),
        agent_type="UNIT", status=TestRunStatus.COMPLETED, bugs_found=["404 case returns 500 instead."],
    )
    db.add(run)
    db.flush()

    preview = build_jira_push_preview(db, project=project, jira_project_link=link, stories=SAMPLE_STORIES)

    assert len(preview.testing_bugs) == 1
    bug_item = preview.testing_bugs[0]
    assert bug_item.source_key == f"{run.id}:0"
    assert bug_item.is_valid
    assert bug_item.parent_source_key == "Password Reset Request"


def test_already_linked_items_are_detected(db, project, actor):
    link = _jira_project_link(db, project)
    db.add(
        JiraIssueLink(
            project_id=project.id, jira_project_link=link, source_type=JiraSourceType.EPIC, source_key="Account Recovery",
            source_label="Account Recovery", jira_issue_key="PROJ-1", jira_issue_type="Epic", jira_issue_url="https://x/PROJ-1",
        )
    )
    db.flush()

    preview = build_jira_push_preview(db, project=project, jira_project_link=link, stories=SAMPLE_STORIES)

    assert preview.epics[0].already_linked is not None
    assert preview.epics[0].already_linked.jira_issue_key == "PROJ-1"
