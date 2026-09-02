"""Scrum Sprint Planning — app/api/routes/sprints.py. Covers the CRUD
surface (create/update/list sprint, add/remove story, start/complete)
and the four explicitly-required rules: only an approved (available)
story can be added; a story's delivery lane requires its sprint to be
ACTIVE; a COMPLETED sprint can't be edited without an Admin override;
every sprint mutation is audited.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.sprints import (
    add_story_to_sprint,
    complete_sprint,
    create_sprint,
    get_sprint_board,
    list_sprints,
    remove_story_from_sprint,
    start_sprint,
    update_sprint,
    update_sprint_story,
)
from app.api.routes.stories import create_story, create_story_lane
from app.models import AuditLog, SprintStatus, StoryStatus, StoryType, User, UserRole
from app.schemas.story import (
    AddStoryToSprintRequest,
    CreateStoryLaneRequest,
    RemoveStoryFromSprintRequest,
    SprintCreate,
    SprintLifecycleRequest,
    SprintUpdate,
    StoryCreate,
    UpdateSprintStoryRequest,
)
from tests.conftest import make_approved_artifact, make_node


def _po(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="PO", role=UserRole.PRODUCT_OWNER)
    db.add(user)
    db.flush()
    return user


def _admin(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Admin", role=UserRole.ADMIN)
    db.add(user)
    db.flush()
    return user


def _story(db, project, creator, *, title: str = "Sample Story"):
    return create_story(
        StoryCreate(project_id=project.id, title=title, mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=creator.id),
        db,
    )


def _sprint(db, project, po, **overrides):
    body = dict(project_id=project.id, name="Sprint 1", goal="Ship checkout", created_by_id=po.id)
    body.update(overrides)
    return create_sprint(SprintCreate(**body), db)


# --- CRUD --------------------------------------------------------------------------------


def test_create_and_list_sprint(db, project, actor):
    po = _po(db)
    sprint = _sprint(db, project, po, capacity_points=20)
    assert sprint.status == SprintStatus.PLANNED
    assert sprint.capacity_points == 20

    listed = list_sprints(project.id, db)
    assert [s.id for s in listed] == [sprint.id]

    audit_actions = [a.action for a in db.query(AuditLog).filter(AuditLog.action == "sprint.created").all()]
    assert audit_actions == ["sprint.created"]


def test_update_sprint(db, project, actor):
    po = _po(db)
    sprint = _sprint(db, project, po)
    updated = update_sprint(sprint.id, SprintUpdate(goal="New goal", updated_by_id=po.id), db)
    assert updated.goal == "New goal"

    audit_actions = [a.action for a in db.query(AuditLog).filter(AuditLog.action == "sprint.updated").all()]
    assert audit_actions == ["sprint.updated"]


# --- Rule: only an approved/available story can be added --------------------------------


def test_add_story_to_sprint(db, project, actor):
    po = _po(db)
    sprint = _sprint(db, project, po)
    story = _story(db, project, po)

    result = add_story_to_sprint(sprint.id, AddStoryToSprintRequest(story_id=story.id, planned_points=5, actor_user_id=po.id), db)
    assert result.planned_points == 5
    assert result.status.value == "PLANNED"

    audit_actions = [a.action for a in db.query(AuditLog).filter(AuditLog.action == "sprint.story_added").all()]
    assert audit_actions == ["sprint.story_added"]

    from app.models import Story
    story_row = db.get(Story, story.id)
    assert story_row.sprint_id == sprint.id
    assert story_row.status == StoryStatus.IN_SPRINT


def test_a_done_story_cannot_be_added_to_a_sprint(db, project, actor):
    po = _po(db)
    sprint = _sprint(db, project, po)
    story = _story(db, project, po)

    from app.models import Story
    story_row = db.get(Story, story.id)
    story_row.status = StoryStatus.DONE
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        add_story_to_sprint(sprint.id, AddStoryToSprintRequest(story_id=story.id, actor_user_id=po.id), db)
    assert exc_info.value.status_code == 409


def test_a_story_cannot_be_added_to_two_sprints_at_once(db, project, actor):
    po = _po(db)
    sprint_a = _sprint(db, project, po, name="Sprint A")
    sprint_b = _sprint(db, project, po, name="Sprint B")
    story = _story(db, project, po)

    add_story_to_sprint(sprint_a.id, AddStoryToSprintRequest(story_id=story.id, actor_user_id=po.id), db)
    with pytest.raises(HTTPException) as exc_info:
        add_story_to_sprint(sprint_b.id, AddStoryToSprintRequest(story_id=story.id, actor_user_id=po.id), db)
    assert exc_info.value.status_code == 409


def test_remove_story_from_sprint_is_a_soft_delete(db, project, actor):
    po = _po(db)
    sprint = _sprint(db, project, po)
    story = _story(db, project, po)
    add_story_to_sprint(sprint.id, AddStoryToSprintRequest(story_id=story.id, actor_user_id=po.id), db)

    removed = remove_story_from_sprint(sprint.id, story.id, RemoveStoryFromSprintRequest(actor_user_id=po.id), db)
    assert removed.status.value == "REMOVED"

    from app.models import Story
    story_row = db.get(Story, story.id)
    assert story_row.sprint_id is None
    assert story_row.status == StoryStatus.PENDING

    audit_actions = [a.action for a in db.query(AuditLog).filter(AuditLog.action == "sprint.story_removed").all()]
    assert audit_actions == ["sprint.story_removed"]

    # Re-adding after removal succeeds (reuses the soft-deleted row).
    re_added = add_story_to_sprint(sprint.id, AddStoryToSprintRequest(story_id=story.id, actor_user_id=po.id), db)
    assert re_added.status.value == "PLANNED"


def test_update_sprint_story_re_estimates_and_reassigns_in_place(db, project, actor):
    po = _po(db)
    dev = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(dev)
    db.flush()
    sprint = _sprint(db, project, po)
    story = _story(db, project, po)
    add_story_to_sprint(sprint.id, AddStoryToSprintRequest(story_id=story.id, planned_points=3, actor_user_id=po.id), db)

    updated = update_sprint_story(
        sprint.id, story.id, UpdateSprintStoryRequest(planned_points=8, assigned_owner_id=dev.id, actor_user_id=po.id), db
    )
    assert updated.planned_points == 8
    assert updated.assigned_owner_id == dev.id

    audit_actions = [a.action for a in db.query(AuditLog).filter(AuditLog.action == "sprint.story_updated").all()]
    assert audit_actions == ["sprint.story_updated"]


# --- start / complete ---------------------------------------------------------------------


def test_start_and_complete_sprint_lifecycle(db, project, actor):
    po = _po(db)
    sprint = _sprint(db, project, po)

    with pytest.raises(HTTPException):
        complete_sprint(sprint.id, SprintLifecycleRequest(actor_user_id=po.id), db)  # not ACTIVE yet

    started = start_sprint(sprint.id, SprintLifecycleRequest(actor_user_id=po.id), db)
    assert started.status == SprintStatus.ACTIVE

    with pytest.raises(HTTPException):
        start_sprint(sprint.id, SprintLifecycleRequest(actor_user_id=po.id), db)  # already ACTIVE

    completed = complete_sprint(sprint.id, SprintLifecycleRequest(actor_user_id=po.id), db)
    assert completed.status == SprintStatus.COMPLETED

    actions = [a.action for a in db.query(AuditLog).filter(AuditLog.entity_type == "Sprint").all()]
    assert "sprint.started" in actions
    assert "sprint.completed" in actions


# --- Rule: delivery lane requires an ACTIVE sprint --------------------------------------


def test_story_lane_blocked_until_its_sprint_is_active(db, project, actor):
    po = _po(db)
    make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, next(n for n in project.workflow_nodes if n.node_key == "story_crafting"), po, content="## Story: X\n")

    sprint = _sprint(db, project, po)
    story = _story(db, project, po)
    add_story_to_sprint(sprint.id, AddStoryToSprintRequest(story_id=story.id, actor_user_id=po.id), db)

    with pytest.raises(HTTPException) as exc_info:
        create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=po.id), db)
    assert exc_info.value.status_code == 409

    start_sprint(sprint.id, SprintLifecycleRequest(actor_user_id=po.id), db)
    result = create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=po.id), db)
    assert result.lane_created_at is not None


# --- Rule: a COMPLETED sprint can't be edited without Admin override --------------------


def test_completed_sprint_cannot_be_edited_without_admin_override(db, project, actor):
    po = _po(db)
    sprint = _sprint(db, project, po)
    start_sprint(sprint.id, SprintLifecycleRequest(actor_user_id=po.id), db)
    complete_sprint(sprint.id, SprintLifecycleRequest(actor_user_id=po.id), db)

    with pytest.raises(HTTPException) as exc_info:
        update_sprint(sprint.id, SprintUpdate(goal="Too late", updated_by_id=po.id), db)
    assert exc_info.value.status_code == 409

    admin = _admin(db)
    updated = update_sprint(sprint.id, SprintUpdate(goal="Admin override", updated_by_id=admin.id), db)
    assert updated.goal == "Admin override"


# --- Sprint board --------------------------------------------------------------------------


def test_sprint_board_totals_planned_points_and_flags_over_capacity(db, project, actor):
    po = _po(db)
    sprint = _sprint(db, project, po, capacity_points=5)
    story_a = _story(db, project, po, title="Story A")
    story_b = _story(db, project, po, title="Story B")
    add_story_to_sprint(sprint.id, AddStoryToSprintRequest(story_id=story_a.id, planned_points=3, actor_user_id=po.id), db)
    add_story_to_sprint(sprint.id, AddStoryToSprintRequest(story_id=story_b.id, planned_points=4, actor_user_id=po.id), db)

    board = get_sprint_board(sprint.id, db)
    assert len(board.items) == 2
    assert board.planned_points_total == 7
    assert board.over_capacity is True
