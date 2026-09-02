"""Route-level tests for app/api/routes/stories.py: sync-from-backlog
idempotency + story.created audit, owner-assignment audit, and the
story-lane-creation gate (requirement 8's "story lanes cannot be created
before Story Crafting approval")."""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.stories import assign_story_owner, create_story_lane, sync_stories_from_backlog, update_story
from app.models import ArtifactStatus, AuditLog, StoryStatus, StoryType, User, UserRole
from app.schemas.story import AssignStoryOwnerRequest, CreateStoryLaneRequest, StoryUpdate, SyncStoriesFromBacklogRequest
from tests.conftest import make_approved_artifact, make_node

SAMPLE_BACKLOG = (
    "## Story: Sample Story One\n\n**Epic:** Checkout\n**User Story:** As a user, I want to check out.\n"
    "**Priority:** High\n**Acceptance Criteria:**\n- [ ] Works\n\n"
    "## Story: Sample Story Two\n\n**Epic:** Checkout\n**User Story:** As a user, I want a receipt.\n"
    "**Priority:** Medium\n**Acceptance Criteria:**\n- [ ] Works\n"
)


def _ba(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="BA", role=UserRole.BA)
    db.add(user)
    db.flush()
    return user


def test_sync_from_backlog_requires_approved_story_crafting(db, project, actor):
    make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    with pytest.raises(HTTPException) as exc_info:
        sync_stories_from_backlog(
            project.id, SyncStoriesFromBacklogRequest(story_type=StoryType.VERTICAL, triggered_by_user_id=actor.id), db
        )
    assert exc_info.value.status_code == 409


def test_sync_from_backlog_is_idempotent_and_audits_creation(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content=SAMPLE_BACKLOG)

    ba = _ba(db)
    result = sync_stories_from_backlog(
        project.id, SyncStoriesFromBacklogRequest(story_type=StoryType.VERTICAL, triggered_by_user_id=ba.id), db
    )
    assert len(result.created) == 2
    assert result.already_existed == 0
    assert result.created[0].suggested_owner_role in {"DEVELOPER", "QA", "DEVOPS", "BA"}
    assert result.created[0].lane_status == "No delivery lane yet"
    assert result.created[0].jira_status == "Not Synced"

    audit_actions = [a.action for a in db.query(AuditLog).filter(AuditLog.action == "story.created").all()]
    assert audit_actions == ["story.created", "story.created"]

    # Re-syncing the same approved backlog creates nothing new.
    result_2 = sync_stories_from_backlog(
        project.id, SyncStoriesFromBacklogRequest(story_type=StoryType.VERTICAL, triggered_by_user_id=ba.id), db
    )
    assert len(result_2.created) == 0
    assert result_2.already_existed == 2


def test_assigning_an_owner_audits_story_assigned(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content=SAMPLE_BACKLOG)
    ba = _ba(db)
    result = sync_stories_from_backlog(
        project.id, SyncStoriesFromBacklogRequest(story_type=StoryType.VERTICAL, triggered_by_user_id=ba.id), db
    )
    story = result.created[0]

    owner = User(email=f"{uuid.uuid4()}@example.com", full_name="Owner", role=UserRole.DEVELOPER)
    db.add(owner)
    db.flush()

    updated = assign_story_owner(story.id, AssignStoryOwnerRequest(owner_user_id=owner.id, assigned_by_id=ba.id), db)
    assert updated.owner_user_id == owner.id

    audit_actions = [a.action for a in db.query(AuditLog).filter(AuditLog.action == "story.assigned").all()]
    assert audit_actions == ["story.assigned"]

    from app.models import StoryAssignee
    assignee_rows = db.query(StoryAssignee).filter(StoryAssignee.story_id == story.id).all()
    assert len(assignee_rows) == 1
    assert assignee_rows[0].user_id == owner.id
    assert assignee_rows[0].unassigned_at is None


def test_create_story_lane_blocked_before_story_crafting_approval(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content=SAMPLE_BACKLOG)
    ba = _ba(db)
    result = sync_stories_from_backlog(
        project.id, SyncStoriesFromBacklogRequest(story_type=StoryType.VERTICAL, triggered_by_user_id=ba.id), db
    )
    story = result.created[0]

    # Revoke approval (simulating a project whose backlog was never
    # approved, or got sent back to NEEDS_CHANGES after sync).
    node.artifacts[0].status = ArtifactStatus.DRAFT
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=ba.id), db)
    assert exc_info.value.status_code == 409


def test_create_story_lane_succeeds_once_approved_and_audits_it(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content=SAMPLE_BACKLOG)
    ba = _ba(db)
    result = sync_stories_from_backlog(
        project.id, SyncStoriesFromBacklogRequest(story_type=StoryType.VERTICAL, triggered_by_user_id=ba.id), db
    )
    story = result.created[0]

    updated = create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=ba.id), db)
    assert updated.status == StoryStatus.LANE_ACTIVE
    assert updated.lane_created_at is not None

    audit_actions = [a.action for a in db.query(AuditLog).filter(AuditLog.action == "story_lane.created").all()]
    assert audit_actions == ["story_lane.created"]

    # Creating a second lane for the same story is rejected.
    with pytest.raises(HTTPException) as exc_info:
        create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=ba.id), db)
    assert exc_info.value.status_code == 409
