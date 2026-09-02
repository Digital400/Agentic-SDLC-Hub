"""Story-Level LLD — app/services/story_lld_agent.py and the
STORY_LLD/LLD_REVIEW/IMPLEMENTATION lock/unlock sequence in a story's
delivery lane. Covers:
  3. Story LLD can start only when: story is approved, a delivery lane
     exists, and HLD is approved.
  5. Tech Lead review gate for Story LLD (LLD_REVIEW).
  6. Implementation unlocks only after Story LLD (LLD_REVIEW) approval.
  8. Graph locking/unlocking.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.stories import create_story, create_story_lane, draft_story_lld, get_story_lld, update_lane_node_status
from app.models import AgentDefinition, ArtifactStatus, StoryDeliveryNodeStatus, StoryType, User, UserRole
from app.schemas.story import CreateStoryLaneRequest, DraftStoryLldRequest, StoryCreate, UpdateLaneNodeStatusRequest
from app.services import ai_generation
from app.services.story_lld_agent import STORY_LLD_AGENT_KEY
from tests.conftest import make_agent_prompt, make_approved_artifact, make_node


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_active_provider", lambda: "mock")


@pytest.fixture(autouse=True)
def _story_lld_agent(db):
    """run_story_lld_agent looks up a real AgentDefinition/AgentPrompt by
    the exact agent_key it uses in production — see
    app/services/story_lld_agent.STORY_LLD_AGENT_KEY. seed.py's own
    _ensure_story_lld_agent isn't run against the test DB, so it's set up
    directly here, same pattern as every other agent-key-dependent test
    in this suite (see tests/conftest.py's make_agent_prompt)."""
    agent = AgentDefinition(agent_key=STORY_LLD_AGENT_KEY, name="Story LLD Agent", model_name="mock")
    db.add(agent)
    db.flush()
    make_agent_prompt(db, stage="story_lld", agent=agent)


def _dev(db, role=UserRole.DEVELOPER) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="User", role=role)
    db.add(user)
    db.flush()
    return user


def _approve_story_crafting(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content="## Story: X\n")


def _lane_for_new_story(db, project, actor, *, with_hld_approved: bool):
    _approve_story_crafting(db, project, actor)
    hld_node = make_node(db, project, node_key="hld", order_index=0, output_artifact_type="hld_document")
    if with_hld_approved:
        make_approved_artifact(db, project, hld_node, actor, content="# HLD\n\n## Architecture\nSome design.\n")

    story = create_story(
        StoryCreate(project_id=project.id, title="Add reset endpoint", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id),
        db,
    )
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    from app.models import Story, StoryDeliveryLane
    lane = db.query(StoryDeliveryLane).filter(StoryDeliveryLane.story_id == story.id).first()
    nodes = {n.node_key: n for n in lane.nodes}
    return story, lane, nodes


def _complete(db, node, actor):
    return update_lane_node_status(node.id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)


# --- Requirement 3: preconditions -------------------------------------------------------


def test_story_lld_blocked_when_hld_not_approved(db, project, actor):
    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=False)
    _complete(db, nodes["STORY_READY"], actor)

    with pytest.raises(HTTPException) as exc_info:
        draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "approved" in exc_info.value.detail.lower()


def test_story_lld_blocked_while_locked_before_story_ready_completes(db, project, actor):
    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    # STORY_READY never completed — STORY_LLD is still LOCKED.
    with pytest.raises(HTTPException) as exc_info:
        draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409


def test_story_lld_drafts_successfully_once_all_preconditions_are_met(db, project, actor):
    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    _complete(db, nodes["STORY_READY"], actor)

    result = draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)
    assert result.needs_clarification is False
    assert result.story_artifact is not None
    assert result.story_artifact.artifact_type == "story_lld"
    assert result.story_artifact.content_markdown != ""
    assert result.node_status == "COMPLETED"

    fetched = get_story_lld(story.id, db)
    assert fetched.id == result.story_artifact.id


def test_draft_story_lld_rejects_a_non_story_lld_node(db, project, actor):
    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    with pytest.raises(HTTPException) as exc_info:
        draft_story_lld(nodes["IMPLEMENTATION"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 400


# --- Requirement 8: graph locking/unlocking sequence ------------------------------------


def test_lld_review_locked_until_story_lld_completes(db, project, actor):
    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    assert nodes["LLD_REVIEW"].status == StoryDeliveryNodeStatus.LOCKED

    _complete(db, nodes["STORY_READY"], actor)
    draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)

    from app.models import StoryDeliveryNode
    lld_review = db.get(StoryDeliveryNode, nodes["LLD_REVIEW"].id)
    assert lld_review.status == StoryDeliveryNodeStatus.READY

    implementation = db.get(StoryDeliveryNode, nodes["IMPLEMENTATION"].id)
    assert implementation.status == StoryDeliveryNodeStatus.LOCKED


def test_implementation_unlocks_only_after_lld_review_completes(db, project, actor):
    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    _complete(db, nodes["STORY_READY"], actor)
    draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)

    tech_lead = _dev(db, role=UserRole.TECH_LEAD)
    update_lane_node_status(nodes["LLD_REVIEW"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db)

    from app.models import StoryDeliveryNode
    implementation = db.get(StoryDeliveryNode, nodes["IMPLEMENTATION"].id)
    assert implementation.status == StoryDeliveryNodeStatus.READY


# --- Requirement 5: Tech Lead review gate -----------------------------------------------


def test_a_non_tech_lead_cannot_approve_lld_review(db, project, actor):
    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    _complete(db, nodes["STORY_READY"], actor)
    draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)

    developer = _dev(db, role=UserRole.DEVELOPER)
    with pytest.raises(HTTPException) as exc_info:
        update_lane_node_status(nodes["LLD_REVIEW"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=developer.id), db)
    assert exc_info.value.status_code == 403

    from app.models import StoryDeliveryNode
    implementation = db.get(StoryDeliveryNode, nodes["IMPLEMENTATION"].id)
    assert implementation.status == StoryDeliveryNodeStatus.LOCKED


def test_an_admin_can_also_approve_lld_review(db, project, actor):
    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    _complete(db, nodes["STORY_READY"], actor)
    draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)

    # `actor` fixture (tests/conftest.py) is ADMIN.
    update_lane_node_status(nodes["LLD_REVIEW"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)
    from app.models import StoryDeliveryNode
    lld_review = db.get(StoryDeliveryNode, nodes["LLD_REVIEW"].id)
    assert lld_review.status == StoryDeliveryNodeStatus.COMPLETED
