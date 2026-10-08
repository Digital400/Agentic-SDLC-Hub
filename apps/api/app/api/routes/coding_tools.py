"""Use your own AI coding tool (Claude Code, Codex, OpenCode, Cursor) for a
workflow stage, and sync the result back into the app.

  GET  /projects/{id}/coding-tools/skills/preview  — the files that would be added
  POST /projects/{id}/coding-tools/skills/install  — commit them to the project's repo on a NEW branch + open a PR
  POST /projects/{id}/coding-tools/sync            — pull docs/sdlc/<stage>.md back in as a DRAFT version

Safety rails are the same as every other repository write in this app
(see repository_file_edit.py): never the default branch, always a real pull
request, nothing merged, every attempt audited. A file the team commonly edits
(AGENTS.md, .claude/settings.json) is never overwritten. See
app/services/coding_tool_skills.py for what each tool receives.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.routes.github_integration import _github_error_to_http, decrypt_repository_token
from app.api.routes.stories import create_story_lane, update_lane_node_status
from app.core.database import get_db
from app.models import ImplementationTask, Project, Repository, Story, StoryArtifact, StoryDeliveryNodeStatus, User, WorkflowNode
from app.schemas.coding_tools import (
    BulkPrepareCodingToolRequest,
    BulkPrepareCodingToolResponse,
    BulkPrepareCodingToolStoryResult,
    BulkSyncStoryStageRequest,
    BulkSyncStoryStageResponse,
    BulkSyncStoryStageStoryResult,
    InstallSkillsRequest,
    InstallSkillsResponse,
    SkillFileRead,
    SkillPackRead,
    SkippedFileRead,
    SyncImplementationTaskInputsRequest,
    SyncStageRequest,
    SyncStageResponse,
    SyncStoryInputsRequest,
    SyncStoryInputsResponse,
    SyncStoryStageRequest,
    SyncStoryStageResponse,
)
from app.schemas.story import CreateStoryLaneRequest, UpdateLaneNodeStatusRequest
from app.services import github_integration as github_api
from app.services.ai_generation import AIGenerationError
from app.services.audit import record_audit_log
from app.services.coding_tool_skills import README_PATH, STAGE_SPECS, SkillFile, SkillPackError, build_skill_pack, merge_readme, readme_section_key
from app.services.github_integration import GitHubIntegrationError
from app.services.permissions import require_can_edit_repository_file, require_can_edit_stage
from app.services.stage_document_sync import StageSyncError, new_branch_name, sync_stage_document
from app.services.story_coding_tool_sync import (
    StoryCodingToolError,
    build_story_input_snapshot,
    story_output_path,
    story_slug,
    sync_story_stage_document,
)
from app.services.story_implementation_plan_agent import (
    STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE,
    StoryImplementationPlanError,
    run_story_implementation_plan_agent,
)
from app.services.story_lld_agent import STORY_LLD_ARTIFACT_TYPE, StoryLldError, run_story_lld_agent
from app.services.story_test_scenarios_agent import (
    STORY_TEST_SCENARIOS_ARTIFACT_TYPE,
    StoryTestScenariosError,
    run_story_test_scenarios_agent,
)

# Bulk "prepare for coding tool" — stage key (as the Stories list's dropdown
# sends it) -> the StoryDeliveryNode.node_key it corresponds to, the
# precondition node it needs COMPLETED first (None if it has none beyond
# the lane simply existing), the StoryArtifact type it drafts, and the
# run_*_agent function that drafts it in-app. Mirrors
# app/services/story_coding_tool_sync.py's own private _LANE_NODE_KEY/
# _ARTIFACT_TYPE maps (kept separate since this one also carries the
# runner function and precondition node, which that module doesn't need).
_BULK_DRAFT_STAGES: dict[str, dict] = {
    "story_lld": {
        "node_key": "STORY_LLD", "precondition_node_key": None, "artifact_type": STORY_LLD_ARTIFACT_TYPE,
        "run": run_story_lld_agent, "error": StoryLldError,
    },
    "story_implementation_plan": {
        "node_key": "IMPLEMENTATION_PLAN", "precondition_node_key": "LLD_REVIEW", "artifact_type": STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE,
        "run": run_story_implementation_plan_agent, "error": StoryImplementationPlanError,
    },
    "story_test_scenarios": {
        "node_key": "TEST_SCENARIOS", "precondition_node_key": "IMPLEMENTATION", "artifact_type": STORY_TEST_SCENARIOS_ARTIFACT_TYPE,
        "run": run_story_test_scenarios_agent, "error": StoryTestScenariosError,
    },
}

router = APIRouter(prefix="/projects/{project_id}/coding-tools", tags=["coding-tools"])


def _project_or_404(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Project {project_id} not found")
    return project


def _user_or_400(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {user_id} does not match an existing user")
    return user


def _primary_repository(db: Session, project_id: uuid.UUID) -> Repository:
    repository = db.query(Repository).filter(Repository.project_id == project_id, Repository.is_primary.is_(True)).first()
    if repository is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This project has no primary GitHub repository connected yet.")
    return repository


@router.get("/skills/preview", response_model=SkillPackRead)
def preview_skill_pack(
    project_id: uuid.UUID, tool: str = Query(...), stage: str = Query(default="requirement_intake"), db: Session = Depends(get_db)
) -> SkillPackRead:
    project = _project_or_404(db, project_id)
    try:
        pack = build_skill_pack(db, project, tool=tool, stage=stage)
    except SkillPackError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return SkillPackRead(
        tool=pack.tool, tool_label=pack.tool_label, stage=pack.stage, usage=pack.usage, notes=pack.notes,
        files=[SkillFileRead(path=f.path, purpose=f.purpose, managed=f.managed, content=f.content) for f in pack.files],
    )


@router.post("/skills/install", response_model=InstallSkillsResponse, status_code=status.HTTP_201_CREATED)
def install_skill_pack(project_id: uuid.UUID, payload: InstallSkillsRequest, db: Session = Depends(get_db)) -> InstallSkillsResponse:
    project = _project_or_404(db, project_id)
    user = _user_or_400(db, payload.triggered_by_user_id)
    require_can_edit_repository_file(user)
    repository = _primary_repository(db, project_id)
    base_branch = payload.base_branch or repository.default_branch
    if not base_branch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No base_branch given and this repository has no known default branch.")

    try:
        pack = build_skill_pack(db, project, tool=payload.tool, stage=payload.stage)
    except SkillPackError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    token = decrypt_repository_token(repository)
    owner, name = repository.owner, repository.name

    # An empty repository has no branch to base a pull request on. Same rule
    # (and same precedent) as repo_bootstrap.py: for a GENUINELY empty repo
    # only, the first commit goes straight onto the default branch. Anything
    # else must branch and open a PR.
    try:
        branches = github_api.list_branches(token, owner, name)
    except GitHubIntegrationError as exc:
        raise _github_error_to_http(exc) from exc
    if not branches:
        return _install_into_empty_repository(db, project, user, repository, pack, token, base_branch, payload)
    if base_branch not in branches:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The branch '{base_branch}' does not exist in {owner}/{name}. Existing branches: {', '.join(branches[:10])}. "
            "Ask an admin to correct the repository's default branch, or use one of those.",
        )

    # Decide what to write BEFORE creating a branch, so nothing is created when
    # there is nothing to add. Existence is checked on the base branch, and a
    # managed file whose computed content is already byte-identical to what's
    # there is skipped too — without this, re-running "Add via pull request"
    # with nothing actually changed still opened a new, empty-diff pull
    # request every time (the same bug "Sync story inputs" had — see
    # app/services/story_coding_tool_sync.py's install route for its twin).
    to_write: list[tuple] = []  # (file, existing_sha, content)
    skipped: list[SkippedFileRead] = []
    try:
        for f in pack.files:
            existing_sha = github_api.get_file_sha(token, owner, name, f.path, base_branch)
            if existing_sha is not None and not f.managed:
                skipped.append(SkippedFileRead(path=f.path, reason="Already exists — left unchanged (see docs/sdlc/README.md to merge by hand)."))
                continue
            existing_content = github_api.read_file(token, owner, name, f.path, base_branch).content if existing_sha is not None else None
            content = f.content
            if f.path == README_PATH:
                # Shared across every stage/tool a team installs — merge this
                # (tool, stage)'s own section into whatever's already there
                # instead of overwriting the whole file (see merge_readme's
                # docstring for why: a plain overwrite silently deleted every
                # other already-installed stage's instructions).
                content = merge_readme(existing_content, f.content, readme_section_key(payload.tool, payload.stage))
            if content == existing_content:
                skipped.append(SkippedFileRead(path=f.path, reason="Already up to date."))
                continue
            to_write.append((f, existing_sha, content))
    except GitHubIntegrationError as exc:
        raise _github_error_to_http(exc) from exc

    if not to_write:
        return InstallSkillsResponse(
            branch_name=None, base_branch=base_branch, pull_request_url=None, committed=[], skipped=skipped,
            message="Nothing to add — every file already exists and is up to date.",
        )

    branch_name = new_branch_name(payload.tool, payload.stage)
    if branch_name == base_branch:  # defense in depth; unreachable by construction
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Refusing to use the repository's default branch.")

    committed: list[str] = []
    try:
        github_api.create_branch(token, owner, name, new_branch=branch_name, base_ref=base_branch)
        for f, existing_sha, content in to_write:
            github_api.create_or_update_file(
                token, owner, name, f.path, content=content,
                message=f"Add {pack.tool_label} skills for {payload.stage.replace('_', ' ')}: {f.path}", branch=branch_name, sha=existing_sha,
            )
            committed.append(f.path)
    except GitHubIntegrationError as exc:
        record_audit_log(
            db, project_id=project.id, actor_user_id=user.id, action="coding_tool_skills.install_failed", entity_type="Repository",
            entity_id=repository.id, extra_data={"tool": payload.tool, "stage": payload.stage, "branch_name": branch_name, "committed": committed, "error": str(exc)},
        )
        db.commit()
        raise _github_error_to_http(exc) from exc

    pull_request_url: str | None = None
    try:
        pr = github_api.create_pull_request(
            token, owner, name,
            title=f"Add {pack.tool_label} skills for {payload.stage.replace('_', ' ').title()}",
            head=branch_name, base=base_branch,
            body=(
                f"Adds {pack.tool_label} skills so the team can run **{payload.stage.replace('_', ' ')}** in {pack.tool_label} "
                "and sync the result to Agentic SDLC Hub.\n\n"
                "**How to use it**\n" + "\n".join(f"{i}. {u}" for i, u in enumerate(pack.usage, start=1)) + "\n\n"
                "**Files**\n" + "\n".join(f"- `{p}`" for p in committed)
                + ("\n\n**Left unchanged (already exist)**\n" + "\n".join(f"- `{s.path}`" for s in skipped) if skipped else "")
                + "\n\n_Generated by Agentic SDLC Hub. Nothing here is merged automatically._"
            ),
        )
        pull_request_url = pr.html_url
    except GitHubIntegrationError as exc:
        record_audit_log(
            db, project_id=project.id, actor_user_id=user.id, action="coding_tool_skills.pr_failed", entity_type="Repository",
            entity_id=repository.id, extra_data={"tool": payload.tool, "branch_name": branch_name, "error": str(exc)},
        )
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Files were committed to {branch_name}, but the pull request could not be opened: {exc}") from exc

    record_audit_log(
        db, project_id=project.id, actor_user_id=user.id, action="coding_tool_skills.installed", entity_type="Repository",
        entity_id=repository.id,
        extra_data={"tool": payload.tool, "stage": payload.stage, "branch_name": branch_name, "committed": committed, "skipped": [s.path for s in skipped], "pull_request_url": pull_request_url},
    )
    db.commit()
    return InstallSkillsResponse(
        branch_name=branch_name, base_branch=base_branch, pull_request_url=pull_request_url, committed=committed, skipped=skipped,
        message=f"Opened a pull request with {len(committed)} file(s). Merge it, then open the repo in {pack.tool_label}.",
    )


def _install_into_empty_repository(db, project, user, repository, pack, token, base_branch, payload) -> InstallSkillsResponse:
    """First commit(s) for a genuinely empty repository — see install_skill_pack."""
    committed: list[str] = []
    try:
        for f in pack.files:
            # No README can already exist in a genuinely empty repository, but
            # still route through merge_readme so this file always carries the
            # shared header, exactly as it would on every later install.
            content = merge_readme(None, f.content, readme_section_key(payload.tool, payload.stage)) if f.path == README_PATH else f.content
            github_api.create_or_update_file(
                token, repository.owner, repository.name, f.path, content=content,
                message=f"Add {pack.tool_label} skills for {payload.stage.replace('_', ' ')}: {f.path}", branch=base_branch,
            )
            committed.append(f.path)
    except GitHubIntegrationError as exc:
        record_audit_log(
            db, project_id=project.id, actor_user_id=user.id, action="coding_tool_skills.install_failed", entity_type="Repository",
            entity_id=repository.id, extra_data={"tool": payload.tool, "stage": payload.stage, "branch_name": base_branch, "committed": committed, "error": str(exc), "empty_repository": True},
        )
        db.commit()
        raise _github_error_to_http(exc) from exc
    record_audit_log(
        db, project_id=project.id, actor_user_id=user.id, action="coding_tool_skills.installed", entity_type="Repository",
        entity_id=repository.id,
        extra_data={"tool": payload.tool, "stage": payload.stage, "branch_name": base_branch, "committed": committed, "empty_repository": True},
    )
    db.commit()
    return InstallSkillsResponse(
        branch_name=base_branch, base_branch=base_branch, pull_request_url=None, committed=committed, skipped=[],
        message=(
            f"The repository was empty, so there was nothing to open a pull request against — the {len(committed)} file(s) were added as its first "
            f"commit on '{base_branch}'. Clone it and open it in {pack.tool_label}. (The one-time “Bootstrap repository” scaffold is no longer "
            "available now that the repository has a commit.)"
        ),
    )


@router.post("/sync", response_model=SyncStageResponse)
def sync_stage(project_id: uuid.UUID, payload: SyncStageRequest, db: Session = Depends(get_db)) -> SyncStageResponse:
    project = _project_or_404(db, project_id)
    user = _user_or_400(db, payload.triggered_by_user_id)
    node = db.query(WorkflowNode).filter(WorkflowNode.project_id == project_id, WorkflowNode.node_key == payload.stage).first()
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Project has no '{payload.stage}' stage.")
    require_can_edit_stage(user, node.node_key)

    from app.services.coding_tool_skills import STAGE_SPECS

    spec = STAGE_SPECS.get(payload.stage)
    if spec is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Stage '{payload.stage}' cannot be synced from a repository yet.")

    repository = _primary_repository(db, project_id)
    ref = payload.ref or repository.default_branch
    if not ref:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No ref given and this repository has no known default branch.")

    token = decrypt_repository_token(repository)
    try:
        file = github_api.read_file(token, repository.owner, repository.name, spec.output_path, ref)
    except GitHubIntegrationError as exc:
        if exc.status_code == 404:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"'{spec.output_path}' was not found on '{ref}'. Push the file (a branch is fine) and enter that branch name.",
            ) from exc
        raise _github_error_to_http(exc) from exc
    if file.content is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"'{spec.output_path}' is too large or is not a text file.")

    try:
        result = sync_stage_document(
            db, project=project, node=node, user=user, markdown=file.content,
            source_label=f"{repository.owner}/{repository.name}@{ref}", source_path=spec.output_path,
        )
    except StageSyncError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, str(exc)) from exc
    db.commit()
    db.refresh(result.artifact)
    db.refresh(node)
    return SyncStageResponse(
        artifact_id=result.artifact.id, artifact_version_id=result.version.id, version_number=result.version.version_number,
        artifact_status=result.artifact.status.value, workflow_node_status=node.status.value, ref=ref, file_sha=file.sha,
        path=spec.output_path, generated_by=result.generated_by, created_artifact=result.created_artifact,
    )


# --- Per-story delivery lane stages (Story LLD, Implementation Plan, Test Scenarios) --------
#
# The skill files themselves (command, reviewer subagent, validator, README
# section) are installed exactly once per repo via the two routes above,
# generically — the same /skills/preview and /skills/install, just called
# with e.g. stage="story_lld". Only what's genuinely per-story lives here:
# snapshotting one story's inputs, and pulling its finished document back in.


def _story_or_404(db: Session, project_id: uuid.UUID, story_id: uuid.UUID) -> Story:
    story = db.query(Story).filter(Story.id == story_id, Story.project_id == project_id).first()
    if story is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Story {story_id} not found in project {project_id}.")
    return story


@router.post("/stories/{story_id}/sync-inputs", response_model=SyncStoryInputsResponse, status_code=status.HTTP_201_CREATED)
def sync_story_inputs(
    project_id: uuid.UUID, story_id: uuid.UUID, payload: SyncStoryInputsRequest, db: Session = Depends(get_db)
) -> SyncStoryInputsResponse:
    """Snapshots this one story's own context plus whatever upstream
    document(s) `stage` needs into docs/sdlc/stories/<slug>/inputs/ — run
    this once per story before that story's coding-tool command can do
    anything, and again any time an upstream document changes."""
    project = _project_or_404(db, project_id)
    story = _story_or_404(db, project_id, story_id)
    user = _user_or_400(db, payload.triggered_by_user_id)
    require_can_edit_repository_file(user)
    repository = _primary_repository(db, project_id)
    base_branch = payload.base_branch or repository.default_branch
    if not base_branch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No base_branch given and this repository has no known default branch.")

    try:
        snapshot = build_story_input_snapshot(db, project=project, story=story, stage=payload.stage)
    except StoryCodingToolError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    files = snapshot.files

    token = decrypt_repository_token(repository)
    owner, name = repository.owner, repository.name
    try:
        branches = github_api.list_branches(token, owner, name)
    except GitHubIntegrationError as exc:
        raise _github_error_to_http(exc) from exc
    if not branches:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This repository is empty — add skills to it first (Add via pull request), which makes its first commit.",
        )
    if base_branch not in branches:
        raise HTTPException(status.HTTP_409_CONFLICT, f"The branch '{base_branch}' does not exist in {owner}/{name}.")

    # Every file here is always-refreshed by design (see
    # app/services/story_coding_tool_sync.py), so comparing against what's
    # already on base_branch — and skipping a file whose content hasn't
    # actually changed — is the only thing that stops "Sync story inputs"
    # from opening an empty-diff PR every single time it's run (a real bug:
    # re-running it when nothing upstream had changed still committed and
    # opened a new pull request with 0 files actually different).
    to_write: list[tuple] = []  # (file, existing_sha)
    try:
        for f in files:
            try:
                current = github_api.read_file(token, owner, name, f.path, base_branch)
            except GitHubIntegrationError as exc:
                if exc.status_code != 404:
                    raise
                to_write.append((f, None))  # doesn't exist on base_branch yet
                continue
            if current.content != f.content:
                to_write.append((f, current.sha))
    except GitHubIntegrationError as exc:
        raise _github_error_to_http(exc) from exc

    if not to_write:
        return SyncStoryInputsResponse(
            branch_name=None, base_branch=base_branch, pull_request_url=None, committed=[], not_ready=snapshot.not_ready,
            message="Nothing to sync — these inputs already match what's on "
            + base_branch
            + (
                f". Note: {', '.join(snapshot.not_ready)} {'is' if len(snapshot.not_ready) == 1 else 'are'} still not available."
                if snapshot.not_ready
                else "."
            ),
        )

    branch_name = new_branch_name(f"sync-inputs-{payload.stage}", story_slug(story.title))
    committed: list[str] = []
    try:
        github_api.create_branch(token, owner, name, new_branch=branch_name, base_ref=base_branch)
        for f, existing_sha in to_write:
            github_api.create_or_update_file(
                token, owner, name, f.path, content=f.content, message=f"Sync inputs for {story.title} ({payload.stage}): {f.path}",
                branch=branch_name, sha=existing_sha,
            )
            committed.append(f.path)
    except GitHubIntegrationError as exc:
        record_audit_log(
            db, project_id=project.id, actor_user_id=user.id, action="coding_tool_skills.sync_inputs_failed", entity_type="Repository",
            entity_id=repository.id, extra_data={"story_id": str(story.id), "stage": payload.stage, "branch_name": branch_name, "committed": committed, "error": str(exc)},
        )
        db.commit()
        raise _github_error_to_http(exc) from exc

    pull_request_url: str | None = None
    try:
        pr = github_api.create_pull_request(
            token, owner, name, title=f"Sync inputs for story: {story.title} ({payload.stage.replace('_', ' ')})",
            head=branch_name, base=base_branch,
            body=(
                f"Refreshes the input snapshot `docs/sdlc/stories/{story_slug(story.title)}/inputs/` this story's "
                f"{payload.stage.replace('_', ' ')} coding-tool command reads.\n\n_Generated by Agentic SDLC Hub._"
            ),
        )
        pull_request_url = pr.html_url
    except GitHubIntegrationError as exc:
        record_audit_log(
            db, project_id=project.id, actor_user_id=user.id, action="coding_tool_skills.sync_inputs_pr_failed", entity_type="Repository",
            entity_id=repository.id, extra_data={"story_id": str(story.id), "branch_name": branch_name, "error": str(exc)},
        )
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Files were committed to {branch_name}, but the pull request could not be opened: {exc}") from exc

    record_audit_log(
        db, project_id=project.id, actor_user_id=user.id, action="coding_tool_skills.sync_inputs", entity_type="Repository",
        entity_id=repository.id, extra_data={"story_id": str(story.id), "stage": payload.stage, "branch_name": branch_name, "committed": committed, "pull_request_url": pull_request_url},
    )
    db.commit()
    message = f"Opened a pull request with {len(committed)} input file(s). Merge it, then run the {payload.stage.replace('_', ' ')} command for this story."
    if snapshot.not_ready:
        message += (
            f" Note: {', '.join(snapshot.not_ready)} {'is' if len(snapshot.not_ready) == 1 else 'are'} not available yet, so that input was "
            "committed as a placeholder — complete it first, then run \"Sync story inputs\" again before running the command."
        )
    return SyncStoryInputsResponse(
        branch_name=branch_name, base_branch=base_branch, pull_request_url=pull_request_url, committed=committed,
        not_ready=snapshot.not_ready, message=message,
    )


def _draft_stage_in_app_best_effort(db: Session, *, story: Story, stage: str, user: User) -> None:
    """Bulk "prepare for coding tool" used to only ever push an INPUTS
    scaffold for a human/external tool to draft from — it never actually
    produced the finished document itself. This drafts `stage` in-app
    first (the exact same run_*_agent function the single-story Draft
    button uses) when it hasn't been drafted yet, so one click both
    completes the stage AND syncs the result to GitHub, for whichever
    stage the dropdown has selected — not just Story LLD.

    Deliberately best-effort, never raises and never changes this story's
    outcome in `results`: if a precondition isn't met yet (e.g. this
    story's LLD Review hasn't been approved, or no agent is configured),
    drafting is silently skipped and the caller falls through to the
    exact same "write a not-yet-available placeholder" behavior this
    action has always had — it must never turn a story that used to be
    "prepared" into "skipped" just because the accelerator couldn't run.
    """
    spec = _BULK_DRAFT_STAGES.get(stage)
    if spec is None:
        return
    lane = story.delivery_lane
    if lane is None:
        return
    node = next((n for n in lane.nodes if n.node_key == spec["node_key"]), None)
    if node is None:
        return
    already = (
        db.query(StoryArtifact).filter(StoryArtifact.story_id == story.id, StoryArtifact.artifact_type == spec["artifact_type"]).first()
    )
    if already is not None:
        return  # already drafted — sync its existing content, don't redraft

    if spec["node_key"] == "STORY_LLD" and node.status == StoryDeliveryNodeStatus.LOCKED:
        story_ready = next((n for n in lane.nodes if n.node_key == "STORY_READY"), None)
        if story_ready is not None and story_ready.status != StoryDeliveryNodeStatus.COMPLETED:
            try:
                update_lane_node_status(story_ready.id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=user.id), db)
            except HTTPException:
                return
            db.refresh(node)

    precondition_key = spec["precondition_node_key"]
    if precondition_key is not None:
        precondition_node = next((n for n in lane.nodes if n.node_key == precondition_key), None)
        if precondition_node is None or precondition_node.status != StoryDeliveryNodeStatus.COMPLETED:
            return  # not ready yet — not an error, just can't draft this far ahead

    try:
        spec["run"](db, node=node, triggered_by=user)
    except spec["error"]:
        db.rollback()
        return  # couldn't draft (e.g. no agent configured, HLD not approved) — fall through, still sync inputs
    except AIGenerationError:
        # A real AI-provider failure (timeout, rate limit, malformed
        # response) for THIS one story's draft must never 500 the whole
        # batch — e.g. a 64-story bulk run where story #40 hits a
        # transient provider hiccup previously took every other story
        # down with it. Roll back this story's partial work and fall
        # through to the unchanged "sync inputs" behavior, same as any
        # other can't-draft-yet case above.
        db.rollback()
        return


@router.post("/stories/bulk-prepare", response_model=BulkPrepareCodingToolResponse, status_code=status.HTTP_201_CREATED)
def bulk_prepare_coding_tool(
    project_id: uuid.UUID, payload: BulkPrepareCodingToolRequest, db: Session = Depends(get_db)
) -> BulkPrepareCodingToolResponse:
    """The Stories list's "Prepare for coding tool" bulk action — one click
    across every selected story instead of opening each one's own
    workspace and clicking "Create lane" then "Sync story inputs"
    one-by-one. For each story: create its delivery lane if it doesn't
    have one yet (skipped, not failed, if that's not possible — e.g. no
    approved Story Crafting, or a PLANNED, not yet ACTIVE sprint — see
    create_story_lane's own preconditions), then snapshot its `stage`
    inputs. Every story's files land in ONE shared branch/PR, not one PR
    per story, so merging once prepares the whole batch.

    Reuses create_story_lane and build_story_input_snapshot exactly as the
    single-story routes do — no duplicated precondition logic — so a
    project with no GitHub repo, no connected token, or an unapproved
    Story Crafting backlog fails exactly the same way here as it would for
    one story at a time.
    """
    project = _project_or_404(db, project_id)
    user = _user_or_400(db, payload.triggered_by_user_id)
    require_can_edit_repository_file(user)
    repository = _primary_repository(db, project_id)
    base_branch = payload.base_branch or repository.default_branch
    if not base_branch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No base_branch given and this repository has no known default branch.")

    token = decrypt_repository_token(repository)
    owner, name = repository.owner, repository.name
    try:
        branches = github_api.list_branches(token, owner, name)
    except GitHubIntegrationError as exc:
        raise _github_error_to_http(exc) from exc
    if not branches:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This repository is empty — add skills to it first (Add via pull request), which makes its first commit.",
        )
    if base_branch not in branches:
        raise HTTPException(status.HTTP_409_CONFLICT, f"The branch '{base_branch}' does not exist in {owner}/{name}.")

    results: list[BulkPrepareCodingToolStoryResult] = []
    # (story, file, existing_sha_or_None) — one shared write list across
    # every prepared story, same "skip a file whose content hasn't
    # actually changed" rule sync_story_inputs uses, just spanning the
    # whole batch instead of one story.
    to_write: list[tuple] = []

    for story_id in payload.story_ids:
        story = db.query(Story).filter(Story.id == story_id, Story.project_id == project_id).first()
        if story is None:
            results.append(BulkPrepareCodingToolStoryResult(story_id=story_id, story_title="(not found)", status="skipped", reason=f"Story {story_id} not found in this project."))
            continue

        lane_created = False
        if story.lane_created_at is None:
            try:
                create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=user.id), db)
                lane_created = True
            except HTTPException as exc:
                results.append(
                    BulkPrepareCodingToolStoryResult(story_id=story.id, story_title=story.title, status="skipped", reason=str(exc.detail))
                )
                continue
            db.refresh(story)

        _draft_stage_in_app_best_effort(db, story=story, stage=payload.stage, user=user)

        try:
            snapshot = build_story_input_snapshot(db, project=project, story=story, stage=payload.stage)
        except StoryCodingToolError as exc:
            results.append(
                BulkPrepareCodingToolStoryResult(story_id=story.id, story_title=story.title, status="skipped", lane_created=lane_created, reason=str(exc))
            )
            continue

        # The stage's own freshly-drafted (or already-drafted) result —
        # not just its upstream inputs — gets pushed too, so "sync this
        # tool" actually lands the finished document in the repo, not only
        # a scaffold for someone else to fill in.
        files_to_sync = list(snapshot.files)
        stage_meta = _BULK_DRAFT_STAGES.get(payload.stage)
        stage_spec = STAGE_SPECS.get(payload.stage)
        if stage_meta is not None and stage_spec is not None:
            latest_artifact = (
                db.query(StoryArtifact)
                .filter(StoryArtifact.story_id == story.id, StoryArtifact.artifact_type == stage_meta["artifact_type"])
                .order_by(StoryArtifact.version_number.desc())
                .first()
            )
            if latest_artifact is not None:
                content = latest_artifact.content_markdown
                if not content.endswith("\n"):
                    content += "\n"
                files_to_sync.append(
                    SkillFile(
                        story_output_path(stage_spec, story_slug(story.title)), content,
                        f"This story's own drafted {payload.stage.replace('_', ' ')} — generated in the app.",
                    )
                )

        committed_paths: list[str] = []
        try:
            for f in files_to_sync:
                try:
                    current = github_api.read_file(token, owner, name, f.path, base_branch)
                except GitHubIntegrationError as exc:
                    if exc.status_code != 404:
                        raise
                    to_write.append((f, None))
                    committed_paths.append(f.path)
                    continue
                if current.content != f.content:
                    to_write.append((f, current.sha))
                    committed_paths.append(f.path)
        except GitHubIntegrationError as exc:
            raise _github_error_to_http(exc) from exc

        results.append(
            BulkPrepareCodingToolStoryResult(
                story_id=story.id, story_title=story.title, status="prepared", lane_created=lane_created,
                committed_paths=committed_paths, not_ready=snapshot.not_ready,
            )
        )

    prepared_count = sum(1 for r in results if r.status == "prepared")
    if not to_write:
        return BulkPrepareCodingToolResponse(
            branch_name=None, base_branch=base_branch, pull_request_url=None, results=results,
            message=f"Nothing to sync — every prepared story's inputs already match what's on {base_branch}."
            if prepared_count
            else "No story was prepared — see each story's own reason above.",
        )

    branch_name = new_branch_name("bulk-sync-inputs", payload.stage)
    committed: list[str] = []
    try:
        github_api.create_branch(token, owner, name, new_branch=branch_name, base_ref=base_branch)
        for f, existing_sha in to_write:
            github_api.create_or_update_file(
                token, owner, name, f.path, content=f.content, message=f"Bulk sync inputs ({payload.stage}): {f.path}",
                branch=branch_name, sha=existing_sha,
            )
            committed.append(f.path)
    except GitHubIntegrationError as exc:
        record_audit_log(
            db, project_id=project.id, actor_user_id=user.id, action="coding_tool_skills.bulk_sync_inputs_failed", entity_type="Repository",
            entity_id=repository.id, extra_data={"stage": payload.stage, "branch_name": branch_name, "committed": committed, "error": str(exc)},
        )
        db.commit()
        raise _github_error_to_http(exc) from exc

    pull_request_url: str | None = None
    try:
        pr = github_api.create_pull_request(
            token, owner, name,
            title=f"Bulk sync coding-tool inputs for {prepared_count} stor{'y' if prepared_count == 1 else 'ies'} ({payload.stage.replace('_', ' ')})",
            head=branch_name, base=base_branch,
            body="Refreshes the input snapshot for:\n\n"
            + "\n".join(f"- {r.story_title}" for r in results if r.status == "prepared")
            + "\n\n_Generated by Agentic SDLC Hub._",
        )
        pull_request_url = pr.html_url
    except GitHubIntegrationError as exc:
        record_audit_log(
            db, project_id=project.id, actor_user_id=user.id, action="coding_tool_skills.bulk_sync_inputs_pr_failed", entity_type="Repository",
            entity_id=repository.id, extra_data={"branch_name": branch_name, "error": str(exc)},
        )
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Files were committed to {branch_name}, but the pull request could not be opened: {exc}") from exc

    record_audit_log(
        db, project_id=project.id, actor_user_id=user.id, action="coding_tool_skills.bulk_sync_inputs", entity_type="Repository",
        entity_id=repository.id,
        extra_data={"stage": payload.stage, "branch_name": branch_name, "committed": committed, "pull_request_url": pull_request_url, "story_count": prepared_count},
    )
    db.commit()
    return BulkPrepareCodingToolResponse(
        branch_name=branch_name, base_branch=base_branch, pull_request_url=pull_request_url, results=results,
        message=f"Opened one pull request with {len(committed)} file(s) across {prepared_count} stor{'y' if prepared_count == 1 else 'ies'}. "
        "Merge it, then each story's own coding-tool command can run.",
    )


@router.post(
    "/implementation-tasks/{task_id}/sync-inputs", response_model=SyncStoryInputsResponse, status_code=status.HTTP_201_CREATED
)
def sync_implementation_task_inputs(
    project_id: uuid.UUID, task_id: uuid.UUID, payload: SyncImplementationTaskInputsRequest, db: Session = Depends(get_db)
) -> SyncStoryInputsResponse:
    """Snapshots one ImplementationTask's own context plus its story's Story
    LLD/Implementation Plan/Test Scenarios and this project's engineering
    setup into docs/sdlc/stories/<slug>/inputs/implementation/ — run this
    once per task before the /implementation command can do anything, and
    again any time an upstream document or the task itself changes. The
    per-story counterpart to sync_story_inputs above, but keyed by task
    (a story can have several sibling tasks — DATABASE/BACKEND/FRONTEND)
    rather than by stage — see app/services/implementation_skill.py."""
    project = _project_or_404(db, project_id)
    task = db.query(ImplementationTask).filter(ImplementationTask.id == task_id, ImplementationTask.project_id == project_id).first()
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Implementation task {task_id} not found in project {project_id}.")
    if task.story_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only story-scoped implementation tasks can sync inputs for a coding tool.")
    story = db.get(Story, task.story_id)
    if story is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Task {task.id}'s story no longer exists.")
    user = _user_or_400(db, payload.triggered_by_user_id)
    require_can_edit_repository_file(user)
    repository = _primary_repository(db, project_id)
    base_branch = payload.base_branch or repository.default_branch
    if not base_branch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No base_branch given and this repository has no known default branch.")

    from app.services.implementation_skill import build_implementation_task_input_snapshot

    snapshot = build_implementation_task_input_snapshot(db, project=project, story=story, task=task)
    file = snapshot.file

    token = decrypt_repository_token(repository)
    owner, name = repository.owner, repository.name
    try:
        branches = github_api.list_branches(token, owner, name)
    except GitHubIntegrationError as exc:
        raise _github_error_to_http(exc) from exc
    if not branches:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This repository is empty — add skills to it first (Add via pull request), which makes its first commit.",
        )
    if base_branch not in branches:
        raise HTTPException(status.HTTP_409_CONFLICT, f"The branch '{base_branch}' does not exist in {owner}/{name}.")

    try:
        existing_sha = None
        try:
            current = github_api.read_file(token, owner, name, file.path, base_branch)
            if current.content == file.content:
                return SyncStoryInputsResponse(
                    branch_name=None, base_branch=base_branch, pull_request_url=None, committed=[], not_ready=snapshot.not_ready,
                    message="Nothing to sync — this task's inputs already match what's on "
                    + base_branch
                    + (
                        f". Note: {', '.join(snapshot.not_ready)} {'is' if len(snapshot.not_ready) == 1 else 'are'} still not available."
                        if snapshot.not_ready
                        else "."
                    ),
                )
            existing_sha = current.sha
        except GitHubIntegrationError as exc:
            if exc.status_code != 404:
                raise
    except GitHubIntegrationError as exc:
        raise _github_error_to_http(exc) from exc

    branch_name = new_branch_name("sync-inputs-implementation", f"{story_slug(story.title)}-{task.area.value.lower()}")
    try:
        github_api.create_branch(token, owner, name, new_branch=branch_name, base_ref=base_branch)
        github_api.create_or_update_file(
            token, owner, name, file.path, content=file.content, message=f"Sync implementation inputs for {task.title}: {file.path}",
            branch=branch_name, sha=existing_sha,
        )
    except GitHubIntegrationError as exc:
        record_audit_log(
            db, project_id=project.id, actor_user_id=user.id, action="coding_tool_skills.sync_inputs_failed", entity_type="Repository",
            entity_id=repository.id, extra_data={"implementation_task_id": str(task.id), "branch_name": branch_name, "error": str(exc)},
        )
        db.commit()
        raise _github_error_to_http(exc) from exc

    pull_request_url: str | None = None
    try:
        pr = github_api.create_pull_request(
            token, owner, name, title=f"Sync implementation inputs: {task.title}",
            head=branch_name, base=base_branch,
            body=f"Refreshes `{file.path}` — the input snapshot this task's `/implementation` coding-tool command reads.\n\n_Generated by Agentic SDLC Hub._",
        )
        pull_request_url = pr.html_url
    except GitHubIntegrationError as exc:
        record_audit_log(
            db, project_id=project.id, actor_user_id=user.id, action="coding_tool_skills.sync_inputs_pr_failed", entity_type="Repository",
            entity_id=repository.id, extra_data={"implementation_task_id": str(task.id), "branch_name": branch_name, "error": str(exc)},
        )
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"The file was committed to {branch_name}, but the pull request could not be opened: {exc}") from exc

    record_audit_log(
        db, project_id=project.id, actor_user_id=user.id, action="coding_tool_skills.sync_inputs", entity_type="Repository",
        entity_id=repository.id,
        extra_data={"implementation_task_id": str(task.id), "branch_name": branch_name, "committed": [file.path], "pull_request_url": pull_request_url},
    )
    db.commit()
    message = f"Opened a pull request with this task's input file. Merge it, then run `/implementation` for it."
    if snapshot.not_ready:
        message += (
            f" Note: {', '.join(snapshot.not_ready)} {'is' if len(snapshot.not_ready) == 1 else 'are'} not available yet, so that input was "
            "committed as a placeholder — complete it first, then run \"Sync this task's inputs\" again before running the command."
        )
    return SyncStoryInputsResponse(
        branch_name=branch_name, base_branch=base_branch, pull_request_url=pull_request_url, committed=[file.path],
        not_ready=snapshot.not_ready, message=message,
    )


@router.post("/stories/{story_id}/sync", response_model=SyncStoryStageResponse)
def sync_story_stage(
    project_id: uuid.UUID, story_id: uuid.UUID, payload: SyncStoryStageRequest, db: Session = Depends(get_db)
) -> SyncStoryStageResponse:
    """Pulls docs/sdlc/stories/<slug>/<stage>.md for this one story back in
    as a new StoryArtifact version, advancing its StoryDeliveryNode exactly
    the way that stage's own in-app agent does."""
    project = _project_or_404(db, project_id)
    story = _story_or_404(db, project_id, story_id)
    user = _user_or_400(db, payload.triggered_by_user_id)
    require_can_edit_stage(user, payload.stage)

    from app.services.coding_tool_skills import STAGE_SPECS

    spec = STAGE_SPECS.get(payload.stage)
    if spec is None or not spec.story_scoped:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{payload.stage}' is not a per-story coding-tool stage.")

    repository = _primary_repository(db, project_id)
    ref = payload.ref or repository.default_branch
    if not ref:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No ref given and this repository has no known default branch.")

    path = story_output_path(spec, story_slug(story.title))
    token = decrypt_repository_token(repository)
    try:
        file = github_api.read_file(token, repository.owner, repository.name, path, ref)
    except GitHubIntegrationError as exc:
        if exc.status_code == 404:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"'{path}' was not found on '{ref}'. Push the file (a branch is fine) and enter that branch name."
            ) from exc
        raise _github_error_to_http(exc) from exc
    if file.content is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"'{path}' is too large or is not a text file.")

    try:
        result = sync_story_stage_document(
            db, project=project, story=story, user=user, stage=payload.stage, markdown=file.content,
            source_label=f"{repository.owner}/{repository.name}@{ref}",
        )
    except StoryCodingToolError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, str(exc)) from exc
    db.commit()
    db.refresh(result.story_artifact)
    return SyncStoryStageResponse(
        story_artifact_id=result.story_artifact.id, version_number=result.story_artifact.version_number, node_status=result.node_status,
        ref=ref, path=path, generated_by=result.generated_by, created=result.created,
    )


@router.post("/stories/bulk-sync", response_model=BulkSyncStoryStageResponse)
def bulk_sync_story_stage(
    project_id: uuid.UUID, payload: BulkSyncStoryStageRequest, db: Session = Depends(get_db)
) -> BulkSyncStoryStageResponse:
    """The Stories list's "Pull Latest from GitHub" bulk action — the
    reverse direction of bulk-prepare. For every selected story, re-reads
    docs/sdlc/stories/<slug>/<stage>.md and saves it as a new version —
    exactly sync_story_stage's own logic, just looped with one story's
    failure (nothing pushed yet, no lane, a locked node) never stopping
    the rest of the batch."""
    project = _project_or_404(db, project_id)
    user = _user_or_400(db, payload.triggered_by_user_id)
    require_can_edit_stage(user, payload.stage)

    spec = STAGE_SPECS.get(payload.stage)
    if spec is None or not spec.story_scoped:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{payload.stage}' is not a per-story coding-tool stage.")

    repository = _primary_repository(db, project_id)
    ref = payload.ref or repository.default_branch
    if not ref:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No ref given and this repository has no known default branch.")
    token = decrypt_repository_token(repository)

    results: list[BulkSyncStoryStageStoryResult] = []
    for story_id in payload.story_ids:
        story = db.query(Story).filter(Story.id == story_id, Story.project_id == project_id).first()
        if story is None:
            results.append(BulkSyncStoryStageStoryResult(story_id=story_id, story_title="(not found)", status="skipped", reason=f"Story {story_id} not found in this project."))
            continue

        path = story_output_path(spec, story_slug(story.title))
        try:
            file = github_api.read_file(token, repository.owner, repository.name, path, ref)
        except GitHubIntegrationError as exc:
            reason = f"'{path}' was not found on '{ref}'." if exc.status_code == 404 else str(exc)
            results.append(BulkSyncStoryStageStoryResult(story_id=story.id, story_title=story.title, status="skipped", reason=reason))
            continue
        if file.content is None:
            results.append(
                BulkSyncStoryStageStoryResult(story_id=story.id, story_title=story.title, status="skipped", reason=f"'{path}' is too large or is not a text file.")
            )
            continue

        try:
            sync_result = sync_story_stage_document(
                db, project=project, story=story, user=user, stage=payload.stage, markdown=file.content,
                source_label=f"{repository.owner}/{repository.name}@{ref}",
            )
        except StoryCodingToolError as exc:
            db.rollback()
            results.append(BulkSyncStoryStageStoryResult(story_id=story.id, story_title=story.title, status="skipped", reason=str(exc)))
            continue
        db.commit()
        results.append(
            BulkSyncStoryStageStoryResult(story_id=story.id, story_title=story.title, status="synced", version_number=sync_result.story_artifact.version_number)
        )

    synced_count = sum(1 for r in results if r.status == "synced")
    skipped_count = len(results) - synced_count
    message = f"Synced {synced_count} stor{'y' if synced_count == 1 else 'ies'} from {ref}."
    if skipped_count:
        message += f" {skipped_count} skipped — see each story's own reason above."
    return BulkSyncStoryStageResponse(ref=ref, results=results, message=message)
