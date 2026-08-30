import uuid

from pydantic import BaseModel, ConfigDict


class JiraFieldMappingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    epic: str | None
    labels: list[str]
    summary: str
    description: str
    priority: str | None
    linked_issues_placeholder: list[str]


class StoryJiraPreviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    story_title: str
    jira_issue_type: str
    mapping: JiraFieldMappingRead
    validation_errors: list[str]
    is_valid: bool


class JiraExportPreviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    artifact_id: uuid.UUID
    artifact_title: str
    story_count: int
    valid_story_count: int
    has_errors: bool
    overall_errors: list[str]
    stories: list[StoryJiraPreviewRead]
    # Always false — see app/services/jira_export.py's PUSH_TO_JIRA_ENABLED.
    # Present so the frontend's "Push to Jira" button reads its disabled
    # state from the API rather than hardcoding it twice.
    push_to_jira_enabled: bool
