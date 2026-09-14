"""Full-stack-per-story implementation — a story's approved Implementation
Plan can call for more than one area of work (DATABASE/BACKEND/FRONTEND),
and this app should implement all of them, not just one best-guessed area:

  1. app/services/implementation_planner.py's infer_story_task_areas reads
     the plan's own Database/Migration, Backend, and Frontend Tasks
     sections and returns only the areas with real content, in
     DATABASE -> BACKEND -> FRONTEND order.
  2. app/api/routes/stories.py's _ensure_story_implementation_tasks creates
     one ImplementationTask per inferred area (falling back to the single
     heuristic-area task when the plan has no recognizable sections —
     never zero tasks for a story).
  3. app/api/routes/implementation_runs.py's start_implementation_run
     refuses to start a task before every earlier-ordered sibling task has
     an Accepted run, and passes those siblings' accepted work forward as
     extra agent context.
  4. Every sibling task's accepted run lands on the SAME shared PR (see
     _get_open_task_pull_request) — one PR per story, not one per area.
  5. get_story_implementation_task (singular) always resolves to "the
     current task" — walking DATABASE, then BACKEND, then FRONTEND
     automatically as each is Accepted — so every existing single-task UI
     (Implementation tab, PR Review, Testing) keeps working unmodified.

Reuses test_story_implementation.py's exact lane-setup convention (plain
functions, direct route calls, no TestClient).
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.implementation_runs import create_pull_request, review_implementation_run, start_implementation_run
from app.api.routes.stories import (
    create_story,
    create_story_lane,
    draft_story_lld,
    get_story_implementation_task,
    get_story_implementation_tasks,
    update_lane_node_status,
)
from app.models import (
    ImplementationRunReviewStatus,
    ImplementationTask,
    ImplementationTaskArea,
    ImplementationTaskStatus,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    PullRequestLink,
    Repository,
    RepositoryFileEntryType,
    RepositoryFileIndex,
    RepositorySnapshot,
    Story,
    StoryArtifact,
    StoryDeliveryLane,
    StoryType,
    User,
    UserRole,
)
from app.schemas.implementation_run import CreatePullRequestRequest, ReviewImplementationRunRequest, StartImplementationRunRequest
from app.schemas.story import CreateStoryLaneRequest, DraftStoryLldRequest, StoryCreate, UpdateLaneNodeStatusRequest
from app.services import ai_generation, implementation_agent
from app.services.implementation_planner import infer_story_task_areas
from tests.conftest import make_agent_prompt, make_approved_artifact, make_node
from tests.test_pull_request_creation import _mock_github
from tests.test_story_implementation import _tech_lead

SAMPLE_HLD = "# HLD\n\n## Architecture\nSome design.\n"

FULL_STACK_PLAN = (
    "## Implementation Summary\nAdd a loyalty points balance.\n\n"
    "## Database/Migration Tasks\n- Add a `points_balance` integer column to `users`.\n\n"
    "## Backend Tasks\n- Add GET /users/{id}/points endpoint.\n\n"
    "## Frontend Tasks\n- Show the points balance on the profile page.\n"
)

BACKEND_ONLY_PLAN = (
    "## Implementation Summary\nAdd a health check endpoint.\n\n"
    "## Database/Migration Tasks\nNone.\n\n"
    "## Backend Tasks\n- Add GET /health.\n\n"
    "## Frontend Tasks\nN/A.\n"
)

NO_SECTIONS_PLAN = "## Implementation Summary\nPlan.\n"


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(implementation_agent, "get_active_provider", lambda: "mock")
    monkeypatch.setattr(ai_generation, "get_active_provider", lambda: "mock")


@pytest.fixture(autouse=True)
def _story_lld_agent(db):
    from app.models import AgentDefinition
    from app.services.story_lld_agent import STORY_LLD_AGENT_KEY

    agent = AgentDefinition(agent_key=STORY_LLD_AGENT_KEY, name="Story LLD Agent", model_name="mock")
    db.add(agent)
    db.flush()
    make_agent_prompt(db, stage="story_lld", agent=agent)


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


def _story_with_plan(db, project, actor, *, plan_markdown: str):
    """Walks a fresh story's lane all the way through IMPLEMENTATION_PLAN
    completion — the point _ensure_story_implementation_tasks actually
    runs — with a caller-supplied plan document, so each test controls
    exactly which areas the plan calls for."""
    story_crafting_node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, story_crafting_node, actor, content="## Story: X\n")
    hld_node = make_node(db, project, node_key="hld", order_index=1, output_artifact_type="hld_document")
    make_approved_artifact(db, project, hld_node, actor, content=SAMPLE_HLD)

    story = create_story(
        StoryCreate(project_id=project.id, title="Add loyalty points", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id),
        db,
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
            artifact_type="story_implementation_plan", title="Implementation Plan", content_markdown=plan_markdown,
            version_number=1, created_by_id=tech_lead.id,
        )
    )
    db.flush()
    update_lane_node_status(nodes["IMPLEMENTATION_PLAN"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db)

    return db.get(Story, story.id)


def _tasks(db, story) -> list[ImplementationTask]:
    return db.query(ImplementationTask).filter(ImplementationTask.story_id == story.id).order_by(ImplementationTask.order_index).all()


def _run_and_accept(db, task, actor):
    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    return review_implementation_run(run.id, ReviewImplementationRunRequest(decision="ACCEPTED", reviewed_by_user_id=actor.id), db)


# --- infer_story_task_areas (pure function) -------------------------------------------------


def test_infer_story_task_areas_finds_every_area_with_real_content():
    areas = infer_story_task_areas(FULL_STACK_PLAN)
    assert areas == [ImplementationTaskArea.DATABASE, ImplementationTaskArea.BACKEND, ImplementationTaskArea.FRONTEND]


def test_infer_story_task_areas_skips_none_and_na_sections():
    areas = infer_story_task_areas(BACKEND_ONLY_PLAN)
    assert areas == [ImplementationTaskArea.BACKEND]


def test_infer_story_task_areas_returns_empty_for_a_plan_with_no_recognizable_sections():
    assert infer_story_task_areas(NO_SECTIONS_PLAN) == []


# --- Task creation ---------------------------------------------------------------------------


def test_multi_area_plan_creates_tasks_in_database_backend_frontend_order(db, project, actor):
    story = _story_with_plan(db, project, actor, plan_markdown=FULL_STACK_PLAN)
    tasks = _tasks(db, story)

    assert [t.area for t in tasks] == [ImplementationTaskArea.DATABASE, ImplementationTaskArea.BACKEND, ImplementationTaskArea.FRONTEND]
    assert [t.order_index for t in tasks] == [0, 1, 2]
    assert all(t.story_id == story.id for t in tasks)
    # Multi-area tasks get area-suffixed titles so they're distinguishable.
    assert tasks[0].title != tasks[1].title != tasks[2].title


def test_single_area_plan_creates_exactly_one_task_with_the_plain_story_title(db, project, actor):
    story = _story_with_plan(db, project, actor, plan_markdown=BACKEND_ONLY_PLAN)
    tasks = _tasks(db, story)

    assert len(tasks) == 1
    assert tasks[0].area == ImplementationTaskArea.BACKEND
    assert tasks[0].title == story.title


def test_plan_with_no_recognizable_sections_falls_back_to_a_single_heuristic_task(db, project, actor):
    """Rule 10 / backward compatibility — a plan that doesn't follow the
    expected heading structure (or every existing test's plain
    "## Implementation Summary\\nPlan.\\n" stub) must still produce
    exactly the one task this app always created before this feature."""
    story = _story_with_plan(db, project, actor, plan_markdown=NO_SECTIONS_PLAN)
    tasks = _tasks(db, story)

    assert len(tasks) == 1
    assert tasks[0].title == story.title


def test_ensure_story_implementation_tasks_is_idempotent(db, project, actor):
    story = _story_with_plan(db, project, actor, plan_markdown=FULL_STACK_PLAN)
    first_ids = {t.id for t in _tasks(db, story)}

    # Re-completing IMPLEMENTATION_PLAN (e.g. a rework/re-approve cycle)
    # must never create a second set of tasks.
    from app.api.routes.stories import _ensure_story_implementation_tasks

    tech_lead = _tech_lead(db)
    _ensure_story_implementation_tasks(db, story=story, created_by=tech_lead)
    assert {t.id for t in _tasks(db, story)} == first_ids


# --- Sequencing gate ---------------------------------------------------------------------------


def test_backend_task_is_blocked_until_database_task_is_accepted(db, project, actor):
    story = _story_with_plan(db, project, actor, plan_markdown=FULL_STACK_PLAN)
    _add_repository(db, project)
    database_task, backend_task, frontend_task = _tasks(db, story)

    with pytest.raises(HTTPException) as exc_info:
        start_implementation_run(StartImplementationRunRequest(implementation_task_id=backend_task.id, triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "DATABASE" in exc_info.value.detail

    _run_and_accept(db, database_task, actor)
    db.refresh(backend_task)
    assert database_task.status == ImplementationTaskStatus.COMPLETED

    # Now unblocked.
    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=backend_task.id, triggered_by_user_id=actor.id), db)
    assert run.status.value == "COMPLETED"

    # Frontend still blocked — only its immediately-earlier sibling being
    # done isn't enough if an even-earlier one weren't (it is here, but
    # frontend itself isn't accepted yet in this test) — checked directly:
    with pytest.raises(HTTPException) as exc_info:
        start_implementation_run(StartImplementationRunRequest(implementation_task_id=frontend_task.id, triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "BACKEND" in exc_info.value.detail


def test_prior_story_task_context_is_passed_to_the_next_agent(db, project, actor):
    story = _story_with_plan(db, project, actor, plan_markdown=FULL_STACK_PLAN)
    _add_repository(db, project)
    database_task, backend_task, _frontend_task = _tasks(db, story)
    _run_and_accept(db, database_task, actor)

    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=backend_task.id, triggered_by_user_id=actor.id), db)

    assert "already-accepted task" in run.explanation


# --- Shared PR across sibling tasks --------------------------------------------------------


def test_sibling_tasks_share_one_pull_request(db, project, actor, monkeypatch):
    story = _story_with_plan(db, project, actor, plan_markdown=FULL_STACK_PLAN)
    _add_repository(db, project)
    database_task, backend_task, _frontend_task = _tasks(db, story)
    calls = _mock_github(monkeypatch)

    database_run = _run_and_accept(db, database_task, actor)
    first_pr = create_pull_request(database_run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)
    calls.clear()

    backend_run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=backend_task.id, triggered_by_user_id=actor.id), db)
    backend_run = review_implementation_run(backend_run.id, ReviewImplementationRunRequest(decision="ACCEPTED", reviewed_by_user_id=actor.id), db)
    second_pr = create_pull_request(backend_run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)

    assert db.query(PullRequestLink).count() == 1
    assert second_pr.pull_request.pr_number == first_pr.pull_request.pr_number
    assert second_pr.pull_request.pr_url == first_pr.pull_request.pr_url
    link = db.query(PullRequestLink).first()
    assert link.implementation_task_id == backend_task.id  # repointed to whichever task most recently landed
    assert link.story_id == story.id
    # No second create_branch/create_pull_request call.
    assert not [c for c in calls if c[0] in ("create_branch", "create_pull_request")]


# --- "current task" resolution (drives every existing single-task UI) ---------------------


def test_get_story_implementation_task_walks_the_sequence(db, project, actor):
    story = _story_with_plan(db, project, actor, plan_markdown=FULL_STACK_PLAN)
    _add_repository(db, project)
    database_task, backend_task, frontend_task = _tasks(db, story)

    current = get_story_implementation_task(story.id, db)
    assert current.id == database_task.id

    _run_and_accept(db, database_task, actor)
    current = get_story_implementation_task(story.id, db)
    assert current.id == backend_task.id

    _run_and_accept(db, backend_task, actor)
    current = get_story_implementation_task(story.id, db)
    assert current.id == frontend_task.id

    _run_and_accept(db, frontend_task, actor)
    # Every task now COMPLETED — resolves to the last one, not an error.
    current = get_story_implementation_task(story.id, db)
    assert current.id == frontend_task.id


def test_get_story_implementation_tasks_returns_the_full_ordered_set(db, project, actor):
    story = _story_with_plan(db, project, actor, plan_markdown=FULL_STACK_PLAN)
    tasks = get_story_implementation_tasks(story.id, db)
    assert [t.area for t in tasks] == [ImplementationTaskArea.DATABASE, ImplementationTaskArea.BACKEND, ImplementationTaskArea.FRONTEND]
