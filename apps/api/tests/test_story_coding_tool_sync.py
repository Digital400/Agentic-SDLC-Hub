"""Coding-tool skills for the per-story delivery lane's generic document
stages (Story LLD, Implementation Plan, Test Scenarios) — see
app/services/story_coding_tool_sync.py and coding_tool_skills.py's
StageSpec.story_scoped."""

import uuid

import pytest
from fastapi import HTTPException

import app.api.routes.coding_tools as coding_tools_routes
from app.api.routes.coding_tools import sync_story_inputs, sync_story_stage
from app.api.routes.stories import create_story, create_story_lane
from app.models import (
    Story,
    StoryArtifact,
    StoryDeliveryLane,
    StoryDeliveryNode,
    StoryDeliveryNodeStatus,
    StoryType,
    User,
    UserRole,
)
from app.schemas.coding_tools import SyncStoryInputsRequest, SyncStoryStageRequest
from app.schemas.story import CreateStoryLaneRequest, StoryCreate
from app.services.coding_tool_skills import STAGE_SPECS, build_skill_pack
from app.services.story_coding_tool_sync import (
    StoryCodingToolError,
    build_story_input_snapshot,
    story_output_path,
    story_slug,
    sync_story_stage_document,
)
from tests.conftest import make_approved_artifact, make_node


def _dev(db, role=UserRole.DEVELOPER) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="User", role=role)
    db.add(user)
    db.flush()
    return user


def _lane(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content="## Story: X\n")
    story_read = create_story(
        StoryCreate(project_id=project.id, title="Export report as CSV", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id),
        db,
    )
    create_story_lane(story_read.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    story = db.get(Story, story_read.id)  # the real ORM row — create_story returns a StoryRead schema, not this
    lane = db.query(StoryDeliveryLane).filter(StoryDeliveryLane.story_id == story.id).first()
    nodes = {n.node_key: n for n in lane.nodes}
    return story, lane, nodes


def _unlock(db, node, status=StoryDeliveryNodeStatus.READY):
    node.status = status
    db.flush()


# --- StageSpecs are real and reachable via build_skill_pack --------------------------------


def test_story_scoped_stages_are_in_stage_specs():
    for stage in ("story_lld", "story_implementation_plan", "story_test_scenarios"):
        assert STAGE_SPECS[stage].story_scoped is True


def test_generic_install_needs_no_story_and_names_the_story_argument(db, project):
    pack = build_skill_pack(db, project, tool="claude_code", stage="story_lld")
    command = next(f for f in pack.files if f.path == ".claude/commands/story-lld.md").content
    assert "argument-hint: [the story's slug or exact title" in command
    assert "$ARGUMENTS names the story" in command
    assert "docs/sdlc/stories/<story-slug>/inputs/story-context.md" in command
    assert "docs/sdlc/stories/<story-slug>/inputs/hld.md" in command
    # No project/story-specific content baked in — same pack regardless of which story runs it.
    assert not any(f.path.startswith("docs/sdlc/inputs/") for f in pack.files)


def test_implementation_plan_and_test_scenarios_list_their_own_upstream_files(db, project):
    plan = build_skill_pack(db, project, tool="codex", stage="story_implementation_plan")
    plan_prompt = next(f for f in plan.files if f.path == ".sdlc/codex/story-implementation-plan.md").content
    assert "docs/sdlc/stories/<story-slug>/inputs/story-lld.md" in plan_prompt

    tests = build_skill_pack(db, project, tool="opencode", stage="story_test_scenarios")
    tests_command = next(f for f in tests.files if f.path == ".opencode/command/story-test-scenarios.md").content
    assert "docs/sdlc/stories/<story-slug>/inputs/story-lld.md" in tests_command
    assert "docs/sdlc/stories/<story-slug>/inputs/implementation-plan.md" in tests_command


# --- build_story_input_snapshot -------------------------------------------------------------


def test_story_lld_snapshot_includes_context_and_hld(db, project, actor):
    story, lane, nodes = _lane(db, project, actor)
    hld_node = make_node(db, project, node_key="hld", order_index=1, output_artifact_type="hld_document")
    make_approved_artifact(db, project, hld_node, actor, content="# HLD\n\nSome design.\n")

    snapshot = build_story_input_snapshot(db, project=project, story=story, stage="story_lld")
    files = snapshot.files
    by_path = {f.path: f for f in files}
    slug = story_slug(story.title)
    assert set(by_path) == {f"docs/sdlc/stories/{slug}/inputs/story-context.md", f"docs/sdlc/stories/{slug}/inputs/hld.md"}
    assert "Export report as CSV" in by_path[f"docs/sdlc/stories/{slug}/inputs/story-context.md"].content
    assert "Some design." in by_path[f"docs/sdlc/stories/{slug}/inputs/hld.md"].content


def test_story_lld_snapshot_reads_hld_delta_on_the_existing_project_feature_template(db, project, actor):
    """Regression — a project on the existing-project-feature template has
    no "hld" node at all, only "hld_delta". The snapshot (and the in-app
    agent's own precondition, see test_story_lld_agent_prompt.py) must find
    that instead of reporting "not yet available" forever."""
    story, lane, nodes = _lane(db, project, actor)
    hld_delta_node = make_node(db, project, node_key="hld_delta", order_index=1, output_artifact_type="hld_delta")
    make_approved_artifact(db, project, hld_delta_node, actor, content="# HLD Delta\n\nArchitecture change.\n")

    snapshot = build_story_input_snapshot(db, project=project, story=story, stage="story_lld")
    hld_file = next(f for f in snapshot.files if f.path.endswith("/hld.md"))
    assert "Architecture change." in hld_file.content and "HLD Delta" in hld_file.content
    assert "not yet available" not in hld_file.content
    assert snapshot.not_ready == []


def test_snapshot_flags_a_not_yet_available_upstream_document(db, project, actor):
    story, lane, nodes = _lane(db, project, actor)
    # No HLD approved at all.
    snapshot = build_story_input_snapshot(db, project=project, story=story, stage="story_lld")
    hld_file = next(f for f in snapshot.files if f.path.endswith("/hld.md"))
    assert "not yet available" in hld_file.content
    assert snapshot.not_ready == ["High-Level Design"]


def test_implementation_plan_snapshot_reads_the_story_lld_artifact(db, project, actor):
    story, lane, nodes = _lane(db, project, actor)
    db.add(StoryArtifact(
        story_id=story.id, lane_id=lane.id, node_id=nodes["STORY_LLD"].id, artifact_type="story_lld",
        title="Story LLD", content_markdown="## Story Summary\nExport as CSV.\n", version_number=1, created_by_id=actor.id,
    ))
    db.flush()
    snapshot = build_story_input_snapshot(db, project=project, story=story, stage="story_implementation_plan")
    lld_file = next(f for f in snapshot.files if f.path.endswith("/story-lld.md"))
    assert "Export as CSV." in lld_file.content
    assert snapshot.not_ready == []


def test_implementation_plan_snapshot_flags_story_lld_not_ready(db, project, actor):
    """Regression — this is the exact scenario a user hit: syncing inputs
    for Implementation Plan before Story LLD has ever been drafted/synced
    for this story. The missing input must be named explicitly, not just
    silently written as a placeholder file nobody is told to look for."""
    story, lane, nodes = _lane(db, project, actor)
    snapshot = build_story_input_snapshot(db, project=project, story=story, stage="story_implementation_plan")
    assert snapshot.not_ready == ["Story LLD"]
    lld_file = next(f for f in snapshot.files if f.path.endswith("/story-lld.md"))
    assert "not yet available" in lld_file.content


def test_snapshot_rejects_a_non_story_scoped_stage(db, project, actor):
    story, lane, nodes = _lane(db, project, actor)
    with pytest.raises(StoryCodingToolError):
        build_story_input_snapshot(db, project=project, story=story, stage="requirement_intake")


# --- sync_story_stage_document ---------------------------------------------------------------

LLD_DOC = """---
sdlc_stage: story_lld
project_id: {pid}
story_id: {sid}
status: draft
generated_by: claude-code
---
# Story LLD — Export report as CSV

## Story Summary
Add CSV export.

## Scope
The reports page.

## Out of Scope
Scheduled exports.

## Related HLD Sections
Reporting module.

## File/Folder Structure
```
apps/api/app/api/routes/reports.py [MODIFIED] — add the export endpoint
```

## API Changes
GET /reports/export.

## Database Changes
None.

## Frontend Changes
Export button.

## Business Rules
Only the current filter is exported.

## Validation Rules
None additional.

## Permission Rules
Finance role only.

## Error Handling
Show a toast on failure.

## Logging/Audit Needs
Log every export.

## Dependencies
None.

## Risks
Large exports may be slow.

## Developer Notes
Keep it simple.
"""


def test_story_lld_sync_creates_artifact_and_auto_completes_the_node(db, project, actor):
    story, lane, nodes = _lane(db, project, actor)
    _unlock(db, nodes["STORY_LLD"])

    result = sync_story_stage_document(
        db, project=project, story=story, user=actor, stage="story_lld", markdown=LLD_DOC.format(pid=project.id, sid=story.id),
        source_label="octocat/app@main",
    )

    assert result.created and result.story_artifact.version_number == 1 and result.generated_by == "claude-code"
    assert result.node_status == "COMPLETED"
    db.refresh(nodes["LLD_REVIEW"])
    assert nodes["LLD_REVIEW"].status == StoryDeliveryNodeStatus.READY


def test_story_output_path_resolves_the_story_slug(project):
    spec = STAGE_SPECS["story_lld"]
    assert story_output_path(spec, "export-report-as-csv") == "docs/sdlc/stories/export-report-as-csv/story-lld.md"


def test_sync_rejects_a_document_for_a_different_story(db, project, actor):
    story, lane, nodes = _lane(db, project, actor)
    _unlock(db, nodes["STORY_LLD"])
    wrong = LLD_DOC.format(pid=project.id, sid=uuid.uuid4())
    with pytest.raises(StoryCodingToolError) as exc:
        sync_story_stage_document(db, project=project, story=story, user=actor, stage="story_lld", markdown=wrong, source_label="x")
    assert "different story" in str(exc.value)


def test_sync_rejects_missing_sections(db, project, actor):
    story, lane, nodes = _lane(db, project, actor)
    _unlock(db, nodes["STORY_LLD"])
    broken = LLD_DOC.format(pid=project.id, sid=story.id).replace("## Risks\nLarge exports may be slow.\n\n", "")
    with pytest.raises(StoryCodingToolError) as exc:
        sync_story_stage_document(db, project=project, story=story, user=actor, stage="story_lld", markdown=broken, source_label="x")
    assert 'missing section(s) "## Risks"' in str(exc.value)


def test_sync_into_a_locked_node_is_refused(db, project, actor):
    story, lane, nodes = _lane(db, project, actor)
    assert nodes["STORY_LLD"].status == StoryDeliveryNodeStatus.LOCKED
    with pytest.raises(StoryCodingToolError) as exc:
        sync_story_stage_document(
            db, project=project, story=story, user=actor, stage="story_lld", markdown=LLD_DOC.format(pid=project.id, sid=story.id),
            source_label="x",
        )
    assert "locked" in str(exc.value)


def test_sync_of_implementation_plan_does_not_auto_complete_the_node(db, project, actor):
    """Unlike Story LLD, Implementation Plan and Test Scenarios leave
    completion to an explicit human action — sync must mirror that."""
    story, lane, nodes = _lane(db, project, actor)
    _unlock(db, nodes["IMPLEMENTATION_PLAN"])

    plan_doc = (
        f"---\nsdlc_stage: story_implementation_plan\nproject_id: {project.id}\nstory_id: {story.id}\n---\n"
        "# Implementation Plan\n\n" + "\n\n".join(f"## {h}\nDetail.\n" for h in __import__(
            "app.services.story_implementation_plan_agent", fromlist=["STORY_IMPLEMENTATION_PLAN_SECTIONS"]
        ).STORY_IMPLEMENTATION_PLAN_SECTIONS)
    )
    result = sync_story_stage_document(
        db, project=project, story=story, user=actor, stage="story_implementation_plan", markdown=plan_doc, source_label="x"
    )
    assert result.node_status == "IN_PROGRESS"


def test_sync_no_story_lane_yet_is_a_clear_error(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content="## Story: X\n")
    story_read = create_story(
        StoryCreate(project_id=project.id, title="No lane yet", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id), db
    )
    story = db.get(Story, story_read.id)
    with pytest.raises(StoryCodingToolError) as exc:
        sync_story_stage_document(
            db, project=project, story=story, user=actor, stage="story_lld", markdown=LLD_DOC.format(pid=project.id, sid=story.id),
            source_label="x",
        )
    assert "no delivery lane yet" in str(exc.value)


# --- Routes: sync-inputs / sync (commit-branch-PR machinery) --------------------------------


def _repo(db, project):
    from app.models import Integration, IntegrationConnection, IntegrationProvider, IntegrationStatus, Repository

    integration = Integration(integration_name="GitHub", provider=IntegrationProvider.GITHUB, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(
        integration=integration, access_token_encrypted="x", token_last_four="7890", github_username="octocat", status=IntegrationStatus.CONNECTED
    )
    db.add(connection)
    db.flush()
    repo = Repository(project=project, connection=connection, owner="octocat", name="app", default_branch="main", is_primary=True)
    db.add(repo)
    db.flush()
    return repo


def _patch_github(monkeypatch, *, read_content=None):
    from app.services import github_integration as github_api
    from app.services.github_integration import GitHubFileContent

    calls = {"branch": [], "files": {}, "pr": 0}
    monkeypatch.setattr(coding_tools_routes, "decrypt_repository_token", lambda repo: "tok")
    monkeypatch.setattr(github_api, "list_branches", lambda token, o, r, **kw: ["main"])
    monkeypatch.setattr(github_api, "get_file_sha", lambda token, o, r, path, ref, **kw: None)
    monkeypatch.setattr(github_api, "create_branch", lambda token, o, r, *, new_branch, base_ref, **kw: calls["branch"].append(new_branch))

    def create_or_update_file(token, o, r, path, *, content, message, branch, sha=None, **kw):
        calls["files"][path] = content
        return "c"

    monkeypatch.setattr(github_api, "create_or_update_file", create_or_update_file)

    class PR:
        html_url = "https://github.com/octocat/app/pull/9"

    def create_pr(*a, **kw):
        calls["pr"] += 1
        return PR()

    monkeypatch.setattr(github_api, "create_pull_request", create_pr)
    monkeypatch.setattr(
        github_api, "read_file",
        lambda token, o, r, path, ref, **kw: GitHubFileContent(path=path, sha="s", size=len(read_content or ""), content=read_content, truncated=False, is_binary=False),
    )
    return calls


def _not_found(*a, **kw):
    from app.services.github_integration import GitHubIntegrationError

    raise GitHubIntegrationError("Not Found", status_code=404)


def test_sync_inputs_route_commits_the_snapshot_via_a_pr(db, project, actor):
    story, lane, nodes = _lane(db, project, actor)
    _repo(db, project)

    from unittest.mock import patch

    with patch.object(coding_tools_routes, "decrypt_repository_token", lambda repo: "tok"), \
         patch("app.services.github_integration.list_branches", lambda token, o, r, **kw: ["main"]), \
         patch("app.services.github_integration.read_file", _not_found), \
         patch("app.services.github_integration.create_branch") as create_branch, \
         patch("app.services.github_integration.create_or_update_file") as create_file, \
         patch("app.services.github_integration.create_pull_request") as create_pr:
        create_file.return_value = "c"

        class PR:
            html_url = "https://github.com/octocat/app/pull/9"

        create_pr.return_value = PR()

        result = sync_story_inputs(project.id, story.id, SyncStoryInputsRequest(stage="story_lld", triggered_by_user_id=actor.id), db)

    assert result.pull_request_url == "https://github.com/octocat/app/pull/9"
    slug = story_slug(story.title)
    assert f"docs/sdlc/stories/{slug}/inputs/story-context.md" in result.committed
    assert f"docs/sdlc/stories/{slug}/inputs/hld.md" in result.committed
    assert create_branch.called and create_pr.called
    # No HLD/HLD Delta was approved for this project — the response says so
    # up front, rather than a human only discovering it inside the coding tool.
    assert result.not_ready == ["High-Level Design"]
    assert "High-Level Design" in result.message and "not available yet" in result.message


def test_sync_inputs_does_nothing_when_content_already_matches_the_base_branch(db, project, actor):
    """Regression — a real bug: re-running "Sync story inputs" when
    nothing upstream had changed still opened a new pull request every
    time, with 2 commits and 0 files actually different. It must now
    recognize nothing changed and open no PR at all."""
    story, lane, nodes = _lane(db, project, actor)
    _repo(db, project)
    snapshot = build_story_input_snapshot(db, project=project, story=story, stage="story_lld")
    existing_by_path = {f.path: f.content for f in snapshot.files}

    from unittest.mock import patch

    from app.services.github_integration import GitHubFileContent

    def read_existing(token, o, r, path, ref, **kw):
        return GitHubFileContent(path=path, sha="s", size=len(existing_by_path[path]), content=existing_by_path[path], truncated=False, is_binary=False)

    with patch.object(coding_tools_routes, "decrypt_repository_token", lambda repo: "tok"), \
         patch("app.services.github_integration.list_branches", lambda token, o, r, **kw: ["main"]), \
         patch("app.services.github_integration.read_file", read_existing), \
         patch("app.services.github_integration.create_branch") as create_branch, \
         patch("app.services.github_integration.create_or_update_file") as create_file, \
         patch("app.services.github_integration.create_pull_request") as create_pr:
        result = sync_story_inputs(project.id, story.id, SyncStoryInputsRequest(stage="story_lld", triggered_by_user_id=actor.id), db)

    assert result.branch_name is None and result.pull_request_url is None and result.committed == []
    assert "Nothing to sync" in result.message
    assert not create_branch.called and not create_file.called and not create_pr.called


def test_sync_inputs_commits_only_the_file_that_actually_changed(db, project, actor):
    story, lane, nodes = _lane(db, project, actor)
    _repo(db, project)
    snapshot = build_story_input_snapshot(db, project=project, story=story, stage="story_lld")
    existing_by_path = {f.path: f.content for f in snapshot.files}
    changed_path = next(p for p in existing_by_path if p.endswith("story-context.md"))
    existing_by_path[changed_path] = "stale content from an earlier sync"

    from unittest.mock import patch

    from app.services.github_integration import GitHubFileContent

    def read_existing(token, o, r, path, ref, **kw):
        return GitHubFileContent(path=path, sha="s", size=len(existing_by_path[path]), content=existing_by_path[path], truncated=False, is_binary=False)

    with patch.object(coding_tools_routes, "decrypt_repository_token", lambda repo: "tok"), \
         patch("app.services.github_integration.list_branches", lambda token, o, r, **kw: ["main"]), \
         patch("app.services.github_integration.read_file", read_existing), \
         patch("app.services.github_integration.create_branch") as create_branch, \
         patch("app.services.github_integration.create_or_update_file") as create_file, \
         patch("app.services.github_integration.create_pull_request") as create_pr:
        create_file.return_value = "c"

        class PR:
            html_url = "https://github.com/octocat/app/pull/9"

        create_pr.return_value = PR()
        result = sync_story_inputs(project.id, story.id, SyncStoryInputsRequest(stage="story_lld", triggered_by_user_id=actor.id), db)

    assert result.committed == [changed_path]
    assert create_branch.called and create_file.call_count == 1


def test_sync_story_stage_route_reads_and_syncs(db, project, actor):
    story, lane, nodes = _lane(db, project, actor)
    _unlock(db, nodes["STORY_LLD"])
    _repo(db, project)

    from unittest.mock import patch

    from app.services.github_integration import GitHubFileContent

    content = LLD_DOC.format(pid=project.id, sid=story.id)
    seen = {}

    def read_file(token, o, r, path, ref, **kw):
        seen["path"], seen["ref"] = path, ref
        return GitHubFileContent(path=path, sha="s", size=len(content), content=content, truncated=False, is_binary=False)

    with patch.object(coding_tools_routes, "decrypt_repository_token", lambda repo: "tok"), \
         patch("app.services.github_integration.read_file", read_file):
        result = sync_story_stage(project.id, story.id, SyncStoryStageRequest(stage="story_lld", triggered_by_user_id=actor.id), db)

    slug = story_slug(story.title)
    assert result.path == f"docs/sdlc/stories/{slug}/story-lld.md"
    assert result.created and result.story_artifact_id is not None
    assert seen == {"path": result.path, "ref": "main"}


def test_sync_story_stage_route_missing_file_is_404(db, project, actor):
    story, lane, nodes = _lane(db, project, actor)
    _unlock(db, nodes["STORY_LLD"])
    _repo(db, project)

    from unittest.mock import patch

    from app.services.github_integration import GitHubIntegrationError

    def missing(*a, **kw):
        raise GitHubIntegrationError("Not Found", status_code=404)

    with patch.object(coding_tools_routes, "decrypt_repository_token", lambda repo: "tok"), \
         patch("app.services.github_integration.read_file", missing):
        with pytest.raises(HTTPException) as exc:
            sync_story_stage(project.id, story.id, SyncStoryStageRequest(stage="story_lld", triggered_by_user_id=actor.id), db)
    assert exc.value.status_code == 404


# --- Bulk "Prepare for coding tool" (Stories list) -----------------------------------------


def test_bulk_prepare_creates_missing_lanes_and_opens_one_shared_pr(db, project, actor):
    """Two stories, neither with a lane yet — the bulk action creates both
    lanes and lands both stories' input files on ONE shared branch/PR, not
    one PR per story."""
    from app.api.routes.coding_tools import bulk_prepare_coding_tool
    from app.schemas.coding_tools import BulkPrepareCodingToolRequest

    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content="## Story: X\n")
    story_a = create_story(StoryCreate(project_id=project.id, title="Story A", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id), db)
    story_b = create_story(StoryCreate(project_id=project.id, title="Story B", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id), db)
    _repo(db, project)

    from unittest.mock import patch

    with patch.object(coding_tools_routes, "decrypt_repository_token", lambda repo: "tok"), \
         patch("app.services.github_integration.list_branches", lambda token, o, r, **kw: ["main"]), \
         patch("app.services.github_integration.read_file", _not_found), \
         patch("app.services.github_integration.create_branch") as create_branch, \
         patch("app.services.github_integration.create_or_update_file") as create_file, \
         patch("app.services.github_integration.create_pull_request") as create_pr:
        create_file.return_value = "c"

        class PR:
            html_url = "https://github.com/octocat/app/pull/11"

        create_pr.return_value = PR()

        result = bulk_prepare_coding_tool(
            project.id,
            BulkPrepareCodingToolRequest(story_ids=[story_a.id, story_b.id], stage="story_lld", triggered_by_user_id=actor.id),
            db,
        )

    assert result.pull_request_url == "https://github.com/octocat/app/pull/11"
    assert create_branch.call_count == 1  # one shared branch, not two
    assert create_pr.call_count == 1  # one shared PR, not two
    assert len(result.results) == 2
    assert all(r.status == "prepared" and r.lane_created for r in result.results)
    db.refresh(db.get(Story, story_a.id))
    assert db.get(Story, story_a.id).lane_created_at is not None
    assert db.get(Story, story_b.id).lane_created_at is not None


def test_bulk_prepare_skips_a_story_with_no_story_crafting_approval_without_failing_the_batch(db, project, actor):
    """A story that can't get a lane yet (no approved Story Crafting at
    all, in this project) is reported as skipped with a real reason —
    never silently dropped, and never aborts the other story's own
    preparation."""
    from app.api.routes.coding_tools import bulk_prepare_coding_tool
    from app.schemas.coding_tools import BulkPrepareCodingToolRequest

    # story_a gets a real lane set up first (Story Crafting approved then);
    # story_b is a bare row with no owning project-level Story Crafting
    # approval of its own lane precondition — simplest way to force
    # create_story_lane's own 409 is a story that already has one.
    story, lane, nodes = _lane(db, project, actor)
    _repo(db, project)

    from unittest.mock import patch

    with patch.object(coding_tools_routes, "decrypt_repository_token", lambda repo: "tok"), \
         patch("app.services.github_integration.list_branches", lambda token, o, r, **kw: ["main"]), \
         patch("app.services.github_integration.read_file", _not_found), \
         patch("app.services.github_integration.create_branch") as create_branch, \
         patch("app.services.github_integration.create_or_update_file"), \
         patch("app.services.github_integration.create_pull_request") as create_pr:
        class PR:
            html_url = "https://github.com/octocat/app/pull/12"

        create_pr.return_value = PR()

        result = bulk_prepare_coding_tool(
            project.id,
            BulkPrepareCodingToolRequest(story_ids=[story.id, uuid.uuid4()], stage="story_lld", triggered_by_user_id=actor.id),
            db,
        )

    prepared = [r for r in result.results if r.status == "prepared"]
    skipped = [r for r in result.results if r.status == "skipped"]
    assert len(prepared) == 1 and prepared[0].story_id == story.id
    assert prepared[0].lane_created is False  # this story's lane already existed
    assert len(skipped) == 1 and skipped[0].reason is not None


def test_bulk_prepare_also_drafts_story_lld_in_app_and_pushes_the_final_document(db, project, actor, monkeypatch):
    """Bulk "prepare for coding tool" used to only ever push an inputs
    scaffold. It must now also draft the stage itself in-app (when it
    hasn't been drafted yet and the real preconditions allow it) and push
    the finished document alongside the inputs, in the same shared PR."""
    from unittest.mock import patch

    from app.api.routes.coding_tools import bulk_prepare_coding_tool
    from app.models import AgentDefinition
    from app.schemas.coding_tools import BulkPrepareCodingToolRequest
    from app.services import ai_generation
    from app.services.story_lld_agent import STORY_LLD_AGENT_KEY
    from tests.conftest import make_agent_prompt

    monkeypatch.setattr(ai_generation, "get_active_provider", lambda: "mock")
    agent = AgentDefinition(agent_key=STORY_LLD_AGENT_KEY, name="Story LLD Agent", model_name="mock")
    db.add(agent)
    db.flush()
    make_agent_prompt(db, stage="story_lld", agent=agent)

    story, lane, nodes = _lane(db, project, actor)
    hld_node = make_node(db, project, node_key="hld", order_index=1, output_artifact_type="hld_document")
    make_approved_artifact(db, project, hld_node, actor, content="# HLD\n\nSome design.\n")
    _repo(db, project)

    with patch.object(coding_tools_routes, "decrypt_repository_token", lambda repo: "tok"), \
         patch("app.services.github_integration.list_branches", lambda token, o, r, **kw: ["main"]), \
         patch("app.services.github_integration.read_file", _not_found), \
         patch("app.services.github_integration.create_branch"), \
         patch("app.services.github_integration.create_or_update_file") as create_file, \
         patch("app.services.github_integration.create_pull_request") as create_pr:
        class PR:
            html_url = "https://github.com/octocat/app/pull/13"

        create_pr.return_value = PR()

        result = bulk_prepare_coding_tool(
            project.id,
            BulkPrepareCodingToolRequest(story_ids=[story.id], stage="story_lld", triggered_by_user_id=actor.id),
            db,
        )

    assert result.results[0].status == "prepared"
    slug = story_slug(story.title)
    output_path = f"docs/sdlc/stories/{slug}/story-lld.md"
    assert output_path in result.results[0].committed_paths
    written_paths = [call.args[3] for call in create_file.call_args_list]
    assert output_path in written_paths
    db.refresh(nodes["STORY_LLD"])
    assert nodes["STORY_LLD"].status == StoryDeliveryNodeStatus.COMPLETED


def test_bulk_prepare_survives_an_ai_provider_failure_on_one_story_without_500ing_the_batch(db, project, actor, monkeypatch):
    """Regression: a transient AI-provider failure (timeout, rate limit,
    malformed response — AIGenerationError) while drafting ONE story in a
    large batch used to propagate uncaught out of
    _draft_stage_in_app_best_effort and crash the whole bulk-prepare
    request with a 500, taking every other story down with it. It must
    instead be swallowed for that one story (which still gets its inputs
    synced, just without a fresh draft) while the rest of the batch
    succeeds normally."""
    from unittest.mock import patch

    from app.api.routes.coding_tools import bulk_prepare_coding_tool
    from app.models import AgentDefinition
    from app.schemas.coding_tools import BulkPrepareCodingToolRequest
    from app.services import ai_generation
    from app.services.ai_generation import AIGenerationError
    from app.services.story_lld_agent import STORY_LLD_AGENT_KEY
    from tests.conftest import make_agent_prompt

    monkeypatch.setattr(ai_generation, "get_active_provider", lambda: "mock")
    agent = AgentDefinition(agent_key=STORY_LLD_AGENT_KEY, name="Story LLD Agent", model_name="mock")
    db.add(agent)
    db.flush()
    make_agent_prompt(db, stage="story_lld", agent=agent)

    hld_node = make_node(db, project, node_key="hld", order_index=1, output_artifact_type="hld_document")
    make_approved_artifact(db, project, hld_node, actor, content="# HLD\n\nSome design.\n")
    story_a, _, _ = _lane(db, project, actor)
    node = make_node(db, project, node_key="story_crafting_2", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content="## Story: Y\n")
    story_b_read = create_story(
        StoryCreate(project_id=project.id, title="Story B", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id), db,
    )
    create_story_lane(story_b_read.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    story_b = db.get(Story, story_b_read.id)
    _repo(db, project)

    # _BULK_DRAFT_STAGES captures a direct reference to run_story_lld_agent
    # at module-import time, so patching the module-level name has no
    # effect on it — the dict entry itself must be patched instead.
    real_run = coding_tools_routes._BULK_DRAFT_STAGES["story_lld"]["run"]

    def flaky_run(db_, *, node, triggered_by):
        if node.lane.story_id == story_a.id:
            raise AIGenerationError("upstream provider timed out")
        return real_run(db_, node=node, triggered_by=triggered_by)

    monkeypatch.setitem(coding_tools_routes._BULK_DRAFT_STAGES["story_lld"], "run", flaky_run)

    with patch.object(coding_tools_routes, "decrypt_repository_token", lambda repo: "tok"), \
         patch("app.services.github_integration.list_branches", lambda token, o, r, **kw: ["main"]), \
         patch("app.services.github_integration.read_file", _not_found), \
         patch("app.services.github_integration.create_branch"), \
         patch("app.services.github_integration.create_or_update_file"), \
         patch("app.services.github_integration.create_pull_request") as create_pr:
        class PR:
            html_url = "https://github.com/octocat/app/pull/14"

        create_pr.return_value = PR()

        result = bulk_prepare_coding_tool(
            project.id,
            BulkPrepareCodingToolRequest(story_ids=[story_a.id, story_b.id], stage="story_lld", triggered_by_user_id=actor.id),
            db,
        )

    # Both stories still get "prepared" (inputs synced) — story_a just
    # didn't get a fresh draft because its provider call failed.
    assert {r.story_id for r in result.results} == {story_a.id, story_b.id}
    assert all(r.status == "prepared" for r in result.results)
    assert db.query(StoryArtifact).filter(StoryArtifact.story_id == story_a.id, StoryArtifact.artifact_type == "story_lld").first() is None
    assert db.query(StoryArtifact).filter(StoryArtifact.story_id == story_b.id, StoryArtifact.artifact_type == "story_lld").first() is not None


def test_bulk_sync_story_stage_pulls_back_multiple_stories_and_skips_one_with_nothing_pushed(db, project, actor):
    """The "Pull Latest from GitHub" bulk action — reverse of bulk-prepare.
    One story has a pushed story-lld.md to read back; a second has nothing
    pushed yet, which must be reported as skipped, not fail the batch."""
    from app.api.routes.coding_tools import bulk_sync_story_stage
    from app.schemas.coding_tools import BulkSyncStoryStageRequest
    from app.services.github_integration import GitHubFileContent

    story, lane, nodes = _lane(db, project, actor)
    _unlock(db, nodes["STORY_LLD"])
    other_story = create_story(
        StoryCreate(project_id=project.id, title="Other Story", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id), db,
    )
    _repo(db, project)

    from unittest.mock import patch

    content = LLD_DOC.format(pid=project.id, sid=story.id)

    def read_file(token, o, r, path, ref, **kw):
        if "other-story" in path:
            from app.services.github_integration import GitHubIntegrationError

            raise GitHubIntegrationError("not found", status_code=404)
        return GitHubFileContent(path=path, sha="s", size=len(content), content=content, truncated=False, is_binary=False)

    with patch.object(coding_tools_routes, "decrypt_repository_token", lambda repo: "tok"), \
         patch("app.services.github_integration.read_file", read_file):
        result = bulk_sync_story_stage(
            project.id,
            BulkSyncStoryStageRequest(story_ids=[story.id, other_story.id], stage="story_lld", triggered_by_user_id=actor.id),
            db,
        )

    assert result.ref == "main"
    synced = [r for r in result.results if r.status == "synced"]
    skipped = [r for r in result.results if r.status == "skipped"]
    assert len(synced) == 1 and synced[0].story_id == story.id and synced[0].version_number == 1
    assert len(skipped) == 1 and skipped[0].story_id == other_story.id and skipped[0].reason is not None
