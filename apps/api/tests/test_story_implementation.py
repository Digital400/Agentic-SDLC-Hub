"""Story-level implementation workflow — app/api/routes/implementation_runs.py's
story-scoped branch of start_implementation_run, plus
app/api/routes/stories.py's _ensure_story_implementation_task (auto-created
the moment Story LLD/LLD_REVIEW is approved). Covers:
  1/2. Implementation belongs to one story delivery lane, and can start
       only after Story LLD (LLD_REVIEW) approval.
  3. ImplementationRun is linked to project/story/lane/story_lld_artifact/
     assigned user/assigned agent key.
  5. Output includes a PR description alongside the existing diff/summary/
     test command/risks.
  8. Audit logs carry story/lane context.

Rules also covered: implementation is rejected before LLD approval; one
run is always exactly one story (structural, via ImplementationTask.story_id
being a single nullable FK — asserted, not separately gated).
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.implementation_runs import start_implementation_run
from app.api.routes.stories import create_story, create_story_lane, draft_story_lld, update_lane_node_status
from app.models import (
    AgentDefinition,
    AuditLog,
    ImplementationRun,
    ImplementationTask,
    ImplementationTaskArea,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    Repository,
    RepositoryFileEntryType,
    RepositoryFileIndex,
    RepositorySnapshot,
    StoryDeliveryLane,
    StoryDeliveryNode,
    StoryType,
    User,
    UserRole,
)
from app.schemas.implementation_run import StartImplementationRunRequest
from app.schemas.story import CreateStoryLaneRequest, DraftStoryLldRequest, StoryCreate, UpdateLaneNodeStatusRequest
from app.services import ai_generation, implementation_agent
from app.services.story_lld_agent import STORY_LLD_AGENT_KEY
from tests.conftest import make_agent_prompt, make_approved_artifact, make_node

SAMPLE_HLD = "# HLD\n\n## Architecture\nSome design.\n"


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(implementation_agent, "get_active_provider", lambda: "mock")
    monkeypatch.setattr(ai_generation, "get_active_provider", lambda: "mock")


@pytest.fixture(autouse=True)
def _story_lld_agent(db):
    agent = AgentDefinition(agent_key=STORY_LLD_AGENT_KEY, name="Story LLD Agent", model_name="mock")
    db.add(agent)
    db.flush()
    make_agent_prompt(db, stage="story_lld", agent=agent)


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


def _story_with_lane(db, project, actor, *, approve_lld: bool):
    story_crafting_node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, story_crafting_node, actor, content="## Story: X\n")
    hld_node = make_node(db, project, node_key="hld", order_index=1, output_artifact_type="hld_document")
    make_approved_artifact(db, project, hld_node, actor, content=SAMPLE_HLD)

    story = create_story(
        StoryCreate(project_id=project.id, title="Add reset endpoint", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id),
        db,
    )
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)

    from app.models import Story
    lane = db.query(StoryDeliveryLane).filter(StoryDeliveryLane.story_id == story.id).first()
    nodes = {n.node_key: n for n in lane.nodes}
    update_lane_node_status(nodes["STORY_READY"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)
    lld_result = draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)

    if approve_lld:
        tech_lead = _tech_lead(db)
        update_lane_node_status(nodes["LLD_REVIEW"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db)
        # IMPLEMENTATION_PLAN now sits between LLD_REVIEW and IMPLEMENTATION
        # (see app/services/story_delivery.py) — its completion is what
        # actually unlocks IMPLEMENTATION and triggers
        # _ensure_story_implementation_task, not LLD_REVIEW's directly.
        # Accepting it (Story Implementation Plan Agent, rule 4) requires a
        # real plan to exist first — fabricated directly here, same
        # convention as QA_APPROVAL's own evidence-artifact stub elsewhere
        # in this suite.
        from app.models import StoryArtifact
        db.add(
            StoryArtifact(
                story_id=story.id, lane_id=lane.id, node_id=nodes["IMPLEMENTATION_PLAN"].id,
                artifact_type="story_implementation_plan", title="Implementation Plan", content_markdown="## Implementation Summary\nPlan.\n",
                version_number=1, created_by_id=tech_lead.id,
            )
        )
        db.flush()
        update_lane_node_status(nodes["IMPLEMENTATION_PLAN"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db)

    story_row = db.get(Story, story.id)
    return story_row, lane, nodes, lld_result.story_artifact


def test_implementation_task_is_not_created_until_lld_review_approves(db, project, actor):
    story_row, lane, nodes, lld_artifact = _story_with_lane(db, project, actor, approve_lld=False)
    assert db.query(ImplementationTask).filter(ImplementationTask.story_id == story_row.id).count() == 0


def test_implementation_task_auto_created_once_story_lld_is_approved(db, project, actor):
    """Requirement 1 — implementation belongs to exactly this story's own
    delivery lane; created automatically the moment IMPLEMENTATION_PLAN
    completes (right after LLD_REVIEW approves it), with no real
    WorkflowNode/Artifact backing it (both nullable now)."""
    story_row, lane, nodes, lld_artifact = _story_with_lane(db, project, actor, approve_lld=True)
    task = db.query(ImplementationTask).filter(ImplementationTask.story_id == story_row.id).first()
    assert task is not None
    assert task.workflow_node_id is None
    assert task.artifact_id is None
    assert task.linked_story == story_row.title


def test_blocked_when_story_lld_not_approved(db, project, actor):
    story_row, lane, nodes, lld_artifact = _story_with_lane(db, project, actor, approve_lld=False)
    # No ImplementationTask exists yet either (see the auto-creation test
    # above) — fabricate one directly to prove the *run* gate itself,
    # independent of task auto-creation timing.
    task = ImplementationTask(
        project_id=project.id, story_id=story_row.id, title="X", description="X",
        area=ImplementationTaskArea.BACKEND, assigned_agent_type="backend-coding-agent", order_index=0,
    )
    db.add(task)
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "Story LLD" in exc_info.value.detail
    assert db.query(ImplementationRun).count() == 0


def test_implementation_run_links_story_lane_and_lld_artifact_once_approved(db, project, actor):
    story_row, lane, nodes, lld_artifact = _story_with_lane(db, project, actor, approve_lld=True)
    task = db.query(ImplementationTask).filter(ImplementationTask.story_id == story_row.id).first()
    _add_repository(db, project)

    run = start_implementation_run(
        StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db
    )
    assert run.status.value == "COMPLETED"

    run_row = db.get(ImplementationRun, run.id)
    assert run_row.story_id == story_row.id
    assert run_row.lane_id == lane.id
    assert run_row.story_lld_artifact_id == lld_artifact.id
    assert run_row.assigned_user_id == actor.id
    assert run_row.assigned_agent_key == task.assigned_agent_type
    # Requirement 6 — saved for human review, not auto-applied.
    assert run_row.review_status.value == "PENDING_REVIEW"
    # Requirement 5 — PR description is part of the drafted output.
    assert run_row.pr_description != ""

    audit_actions = {a.action: a for a in db.query(AuditLog).filter(AuditLog.entity_type == "ImplementationRun").all()}
    assert "implementation_run.started" in audit_actions
    assert audit_actions["implementation_run.started"].extra_data["story_id"] == str(story_row.id)

    implementation_lane_node = db.get(StoryDeliveryNode, nodes["IMPLEMENTATION"].id)
    assert implementation_lane_node.status.value == "IN_PROGRESS"


def test_one_run_is_always_exactly_one_story(db, project, actor):
    """Structural, not a runtime gate: ImplementationTask.story_id is a
    single nullable FK, never a collection — a run spanning multiple
    stories has no representation to even attempt."""
    story_row, lane, nodes, lld_artifact = _story_with_lane(db, project, actor, approve_lld=True)
    task = db.query(ImplementationTask).filter(ImplementationTask.story_id == story_row.id).first()
    assert isinstance(task.story_id, uuid.UUID)
    _add_repository(db, project)
    run = start_implementation_run(
        StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db
    )
    run_row = db.get(ImplementationRun, run.id)
    assert run_row.story_id == story_row.id  # exactly one story, never more
