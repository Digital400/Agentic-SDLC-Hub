"""The Implementation stage's own coding-tool skill — a slash command that
opens a real pull request, not a docs/sdlc document (see
app/services/implementation_skill.py and app/services/coding_tool_skills.py's
build_skill_pack dispatch to it for stage="implementation")."""

import uuid

import pytest

import app.api.routes.coding_tools as coding_tools_routes
from app.api.routes.coding_tools import sync_implementation_task_inputs
from app.api.routes.stories import create_story, create_story_lane
from app.models import (
    ImplementationTaskArea,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    Repository,
    Story,
    StoryDeliveryLane,
    StoryType,
    User,
    UserRole,
)
from app.schemas.coding_tools import SyncImplementationTaskInputsRequest
from app.schemas.story import CreateStoryLaneRequest, StoryCreate
from app.services.coding_tool_skills import TOOLS, build_skill_pack
from app.services.github_integration import GitHubIntegrationError
from tests.conftest import make_approved_artifact, make_implementation_task, make_node


def _dev(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="User", role=UserRole.DEVELOPER)
    db.add(user)
    db.flush()
    return user


def _story_with_task(db, project, actor, *, with_lld=True, with_plan=True):
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content="## Story: X\n")
    story_read = create_story(
        StoryCreate(project_id=project.id, title="Decide API Gateway Routing", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id),
        db,
    )
    create_story_lane(story_read.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    story = db.get(Story, story_read.id)
    lane = db.query(StoryDeliveryLane).filter(StoryDeliveryLane.story_id == story.id).first()

    if with_lld:
        from app.services.story_lld_agent import STORY_LLD_ARTIFACT_TYPE
        from app.models import StoryArtifact

        lld_node = next(n for n in lane.nodes if n.node_key == "STORY_LLD")
        db.add(StoryArtifact(story_id=story.id, lane_id=lane.id, node_id=lld_node.id, artifact_type=STORY_LLD_ARTIFACT_TYPE, title="Story LLD", content_markdown="## Approach\nUse a shared gateway.\n", version_number=1, created_by_id=actor.id))
    if with_plan:
        from app.services.story_implementation_plan_agent import STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE
        from app.models import StoryArtifact

        plan_node = next(n for n in lane.nodes if n.node_key == "IMPLEMENTATION_PLAN")
        db.add(StoryArtifact(story_id=story.id, lane_id=lane.id, node_id=plan_node.id, artifact_type=STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE, title="Implementation Plan", content_markdown="## Database/Migration Tasks\nAdd routing table.\n", version_number=1, created_by_id=actor.id))
    db.flush()

    planning_node = make_node(db, project, node_key="implementation_planning", order_index=2, output_artifact_type="implementation_plan")
    plan_artifact = make_approved_artifact(db, project, planning_node, actor, content="# Plan")
    task = make_implementation_task(
        db, project, planning_node, plan_artifact,
        title="Decide routing approach — Database", description="Add the gateway routing table and migration.",
        area=ImplementationTaskArea.DATABASE, expected_paths=["apps/api/alembic/versions/xyz.py"],
        acceptance_criteria=["A routing table exists", "Migration runs cleanly"],
        test_expectation="Run alembic upgrade head against a scratch DB.",
        story_id=story.id,
    )
    return story, task


def _repo(db, project):
    integration = Integration(integration_name="GitHub", provider=IntegrationProvider.GITHUB, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(integration=integration, access_token_encrypted="x", token_last_four="1234", github_username="octocat", status=IntegrationStatus.CONNECTED)
    db.add(connection)
    db.flush()
    repo = Repository(project=project, connection=connection, owner="octocat", name="app", default_branch="main", is_primary=True)
    db.add(repo)
    db.flush()
    return repo


# --- build_skill_pack dispatches here for stage="implementation" -----------------------


def test_build_skill_pack_for_implementation_for_every_tool(db, project):
    for tool in TOOLS:
        pack = build_skill_pack(db, project, tool=tool, stage="implementation")
        assert pack.stage == "implementation"
        assert pack.files
        joined = "\n".join(f.path for f in pack.files)
        assert "implementation" in joined.lower()


def test_implementation_pack_never_mentions_a_docs_sdlc_output_document(db, project):
    """Implementation's own command never tells the developer to write a
    docs/sdlc/<stage>.md file — unlike every other stage, its output is a
    pull request (see app/services/implementation_skill.py's docstring)."""
    pack = build_skill_pack(db, project, tool="claude_code", stage="implementation")
    command = next(f for f in pack.files if f.path == ".claude/commands/implementation.md")
    assert "pull request" in command.content.lower()
    assert "register" in command.content.lower()


def test_claude_code_pack_includes_a_push_guard_hook_and_reviewer_subagent(db, project):
    pack = build_skill_pack(db, project, tool="claude_code", stage="implementation")
    paths = {f.path for f in pack.files}
    assert ".sdlc/hooks/guard-implementation.mjs" in paths
    assert ".claude/agents/sdlc-reviewer-implementation.md" in paths
    assert ".claude/settings.json" in paths
    hook = next(f for f in pack.files if f.path == ".sdlc/hooks/guard-implementation.mjs")
    assert "main" in hook.content  # the test project's repo default_branch, baked in


def test_every_tool_requires_real_error_handling_not_just_happy_path(db, project):
    """Regression: a user reported the skill's generated code had no real
    error/exception handling (no try/catch). It must be stated explicitly
    in the command's procedure, its guardrails, its rules, AND the reviewer
    (where one exists) — a vague "follow coding standards" line wasn't
    enough for the coding tool to actually act on it."""
    for tool in TOOLS:
        pack = build_skill_pack(db, project, tool=tool, stage="implementation")
        command = next(f for f in pack.files if "implementation" in f.path and f.path.endswith((".md",)) and "reviewer" not in f.path and "agent" not in f.path)
        assert "error" in command.content.lower() and "try/catch" in command.content.lower()

    reviewer_claude = next(f for f in build_skill_pack(db, project, tool="claude_code", stage="implementation").files if "reviewer" in f.path)
    assert "error" in reviewer_claude.content.lower()
    reviewer_opencode = next(f for f in build_skill_pack(db, project, tool="opencode", stage="implementation").files if "reviewer" in f.path)
    assert "error" in reviewer_opencode.content.lower()


# --- Per-task input snapshot -------------------------------------------------------------


def test_task_input_snapshot_includes_task_and_approved_upstream(db, project, actor):
    from app.services.implementation_skill import build_implementation_task_input_snapshot

    story, task = _story_with_task(db, project, actor)
    snapshot = build_implementation_task_input_snapshot(db, project=project, story=story, task=task)

    assert snapshot.not_ready == []
    assert task.title in snapshot.file.content
    assert "Add the gateway routing table" in snapshot.file.content
    assert "apps/api/alembic/versions/xyz.py" in snapshot.file.content
    assert "A routing table exists" in snapshot.file.content
    assert "Use a shared gateway" in snapshot.file.content  # Story LLD content
    assert "routing table" in snapshot.file.content.lower()  # Implementation Plan content
    assert snapshot.file.path == f"docs/sdlc/stories/decide-api-gateway-routing/inputs/implementation/database-task-context.md"


def test_task_input_snapshot_flags_not_yet_available_upstream(db, project, actor):
    from app.services.implementation_skill import build_implementation_task_input_snapshot

    story, task = _story_with_task(db, project, actor, with_lld=False, with_plan=False)
    snapshot = build_implementation_task_input_snapshot(db, project=project, story=story, task=task)

    assert set(snapshot.not_ready) == {"Story LLD", "Implementation Plan"}
    assert "not yet available" in snapshot.file.content.lower()


def test_task_input_snapshot_says_no_pr_exists_yet_for_a_fresh_story(db, project, actor):
    """Regression — the root cause of "too many PRs, each depending on the
    previous": without this, the /implementation command had no way to
    know a sibling area's PR already existed and opened a brand-new one
    every time, producing three separate, stacked, dependent PRs for one
    story instead of one shared PR."""
    from app.services.implementation_skill import build_implementation_task_input_snapshot

    story, task = _story_with_task(db, project, actor)
    snapshot = build_implementation_task_input_snapshot(db, project=project, story=story, task=task)

    assert "none yet" in snapshot.file.content.lower()
    assert "create a new branch and open a new pull request" in snapshot.file.content.lower()


def test_task_input_snapshot_names_an_existing_open_pr_for_a_sibling_task(db, project, actor):
    from app.models import PullRequestLink, PullRequestStatus
    from app.services.implementation_skill import build_implementation_task_input_snapshot

    story, database_task = _story_with_task(db, project, actor)
    repo = _repo(db, project)
    db.add(
        PullRequestLink(
            project=project, implementation_task_id=database_task.id, implementation_run_id=uuid.uuid4(), repository_id=repo.id,
            story_id=story.id, branch_name="feature/gateway-routing", base_branch="main", pr_number=1265,
            pr_url="https://github.com/octocat/app/pull/1265", status=PullRequestStatus.OPEN, commit_message="x",
        )
    )
    db.flush()

    backend_task = make_implementation_task(
        db, project, database_task.workflow_node, database_task.artifact, title="Backend routing", area=ImplementationTaskArea.BACKEND,
        story_id=story.id,
    )

    snapshot = build_implementation_task_input_snapshot(db, project=project, story=story, task=backend_task)

    assert "#1265" in snapshot.file.content
    assert "feature/gateway-routing" in snapshot.file.content
    assert "do not create a new branch" in snapshot.file.content.lower()


# --- Route: commit the snapshot via a PR --------------------------------------------------


def test_sync_implementation_task_inputs_commits_via_a_pr(db, project, actor, monkeypatch):
    story, task = _story_with_task(db, project, actor)
    _repo(db, project)

    monkeypatch.setattr(coding_tools_routes, "decrypt_repository_token", lambda repo: "tok")
    from app.services import github_integration as github_api

    monkeypatch.setattr(github_api, "list_branches", lambda token, o, r, **kw: ["main"])

    def _not_found(*a, **kw):
        raise GitHubIntegrationError("Not Found", status_code=404)

    monkeypatch.setattr(github_api, "read_file", _not_found)
    calls = {"branch": [], "files": {}, "pr": 0}
    monkeypatch.setattr(github_api, "create_branch", lambda token, o, r, *, new_branch, base_ref, **kw: calls["branch"].append(new_branch))

    def _create_file(token, o, r, path, *, content, message, branch, sha=None, **kw):
        calls["files"][path] = content
        return "c"

    monkeypatch.setattr(github_api, "create_or_update_file", _create_file)

    class PR:
        html_url = "https://github.com/octocat/app/pull/11"

    def _create_pr(*a, **kw):
        calls["pr"] += 1
        return PR()

    monkeypatch.setattr(github_api, "create_pull_request", _create_pr)

    result = sync_implementation_task_inputs(
        project.id, task.id, SyncImplementationTaskInputsRequest(triggered_by_user_id=actor.id), db
    )

    assert result.pull_request_url == "https://github.com/octocat/app/pull/11"
    assert result.committed == ["docs/sdlc/stories/decide-api-gateway-routing/inputs/implementation/database-task-context.md"]
    assert calls["branch"] and calls["pr"] == 1
    assert result.not_ready == []


def test_sync_implementation_task_inputs_rejects_a_project_level_task(db, project, actor):
    node = make_node(db, project, node_key="implementation_planning", order_index=2, output_artifact_type="implementation_plan")
    plan_artifact = make_approved_artifact(db, project, node, actor, content="# Plan")
    task = make_implementation_task(db, project, node, plan_artifact)  # story_id defaults to None here

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        sync_implementation_task_inputs(project.id, task.id, SyncImplementationTaskInputsRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 400
