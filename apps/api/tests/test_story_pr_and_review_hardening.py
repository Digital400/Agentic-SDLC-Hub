"""Hardening fix — story-scoped PR creation and PR Review Agent runs.

Before this fix, app/api/routes/implementation_runs.py's create_pull_request
and app/api/routes/pr_review_runs.py's start_pr_review_run both looked up a
project-level WorkflowNode row scoped by `story_id` — but a story delivery
lane's PULL_REQUEST/PR_REVIEW_AGENT stages live entirely in StoryDeliveryNode
(a different table; see app/services/story_delivery.py), so that lookup
always returned None for a story-scoped task and both routes 400'd on every
single call. This file proves the fix: a story-scoped implementation run can
reach an open PR and a completed PR Review Agent run, exactly like the
project-level path already could.

Covers requirement 4 of "Harden the Scrum-style story delivery workflow" —
each story has independent LLD, implementation, PR review, testing, and QA
approval — for the PR review leg specifically, the one this session found
broken.
"""

import uuid

import pytest

from app.api.routes.implementation_runs import create_pull_request, review_implementation_run, start_implementation_run
from app.api.routes.pr_review_runs import start_pr_review_run
from app.api.routes.stories import create_story, create_story_lane, draft_story_lld, update_lane_node_status
from app.models import (
    AgentDefinition,
    ImplementationRun,
    ImplementationTask,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    PRReviewRun,
    PullRequestLink,
    Repository,
    RepositoryFileEntryType,
    RepositoryFileIndex,
    RepositorySnapshot,
    Story,
    StoryDeliveryLane,
    StoryType,
    User,
    UserRole,
)
from app.schemas.implementation_run import CreatePullRequestRequest, ReviewImplementationRunRequest, StartImplementationRunRequest
from app.schemas.pr_review_run import StartPRReviewRunRequest
from app.schemas.story import CreateStoryLaneRequest, DraftStoryLldRequest, StoryCreate, UpdateLaneNodeStatusRequest
from app.services import ai_generation, implementation_agent, pr_review_agent
from app.services import github_integration as github_api
from app.services.story_lld_agent import STORY_LLD_AGENT_KEY
from tests.conftest import make_agent_prompt, make_approved_artifact, make_node

SAMPLE_HLD = "# HLD\n\n## Architecture\nSome design.\n"


@pytest.fixture(autouse=True)
def _force_mock_providers(monkeypatch):
    monkeypatch.setattr(implementation_agent, "get_active_provider", lambda: "mock")
    monkeypatch.setattr(ai_generation, "get_active_provider", lambda: "mock")
    monkeypatch.setattr(pr_review_agent, "get_active_provider", lambda: "mock")


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
    return repository


def _mock_github(monkeypatch):
    import app.api.routes.implementation_runs as impl_routes
    import app.api.routes.pr_review_runs as review_routes

    monkeypatch.setattr(impl_routes, "decrypt_repository_token", lambda repo: "fake-token")
    monkeypatch.setattr(review_routes, "decrypt_repository_token", lambda repo: "fake-token")
    monkeypatch.setattr(impl_routes.github_api, "create_branch", lambda *a, **kw: "base-sha")
    monkeypatch.setattr(impl_routes.github_api, "get_file_sha", lambda *a, **kw: None)
    monkeypatch.setattr(impl_routes.github_api, "create_or_update_file", lambda *a, **kw: "commit-sha")
    monkeypatch.setattr(impl_routes.github_api, "delete_file", lambda *a, **kw: "commit-sha")
    monkeypatch.setattr(
        impl_routes.github_api, "create_pull_request",
        lambda *a, **kw: github_api.GitHubPullRequest(number=7, html_url="https://github.com/octocat/hello-world/pull/7", state="open"),
    )
    monkeypatch.setattr(
        review_routes.github_api, "get_pull_request",
        lambda *a, **kw: github_api.GitHubPullRequestDetail(
            number=7, title="PR", body="", html_url="https://github.com/octocat/hello-world/pull/7", state="open",
        ),
    )


def _ensure_project_approved(db, project, actor):
    """Story Crafting + HLD approved — the project-level gate every lane
    creation must pass. Idempotent so several stories in the same test
    can share one project-level setup (WorkflowNode has a unique
    (project_id, node_key, story_id) constraint — calling make_node twice
    for the same project-level stage would violate it)."""
    from app.models import Artifact, ArtifactStatus, WorkflowNode

    if db.query(WorkflowNode).filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == "story_crafting").first() is None:
        story_crafting_node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
        make_approved_artifact(db, project, story_crafting_node, actor, content="## Story: X\n")
        hld_node = make_node(db, project, node_key="hld", order_index=1, output_artifact_type="hld_document")
        make_approved_artifact(db, project, hld_node, actor, content=SAMPLE_HLD)


def _story_with_accepted_run(db, project, actor, *, title: str = "Add reset endpoint"):
    _ensure_project_approved(db, project, actor)

    story = create_story(
        StoryCreate(project_id=project.id, title=title, mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id),
        db,
    )
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)

    lane = db.query(StoryDeliveryLane).filter(StoryDeliveryLane.story_id == story.id).first()
    nodes = {n.node_key: n for n in lane.nodes}
    update_lane_node_status(nodes["STORY_READY"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)
    draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)
    tech_lead = _tech_lead(db)
    update_lane_node_status(nodes["LLD_REVIEW"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db)

    story_row = db.get(Story, story.id)
    task = db.query(ImplementationTask).filter(ImplementationTask.story_id == story_row.id).first()
    _add_repository(db, project)

    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    run = review_implementation_run(run.id, ReviewImplementationRunRequest(decision="ACCEPTED", reviewed_by_user_id=actor.id), db)
    return story_row, lane, nodes, task, run


# --- The actual regression this file guards against ---------------------------------------


def test_story_scoped_pr_creation_succeeds(db, project, actor, monkeypatch):
    """Before the fix: this always 400'd — 'no implementation stage' —
    for a story-scoped run, because it looked up a project-level
    WorkflowNode that never exists for one."""
    story_row, lane, nodes, task, run = _story_with_accepted_run(db, project, actor)
    _mock_github(monkeypatch)

    result = create_pull_request(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)

    assert result.pull_request is not None
    link = db.query(PullRequestLink).filter(PullRequestLink.implementation_run_id == run.id).first()
    assert link is not None
    assert link.story_id == story_row.id
    assert link.workflow_node_id is None  # no real project-level node for a story-scoped PR


def test_story_scoped_pr_review_run_succeeds(db, project, actor, monkeypatch):
    """Before the fix: this always 400'd — 'no pr_review stage' — for the
    exact same reason as PR creation above."""
    story_row, lane, nodes, task, run = _story_with_accepted_run(db, project, actor)
    _mock_github(monkeypatch)
    create_pull_request(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)

    result = start_pr_review_run(StartPRReviewRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)

    assert result.status.value == "COMPLETED"
    run_row = db.get(PRReviewRun, result.id)
    assert run_row.story_id == story_row.id
    assert run_row.workflow_node_id is None  # no real project-level node for a story-scoped review


def test_two_stories_get_independent_pr_review_runs(db, project, actor, monkeypatch):
    """Requirement 3/4 — parallel lanes, each with its own independent PR
    review, never colliding on a shared project-level node."""
    _mock_github(monkeypatch)

    story_a, lane_a, nodes_a, task_a, run_a = _story_with_accepted_run(db, project, actor, title="Story A")
    create_pull_request(run_a.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)
    result_a = start_pr_review_run(StartPRReviewRunRequest(implementation_task_id=task_a.id, triggered_by_user_id=actor.id), db)

    story_b, lane_b, nodes_b, task_b, run_b = _story_with_accepted_run(db, project, actor, title="Story B")
    create_pull_request(run_b.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)
    result_b = start_pr_review_run(StartPRReviewRunRequest(implementation_task_id=task_b.id, triggered_by_user_id=actor.id), db)

    assert result_a.id != result_b.id
    row_a, row_b = db.get(PRReviewRun, result_a.id), db.get(PRReviewRun, result_b.id)
    assert row_a.story_id == story_a.id
    assert row_b.story_id == story_b.id
    assert row_a.implementation_task_id != row_b.implementation_task_id
