"""Story Code Implementation Agent, Flow steps 4-9 —
app/services/story_code_implementation.py and the
POST /code-runs route (app/api/routes/code_runs.py). Covers:
  - Rule: do not apply changes without user approval (non-ACCEPTED run
    refused, before any workspace/clone/anything happens).
  - Rule: do not implement multiple stories in one run (structural —
    project-level runs, which have no single story, are refused).
  - Rule: do not push to main (branch-collision refusal).
  - Rule: if tests fail, keep logs and mark CodeRun failed — no commit,
    no push, no lane advance.
  - Happy path: apply -> test -> commit -> push -> IMPLEMENTATION node
    completes and the lane advances ("move lane to GitHub PR stage").

No real git/network call is ever made — every git-invoking
CodeRunnerService method is exercised against a monkeypatched
subprocess.run, same convention as tests/test_code_runner.py.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.code_runs import apply_via_code_runner, get_code_run
from app.api.routes.implementation_runs import review_implementation_run, start_implementation_run
from app.api.routes.stories import create_story, create_story_lane, draft_story_lld, update_lane_node_status
from app.models import (
    AgentDefinition,
    CodeRunStatus,
    ImplementationRunReviewStatus,
    ImplementationTask,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    Repository,
    RepositoryFileEntryType,
    RepositoryFileIndex,
    RepositorySnapshot,
    Story,
    StoryArtifact,
    StoryDeliveryLane,
    StoryDeliveryNode,
    StoryDeliveryNodeStatus,
    StoryType,
    User,
    UserRole,
)
from app.schemas.code_run import ApplyViaCodeRunnerRequest
from app.schemas.implementation_run import ReviewImplementationRunRequest, StartImplementationRunRequest
from app.schemas.story import CreateStoryLaneRequest, DraftStoryLldRequest, StoryCreate, UpdateLaneNodeStatusRequest
from app.services import ai_generation, code_runner as code_runner_module, implementation_agent
from app.services.story_code_implementation import StoryCodeImplementationError, create_code_run
from app.services.story_lld_agent import STORY_LLD_AGENT_KEY

SAMPLE_HLD = "# HLD\n\n## Architecture\nSome design.\n"


@pytest.fixture(autouse=True)
def _force_mock_providers(monkeypatch):
    monkeypatch.setattr(implementation_agent, "get_active_provider", lambda: "mock")
    monkeypatch.setattr(ai_generation, "get_active_provider", lambda: "mock")


@pytest.fixture(autouse=True)
def _story_lld_agent(db):
    agent = AgentDefinition(agent_key=STORY_LLD_AGENT_KEY, name="Story LLD Agent", model_name="mock")
    db.add(agent)
    db.flush()
    from tests.conftest import make_agent_prompt

    make_agent_prompt(db, stage="story_lld", agent=agent)


def _tech_lead(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Tech Lead", role=UserRole.TECH_LEAD)
    db.add(user)
    db.flush()
    return user


def _add_repository(db, project) -> Repository:
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
    return repository


def _ensure_project_approved(db, project, actor):
    from app.models import WorkflowNode
    from tests.conftest import make_approved_artifact, make_node

    if db.query(WorkflowNode).filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == "story_crafting").first() is None:
        story_crafting_node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
        make_approved_artifact(db, project, story_crafting_node, actor, content="## Story: X\n")
        hld_node = make_node(db, project, node_key="hld", order_index=1, output_artifact_type="hld_document")
        make_approved_artifact(db, project, hld_node, actor, content=SAMPLE_HLD)


def _story_with_accepted_run(db, project, actor, *, title: str = "Add reset endpoint"):
    _ensure_project_approved(db, project, actor)

    story = create_story(
        StoryCreate(project_id=project.id, title=title, mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id), db,
    )
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)

    lane = db.query(StoryDeliveryLane).filter(StoryDeliveryLane.story_id == story.id).first()
    nodes = {n.node_key: n for n in lane.nodes}
    update_lane_node_status(nodes["STORY_READY"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)
    draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)
    tech_lead = _tech_lead(db)
    update_lane_node_status(nodes["LLD_REVIEW"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db)
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
    task = db.query(ImplementationTask).filter(ImplementationTask.story_id == story_row.id).first()
    repository = _add_repository(db, project)

    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    run = review_implementation_run(run.id, ReviewImplementationRunRequest(decision="ACCEPTED", reviewed_by_user_id=actor.id), db)
    return story_row, lane, nodes, task, run, repository


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _mock_git_success(monkeypatch, tmp_path):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "CODE_RUNNER_WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(code_runner_module, "decrypt_secret", lambda ciphertext: "fake-token")
    calls = []

    def _fake_run(command, **kwargs):
        calls.append(command)
        return _FakeCompletedProcess(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(code_runner_module.subprocess, "run", _fake_run)
    return calls


# --- Rule: no apply without approval ----------------------------------------------------


def test_create_code_run_refuses_a_non_accepted_run(db, project, actor):
    story, lane, nodes, task, run, repository = _story_with_accepted_run(db, project, actor)
    run.review_status = ImplementationRunReviewStatus.PENDING_REVIEW
    db.flush()

    with pytest.raises(StoryCodeImplementationError, match="not ACCEPTED"):
        create_code_run(db, implementation_run=run, repository=repository, triggered_by=actor)


def test_create_code_run_refuses_a_project_level_run(db, project, actor):
    story, lane, nodes, task, run, repository = _story_with_accepted_run(db, project, actor)
    run.story_id = None
    db.flush()

    with pytest.raises(StoryCodeImplementationError, match="story-scoped"):
        create_code_run(db, implementation_run=run, repository=repository, triggered_by=actor)


def test_route_rejects_a_non_story_scoped_run(db, project, actor):
    from app.models import ImplementationRun

    story, lane, nodes, task, run, repository = _story_with_accepted_run(db, project, actor)
    run_row = db.get(ImplementationRun, run.id)
    run_row.story_id = None
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        apply_via_code_runner(ApplyViaCodeRunnerRequest(implementation_run_id=run.id, triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 400


# --- Happy path ----------------------------------------------------------------------------


def test_apply_via_code_runner_succeeds_and_advances_the_lane(db, project, actor, tmp_path, monkeypatch):
    story, lane, nodes, task, run, repository = _story_with_accepted_run(db, project, actor)
    calls = _mock_git_success(monkeypatch, tmp_path)

    code_run = apply_via_code_runner(ApplyViaCodeRunnerRequest(implementation_run_id=run.id, triggered_by_user_id=actor.id, test_commands=["pytest -q"]), db)

    assert code_run.status == CodeRunStatus.PUSHED
    assert code_run.story_id == story.id
    assert code_run.completed_at is not None
    assert any(c[:2] == ["git", "clone"] for c in calls)
    assert any(c[:3] == ["git", "checkout", "-b"] for c in calls)
    assert any(c[0] == "pytest" for c in calls)
    assert any(c[:2] == ["git", "commit"] for c in calls)
    assert any(c[:2] == ["git", "push"] for c in calls)

    implementation_node = db.get(StoryDeliveryNode, nodes["IMPLEMENTATION"].id)
    assert implementation_node.status == StoryDeliveryNodeStatus.COMPLETED
    test_scenarios_node = db.get(StoryDeliveryNode, nodes["TEST_SCENARIOS"].id)
    assert test_scenarios_node.status == StoryDeliveryNodeStatus.READY  # "moved toward the GitHub PR stage"

    fetched = get_code_run(code_run.id, db)
    assert fetched.id == code_run.id


def test_apply_via_code_runner_never_targets_the_default_branch(db, project, actor, tmp_path, monkeypatch):
    story, lane, nodes, task, run, repository = _story_with_accepted_run(db, project, actor)
    calls = _mock_git_success(monkeypatch, tmp_path)
    # Force a collision — the generated branch name happens to equal "main".
    # Patched where it's looked up (story_code_implementation imports the
    # name directly), not on code_runner's own module namespace.
    import app.services.story_code_implementation as story_code_impl_module

    monkeypatch.setattr(story_code_impl_module, "generate_branch_name", lambda title, run_id: "main")

    code_run = apply_via_code_runner(ApplyViaCodeRunnerRequest(implementation_run_id=run.id, triggered_by_user_id=actor.id), db)

    assert code_run.status == CodeRunStatus.FAILED
    assert "base/default branch" in (code_run.error_message or "")
    assert not any(c[:2] == ["git", "push"] for c in calls)
    implementation_node = db.get(StoryDeliveryNode, nodes["IMPLEMENTATION"].id)
    assert implementation_node.status != StoryDeliveryNodeStatus.COMPLETED


# --- Rule: tests fail -> keep logs, mark CodeRun failed, stop ------------------------------


def test_failing_test_command_marks_code_run_failed_and_never_commits_or_pushes(db, project, actor, tmp_path, monkeypatch):
    story, lane, nodes, task, run, repository = _story_with_accepted_run(db, project, actor)
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "CODE_RUNNER_WORKSPACE_ROOT", tmp_path)
    calls = []

    def _fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "pytest":
            return _FakeCompletedProcess(returncode=1, stdout="", stderr="1 failed")
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(code_runner_module.subprocess, "run", _fake_run)

    code_run = apply_via_code_runner(
        ApplyViaCodeRunnerRequest(implementation_run_id=run.id, triggered_by_user_id=actor.id, test_commands=["pytest -q"]), db,
    )

    assert code_run.status == CodeRunStatus.FAILED
    assert code_run.error_message
    assert code_run.logs  # rule: logs are kept, never cleared
    assert not any(c[:2] == ["git", "commit"] for c in calls)
    assert not any(c[:2] == ["git", "push"] for c in calls)
    implementation_node = db.get(StoryDeliveryNode, nodes["IMPLEMENTATION"].id)
    assert implementation_node.status != StoryDeliveryNodeStatus.COMPLETED
