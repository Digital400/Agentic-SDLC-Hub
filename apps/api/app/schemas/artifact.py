import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ArtifactStatus
from app.schemas.validators import NonBlankStr


class ArtifactCreate(BaseModel):
    project_id: uuid.UUID
    workflow_node_id: uuid.UUID = Field(..., description="Must belong to project_id.")
    artifact_type: NonBlankStr = Field(..., max_length=100)
    title: NonBlankStr = Field(..., max_length=255)
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

    # Denormalized for list/detail views — avoids the frontend needing a
    # separate project/node/version fetch just to render a document row.
    project_name: str
    workflow_stage_name: str
    current_version_number: int | None

    @classmethod
    def from_orm_artifact(cls, artifact) -> "ArtifactRead":
        return cls(
            id=artifact.id,
            project_id=artifact.project_id,
            workflow_node_id=artifact.workflow_node_id,
            artifact_type=artifact.artifact_type,
            title=artifact.title,
            current_version_id=artifact.current_version_id,
            status=artifact.status,
            created_by_id=artifact.created_by_id,
            created_at=artifact.created_at,
            updated_at=artifact.updated_at,
            project_name=artifact.project.name,
            workflow_stage_name=artifact.workflow_node.name,
            current_version_number=artifact.current_version.version_number if artifact.current_version else None,
        )


class ArtifactVersionCreate(BaseModel):
    content_markdown: NonBlankStr
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

    # Denormalized for version-history display.
    created_by_name: str

    # Compression summaries — see app/services/artifact_summary.py. Null
    # until this version is approved (see app/api/routes/reviews.py's
    # approve_review, which is the only place these are (re)generated).
    executive_summary: str | None
    agent_context_summary: str | None
    key_decisions: list[str] | None
    open_questions: list[str] | None
    risks: list[str] | None
    generated_summary_at: datetime | None

    @classmethod
    def from_orm_version(cls, version) -> "ArtifactVersionRead":
        return cls(
            id=version.id,
            artifact_id=version.artifact_id,
            version_number=version.version_number,
            content_markdown=version.content_markdown,
            content_json=version.content_json,
            change_summary=version.change_summary,
            created_by_id=version.created_by_id,
            created_at=version.created_at,
            created_by_name=version.created_by.full_name,
            executive_summary=version.executive_summary,
            agent_context_summary=version.agent_context_summary,
            key_decisions=version.key_decisions,
            open_questions=version.open_questions,
            risks=version.risks,
            generated_summary_at=version.generated_summary_at,
        )


class GithubPrPreviewRead(BaseModel):
    """Response for GET /artifacts/{id}/github-pr-preview — see
    app/services/github_export.py. No real GitHub connection exists; this
    is what a human pastes into a real PR's title/description boxes."""

    suggested_title: str
    description_markdown: str
    checklist: list[str]


class ArtifactContentUpdate(BaseModel):
    """Body for PATCH /artifacts/{id} — edits the CURRENT version's content
    in place. Only allowed while the artifact is DRAFT; see the route for
    why this is a separate concept from "create a new version"."""

    content_markdown: NonBlankStr
    content_json: dict[str, Any] | None = None
    change_summary: str | None = None
    edited_by_id: uuid.UUID = Field(
        ..., description="Existing user id — checked against app/services/permissions.py's stage-edit rule."
    )
