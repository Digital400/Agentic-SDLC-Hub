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

from tests.conftest import fabricate_done_gate_prereqs, make_approved_artifact, make_node


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
    assert [n.order_index for n in nodes] == list(range(len(DEFAULT_STORY_DELIVERY_NODES)))
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


def test_implementation_plan_and_test_scenarios_are_in_the_default_sequence(db, project, actor):
    """"Update the existing workflow after HLD and Story Crafting" —
    IMPLEMENTATION_PLAN sits between LLD_REVIEW and IMPLEMENTATION;
    TEST_SCENARIOS sits between IMPLEMENTATION and PULL_REQUEST. Both are
    plain, non-review-gated nodes, driven the same generic way as
    IMPLEMENTATION/PULL_REQUEST/TESTING already are."""
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor)
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    lane = get_story_delivery_lane(story.id, db)
    nodes = list_lane_nodes(lane.id, db)
    keys_in_order = [n.node_key for n in nodes]

    assert keys_in_order.index("LLD_REVIEW") < keys_in_order.index("IMPLEMENTATION_PLAN") < keys_in_order.index("IMPLEMENTATION")
    assert keys_in_order.index("IMPLEMENTATION") < keys_in_order.index("TEST_SCENARIOS") < keys_in_order.index("PULL_REQUEST")

    by_key = {n.node_key: n for n in nodes}
    assert by_key["IMPLEMENTATION_PLAN"].requires_approval is False
    assert by_key["TEST_SCENARIOS"].requires_approval is False
    assert by_key["IMPLEMENTATION_PLAN"].assigned_role == "TECH_LEAD"
    assert by_key["TEST_SCENARIOS"].assigned_role == "QA"


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
        if current.node_key == "QA_APPROVAL":
            # Story-level testing workflow's evidence gate — fabricate a
            # minimal story_test_report with a real Test Evidence section
            # (see app/api/routes/stories.py's _require_test_evidence).
            from app.models import StoryArtifact
            db.add(
                StoryArtifact(
                    story_id=story.id, lane_id=lane.id, node_id=current.id, artifact_type="story_test_report",
                    title="Test Report", content_markdown="## Test Evidence\nPass: 1 · Fail: 0\n", version_number=1,
                    created_by_id=actor.id,
                )
            )
            db.flush()
        elif current.node_key == "IMPLEMENTATION_PLAN":
            # Story Implementation Plan Agent, rule 4 — accepting the plan
            # requires a real one to exist first. Fabricated directly.
            from app.models import StoryArtifact
            db.add(
                StoryArtifact(
                    story_id=story.id, lane_id=lane.id, node_id=current.id, artifact_type="story_implementation_plan",
                    title="Implementation Plan", content_markdown="## Implementation Summary\nPlan.\n", version_number=1,
                    created_by_id=actor.id,
                )
            )
            db.flush()
        # Final Done gate (app/services/story_done_gate.py) additionally
        # requires Jira sync / Test Scenarios / a PR / a completed PR
        # review / a QA-approved StoryTestExecution — fabricated here too.
        fabricate_done_gate_prereqs(db, project=project, story=story, lane=lane, node=current, actor=actor)
        update_lane_node_status(current.id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)

    lane_after = get_story_delivery_lane(story.id, db)
    assert lane_after.status == StoryDeliveryLaneStatus.COMPLETED

    nodes_after = list_lane_nodes(lane.id, db)
    assert all(n.status == StoryDeliveryNodeStatus.COMPLETED for n in nodes_after)

    from app.models import Story
    story_row = db.get(Story, story.id)
    assert story_row.status == StoryStatus.DONE


def _drive_to_release_ready(db, project, actor):
    """Runs a fresh story's lane up to (but not including) completing its
    final RELEASE_READY node — shared setup for the Done-approval-gate
    tests below."""
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor)
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    lane = get_story_delivery_lane(story.id, db)

    for _ in range(len(DEFAULT_STORY_DELIVERY_NODES) - 1):
        nodes = list_lane_nodes(lane.id, db)
        current = next(n for n in nodes if n.status == StoryDeliveryNodeStatus.READY)
        if current.node_key == "QA_APPROVAL":
            from app.models import StoryArtifact

            db.add(
                StoryArtifact(
                    story_id=story.id, lane_id=lane.id, node_id=current.id, artifact_type="story_test_report",
                    title="Test Report", content_markdown="## Test Evidence\nPass: 1 · Fail: 0\n", version_number=1,
                    created_by_id=actor.id,
                )
            )
            db.flush()
        elif current.node_key == "IMPLEMENTATION_PLAN":
            from app.models import StoryArtifact

            db.add(
                StoryArtifact(
                    story_id=story.id, lane_id=lane.id, node_id=current.id, artifact_type="story_implementation_plan",
                    title="Implementation Plan", content_markdown="## Implementation Summary\nPlan.\n", version_number=1,
                    created_by_id=actor.id,
                )
            )
            db.flush()
        fabricate_done_gate_prereqs(db, project=project, story=story, lane=lane, node=current, actor=actor)
        update_lane_node_status(current.id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)

    nodes = list_lane_nodes(lane.id, db)
    release_ready_node = next(n for n in nodes if n.node_key == "RELEASE_READY")
    assert release_ready_node.status == StoryDeliveryNodeStatus.READY
    return story, lane, release_ready_node


def test_a_non_product_owner_cannot_mark_a_story_done(db, project, actor):
    """Rule 8 — "Human approval is required before final Done." Before
    this gate existed, any role could complete RELEASE_READY (and so
    mark the story DONE) with a single PATCH."""
    story, lane, release_ready_node = _drive_to_release_ready(db, project, actor)
    developer = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(developer)
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        update_lane_node_status(release_ready_node.id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=developer.id), db)
    assert exc_info.value.status_code == 403

    from app.models import Story

    assert db.get(Story, story.id).status != StoryStatus.DONE


def test_a_product_owner_can_mark_a_story_done(db, project, actor):
    story, lane, release_ready_node = _drive_to_release_ready(db, project, actor)
    product_owner = User(email=f"{uuid.uuid4()}@example.com", full_name="PO", role=UserRole.PRODUCT_OWNER)
    db.add(product_owner)
    db.flush()

    update_lane_node_status(release_ready_node.id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=product_owner.id), db)

    from app.models import Story

    assert db.get(Story, story.id).status == StoryStatus.DONE


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
