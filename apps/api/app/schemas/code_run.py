import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CodeRunStatus


class CodeRunLogEntryRead(BaseModel):
    timestamp: str
    level: str
    message: str


class CodeRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    story_id: uuid.UUID
    lane_id: uuid.UUID | None
    repository_id: uuid.UUID
    triggered_by_user_id: uuid.UUID | None
    branch_name: str
    status: CodeRunStatus
    started_at: datetime | None
    completed_at: datetime | None
    logs: list[CodeRunLogEntryRead]
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ApplyViaCodeRunnerRequest(BaseModel):
    implementation_run_id: uuid.UUID = Field(..., description="An ACCEPTED, story-scoped ImplementationRun.")
    triggered_by_user_id: uuid.UUID = Field(..., description="Existing user id — who triggered the apply.")
    base_branch: str | None = Field(default=None, description="Defaults to the repository's default_branch.")
    test_commands: list[str] = Field(
        default_factory=list,
        description="Configured test commands to run in the workspace before committing — each checked against "
        "the server's allowlisted test executables (see app/core/config.py's CODE_RUNNER_ALLOWED_TEST_EXECUTABLES).",
    )
