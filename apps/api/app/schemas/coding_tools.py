import uuid
from typing import Literal

from pydantic import BaseModel, Field

CodingTool = Literal["claude_code", "codex", "opencode", "cursor"]


class SkillFileRead(BaseModel):
    path: str
    purpose: str
    managed: bool
    content: str


class SkillPackRead(BaseModel):
    tool: CodingTool
    tool_label: str
    stage: str
    files: list[SkillFileRead]
    usage: list[str]
    notes: list[str]


class InstallSkillsRequest(BaseModel):
    tool: CodingTool
    stage: str = "requirement_intake"
    triggered_by_user_id: uuid.UUID
    base_branch: str | None = Field(default=None, description="Defaults to the repository's default branch.")


class SkippedFileRead(BaseModel):
    path: str
    reason: str


class InstallSkillsResponse(BaseModel):
    branch_name: str | None
    base_branch: str
    pull_request_url: str | None
    committed: list[str]
    skipped: list[SkippedFileRead]
    message: str


class SyncStageRequest(BaseModel):
    stage: str = "requirement_intake"
    triggered_by_user_id: uuid.UUID
    ref: str | None = Field(default=None, description="Branch, tag or sha to read; defaults to the repository's default branch.")


class SyncStageResponse(BaseModel):
    artifact_id: uuid.UUID
    artifact_version_id: uuid.UUID
    version_number: int
    artifact_status: str
    workflow_node_status: str
    ref: str
    file_sha: str
    path: str
    generated_by: str | None
    created_artifact: bool


# --- Per-story delivery lane stages (Story LLD, Implementation Plan, Test Scenarios) --------


class SyncStoryInputsRequest(BaseModel):
    stage: str
    triggered_by_user_id: uuid.UUID
    base_branch: str | None = Field(default=None, description="Defaults to the repository's default branch.")


class SyncStoryInputsResponse(BaseModel):
    branch_name: str | None
    base_branch: str
    pull_request_url: str | None
    committed: list[str]
    # Display names of upstream documents that aren't ready yet (e.g.
    # ["Story LLD"]) — that input was committed as a placeholder; empty
    # means everything this stage needs is actually available right now.
    not_ready: list[str]
    message: str


class BulkPrepareCodingToolRequest(BaseModel):
    """One click across many stories, from the Stories list — for each
    story: create its delivery lane if it doesn't have one yet, then
    commit its `stage` input snapshot. Every story's files land in ONE
    shared branch/PR, not one PR per story — see
    app/api/routes/coding_tools.py's bulk_prepare_coding_tool."""

    story_ids: list[uuid.UUID] = Field(..., min_length=1)
    stage: str = "story_lld"
    triggered_by_user_id: uuid.UUID
    base_branch: str | None = Field(default=None, description="Defaults to the repository's default branch.")


class BulkPrepareCodingToolStoryResult(BaseModel):
    story_id: uuid.UUID
    story_title: str
    status: Literal["prepared", "skipped"]
    # Set when status == "prepared".
    lane_created: bool = False
    committed_paths: list[str] = Field(default_factory=list)
    not_ready: list[str] = Field(default_factory=list)
    # Set when status == "skipped" (story not found, no permission to
    # create its lane yet, Story LLD not approved, etc.) — the exact
    # reason, never silently dropped from the response.
    reason: str | None = None


class BulkPrepareCodingToolResponse(BaseModel):
    branch_name: str | None
    base_branch: str
    pull_request_url: str | None
    results: list[BulkPrepareCodingToolStoryResult]
    message: str


class SyncImplementationTaskInputsRequest(BaseModel):
    triggered_by_user_id: uuid.UUID
    base_branch: str | None = Field(default=None, description="Defaults to the repository's default branch.")


class SyncStoryStageRequest(BaseModel):
    stage: str
    triggered_by_user_id: uuid.UUID
    ref: str | None = Field(default=None, description="Branch, tag or sha to read; defaults to the repository's default branch.")


class SyncStoryStageResponse(BaseModel):
    story_artifact_id: uuid.UUID
    version_number: int
    node_status: str
    ref: str
    path: str
    generated_by: str | None
    created: bool


class BulkSyncStoryStageRequest(BaseModel):
    """The Stories list's "Pull Latest from GitHub" bulk action — the
    reverse of bulk-prepare: for every selected story, read back whatever
    is currently at docs/sdlc/stories/<slug>/<stage>.md (e.g. a human or
    an external coding tool refined what bulk-prepare originally pushed)
    and save it as this story's new version, same as sync_story_stage does
    for one story. See app/api/routes/coding_tools.py's
    bulk_sync_story_stage."""

    story_ids: list[uuid.UUID] = Field(..., min_length=1)
    stage: str
    triggered_by_user_id: uuid.UUID
    ref: str | None = Field(default=None, description="Branch, tag or sha to read; defaults to the repository's default branch.")


class BulkSyncStoryStageStoryResult(BaseModel):
    story_id: uuid.UUID
    story_title: str
    status: Literal["synced", "skipped"]
    version_number: int | None = None
    # Set when status == "skipped" — e.g. nothing has been pushed for this
    # story/stage yet, or this story has no delivery lane.
    reason: str | None = None


class BulkSyncStoryStageResponse(BaseModel):
    ref: str
    results: list[BulkSyncStoryStageStoryResult]
    message: str
