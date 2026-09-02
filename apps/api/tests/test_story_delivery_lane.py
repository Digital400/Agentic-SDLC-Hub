"""Story delivery lane backend — app/services/story_delivery.py and the
route handlers in app/api/routes/stories.py (create_story,
assign_story_owner, create_story_lane, get_story_delivery_lane,
list_lane_nodes, update_lane_node_status).

Covers: lane creation materializes the exact 10-node default sequence in
order, with only the first node READY and the rest LOCKED; completing a
node unlocks its immediate successor and no other node; a LOCKED node
can't be moved directly; and the lane itself completes once its last
node does.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.stories import (
    assign_story_owner,
    create_story,
    create_story_lane,
    get_story_delivery_lane,
    list_lane_nodes,
    update_lane_node_status,
)
from app.models import StoryDeliveryLaneStatus, StoryDeliveryNodeStatus, StoryStatus, StoryType, User, UserRole
from app.schemas.story import (
    AssignStoryOwnerRequest,
    CreateStoryLaneRequest,
    StoryCreate,
    UpdateLaneNodeStatusRequest,
)
from app.services.story_delivery import DEFAULT_STORY_DELIVERY_NODES

from tests.conftest import make_approved_artifact, make_node


def _ba(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="BA", role=UserRole.BA)
    db.add(user)
    db.flush()
    return user


def _approved_project(db, project, actor):
    """Story Crafting approved — the gate every lane creation must pass."""
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content="## Story: Placeholder\n")


def _create_direct_story(db, project, creator, *, title: str = "Add password reset endpoint"):
    return create_story(
        StoryCreate(project_id=project.id, title=title, mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=creator.id),
        db,
    )


# --- create_story / assign_story_owner -------------------------------------------------


def test_create_story_direct_without_a_backlog(db, project, actor):
    story = _create_direct_story(db, project, actor)
    assert story.title == "Add password reset endpoint"
    assert story.story_type == StoryType.VERTICAL
    assert story.status == StoryStatus.PENDING
    assert story.source_artifact_version_id is None


def test_create_story_rejects_a_duplicate_title(db, project, actor):
    _create_direct_story(db, project, actor)
    with pytest.raises(HTTPException) as exc_info:
        _create_direct_story(db, project, actor)
    assert exc_info.value.status_code == 409


def test_assign_story_owner_updates_denormalized_owner_and_history(db, project, actor):
    story = _create_direct_story(db, project, actor)
    owner_a = User(email=f"{uuid.uuid4()}@example.com", full_name="Owner A", role=UserRole.DEVELOPER)
    owner_b = User(email=f"{uuid.uuid4()}@example.com", full_name="Owner B", role=UserRole.DEVELOPER)
    db.add_all([owner_a, owner_b])
    db.flush()

    updated = assign_story_owner(story.id, AssignStoryOwnerRequest(owner_user_id=owner_a.id, assigned_by_id=actor.id), db)
    assert updated.owner_user_id == owner_a.id

    # Re-assigning closes the first StoryAssignee row and opens a new one.
    updated_2 = assign_story_owner(story.id, AssignStoryOwnerRequest(owner_user_id=owner_b.id, assigned_by_id=actor.id), db)
    assert updated_2.owner_user_id == owner_b.id

    from app.models import Story, StoryAssignee
    story_row = db.get(Story, story.id)
    history = db.query(StoryAssignee).filter(StoryAssignee.story_id == story.id).order_by(StoryAssignee.assigned_at).all()
    assert len(history) == 2
    assert history[0].user_id == owner_a.id and history[0].unassigned_at is not None
    assert history[1].user_id == owner_b.id and history[1].unassigned_at is None
    assert story_row.owner_user_id == owner_b.id


# --- Lane creation: exact node sequence -------------------------------------------------


def test_create_story_lane_materializes_the_exact_default_node_sequence(db, project, actor):
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor)

    result = create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    assert result.status == StoryStatus.LANE_ACTIVE
    assert result.lane_created_at is not None

    lane = get_story_delivery_lane(story.id, db)
    assert lane.status == StoryDeliveryLaneStatus.ACTIVE

    nodes = list_lane_nodes(lane.id, db)
    assert [n.node_key for n in nodes] == [key for key, _, _ in DEFAULT_STORY_DELIVERY_NODES]
    assert [n.order_index for n in nodes] == list(range(10))
    # Only the first node starts unlocked.
    assert nodes[0].status == StoryDeliveryNodeStatus.READY
    assert all(n.status == StoryDeliveryNodeStatus.LOCKED for n in nodes[1:])
    assert lane.current_node_id == nodes[0].id
    # The three review gates require approval; nothing else does.
    requires_approval = {n.node_key for n in nodes if n.requires_approval}
    assert requires_approval == {"LLD_REVIEW", "HUMAN_CODE_REVIEW", "QA_APPROVAL"}


def test_create_story_lane_is_blocked_before_story_crafting_approval(db, project, actor):
    make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")  # not approved
    story = _create_direct_story(db, project, actor)
    with pytest.raises(HTTPException) as exc_info:
        create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409


def test_create_story_lane_twice_for_the_same_story_is_rejected(db, project, actor):
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor)
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    with pytest.raises(HTTPException) as exc_info:
        create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409


# --- Node sequence advancement -----------------------------------------------------------


def test_completing_a_node_unlocks_only_its_immediate_successor(db, project, actor):
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor)
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    lane = get_story_delivery_lane(story.id, db)
    nodes = list_lane_nodes(lane.id, db)
    story_ready, story_lld, lld_review = nodes[0], nodes[1], nodes[2]

    updated = update_lane_node_status(
        story_ready.id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db
    )
    assert updated.status == StoryDeliveryNodeStatus.COMPLETED
    assert updated.completed_at is not None

    nodes_after = list_lane_nodes(lane.id, db)
    by_key = {n.node_key: n for n in nodes_after}
    assert by_key["STORY_LLD"].status == StoryDeliveryNodeStatus.READY
    # Everything past the immediate successor is still LOCKED.
    assert by_key["LLD_REVIEW"].status == StoryDeliveryNodeStatus.LOCKED
    assert by_key["IMPLEMENTATION"].status == StoryDeliveryNodeStatus.LOCKED

    lane_after = get_story_delivery_lane(story.id, db)
    assert lane_after.current_node_id == by_key["STORY_LLD"].id


def test_a_locked_node_cannot_be_moved_directly(db, project, actor):
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor)
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    lane = get_story_delivery_lane(story.id, db)
    nodes = list_lane_nodes(lane.id, db)
    still_locked_node = nodes[2]  # LLD_REVIEW — never touched yet

    with pytest.raises(HTTPException) as exc_info:
        update_lane_node_status(
            still_locked_node.id, UpdateLaneNodeStatusRequest(status="IN_PROGRESS", actor_user_id=actor.id), db
        )
    assert exc_info.value.status_code == 409


def test_completing_the_final_node_completes_the_lane_and_marks_the_story_done(db, project, actor):
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor)
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    lane = get_story_delivery_lane(story.id, db)

    for _ in range(len(DEFAULT_STORY_DELIVERY_NODES)):
        nodes = list_lane_nodes(lane.id, db)
        current = next(n for n in nodes if n.status == StoryDeliveryNodeStatus.READY)
        update_lane_node_status(current.id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)

    lane_after = get_story_delivery_lane(story.id, db)
    assert lane_after.status == StoryDeliveryLaneStatus.COMPLETED

    nodes_after = list_lane_nodes(lane.id, db)
    assert all(n.status == StoryDeliveryNodeStatus.COMPLETED for n in nodes_after)

    from app.models import Story
    story_row = db.get(Story, story.id)
    assert story_row.status == StoryStatus.DONE


def test_blocking_a_node_records_the_reason(db, project, actor):
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor)
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    lane = get_story_delivery_lane(story.id, db)
    story_ready = list_lane_nodes(lane.id, db)[0]

    blocked = update_lane_node_status(
        story_ready.id,
        UpdateLaneNodeStatusRequest(status="BLOCKED", actor_user_id=actor.id, blocked_reason="Waiting on design sign-off"),
        db,
    )
    assert blocked.status == StoryDeliveryNodeStatus.BLOCKED
    assert blocked.blocked_reason == "Waiting on design sign-off"


def test_story_activity_log_records_lane_and_node_events(db, project, actor):
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor)
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    lane = get_story_delivery_lane(story.id, db)
    story_ready = list_lane_nodes(lane.id, db)[0]
    update_lane_node_status(story_ready.id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)

    from app.models import StoryActivityLog
    actions = [a.action for a in db.query(StoryActivityLog).filter(StoryActivityLog.story_id == story.id).order_by(StoryActivityLog.created_at).all()]
    assert "story.created" in actions
    assert "story_lane.created" in actions
    assert "story_lane_node.status_changed" in actions
    assert "story_lane_node.unlocked" in actions
