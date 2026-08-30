import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ProjectStatus
from app.schemas.validators import NonBlankStr


class ProjectCreate(BaseModel):
    name: NonBlankStr = Field(..., max_length=255, description="Required; whitespace-only is rejected.")
    business_owner: NonBlankStr = Field(..., max_length=255, description="Required; whitespace-only is rejected.")
    description: str | None = None
    created_by_id: uuid.UUID = Field(..., description="Existing user id; becomes the project's OWNER member.")
    # Optional override of which workflow template file (in WORKFLOWS_DIR)
    # to generate the project's nodes/edges from. Defaults to
    # settings.DEFAULT_WORKFLOW_FILE (sdlc-workflow.json) when omitted.
    workflow_template_file: str | None = None


class ProjectUpdate(BaseModel):
    """All fields optional (except the actor) — only the ones provided are
    changed.

    `current_stage`, if provided, must match the `node_key` of one of this
    project's own workflow nodes (validated in the route, since it depends
    on the project's generated graph, not just the request body).
    """

    name: NonBlankStr | None = Field(default=None, max_length=255)
    business_owner: NonBlankStr | None = Field(default=None, max_length=255)
    description: str | None = None
    current_stage: str | None = None
    updated_by_id: uuid.UUID = Field(
        ..., description="Existing user id — checked against app/services/permissions.py's project-update rule."
    )


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    business_owner: str
    current_stage: str
    status: ProjectStatus
    workflow_template_id: str
    workflow_template_version: str
    created_by_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    items: list[ProjectRead]
    total: int
