"""Story Implementation Plan Agent — app/services/story_implementation_plan_agent.py
and the IMPLEMENTATION_PLAN node's Accept/Request Changes gate in
app/api/routes/stories.py's update_lane_node_status. Covers:
  - Belongs to one story lane only / no code generation (structural —
    the agent never touches app/services/implementation_agent.py's
    codegen path at all).
  - Preconditions: blocked until Story LLD (LLD_REVIEW) is approved.
  - Rule 4 — Implementation cannot start before the plan is approved or
    accepted by the assigned user: IMPLEMENTATION stays LOCKED until
    IMPLEMENTATION_PLAN is accepted (completed), and only its assigned
    user or a Tech Lead/Admin may accept it.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.stories import (
    create_story,
    create_story_lane,
    draft_story_implementation_plan,
    draft_story_lld,
    get_story_implementation_plan,
    update_lane_node_status,
)
from app.models import (
    AgentDefinition,
    ImplementationTask,
    StoryDeliveryLane,
    StoryDeliveryNode,
    StoryDeliveryNodeStatus,
    StoryType,
    User,
    UserRole,
)
from app.schemas.story import (
    CreateStoryLaneRequest,
    DraftStoryImplementationPlanRequest,
    DraftStoryLldRequest,
    StoryCreate,
    UpdateLaneNodeStatusRequest,
)
from app.services import ai_generation
from app.services.story_implementation_plan_agent import STORY_IMPLEMENTATION_PLAN_AGENT_KEY, StoryImplementationPlanError
from app.services.story_lld_agent import STORY_LLD_AGENT_KEY
from tests.conftest import make_agent_prompt, make_approved_artifact, make_node

SAMPLE_HLD = "# HLD\n\n## Architecture\nSome design.\n"


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_active_provider", lambda: "mock")


@pytest.fixture(autouse=True)
def _agents(db):
    lld_agent = AgentDefinition(agent_key=STORY_LLD_AGENT_KEY, name="Story LLD Agent", model_name="mock")
    db.add(lld_agent)
    db.flush()
    make_agent_prompt(db, stage="story_lld", agent=lld_agent)

    plan_agent = AgentDefinition(agent_key=STORY_IMPLEMENTATION_PLAN_AGENT_KEY, name="Story Implementation Plan Agent", model_name="mock")
    db.add(plan_agent)
    db.flush()
    make_agent_prompt(db, stage="story_implementation_plan", agent=plan_agent)


def _dev(db, role=UserRole.DEVELOPER) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="User", role=role)
    db.add(user)
    db.flush()
    return user


def _lane_with_lld_approved(db, project, actor):
    story_crafting_node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, story_crafting_node, actor, content="## Story: X\n")
    hld_node = make_node(db, project, node_key="hld", order_index=1, output_artifact_type="hld_document")
    make_approved_artifact(db, project, hld_node, actor, content=SAMPLE_HLD)

    story = create_story(
        StoryCreate(project_id=project.id, title="Add reset endpoint", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id),
        db,
    )
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    lane = db.query(StoryDeliveryLane).filter(StoryDeliveryLane.story_id == story.id).first()
    nodes = {n.node_key: n for n in lane.nodes}

    update_lane_node_status(nodes["STORY_READY"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)
    draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)
    tech_lead = _dev(db, role=UserRole.TECH_LEAD)
    update_lane_node_status(nodes["LLD_REVIEW"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db)

    return story, lane, nodes, tech_lead


# --- Preconditions --------------------------------------------------------------------


def test_blocked_before_story_lld_is_approved(db, project, actor):
    story_crafting_node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, story_crafting_node, actor, content="## Story: X\n")
    hld_node = make_node(db, project, node_key="hld", order_index=1, output_artifact_type="hld_document")
    make_approved_artifact(db, project, hld_node, actor, content=SAMPLE_HLD)
    story = create_story(
        StoryCreate(project_id=project.id, title="Add reset endpoint", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id), db,
    )
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    lane = db.query(StoryDeliveryLane).filter(StoryDeliveryLane.story_id == story.id).first()
    nodes = {n.node_key: n for n in lane.nodes}
    # STORY_LLD/LLD_REVIEW never touched — IMPLEMENTATION_PLAN is still LOCKED.

    with pytest.raises(HTTPException) as exc_info:
        draft_story_implementation_plan(nodes["IMPLEMENTATION_PLAN"].id, DraftStoryImplementationPlanRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "approved" in exc_info.value.detail.lower()


def test_rejects_a_non_implementation_plan_node(db, project, actor):
    story, lane, nodes, tech_lead = _lane_with_lld_approved(db, project, actor)
    with pytest.raises(HTTPException) as exc_info:
        draft_story_implementation_plan(nodes["STORY_LLD"].id, DraftStoryImplementationPlanRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 400


# --- Drafting -------------------------------------------------------------------------


def test_drafts_successfully_once_story_lld_is_approved_and_never_auto_completes(db, project, actor):
    story, lane, nodes, tech_lead = _lane_with_lld_approved(db, project, actor)

    result = draft_story_implementation_plan(nodes["IMPLEMENTATION_PLAN"].id, DraftStoryImplementationPlanRequest(triggered_by_user_id=actor.id), db)

    assert result.story_artifact is not None
    assert result.node_status == "IN_PROGRESS"  # drafting alone never accepts the plan
    node_row = db.get(StoryDeliveryNode, nodes["IMPLEMENTATION_PLAN"].id)
    assert node_row.status == StoryDeliveryNodeStatus.IN_PROGRESS

    fetched = get_story_implementation_plan(story.id, db)
    assert fetched.id == result.story_artifact.id


def test_implementation_stays_locked_after_drafting_alone(db, project, actor):
    story, lane, nodes, tech_lead = _lane_with_lld_approved(db, project, actor)
    draft_story_implementation_plan(nodes["IMPLEMENTATION_PLAN"].id, DraftStoryImplementationPlanRequest(triggered_by_user_id=actor.id), db)

    implementation_node = db.get(StoryDeliveryNode, nodes["IMPLEMENTATION"].id)
    assert implementation_node.status == StoryDeliveryNodeStatus.LOCKED
    assert db.query(ImplementationTask).filter(ImplementationTask.story_id == story.id).count() == 0


# --- Accept / Request Changes (rule 4) -------------------------------------------------


def test_a_non_assignee_non_tech_lead_cannot_accept_the_plan(db, project, actor):
    story, lane, nodes, tech_lead = _lane_with_lld_approved(db, project, actor)
    draft_story_implementation_plan(nodes["IMPLEMENTATION_PLAN"].id, DraftStoryImplementationPlanRequest(triggered_by_user_id=actor.id), db)
    developer = _dev(db)

    with pytest.raises(HTTPException) as exc_info:
        update_lane_node_status(nodes["IMPLEMENTATION_PLAN"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=developer.id), db)
    assert exc_info.value.status_code == 403

    implementation_node = db.get(StoryDeliveryNode, nodes["IMPLEMENTATION"].id)
    assert implementation_node.status == StoryDeliveryNodeStatus.LOCKED


def test_a_tech_lead_can_accept_the_plan_and_unlock_implementation(db, project, actor):
    story, lane, nodes, tech_lead = _lane_with_lld_approved(db, project, actor)
    draft_story_implementation_plan(nodes["IMPLEMENTATION_PLAN"].id, DraftStoryImplementationPlanRequest(triggered_by_user_id=actor.id), db)

    update_lane_node_status(nodes["IMPLEMENTATION_PLAN"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db)

    implementation_node = db.get(StoryDeliveryNode, nodes["IMPLEMENTATION"].id)
    assert implementation_node.status == StoryDeliveryNodeStatus.READY
    # Auto-created the moment IMPLEMENTATION unlocks (see _ensure_story_implementation_task).
    assert db.query(ImplementationTask).filter(ImplementationTask.story_id == story.id).count() == 1


def test_the_assigned_user_can_accept_even_without_the_tech_lead_role(db, project, actor):
    story, lane, nodes, tech_lead = _lane_with_lld_approved(db, project, actor)
    draft_story_implementation_plan(nodes["IMPLEMENTATION_PLAN"].id, DraftStoryImplementationPlanRequest(triggered_by_user_id=actor.id), db)
    assignee = _dev(db)  # DEVELOPER role — would normally be rejected
    update_lane_node_status(
        nodes["IMPLEMENTATION_PLAN"].id, UpdateLaneNodeStatusRequest(status="IN_PROGRESS", actor_user_id=tech_lead.id, assigned_user_id=assignee.id), db,
    )

    update_lane_node_status(nodes["IMPLEMENTATION_PLAN"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=assignee.id), db)

    implementation_node = db.get(StoryDeliveryNode, nodes["IMPLEMENTATION"].id)
    assert implementation_node.status == StoryDeliveryNodeStatus.READY


def test_cannot_accept_before_a_plan_has_been_drafted(db, project, actor):
    story, lane, nodes, tech_lead = _lane_with_lld_approved(db, project, actor)
    # No draft_story_implementation_plan call at all.
    with pytest.raises(HTTPException) as exc_info:
        update_lane_node_status(nodes["IMPLEMENTATION_PLAN"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db)
    assert exc_info.value.status_code == 409


def test_request_changes_keeps_implementation_locked(db, project, actor):
    story, lane, nodes, tech_lead = _lane_with_lld_approved(db, project, actor)
    draft_story_implementation_plan(nodes["IMPLEMENTATION_PLAN"].id, DraftStoryImplementationPlanRequest(triggered_by_user_id=actor.id), db)

    update_lane_node_status(
        nodes["IMPLEMENTATION_PLAN"].id,
        UpdateLaneNodeStatusRequest(status="BLOCKED", actor_user_id=tech_lead.id, blocked_reason="Missing rollback detail."),
        db,
    )

    node_row = db.get(StoryDeliveryNode, nodes["IMPLEMENTATION_PLAN"].id)
    assert node_row.status == StoryDeliveryNodeStatus.BLOCKED
    assert node_row.blocked_reason == "Missing rollback detail."
    implementation_node = db.get(StoryDeliveryNode, nodes["IMPLEMENTATION"].id)
    assert implementation_node.status == StoryDeliveryNodeStatus.LOCKED
