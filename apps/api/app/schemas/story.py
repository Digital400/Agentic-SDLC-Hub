import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import SprintStatus, SprintStoryStatus, StoryJiraSyncStatus, StoryStatus, StoryType


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


class DraftStoryImplementationPlanRequest(BaseModel):
    triggered_by_user_id: uuid.UUID


class DraftStoryImplementationPlanResponse(BaseModel):
    needs_clarification: bool
    story_artifact: StoryArtifactRead | None
    node_status: str


class DraftStoryTestScenariosRequest(BaseModel):
    triggered_by_user_id: uuid.UUID


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
