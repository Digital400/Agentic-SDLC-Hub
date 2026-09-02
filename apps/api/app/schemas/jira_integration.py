import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import IntegrationStatus, JiraSourceType
from app.schemas.validators import NonBlankStr


class ConnectJiraRequest(BaseModel):
    """Body for POST /jira/connections. `api_token` is write-only — it is
    verified against Jira, encrypted, and stored; it is never echoed back
    in this or any other response (see JiraConnectionRead, which
    deliberately has no token field). `base_url`/`email` are non-secret
    and stored plainly in Integration.config_json."""

    base_url: NonBlankStr = Field(..., description="e.g. https://yourcompany.atlassian.net")
    email: NonBlankStr = Field(..., description="The Atlassian account email the API token belongs to.")
    api_token: NonBlankStr = Field(..., description="A Jira Cloud API token (id.atlassian.com -> Security -> API tokens).")
    connected_by_id: uuid.UUID = Field(..., description="Existing user id.")


class JiraConnectionRead(BaseModel):
    """Deliberately has NO token field, encrypted or otherwise. `token_hint`
    (e.g. "****d3f9") is the only thing a UI can show about the stored
    credential."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    integration_id: uuid.UUID
    base_url: str
    email: str
    status: IntegrationStatus
    connected_by_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    token_hint: str
    connected_by_name: str | None

    @classmethod
    def from_orm_connection(cls, connection, *, base_url: str, email: str) -> "JiraConnectionRead":
        return cls(
            id=connection.id,
            integration_id=connection.integration_id,
            base_url=base_url,
            email=email,
            status=connection.status,
            connected_by_id=connection.connected_by_id,
            created_at=connection.created_at,
            updated_at=connection.updated_at,
            token_hint=f"****{connection.token_last_four}",
            connected_by_name=connection.connected_by.full_name if connection.connected_by else None,
        )


class CreateJiraProjectLinkRequest(BaseModel):
    project_id: uuid.UUID
    connection_id: uuid.UUID
    jira_project_key: NonBlankStr = Field(..., max_length=50)


class JiraProjectLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    connection_id: uuid.UUID
    jira_project_key: str
    jira_project_name: str | None
    created_at: datetime
    updated_at: datetime


class JiraIssueLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    jira_project_link_id: uuid.UUID
    source_type: JiraSourceType
    source_key: str
    source_label: str
    jira_issue_key: str
    jira_issue_type: str
    jira_issue_url: str
    parent_jira_issue_key: str | None
    jira_status: str | None
    last_synced_at: datetime | None
    created_at: datetime


class JiraPushItemRead(BaseModel):
    source_type: JiraSourceType
    source_key: str
    label: str
    jira_issue_type: str
    parent_source_key: str | None
    summary: str
    description: str
    validation_errors: list[str]
    already_linked: JiraIssueLinkRead | None

    @property
    def is_valid(self) -> bool:
        return len(self.validation_errors) == 0


class JiraPushPreviewRead(BaseModel):
    project_id: uuid.UUID
    jira_project_key: str
    epics: list[JiraPushItemRead]
    stories: list[JiraPushItemRead]
    implementation_tasks: list[JiraPushItemRead]
    testing_bugs: list[JiraPushItemRead]
    overall_errors: list[str]


class JiraPushSelectionItem(BaseModel):
    source_type: JiraSourceType
    source_key: NonBlankStr


class JiraPushRequest(BaseModel):
    project_id: uuid.UUID
    triggered_by_user_id: uuid.UUID
    selections: list[JiraPushSelectionItem] = Field(..., min_length=1)


class JiraPushResultItem(BaseModel):
    source_type: JiraSourceType
    source_key: str
    status: str  # "created" | "skipped_duplicate" | "skipped_invalid" | "failed"
    jira_issue_key: str | None = None
    jira_issue_url: str | None = None
    errors: list[str] = Field(default_factory=list)


class JiraPushResponse(BaseModel):
    results: list[JiraPushResultItem]


class JiraSyncStatusRequest(BaseModel):
    project_id: uuid.UUID


class JiraSyncStatusResponse(BaseModel):
    links: list[JiraIssueLinkRead]


# --- Jira sync per story (see app/services/story_jira_sync.py) -----------------------


class StoryJiraSubtaskPreviewRead(BaseModel):
    implementation_task_id: uuid.UUID
    title: str
    description: str
    validation_errors: list[str]
    already_linked: JiraIssueLinkRead | None

    @property
    def is_valid(self) -> bool:
        return len(self.validation_errors) == 0


class StoryJiraPreviewRead(BaseModel):
    story_id: uuid.UUID
    summary: str
    description: str
    priority: str | None
    story_points: int | None
    sprint_name: str | None
    labels: list[str] = Field(default_factory=list)
    subtasks: list[StoryJiraSubtaskPreviewRead]
    validation_errors: list[str]
    already_linked: JiraIssueLinkRead | None

    @property
    def is_valid(self) -> bool:
        return len(self.validation_errors) == 0


class BulkPreviewStoriesToJiraRequest(BaseModel):
    """Requirement 4 — preview several stories' Jira payloads in one
    call, e.g. to render every checkbox-selected story's summary/
    description/validation state before offering the bulk-sync
    confirmation. Never syncs anything by itself."""

    story_ids: list[uuid.UUID] = Field(..., min_length=1)


class BulkStoryJiraPreviewResponse(BaseModel):
    previews: list[StoryJiraPreviewRead]


class SyncStoryToJiraRequest(BaseModel):
    triggered_by_user_id: uuid.UUID


class SubtaskJiraSyncResultRead(BaseModel):
    implementation_task_id: uuid.UUID
    status: str  # "created" | "skipped_duplicate" | "skipped_invalid" | "failed"
    jira_issue_key: str | None = None
    jira_issue_url: str | None = None
    errors: list[str] = Field(default_factory=list)


class StoryJiraSyncResultRead(BaseModel):
    story_id: uuid.UUID
    status: str  # "created" | "skipped_duplicate" | "skipped_invalid" | "failed"
    jira_issue_key: str | None = None
    jira_issue_url: str | None = None
    errors: list[str] = Field(default_factory=list)
    subtasks: list[SubtaskJiraSyncResultRead] = Field(default_factory=list)


class BulkSyncStoriesToJiraRequest(BaseModel):
    """Rule: bulk sync still needs preview and confirmation — this
    endpoint never selects stories on its own; `story_ids` must be the
    exact, human-confirmed set (typically every id a bulk-preview call
    just returned and the user then approved)."""

    story_ids: list[uuid.UUID] = Field(..., min_length=1)
    triggered_by_user_id: uuid.UUID


class BulkStoryJiraSyncResponse(BaseModel):
    results: list[StoryJiraSyncResultRead]
