import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AgentPromptRole
from app.schemas.validators import NonBlankStr


class AgentPromptCreate(BaseModel):
    """Creates the first version (v1) of a prompt for an (agent, role) pair.
    Use POST /prompts/{id}/versions to add a later version instead."""

    agent_key: str = Field(..., description="Must match an existing AgentDefinition.")
    role: AgentPromptRole = AgentPromptRole.DRAFT
    name: NonBlankStr = Field(..., max_length=255)
    stage: NonBlankStr = Field(..., max_length=100, description="Workflow node_key this prompt targets, e.g. 'hld'.")
    system_prompt: NonBlankStr
    output_format: NonBlankStr
    validation_checklist: list[str] = Field(default_factory=list)


class AgentPromptVersionCreate(BaseModel):
    """Body for POST /prompts/{id}/versions — a full new version, not a
    partial patch (mirrors ArtifactVersionCreate)."""

    name: NonBlankStr = Field(..., max_length=255)
    stage: NonBlankStr = Field(..., max_length=100)
    system_prompt: NonBlankStr
    output_format: NonBlankStr
    validation_checklist: list[str] = Field(default_factory=list)
    created_by_id: uuid.UUID = Field(
        ..., description="Existing user id — checked against app/services/permissions.py's prompt-update rule."
    )


class AgentPromptUpdate(BaseModel):
    """Body for PATCH /prompts/{id} — edits this version in place. Only
    allowed while it isn't the active version (see the route)."""

    name: NonBlankStr | None = Field(default=None, max_length=255)
    stage: NonBlankStr | None = Field(default=None, max_length=100)
    system_prompt: NonBlankStr | None = None
    output_format: NonBlankStr | None = None
    validation_checklist: list[str] | None = None
    updated_by_id: uuid.UUID = Field(
        ..., description="Existing user id — checked against app/services/permissions.py's prompt-update rule."
    )


class AgentPromptActivateRequest(BaseModel):
    activated_by_id: uuid.UUID = Field(
        ..., description="Existing user id — checked against app/services/permissions.py's prompt-update rule."
    )


class AgentPromptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_definition_id: uuid.UUID
    agent_key: str
    role: AgentPromptRole
    version: int
    name: str
    stage: str
    system_prompt: str
    output_format: str
    validation_checklist: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_prompt(cls, prompt) -> "AgentPromptRead":
        """`agent_key` lives on the related AgentDefinition, not this row
        itself — build the response with it resolved rather than adding a
        denormalized column."""
        return cls(
            id=prompt.id,
            agent_definition_id=prompt.agent_definition_id,
            agent_key=prompt.agent_definition.agent_key,
            role=prompt.role,
            version=prompt.version,
            name=prompt.name,
            stage=prompt.stage,
            system_prompt=prompt.system_prompt,
            output_format=prompt.output_format,
            validation_checklist=prompt.validation_checklist,
            is_active=prompt.is_active,
            created_at=prompt.created_at,
            updated_at=prompt.updated_at,
        )
