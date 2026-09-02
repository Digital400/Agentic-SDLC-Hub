"""Story-level testing workflow — app/api/routes/test_runs.py's
story-scoped branch of start_test_run, plus QA_APPROVAL's evidence gate
in app/api/routes/stories.py's update_lane_node_status. Covers:
  1/2. Testing belongs to one story delivery lane, and can start only
       once implementation exists (an accepted ImplementationRun — which
       a PR always originates from anyway).
  3. TestRun is linked to project/story/lane/pull_request/test_agent_key.
  6/7/9. QA Approval requires test evidence (a story_test_report
       StoryArtifact with a real, non-empty "Test Evidence" section) —
       blocked when missing, empty, or absent entirely; also role-gated
       to QA/Admin.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.implementation_runs import review_implementation_run, start_implementation_run
from app.api.routes.stories import create_story, create_story_lane, draft_story_lld, update_lane_node_status
from app.api.routes.test_runs import start_test_run
from app.models import (
    AgentDefinition,
    ImplementationRun,
    ImplementationTask,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    Repository,
    RepositoryFileEntryType,
    RepositoryFileIndex,
    RepositorySnapshot,
    StoryArtifact,
    StoryDeliveryNode,
    StoryType,
    TestAgentType,
    TestRun,
    User,
    UserRole,
)
from app.schemas.implementation_run import ReviewImplementationRunRequest, StartImplementationRunRequest
from app.schemas.story import CreateStoryLaneRequest, DraftStoryLldRequest, StoryCreate, UpdateLaneNodeStatusRequest
from app.schemas.test_run import StartTestRunRequest
from app.services import ai_generation, implementation_agent, testing_agent
from app.services.story_lld_agent import STORY_LLD_AGENT_KEY
from tests.conftest import make_agent_prompt, make_approved_artifact, make_node

SAMPLE_HLD = "# HLD\n\n## Architecture\nSome design.\n"


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(implementation_agent, "get_active_provider", lambda: "mock")
    monkeypatch.setattr(testing_agent, "get_active_provider", lambda: "mock")
    monkeypatch.setattr(ai_generation, "get_active_provider", lambda: "mock")


@pytest.fixture(autouse=True)
def _story_lld_agent(db):
    agent = AgentDefinition(agent_key=STORY_LLD_AGENT_KEY, name="Story LLD Agent", model_name="mock")
    db.add(agent)
    db.flush()
    make_agent_prompt(db, stage="story_lld", agent=agent)


def _qa(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="QA", role=UserRole.QA)
    db.add(user)
    db.flush()
    return user


def _tech_lead(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Tech Lead", role=UserRole.TECH_LEAD)
    db.add(user)
    db.flush()
    return user


def _add_repository(db, project):
    integration = Integration(integration_name="GitHub", provider=IntegrationProvider.GITHUB, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(
        integration=integration, access_token_encrypted="not-a-real-fernet-token",
        token_last_four="7890", github_username="octocat", status=IntegrationStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    repository = Repository(project=project, connection=connection, owner="octocat", name="hello-world", default_branch="main")
    db.add(repository)
    db.flush()
    snapshot = RepositorySnapshot(repository=repository, ref="main", commit_sha="abc123", file_count=1, truncated=False)
    db.add(snapshot)
    db.flush()
    db.add(RepositoryFileIndex(snapshot=snapshot, path="apps/api/app/api/routes/auth.py", entry_type=RepositoryFileEntryType.FILE, size=200, sha="deadbeef"))
    db.flush()
    return repository, snapshot


def _story_ready_for_testing(db, project, actor):
    """Full setup through an ACCEPTED ImplementationRun — everything
    requirement 2's gate needs."""
    story_crafting_node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, story_crafting_node, actor, content="## Story: X\n")
    hld_node = make_node(db, project, node_key="hld", order_index=1, output_artifact_type="hld_document")
    make_approved_artifact(db, project, hld_node, actor, content=SAMPLE_HLD)

    story = create_story(
        StoryCreate(project_id=project.id, title="Add reset endpoint", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id),
        db,
    )
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)

    from app.models import Story, StoryDeliveryLane
    lane = db.query(StoryDeliveryLane).filter(StoryDeliveryLane.story_id == story.id).first()
    nodes = {n.node_key: n for n in lane.nodes}
    update_lane_node_status(nodes["STORY_READY"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)
    draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)
    tech_lead = _tech_lead(db)
    update_lane_node_status(nodes["LLD_REVIEW"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db)

    task = db.query(ImplementationTask).filter(ImplementationTask.story_id == story.id).first()
    _add_repository(db, project)
    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    review_implementation_run(run.id, ReviewImplementationRunRequest(decision="ACCEPTED", reviewed_by_user_id=tech_lead.id), db)

    # Progress the lane's own sequence up through HUMAN_CODE_REVIEW so
    # TESTING unlocks — this module's own gate (an accepted
    # ImplementationRun) is independent of these generic node-status
    # transitions, so completing them doesn't itself satisfy requirement 2.
    for key in ("IMPLEMENTATION", "PULL_REQUEST", "PR_REVIEW_AGENT", "HUMAN_CODE_REVIEW"):
        update_lane_node_status(nodes[key].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)

    story_row = db.get(Story, story.id)
    return story_row, lane, nodes, task


# --- Requirement 2: gate -----------------------------------------------------------------


def test_testing_blocked_until_implementation_is_accepted(db, project, actor):
    story_crafting_node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, story_crafting_node, actor, content="## Story: X\n")
    hld_node = make_node(db, project, node_key="hld", order_index=1, output_artifact_type="hld_document")
    make_approved_artifact(db, project, hld_node, actor, content=SAMPLE_HLD)
    story = create_story(
        StoryCreate(project_id=project.id, title="No impl yet", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id), db,
    )
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)

    from app.models import Story, StoryDeliveryLane
    lane = db.query(StoryDeliveryLane).filter(StoryDeliveryLane.story_id == story.id).first()
    nodes = {n.node_key: n for n in lane.nodes}
    update_lane_node_status(nodes["STORY_READY"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)
    draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)
    tech_lead = _tech_lead(db)
    update_lane_node_status(nodes["LLD_REVIEW"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db)
    # Advance the lane's own sequence up through HUMAN_CODE_REVIEW without
    # ever creating/accepting a real ImplementationRun — this module's own
    # gate is independent of those generic node-status transitions.
    for key in ("IMPLEMENTATION", "PULL_REQUEST", "PR_REVIEW_AGENT", "HUMAN_CODE_REVIEW"):
        update_lane_node_status(nodes[key].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)

    task = db.query(ImplementationTask).filter(ImplementationTask.story_id == story.id).first()

    with pytest.raises(HTTPException) as exc_info:
        start_test_run(StartTestRunRequest(implementation_task_id=task.id, agent_type=TestAgentType.UNIT, triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "accept an implementation run" in exc_info.value.detail.lower()
    assert db.query(TestRun).count() == 0


# --- Requirements 1/3: lane/model links --------------------------------------------------


def test_test_run_links_story_lane_and_test_agent_key_and_produces_story_artifact(db, project, actor):
    story_row, lane, nodes, task = _story_ready_for_testing(db, project, actor)

    run = start_test_run(
        StartTestRunRequest(implementation_task_id=task.id, agent_type=TestAgentType.UNIT, triggered_by_user_id=actor.id), db
    )
    assert run.status.value == "COMPLETED"

    run_row = db.get(TestRun, run.id)
    assert run_row.story_id == story_row.id
    assert run_row.lane_id == lane.id
    assert run_row.test_agent_key == "UNIT"
    assert run_row.workflow_node_id is None
    assert run_row.story_artifact_id is not None

    story_artifact = db.get(StoryArtifact, run_row.story_artifact_id)
    assert story_artifact.artifact_type == "story_test_report"
    assert "Test Evidence" in story_artifact.content_markdown

    testing_lane_node = db.get(StoryDeliveryNode, nodes["TESTING"].id)
    assert testing_lane_node.status.value == "COMPLETED"
    qa_approval_node = db.get(StoryDeliveryNode, nodes["QA_APPROVAL"].id)
    assert qa_approval_node.status.value == "READY"


# --- Requirements 6/7/9: QA Approval evidence gate --------------------------------------


def test_qa_approval_succeeds_once_a_real_test_report_exists(db, project, actor):
    story_row, lane, nodes, task = _story_ready_for_testing(db, project, actor)
    start_test_run(StartTestRunRequest(implementation_task_id=task.id, agent_type=TestAgentType.UNIT, triggered_by_user_id=actor.id), db)

    qa = _qa(db)
    updated = update_lane_node_status(nodes["QA_APPROVAL"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=qa.id), db)
    assert updated.status.value == "COMPLETED"


def test_qa_approval_blocked_when_no_test_report_exists_yet(db, project, actor):
    story_row, lane, nodes, task = _story_ready_for_testing(db, project, actor)
    # Bypass the sequence — QA_APPROVAL would otherwise still be LOCKED —
    # to isolate the evidence check itself, same pattern as
    # test_story_delivery_lane.py's own locked-node tests.
    qa_approval_node = db.get(StoryDeliveryNode, nodes["QA_APPROVAL"].id)
    from app.models import StoryDeliveryNodeStatus
    qa_approval_node.status = StoryDeliveryNodeStatus.READY
    db.flush()

    qa = _qa(db)
    with pytest.raises(HTTPException) as exc_info:
        update_lane_node_status(qa_approval_node.id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=qa.id), db)
    assert exc_info.value.status_code == 409
    assert "test report" in exc_info.value.detail.lower()


def test_qa_approval_blocked_when_test_evidence_section_missing(db, project, actor):
    story_row, lane, nodes, task = _story_ready_for_testing(db, project, actor)
    qa_approval_node = db.get(StoryDeliveryNode, nodes["QA_APPROVAL"].id)
    from app.models import StoryDeliveryNodeStatus
    qa_approval_node.status = StoryDeliveryNodeStatus.READY
    db.add(
        StoryArtifact(
            story_id=story_row.id, lane_id=lane.id, artifact_type="story_test_report", title="Test Report",
            content_markdown="## Summary\nAll good, trust me.\n", version_number=1, created_by_id=actor.id,
        )
    )
    db.flush()

    qa = _qa(db)
    with pytest.raises(HTTPException) as exc_info:
        update_lane_node_status(qa_approval_node.id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=qa.id), db)
    assert exc_info.value.status_code == 409
    assert "Test Evidence" in exc_info.value.detail


def test_qa_approval_blocked_when_test_evidence_section_is_empty(db, project, actor):
    story_row, lane, nodes, task = _story_ready_for_testing(db, project, actor)
    qa_approval_node = db.get(StoryDeliveryNode, nodes["QA_APPROVAL"].id)
    from app.models import StoryDeliveryNodeStatus
    qa_approval_node.status = StoryDeliveryNodeStatus.READY
    db.add(
        StoryArtifact(
            story_id=story_row.id, lane_id=lane.id, artifact_type="story_test_report", title="Test Report",
            content_markdown="## Test Evidence\n\n## Summary\nOk.\n", version_number=1, created_by_id=actor.id,
        )
    )
    db.flush()

    qa = _qa(db)
    with pytest.raises(HTTPException) as exc_info:
        update_lane_node_status(qa_approval_node.id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=qa.id), db)
    assert exc_info.value.status_code == 409
    assert "empty" in exc_info.value.detail.lower()


def test_a_non_qa_cannot_grant_qa_approval_even_with_evidence(db, project, actor):
    story_row, lane, nodes, task = _story_ready_for_testing(db, project, actor)
    start_test_run(StartTestRunRequest(implementation_task_id=task.id, agent_type=TestAgentType.UNIT, triggered_by_user_id=actor.id), db)

    developer = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(developer)
    db.flush()
    with pytest.raises(HTTPException) as exc_info:
        update_lane_node_status(nodes["QA_APPROVAL"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=developer.id), db)
    assert exc_info.value.status_code == 403
