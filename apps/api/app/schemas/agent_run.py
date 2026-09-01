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

    # Knowledge Base chunks retrieved for this run's context — this run's
    # AgentRunContext for RAG, see app/services/retrieval.py. Each entry:
    # source_id, source_title, chunk_id, chunk_index, snippet, similarity,
    # stage, domain, project_type, content_type, tags. None means
    # retrieval didn't run (e.g. the run failed before reaching that
    # step); an empty list means it ran and found nothing above the
    # relevance threshold.
    retrieved_sources: list[dict[str, Any]] | None
    # Convenience: the deduped, ordered list of source titles behind
    # retrieved_sources — so a UI can show "Sources used" without
    # unpacking every chunk entry. None/empty mirror retrieved_sources.
    retrieved_source_titles: list[str] | None

    # Loop Engine summary (see app/services/loop_engine.py) — the latest
    # snapshot; the full per-step trail is GET
    # /agent-runs/{id}/loop-events. Only DRAFT-action runs go through the
    # loop, so these stay at their NOT_STARTED/0/None defaults otherwise.
    loop_status: LoopStatus
    loop_iteration: int
    loop_max_iterations: int | None
    loop_quality_threshold: float | None
    loop_quality_score: float | None
    loop_validation_issues: list[str] | None
    # The full structured validator output (see
    # app/services/validator_agent.py's ValidatorResult): quality_score,
    # completeness_score, clarity_score, risk_coverage_score,
    # critical_issues, suggestions, approval_recommendation,
    # missing_details, risks, recommendation (a derived READY_FOR_REVIEW /
    # NEEDS_IMPROVEMENT rollup of approval_recommendation). None for a run
    # that never reached VALIDATE (e.g. a non-DRAFT action, or one that
    # failed before its first draft).
    loop_validation_result: dict[str, Any] | None

    # Token Budget Service (see app/services/token_budget.py) — the node's
    # budgets that applied to this run, the pre-call estimate, and the
    # actual usage's own prompt_tokens (in token_usage above) for
    # estimated-vs-actual comparison. token_budget_report is the full
    # per-block breakdown of what was included, compressed, or dropped.
    context_token_budget: int | None
    output_token_budget: int | None
    estimated_context_tokens: int | None
    token_budget_report: dict[str, Any] | None

    @classmethod
    def from_orm_run(cls, run) -> "AgentRunRead":
        """`agent_key`/`prompt_version` are resolved from the related
        AgentDefinition/AgentPrompt rather than stored redundantly."""
        retrieved_source_titles = None
        if run.retrieved_sources is not None:
            # dict.fromkeys, not set(), to keep first-seen (i.e.
            # most-similar) order rather than an arbitrary one.
            retrieved_source_titles = list(dict.fromkeys(s["source_title"] for s in run.retrieved_sources))
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
            retrieved_source_titles=retrieved_source_titles,
            loop_status=run.loop_status,
            loop_iteration=run.loop_iteration,
            loop_max_iterations=run.loop_max_iterations,
            loop_quality_threshold=run.loop_quality_threshold,
            loop_quality_score=run.loop_quality_score,
            loop_validation_issues=run.loop_validation_issues,
            loop_validation_result=run.loop_validation_result,
            context_token_budget=run.context_token_budget,
            output_token_budget=run.output_token_budget,
            estimated_context_tokens=run.estimated_context_tokens,
            token_budget_report=run.token_budget_report,
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
    validation_issues: list[str] | None
    validation_result: dict[str, Any] | None
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
