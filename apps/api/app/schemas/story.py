import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import SprintStatus, SprintStoryStatus, StoryJiraSyncStatus, StoryStatus, StoryType
from app.schemas.agent_run import ProviderOverride

# Shared by every Draft*Request below (Story LLD, Implementation Plan, Test
# Scenarios) — see app/schemas/agent_run.py's own fields on AgentRunCreate
# for the project-level equivalent. Documented once here rather than
# repeating the same two docstrings three times.
_PROVIDER_OVERRIDE_DOC = (
    "Force this one draft to use a specific LLM backend instead of the project's default "
    "auto-selected one (see app/services/ai_generation.py's get_active_provider). 'claude_agent_sdk' "
    "requires the project to have a connected GitHub repository; any other value requires that "
    "provider's own API key to actually be configured in the backend's environment."
)
_MODEL_OVERRIDE_DOC = (
    "Force this one draft to use a specific model within provider_override's provider (e.g. "
    "'claude-opus-5'). Ignored unless provider_override is also set."
)


class SyncStoriesFromBacklogRequest(BaseModel):
    story_type: StoryType = Field(..., description="Which Story Crafting mode produced this backlog generation.")
    triggered_by_user_id: uuid.UUID


class StoryCreate(BaseModel):
    """Direct story creation — POST /stories — distinct from
    sync-from-backlog: no story_backlog document is required or parsed;
    a human (or another system) states a story's fields outright."""

    project_id: uuid.UUID
    title: str = Field(..., max_length=500)
    description: str = ""
    mode: StoryType = Field(..., description="VERTICAL or HORIZONTAL — see Story.story_type.")
    user_story: str = ""
    business_value: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    priority: str = Field(default="", max_length=50)
    story_points: int | None = Field(default=None, ge=0, le=100)
    suggested_owner_role: str | None = None
    sprint_id: uuid.UUID | None = None
    created_by_id: uuid.UUID


class StoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    source_artifact_version_id: uuid.UUID | None
    story_type: StoryType
    status: StoryStatus
    epic: str
    feature: str
    title: str
    description: str
    user_story: str
    priority: str
    dependencies: str
    acceptance_criteria: list[str]
    definition_of_done: list[str]
    suggested_owner_role: str | None
    story_points: int | None
    estimated_pr_review_time: str
    estimated_review_worst_case_minutes: int | None
    business_value: str
    technical_areas: list[str]
    jira_issue_type: str
    suggested_subtasks: list[str]
    release_readiness_criteria: list[str]
    owner_user_id: uuid.UUID | None
    sprint_id: uuid.UUID | None
    lane_created_at: datetime | None
    created_by_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    # Real, stored fields (requirement 6 — key, id, and URL, plus the
    # sync state machine — see app/models/story.py).
    jira_sync_status: StoryJiraSyncStatus = StoryJiraSyncStatus.NOT_SYNCED
    jira_issue_id: str | None = None

    # Computed, not stored on Story itself — see
    # app/api/routes/stories.py's _story_to_read. jira_issue_key/url
    # prefer Story's own stored value (requirement 6) but still fall back
    # to the live JiraIssueLink for a story synced before those columns
    # existed.
    lane_status: str = "No lane"
    jira_status: str = "Not Synced"
    jira_issue_key: str | None = None
    jira_issue_url: str | None = None


class StoryListResponse(BaseModel):
    items: list[StoryRead]
    total: int


class SyncStoriesResponse(BaseModel):
    created: list[StoryRead]
    already_existed: int
    # Total `## Story: <title>` blocks parse_story_backlog found in the
    # approved document, before dedup against already-persisted titles —
    # `created` and `already_existed` alone can't distinguish "the
    # backlog has 0 stories in it" (a real drafting/formatting problem)
    # from "every story already synced" (nothing to do); the UI needs
    # this to tell those apart and never silently show "nothing happened."
    parsed_count: int


class StoryUpdate(BaseModel):
    """All fields optional except the actor — only what's provided changes.
    Editing any of the copied backlog fields here does NOT touch the
    original story_backlog artifact/version — see Story's class docstring."""

    title: str | None = Field(default=None, max_length=500)
    description: str | None = None
    user_story: str | None = None
    priority: str | None = Field(default=None, max_length=50)
    dependencies: str | None = None
    acceptance_criteria: list[str] | None = None
    definition_of_done: list[str] | None = None
    suggested_owner_role: str | None = None
    story_points: int | None = Field(default=None, ge=0, le=100)
    estimated_pr_review_time: str | None = Field(default=None, max_length=120)
    estimated_review_worst_case_minutes: int | None = Field(default=None, ge=0, le=1000)
    business_value: str | None = None
    technical_areas: list[str] | None = None
    jira_issue_type: str | None = Field(default=None, max_length=50)
    jira_issue_key: str | None = Field(default=None, max_length=50)
    suggested_subtasks: list[str] | None = None
    release_readiness_criteria: list[str] | None = None
    # Deliberately no owner_user_id here — see POST /stories/{id}/assign,
    # the dedicated endpoint that also preserves assignment history via
    # StoryAssignee.
    updated_by_id: uuid.UUID = Field(..., description="Existing user id — the actor making this change.")


class AssignStoryOwnerRequest(BaseModel):
    owner_user_id: uuid.UUID
    assigned_by_id: uuid.UUID


class CreateStoryLaneRequest(BaseModel):
    triggered_by_user_id: uuid.UUID


class StoryDeliveryNodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lane_id: uuid.UUID
    node_key: str
    name: str
    status: str
    assigned_role: str | None
    assigned_user_id: uuid.UUID | None
    requires_approval: bool
    blocked_reason: str | None
    started_at: datetime | None
    completed_at: datetime | None
    order_index: int
    created_at: datetime
    updated_at: datetime


class StoryDeliveryEdgeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lane_id: uuid.UUID
    source_node_id: uuid.UUID
    target_node_id: uuid.UUID
    label: str | None


class StoryDeliveryLaneRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    story_id: uuid.UUID
    current_node_id: uuid.UUID | None
    status: str
    created_by_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class UpdateLaneNodeStatusRequest(BaseModel):
    status: str = Field(..., description="One of StoryDeliveryNodeStatus's values.")
    actor_user_id: uuid.UUID
    blocked_reason: str | None = None
    assigned_user_id: uuid.UUID | None = None


class DraftStoryLldRequest(BaseModel):
    triggered_by_user_id: uuid.UUID
    # When the previous draft came back needing clarification (see
    # DraftStoryLldResponse.needs_clarification), a human's answers to
    # those questions — fed into the next attempt's freeform context so
    # the agent isn't just asked the identical question again. Optional;
    # omitted (or blank) on a first attempt.
    clarification_answers: str | None = None
    provider_override: ProviderOverride | None = Field(default=None, description=_PROVIDER_OVERRIDE_DOC)
    model_override: str | None = Field(default=None, max_length=200, description=_MODEL_OVERRIDE_DOC)


class StoryArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    story_id: uuid.UUID
    lane_id: uuid.UUID | None
    node_id: uuid.UUID | None
    artifact_type: str
    title: str
    content_markdown: str
    version_number: int
    created_by_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class DraftStoryLldResponse(BaseModel):
    needs_clarification: bool
    story_artifact: StoryArtifactRead | None
    node_status: str


class BulkRunStoryLldRequest(BaseModel):
    """The Stories list's "Run Story LLD" bulk button — one click across
    many stories instead of opening each one's own workspace and clicking
    Draft. For each story: create its delivery lane if missing, auto-
    complete Story Ready if it's still the only thing blocking STORY_LLD,
    then draft the Story LLD. See app/api/routes/stories.py's
    bulk_run_story_lld."""

    story_ids: list[uuid.UUID] = Field(..., min_length=1)
    triggered_by_user_id: uuid.UUID


class BulkRunStoryLldStoryResult(BaseModel):
    story_id: uuid.UUID
    story_title: str
    status: Literal["drafted", "already_drafted", "needs_clarification", "skipped"]
    lane_created: bool = False
    # Set for "needs_clarification" (why — the agent's own questions are in
    # the drafted StoryArtifact itself, not repeated here) and "skipped"
    # (why this story couldn't even start, e.g. HLD not approved yet).
    reason: str | None = None


class BulkRunStoryLldResponse(BaseModel):
    results: list[BulkRunStoryLldStoryResult]
    # The subset of `results` that still needs a human's attention before
    # this story can move on — every "needs_clarification" and "skipped"
    # entry, surfaced separately so nothing is missed in the full list.
    remaining: list[BulkRunStoryLldStoryResult]
    message: str


class BulkApproveLaneNodeRequest(BaseModel):
    """The Stories list's bulk "Approve & Unlock Next" action — a human
    with the right role (Tech Lead for LLD_REVIEW, assignee/Tech Lead for
    IMPLEMENTATION_PLAN, QA/Tech Lead for TEST_SCENARIOS) explicitly
    approves one review gate across many stories at once, instead of
    clicking through each story's own workspace. This is still a real,
    role-checked, audited human approval for every story — it reuses
    app/api/routes/stories.py's own update_lane_node_status and its
    existing role/precondition checks unchanged; it only saves the clicks,
    it never bypasses the gate. See bulk_approve_lane_node."""

    node_key: str = Field(..., description="One of LLD_REVIEW, IMPLEMENTATION_PLAN, TEST_SCENARIOS.")
    story_ids: list[uuid.UUID] = Field(..., min_length=1)
    triggered_by_user_id: uuid.UUID


class BulkApproveLaneNodeStoryResult(BaseModel):
    story_id: uuid.UUID
    story_title: str
    status: Literal["approved", "already_approved", "skipped"]
    # Set for "skipped" — not ready yet, missing a precondition
    # (e.g. no Implementation Plan drafted), or the caller's role isn't
    # allowed to approve this gate.
    reason: str | None = None


class BulkApproveLaneNodeResponse(BaseModel):
    results: list[BulkApproveLaneNodeStoryResult]
    message: str


class BulkStartImplementationRequest(BaseModel):
    """Story-wise bulk code Implementation — for every selected story,
    starts a real Implementation Agent run for that story's own NEXT
    runnable task. A story's own tasks still run strictly in order
    (DATABASE -> BACKEND -> FRONTEND, see start_implementation_run's own
    sequencing gate) — this never starts more than one task per story per
    call, and it never creates or merges a pull request; that stays a
    deliberate, reviewed, per-run action. See
    app/api/routes/stories.py's bulk_start_implementation."""

    story_ids: list[uuid.UUID] = Field(..., min_length=1)
    triggered_by_user_id: uuid.UUID
    provider_override: ProviderOverride | None = Field(default=None, description=_PROVIDER_OVERRIDE_DOC)
    model_override: str | None = Field(default=None, max_length=200, description=_MODEL_OVERRIDE_DOC)


class BulkStartImplementationStoryResult(BaseModel):
    story_id: uuid.UUID
    story_title: str
    status: Literal["started", "awaiting_review", "all_complete", "skipped"]
    task_area: str | None = None
    task_title: str | None = None
    implementation_run_id: uuid.UUID | None = None
    run_status: str | None = None
    # Set for "skipped" (a real precondition failed — e.g. Implementation
    # Plan not accepted yet, no repository connected) and "awaiting_review"
    # (the next task already has a run a human hasn't reviewed yet).
    reason: str | None = None


class BulkStartImplementationResponse(BaseModel):
    results: list[BulkStartImplementationStoryResult]
    message: str


class DraftStoryImplementationPlanRequest(BaseModel):
    triggered_by_user_id: uuid.UUID
    clarification_answers: str | None = None
    provider_override: ProviderOverride | None = Field(default=None, description=_PROVIDER_OVERRIDE_DOC)
    model_override: str | None = Field(default=None, max_length=200, description=_MODEL_OVERRIDE_DOC)


class DraftStoryImplementationPlanResponse(BaseModel):
    needs_clarification: bool
    story_artifact: StoryArtifactRead | None
    node_status: str


class DraftStoryTestScenariosRequest(BaseModel):
    triggered_by_user_id: uuid.UUID
    clarification_answers: str | None = None
    provider_override: ProviderOverride | None = Field(default=None, description=_PROVIDER_OVERRIDE_DOC)
    model_override: str | None = Field(default=None, max_length=200, description=_MODEL_OVERRIDE_DOC)


class DraftStoryTestScenariosResponse(BaseModel):
    needs_clarification: bool
    story_artifact: StoryArtifactRead | None
    node_status: str


class SprintCreate(BaseModel):
    """Stories are NOT added here — see POST /sprints/{id}/stories. A
    sprint always starts empty; membership is its own explicit action."""

    project_id: uuid.UUID
    name: str = Field(..., max_length=255)
    goal: str = ""
    start_date: date | None = None
    end_date: date | None = None
    capacity_points: int | None = Field(default=None, ge=0)
    created_by_id: uuid.UUID


class SprintUpdate(BaseModel):
    """All fields optional except the actor — only what's provided
    changes. Refused against a COMPLETED sprint unless the actor is
    ADMIN — see require_sprint_editable in app/api/routes/stories.py."""

    name: str | None = Field(default=None, max_length=255)
    goal: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    capacity_points: int | None = Field(default=None, ge=0)
    updated_by_id: uuid.UUID = Field(..., description="Existing user id — the actor making this change.")


class SprintRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    goal: str
    start_date: date | None
    end_date: date | None
    capacity_points: int | None
    status: SprintStatus
    created_by_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class AddStoryToSprintRequest(BaseModel):
    story_id: uuid.UUID
    planned_points: int | None = Field(default=None, ge=0, le=100)
    assigned_owner_id: uuid.UUID | None = None
    actor_user_id: uuid.UUID = Field(..., description="Existing user id — the actor adding this story.")


class RemoveStoryFromSprintRequest(BaseModel):
    actor_user_id: uuid.UUID


class UpdateSprintStoryRequest(BaseModel):
    """PATCH /sprints/{id}/stories/{story_id} — re-estimate or reassign a
    story already in the sprint, without removing and re-adding it (which
    would needlessly churn the SprintStory row's history)."""

    planned_points: int | None = Field(default=None, ge=0, le=100)
    assigned_owner_id: uuid.UUID | None = None
    actor_user_id: uuid.UUID


class SprintLifecycleRequest(BaseModel):
    """POST /sprints/{id}/start and /complete — no other fields needed,
    the transition itself is the whole action."""

    actor_user_id: uuid.UUID


class SprintStoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sprint_id: uuid.UUID
    story_id: uuid.UUID
    planned_points: int | None
    assigned_owner_id: uuid.UUID | None
    status: SprintStoryStatus
    created_at: datetime
    updated_at: datetime


class SprintBoardItem(BaseModel):
    sprint_story: SprintStoryRead
    story: StoryRead


class SprintBoardRead(BaseModel):
    sprint: SprintRead
    items: list[SprintBoardItem]
    planned_points_total: int
    capacity_points: int | None
    over_capacity: bool


class GeneratePlanRequest(BaseModel):
    triggered_by_user_id: uuid.UUID
    # Only used by generate-release-plan (release_planning requires human
    # approval); ignored by generate-plan (sprint_planning does not).
    reviewer_id: uuid.UUID | None = None


# --- Recommended implementation order (see app/services/story_sequencing.py) ----------------


class StoryOrderWave(BaseModel):
    """Stories with no ordering constraint between them — can be worked in
    parallel. Waves are returned in recommended order; a story in wave 2
    depends (directly or transitively) on something in wave 1."""

    stories: list[StoryRead]


class StoryOrderResponse(BaseModel):
    waves: list[StoryOrderWave]
    # story_id -> the leftover text from its Dependencies field that didn't
    # match any known story title (a likely typo, or a renamed/deleted
    # story) — present only when something didn't resolve.
    unresolved_dependencies: dict[uuid.UUID, str]
    # Stories whose Dependencies form a cycle and so have no valid order —
    # reported rather than silently dropped or arbitrarily ordered.
    circular: list[StoryRead]


# --- Re-checking a story against the current approved backlog (see -------------------------
# app/api/routes/stories.py's get_story_backlog_diff/apply_story_backlog_diff) ---------------


class StoryFieldChange(BaseModel):
    field: str
    current: str
    proposed: str


class StoryBacklogDiffRead(BaseModel):
    story_id: uuid.UUID
    # False when this story's title no longer appears in the current
    # approved backlog at all (renamed, or removed) — `changes` is then
    # always empty; there is nothing here to apply.
    found_in_backlog: bool
    source_version_number: int
    changes: list[StoryFieldChange]
    up_to_date: bool
    # True when this story already has an active delivery lane — applying
    # a change (e.g. to Acceptance Criteria) after work has started may
    # make an already-drafted LLD/Implementation Plan stale. Shown so a
    # human can decide, never blocked automatically.
    lane_active: bool


class ApplyStoryBacklogDiffRequest(BaseModel):
    triggered_by_user_id: uuid.UUID
    # Field names to apply (as named in StoryFieldChange.field) — omit to
    # apply every changed field.
    fields: list[str] | None = None
