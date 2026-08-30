import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AgentPromptRole, AgentRunStatus, LoopStatus, LoopStepType


class AgentRunCreate(BaseModel):
    project_id: uuid.UUID
    workflow_node_id: uuid.UUID = Field(..., description="Must belong to project_id.")
    action: AgentPromptRole = AgentPromptRole.DRAFT
    input_artifact_ids: list[uuid.UUID] = Field(default_factory=list, description="Must all exist.")
    triggered_by_user_id: uuid.UUID = Field(..., description="Existing user id — attributes the resulting artifact version.")
    input_context: dict[str, Any] = Field(default_factory=dict, description="Freeform extra context, e.g. a stakeholder request.")


class AgentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    workflow_node_id: uuid.UUID
    agent_key: str
    prompt_version: int | None
    action: AgentPromptRole
    status: AgentRunStatus
    input_artifact_ids: list[uuid.UUID]
    input_context: dict[str, Any]
    output_text: str | None
    output_artifact_id: uuid.UUID | None
    error_message: str | None
    token_usage: dict[str, Any] | None
    cost: float | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    # Knowledge Base chunks retrieved for this run's context — see
    # app/services/retrieval.py. Each entry: source_id, source_title,
    # chunk_id, chunk_index, snippet, similarity. None means retrieval
    # didn't run (e.g. the run failed before reaching that step); an empty
    # list means it ran and found nothing above the relevance threshold.
    retrieved_sources: list[dict[str, Any]] | None

    # Loop Engine summary (see app/services/loop_engine.py) — the latest
    # snapshot; the full per-step trail is GET
    # /agent-runs/{id}/loop-events. Only DRAFT-action runs go through the
    # loop, so these stay at their NOT_STARTED/0/None defaults otherwise.
    loop_status: LoopStatus
    loop_iteration: int
    loop_max_iterations: int | None
    loop_quality_threshold: float | None
    loop_quality_score: float | None
    loop_validation_issues: list[dict[str, Any]] | None

    @classmethod
    def from_orm_run(cls, run) -> "AgentRunRead":
        """`agent_key`/`prompt_version` are resolved from the related
        AgentDefinition/AgentPrompt rather than stored redundantly."""
        return cls(
            id=run.id,
            project_id=run.project_id,
            workflow_node_id=run.workflow_node_id,
            agent_key=run.agent_definition.agent_key,
            prompt_version=run.agent_prompt.version if run.agent_prompt else None,
            action=run.action,
            status=run.status,
            input_artifact_ids=[uuid.UUID(i) for i in run.input_artifact_ids],
            input_context=run.input_context,
            output_text=run.output_text,
            output_artifact_id=run.output_artifact_id,
            error_message=run.error_message,
            token_usage=run.token_usage,
            cost=run.cost,
            started_at=run.started_at,
            completed_at=run.completed_at,
            created_at=run.created_at,
            updated_at=run.updated_at,
            retrieved_sources=run.retrieved_sources,
            loop_status=run.loop_status,
            loop_iteration=run.loop_iteration,
            loop_max_iterations=run.loop_max_iterations,
            loop_quality_threshold=run.loop_quality_threshold,
            loop_quality_score=run.loop_quality_score,
            loop_validation_issues=run.loop_validation_issues,
        )


class AgentRunLoopEventRead(BaseModel):
    """One row of an agent run's loop execution history — see
    app/services/loop_engine.py. Returned in iteration/creation order by
    GET /agent-runs/{id}/loop-events."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_run_id: uuid.UUID
    iteration: int
    step: LoopStepType
    quality_score: float | None
    validation_issues: list[dict[str, Any]] | None
    content_snapshot: str | None
    notes: str | None
    created_at: datetime


class SaveAgentOutputResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    agent_run: AgentRunRead
    artifact_id: uuid.UUID
    artifact_version_id: uuid.UUID
    artifact_status: str
    workflow_node_status: str
