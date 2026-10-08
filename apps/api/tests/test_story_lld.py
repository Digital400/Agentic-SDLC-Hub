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

from app.api.routes.stories import bulk_approve_lane_node, bulk_run_story_lld, create_story, create_story_lane, draft_story_lld, get_story_lld, update_lane_node_status
from app.models import AgentDefinition, ArtifactStatus, StoryDeliveryNodeStatus, StoryType, User, UserRole
from app.schemas.story import (
    BulkApproveLaneNodeRequest,
    BulkRunStoryLldRequest,
    CreateStoryLaneRequest,
    DraftStoryLldRequest,
    StoryCreate,
    UpdateLaneNodeStatusRequest,
)
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


def test_story_lld_uses_hld_delta_on_the_existing_project_feature_template(db, project, actor):
    """Regression — a project on the existing-project-feature template has
    no "hld" node, only "hld_delta". Story LLD must recognize that
    instead of reporting "The High-Level Design must be approved" forever."""
    _approve_story_crafting(db, project, actor)
    hld_delta_node = make_node(db, project, node_key="hld_delta", order_index=0, output_artifact_type="hld_delta")
    make_approved_artifact(db, project, hld_delta_node, actor, content="# HLD Delta\n\nArchitecture change.\n")

    story = create_story(
        StoryCreate(project_id=project.id, title="Add reset endpoint", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id),
        db,
    )
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    from app.models import StoryDeliveryLane

    lane = db.query(StoryDeliveryLane).filter(StoryDeliveryLane.story_id == story.id).first()
    nodes = {n.node_key: n for n in lane.nodes}
    _complete(db, nodes["STORY_READY"], actor)

    response = draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)
    assert response.story_artifact is not None and not response.needs_clarification


def test_story_lld_blocked_while_locked_before_story_ready_completes(db, project, actor):
    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    # STORY_READY never completed — STORY_LLD is still LOCKED.
    with pytest.raises(HTTPException) as exc_info:
        draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409


def test_story_lld_blocked_when_story_itself_is_not_approved(db, project, actor):
    """Precondition 1 — "story is approved". A lane can't normally exist
    for a PENDING story (create_story_lane itself requires an approved
    Story Crafting backlog and sets status=LANE_ACTIVE), so this
    defensive check in run_story_lld_agent is only reachable by directly
    reverting the story's own status — done here to prove the check
    itself, independent of how a real PENDING story could ever get a lane."""
    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    _complete(db, nodes["STORY_READY"], actor)

    from app.models import Story, StoryStatus

    story_row = db.get(Story, story.id)
    story_row.status = StoryStatus.PENDING
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "approved" in exc_info.value.detail.lower()


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


def test_story_lld_uses_the_claude_agent_sdk_harness_when_enabled_and_a_repository_is_connected(db, project, actor, monkeypatch):
    """Story LLD calls ai_generation.generate() with the story's REAL
    project (just a transient WorkflowNode standing in for the story's
    own context — see story_lld_agent.py's module docstring) — so it
    already goes through generate()'s harness dispatch with zero extra
    wiring. This is the proof: an earlier version of
    claude_agent_harness.py's own docstring incorrectly claimed Story LLD
    was unaffected by it."""
    from app.models import Integration, IntegrationConnection, IntegrationProvider, IntegrationStatus, Repository
    from app.services.claude_agent_harness import DocumentSessionResult

    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    _complete(db, nodes["STORY_READY"], actor)

    integration = Integration(integration_name="GitHub", provider=IntegrationProvider.GITHUB, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(integration=integration, access_token_encrypted="x", token_last_four="1234", github_username="octocat", status=IntegrationStatus.CONNECTED)
    db.add(connection)
    db.flush()
    db.add(Repository(project=project, connection=connection, owner="octocat", name="hello-world", default_branch="main", is_primary=True))
    db.flush()
    db.refresh(project)

    monkeypatch.setattr(ai_generation, "get_settings", lambda: _settings_with(CLAUDE_AGENT_SDK_ENABLED=True))
    captured = {}

    def _fake_run_document_session(**kwargs):
        captured["called"] = True
        return DocumentSessionResult(text="## Design\n\nGrounded in the real repo.", prompt_tokens=1, completion_tokens=1, cost=0.0, truncated=False)

    monkeypatch.setattr("app.services.claude_agent_harness.run_document_session", _fake_run_document_session)

    result = draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)

    assert captured.get("called") is True
    assert "Grounded in the real repo" in result.story_artifact.content_markdown


def test_story_lld_honors_provider_and_model_override(db, project, actor, monkeypatch):
    """provider_override/model_override (see DraftStoryLldRequest) must
    actually reach ai_generation.generate() — this proves the
    use_provider_override/use_model_override context managers in
    run_story_lld_agent are active for the duration of the real call, the
    same mechanism agent_runs.py's start_agent_run already relies on for
    the project-level LLM dropdown."""
    from app.services import story_lld_agent as story_lld_agent_module
    from app.services import ai_generation as ai_generation_module
    from app.services.ai_generation import AgentGenerationResult, get_model_override

    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    _complete(db, nodes["STORY_READY"], actor)

    captured = {}

    def _fake_generate(**kwargs):
        # This test file's own autouse fixture replaces get_active_provider
        # outright (always "mock"), so the raw contextvar is read directly
        # here instead — get_active_provider's own real behavior (honoring
        # this same contextvar) is already covered by
        # tests/test_ai_generation_providers.py.
        captured["provider"] = ai_generation_module._provider_override_var.get()
        captured["model"] = get_model_override()
        return AgentGenerationResult(content_markdown="## Story Summary\n\nok", needs_clarification=False)

    monkeypatch.setattr(story_lld_agent_module, "generate", _fake_generate)

    draft_story_lld(
        nodes["STORY_LLD"].id,
        DraftStoryLldRequest(triggered_by_user_id=actor.id, provider_override="huggingface", model_override="my-custom-model"),
        db,
    )

    assert captured["provider"] == "huggingface"
    assert captured["model"] == "my-custom-model"
    # And confirms it doesn't leak into a later, override-less call.
    assert get_model_override() is None


def _settings_with(**overrides):
    from types import SimpleNamespace

    from app.core.config import Settings

    base = Settings().model_dump()
    base.update(overrides)
    return SimpleNamespace(**base)


def test_clarification_response_is_persisted_as_a_real_story_artifact(db, project, actor, monkeypatch):
    """Regression test for a real bug: needs_clarification used to return
    story_artifact=None, discarding the model's actual clarification
    questions (already formatted into result.content_markdown by
    ai_generation.generate) — a human had nothing to read, and the UI's
    "see the generated notes" message pointed at notes that were never
    saved. The clarification content must now be persisted like any other
    draft."""
    from app.services import ai_generation
    from app.services.ai_generation import AgentGenerationResult

    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    _complete(db, nodes["STORY_READY"], actor)

    canned = AgentGenerationResult(
        content_markdown="# Clarification Needed\n\n- Which database should this use?",
        needs_clarification=True,
        clarification_questions=["Which database should this use?"],
    )
    import app.services.story_lld_agent as story_lld_agent_module

    monkeypatch.setattr(story_lld_agent_module, "generate", lambda **kwargs: canned)

    result = draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)

    assert result.needs_clarification is True
    assert result.story_artifact is not None
    assert "Which database should this use?" in result.story_artifact.content_markdown

    fetched = get_story_lld(story.id, db)
    assert fetched.id == result.story_artifact.id
    assert "Which database should this use?" in fetched.content_markdown


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


def test_implementation_plan_unlocks_only_after_lld_review_completes(db, project, actor):
    """IMPLEMENTATION_PLAN sits between LLD_REVIEW and IMPLEMENTATION (see
    app/services/story_delivery.py) — LLD_REVIEW's completion unlocks
    IMPLEMENTATION_PLAN directly; IMPLEMENTATION itself stays LOCKED
    until IMPLEMENTATION_PLAN also completes."""
    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    _complete(db, nodes["STORY_READY"], actor)
    draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)

    tech_lead = _dev(db, role=UserRole.TECH_LEAD)
    update_lane_node_status(nodes["LLD_REVIEW"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db)

    from app.models import StoryDeliveryNode
    implementation_plan = db.get(StoryDeliveryNode, nodes["IMPLEMENTATION_PLAN"].id)
    assert implementation_plan.status == StoryDeliveryNodeStatus.READY
    implementation = db.get(StoryDeliveryNode, nodes["IMPLEMENTATION"].id)
    assert implementation.status == StoryDeliveryNodeStatus.LOCKED

    # Accepting IMPLEMENTATION_PLAN (Story Implementation Plan Agent, rule
    # 4) requires a real plan to exist first — fabricated directly here.
    from app.models import StoryArtifact
    db.add(
        StoryArtifact(
            story_id=story.id, lane_id=lane.id, node_id=implementation_plan.id, artifact_type="story_implementation_plan",
            title="Implementation Plan", content_markdown="## Implementation Summary\nPlan.\n", version_number=1,
            created_by_id=tech_lead.id,
        )
    )
    db.flush()
    update_lane_node_status(implementation_plan.id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db)
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


# --- "Run Story LLD" bulk button (Stories list) -----------------------------------------


def test_bulk_run_story_lld_creates_lanes_completes_story_ready_and_drafts_both_stories(db, project, actor):
    """No lane yet for either story — the bulk action must create one,
    auto-complete Story Ready (it has no review gate of its own), and then
    draft Story LLD, all in a single call."""
    _approve_story_crafting(db, project, actor)
    hld_node = make_node(db, project, node_key="hld", order_index=0, output_artifact_type="hld_document")
    make_approved_artifact(db, project, hld_node, actor, content="# HLD\n\n## Architecture\nSome design.\n")

    story_a = create_story(
        StoryCreate(project_id=project.id, title="Add reset endpoint", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id), db,
    )
    story_b = create_story(
        StoryCreate(project_id=project.id, title="Add logout endpoint", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id), db,
    )

    response = bulk_run_story_lld(project.id, BulkRunStoryLldRequest(story_ids=[story_a.id, story_b.id], triggered_by_user_id=actor.id), db)

    assert [r.status for r in response.results] == ["drafted", "drafted"]
    assert all(r.lane_created for r in response.results)
    assert response.remaining == []
    assert get_story_lld(story_a.id, db) is not None
    assert get_story_lld(story_b.id, db) is not None


def test_bulk_run_story_lld_reports_an_already_drafted_story_without_redrafting(db, project, actor):
    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    _complete(db, nodes["STORY_READY"], actor)
    first = draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)

    response = bulk_run_story_lld(project.id, BulkRunStoryLldRequest(story_ids=[story.id], triggered_by_user_id=actor.id), db)

    assert response.results[0].status == "already_drafted"
    assert response.results[0].lane_created is False
    assert response.remaining == []
    # Unchanged — no second version was drafted.
    assert get_story_lld(story.id, db).id == first.story_artifact.id


def test_bulk_run_story_lld_surfaces_a_failed_precondition_as_remaining_without_failing_the_batch(db, project, actor):
    """HLD not approved yet — one story can't proceed, but a second,
    unrelated story in the same batch must still succeed."""
    _approve_story_crafting(db, project, actor)
    # No HLD/hld_delta artifact approved at all.
    blocked_story = create_story(
        StoryCreate(project_id=project.id, title="Add reset endpoint", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id), db,
    )

    response = bulk_run_story_lld(project.id, BulkRunStoryLldRequest(story_ids=[blocked_story.id], triggered_by_user_id=actor.id), db)

    assert response.results[0].status == "skipped"
    assert response.results[0].reason is not None
    assert response.remaining == response.results
    assert "still need your attention" in response.message


# --- "Approve & Unlock Next" bulk button (Stories list) ----------------------------------


def test_bulk_approve_lane_node_approves_lld_review_and_unlocks_implementation_plan(db, project, actor):
    """`actor` (tests/conftest.py) is ADMIN, which passes LLD_REVIEW's
    Tech-Lead-only gate — approving it must also unlock IMPLEMENTATION_PLAN
    for both stories, same as a human clicking Approve one at a time."""
    story_a, lane_a, nodes_a = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    _complete(db, nodes_a["STORY_READY"], actor)
    draft_story_lld(nodes_a["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)

    story_b = create_story(
        StoryCreate(project_id=project.id, title="Add logout endpoint", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id), db,
    )
    create_story_lane(story_b.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    from app.models import StoryDeliveryLane

    lane_b = db.query(StoryDeliveryLane).filter(StoryDeliveryLane.story_id == story_b.id).first()
    nodes_b = {n.node_key: n for n in lane_b.nodes}
    _complete(db, nodes_b["STORY_READY"], actor)
    draft_story_lld(nodes_b["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)

    response = bulk_approve_lane_node(
        project.id, BulkApproveLaneNodeRequest(node_key="LLD_REVIEW", story_ids=[story_a.id, story_b.id], triggered_by_user_id=actor.id), db,
    )

    assert {r.status for r in response.results} == {"approved"}
    from app.models import StoryDeliveryNode

    assert db.get(StoryDeliveryNode, nodes_a["IMPLEMENTATION_PLAN"].id).status == StoryDeliveryNodeStatus.READY
    assert db.get(StoryDeliveryNode, nodes_b["IMPLEMENTATION_PLAN"].id).status == StoryDeliveryNodeStatus.READY


def test_bulk_approve_lane_node_reports_already_approved_without_erroring(db, project, actor):
    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    _complete(db, nodes["STORY_READY"], actor)
    draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)
    _complete(db, nodes["LLD_REVIEW"], actor)

    response = bulk_approve_lane_node(project.id, BulkApproveLaneNodeRequest(node_key="LLD_REVIEW", story_ids=[story.id], triggered_by_user_id=actor.id), db)

    assert response.results[0].status == "already_approved"


def test_bulk_approve_lane_node_skips_without_approving_when_caller_lacks_the_role(db, project, actor):
    """A Developer may not approve LLD_REVIEW — this must be reported as a
    per-story skip with the real reason, never silently approved anyway
    and never raised as a request-level error for the whole batch."""
    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    _complete(db, nodes["STORY_READY"], actor)
    draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)
    developer = _dev(db, role=UserRole.DEVELOPER)

    response = bulk_approve_lane_node(project.id, BulkApproveLaneNodeRequest(node_key="LLD_REVIEW", story_ids=[story.id], triggered_by_user_id=developer.id), db)

    assert response.results[0].status == "skipped"
    assert response.results[0].reason is not None
    from app.models import StoryDeliveryNode

    assert db.get(StoryDeliveryNode, nodes["LLD_REVIEW"].id).status != StoryDeliveryNodeStatus.COMPLETED


def test_bulk_approve_lane_node_reports_locked_as_skipped_not_approved(db, project, actor):
    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)
    # STORY_READY never completed — STORY_LLD (and therefore LLD_REVIEW) stay LOCKED.

    response = bulk_approve_lane_node(project.id, BulkApproveLaneNodeRequest(node_key="LLD_REVIEW", story_ids=[story.id], triggered_by_user_id=actor.id), db)

    assert response.results[0].status == "skipped"
    assert "locked" in response.results[0].reason.lower()


def test_bulk_approve_lane_node_rejects_an_unsupported_node_key(db, project, actor):
    story, lane, nodes = _lane_for_new_story(db, project, actor, with_hld_approved=True)

    with pytest.raises(HTTPException) as exc_info:
        bulk_approve_lane_node(project.id, BulkApproveLaneNodeRequest(node_key="QA_APPROVAL", story_ids=[story.id], triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 400
