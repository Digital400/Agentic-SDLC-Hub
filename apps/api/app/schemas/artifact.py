import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ArtifactStatus


class ArtifactCreate(BaseModel):
    project_id: uuid.UUID
    workflow_node_id: uuid.UUID = Field(..., description="Must belong to project_id.")
    artifact_type: str = Field(..., min_length=1, max_length=100)
    title: str = Field(..., min_length=1, max_length=255)
    created_by_id: uuid.UUID = Field(..., description="Existing user id.")


class ArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    workflow_node_id: uuid.UUID
    artifact_type: str
    title: str
    current_version_id: uuid.UUID | None
    status: ArtifactStatus
    created_by_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ArtifactVersionCreate(BaseModel):
    content_markdown: str = Field(..., min_length=1)
    content_json: dict[str, Any] | None = None
    change_summary: str | None = None
    created_by_id: uuid.UUID = Field(..., description="Existing user id.")


class ArtifactVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    artifact_id: uuid.UUID
    version_number: int
    content_markdown: str
    content_json: dict[str, Any] | None
    change_summary: str | None
    created_by_id: uuid.UUID
    created_at: datetime


class ArtifactContentUpdate(BaseModel):
    """Body for PATCH /artifacts/{id} — edits the CURRENT version's content
    in place. Only allowed while the artifact is DRAFT; see the route for
    why this is a separate concept from "create a new version"."""

    content_markdown: str = Field(..., min_length=1)
    content_json: dict[str, Any] | None = None
    change_summary: str | None = None
