"""Story Test Scenario Agent — app/services/story_test_scenarios_agent.py
and the TEST_SCENARIOS node's review/approve gate in
app/api/routes/stories.py's update_lane_node_status. Covers:
  - Belongs to one story only (structural).
  - Preconditions: blocked while the node is still LOCKED (i.e. before
    Implementation completes, which itself requires Story LLD +
    Implementation Plan already approved/accepted).
  - Rule 3 — QA or Tech Lead can review/approve; anyone else is rejected.
  - Rule 4 — Testing later uses the approved scenarios (see
    tests/test_story_testing.py's own coverage of the wiring; this file
    covers generation/review only).
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.stories import (
    create_story,
    create_story_lane,
    draft_story_implementation_plan,
    draft_story_lld,
    draft_story_test_scenarios,
    get_story_test_scenarios,
    update_lane_node_status,
)
from app.models import (
    AgentDefinition,
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
    DraftStoryTestScenariosRequest,
    StoryCreate,
    UpdateLaneNodeStatusRequest,
)
from app.services import ai_generation
from app.services.story_implementation_plan_agent import STORY_IMPLEMENTATION_PLAN_AGENT_KEY
from app.services.story_lld_agent import STORY_LLD_AGENT_KEY
from app.services.story_test_scenarios_agent import STORY_TEST_SCENARIOS_AGENT_KEY
from tests.conftest import make_agent_prompt, make_approved_artifact, make_node

SAMPLE_HLD = "# HLD\n\n## Architecture\nSome design.\n"


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_active_provider", lambda: "mock")


@pytest.fixture(autouse=True)
def _agents(db):
    for key, stage in (
        (STORY_LLD_AGENT_KEY, "story_lld"),
        (STORY_IMPLEMENTATION_PLAN_AGENT_KEY, "story_implementation_plan"),
        (STORY_TEST_SCENARIOS_AGENT_KEY, "story_test_scenarios"),
    ):
        agent = AgentDefinition(agent_key=key, name=key, model_name="mock")
        db.add(agent)
        db.flush()
        make_agent_prompt(db, stage=stage, agent=agent)


def _dev(db, role=UserRole.DEVELOPER) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="User", role=role)
    db.add(user)
    db.flush()
    return user


def _lane_ready_for_test_scenarios(db, project, actor):
    """Drives a fresh story's lane to TEST_SCENARIOS being READY — Story
    LLD approved, Implementation Plan accepted, IMPLEMENTATION completed
    generically (no real code needed for this module's own tests)."""
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
    draft_story_implementation_plan(nodes["IMPLEMENTATION_PLAN"].id, DraftStoryImplementationPlanRequest(triggered_by_user_id=actor.id), db)
    update_lane_node_status(nodes["IMPLEMENTATION_PLAN"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db)
    update_lane_node_status(nodes["IMPLEMENTATION"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)

    return story, lane, nodes, tech_lead


# --- Preconditions --------------------------------------------------------------------


def test_blocked_while_locked_before_implementation_completes(db, project, actor):
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
    # Nothing progressed — TEST_SCENARIOS is still LOCKED.

    with pytest.raises(HTTPException) as exc_info:
        draft_story_test_scenarios(nodes["TEST_SCENARIOS"].id, DraftStoryTestScenariosRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "locked" in exc_info.value.detail.lower()


def test_rejects_a_non_test_scenarios_node(db, project, actor):
    story, lane, nodes, tech_lead = _lane_ready_for_test_scenarios(db, project, actor)
    with pytest.raises(HTTPException) as exc_info:
        draft_story_test_scenarios(nodes["STORY_LLD"].id, DraftStoryTestScenariosRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 400


# --- Drafting -------------------------------------------------------------------------


def test_drafts_successfully_once_implementation_completes_and_never_auto_approves(db, project, actor):
    story, lane, nodes, tech_lead = _lane_ready_for_test_scenarios(db, project, actor)

    result = draft_story_test_scenarios(nodes["TEST_SCENARIOS"].id, DraftStoryTestScenariosRequest(triggered_by_user_id=actor.id), db)

    assert result.story_artifact is not None
    assert result.node_status == "IN_PROGRESS"
    node_row = db.get(StoryDeliveryNode, nodes["TEST_SCENARIOS"].id)
    assert node_row.status == StoryDeliveryNodeStatus.IN_PROGRESS

    fetched = get_story_test_scenarios(story.id, db)
    assert fetched.id == result.story_artifact.id


def test_clarification_response_is_persisted_as_a_real_story_artifact(db, project, actor, monkeypatch):
    """Regression test for a real bug: needs_clarification used to return
    story_artifact=None, discarding the model's actual clarification
    questions (already formatted into result.content_markdown by
    ai_generation.generate) — a human had nothing to read, and the UI's
    "see the generated notes" message pointed at notes that were never
    saved. The clarification content must now be persisted like any other
    draft."""
    from app.services.ai_generation import AgentGenerationResult

    story, lane, nodes, tech_lead = _lane_ready_for_test_scenarios(db, project, actor)

    canned = AgentGenerationResult(
        content_markdown="# Clarification Needed\n\n- Which browsers must the UI tests cover?",
        needs_clarification=True,
        clarification_questions=["Which browsers must the UI tests cover?"],
    )
    import app.services.story_test_scenarios_agent as test_scenarios_agent_module

    monkeypatch.setattr(test_scenarios_agent_module, "generate", lambda **kwargs: canned)

    result = draft_story_test_scenarios(nodes["TEST_SCENARIOS"].id, DraftStoryTestScenariosRequest(triggered_by_user_id=actor.id), db)

    assert result.needs_clarification is True
    assert result.story_artifact is not None
    assert "Which browsers must the UI tests cover?" in result.story_artifact.content_markdown

    fetched = get_story_test_scenarios(story.id, db)
    assert fetched.id == result.story_artifact.id


def test_draft_is_based_on_story_lld_and_implementation_plan(db, project, actor, monkeypatch):
    """Rule 2 — content is built from the story, Story LLD, and
    Implementation Plan. Asserted at the input-assembly level (both
    documents are actually fetched and passed to generate()), not by
    string-matching mock output."""
    story, lane, nodes, tech_lead = _lane_ready_for_test_scenarios(db, project, actor)

    captured = {}
    from app.services import story_test_scenarios_agent as module

    original_generate = module.generate

    def _spy(*args, **kwargs):
        captured.update(kwargs)
        return original_generate(*args, **kwargs)

    monkeypatch.setattr(module, "generate", _spy)
    draft_story_test_scenarios(nodes["TEST_SCENARIOS"].id, DraftStoryTestScenariosRequest(triggered_by_user_id=actor.id), db)

    assert "story_lld" in captured["approved_artifact_content"]
    assert "story_implementation_plan" in captured["approved_artifact_content"]
    assert captured["approved_artifact_content"]["story_lld"].strip() != ""
    assert captured["approved_artifact_content"]["story_implementation_plan"].strip() != ""


# --- Review / Approve (rule 3) ----------------------------------------------------------


def test_a_developer_cannot_approve_test_scenarios(db, project, actor):
    story, lane, nodes, tech_lead = _lane_ready_for_test_scenarios(db, project, actor)
    draft_story_test_scenarios(nodes["TEST_SCENARIOS"].id, DraftStoryTestScenariosRequest(triggered_by_user_id=actor.id), db)
    developer = _dev(db)

    with pytest.raises(HTTPException) as exc_info:
        update_lane_node_status(nodes["TEST_SCENARIOS"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=developer.id), db)
    assert exc_info.value.status_code == 403


def test_qa_can_approve_test_scenarios(db, project, actor):
    story, lane, nodes, tech_lead = _lane_ready_for_test_scenarios(db, project, actor)
    draft_story_test_scenarios(nodes["TEST_SCENARIOS"].id, DraftStoryTestScenariosRequest(triggered_by_user_id=actor.id), db)
    qa = _dev(db, role=UserRole.QA)

    update_lane_node_status(nodes["TEST_SCENARIOS"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=qa.id), db)

    node_row = db.get(StoryDeliveryNode, nodes["TEST_SCENARIOS"].id)
    assert node_row.status == StoryDeliveryNodeStatus.COMPLETED


def test_tech_lead_can_approve_test_scenarios(db, project, actor):
    story, lane, nodes, tech_lead = _lane_ready_for_test_scenarios(db, project, actor)
    draft_story_test_scenarios(nodes["TEST_SCENARIOS"].id, DraftStoryTestScenariosRequest(triggered_by_user_id=actor.id), db)

    update_lane_node_status(nodes["TEST_SCENARIOS"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db)

    node_row = db.get(StoryDeliveryNode, nodes["TEST_SCENARIOS"].id)
    assert node_row.status == StoryDeliveryNodeStatus.COMPLETED


def test_request_changes_on_test_scenarios(db, project, actor):
    story, lane, nodes, tech_lead = _lane_ready_for_test_scenarios(db, project, actor)
    draft_story_test_scenarios(nodes["TEST_SCENARIOS"].id, DraftStoryTestScenariosRequest(triggered_by_user_id=actor.id), db)
    qa = _dev(db, role=UserRole.QA)

    update_lane_node_status(
        nodes["TEST_SCENARIOS"].id, UpdateLaneNodeStatusRequest(status="BLOCKED", actor_user_id=qa.id, blocked_reason="Missing edge cases."), db,
    )

    node_row = db.get(StoryDeliveryNode, nodes["TEST_SCENARIOS"].id)
    assert node_row.status == StoryDeliveryNodeStatus.BLOCKED
    assert node_row.blocked_reason == "Missing edge cases."
