import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import AgentPromptRole, AgentRunStatus, LoopStatus, LoopStepType

# Mirrors app/services/ai_generation.py's ProviderOverride — kept as a
# separate Literal here rather than imported, so this schema module (a thin
# request/response contract) doesn't reach into the services layer just for
# a type. Keep the two lists in sync by hand if a new backend is added.
ProviderOverride = Literal["claude_agent_sdk", "anthropic", "gemini", "openrouter", "nvidia", "huggingface", "ollama"]

# Hard ceiling on all freeform text in one run request (roughly 30–40k words).
# Longer-than-budget input below this is condensed, not rejected — see
# app/services/intake_text_condenser.py.
MAX_INPUT_CHARS = 200_000


class AgentRunCreate(BaseModel):
    project_id: uuid.UUID
    workflow_node_id: uuid.UUID = Field(..., description="Must belong to project_id.")
    action: AgentPromptRole = AgentPromptRole.DRAFT
    input_artifact_ids: list[uuid.UUID] = Field(default_factory=list, description="Must all exist.")
    triggered_by_user_id: uuid.UUID = Field(..., description="Existing user id — attributes the resulting artifact version.")
    input_context: dict[str, Any] = Field(default_factory=dict, description="Freeform extra context, e.g. a stakeholder request.")
    provider_override: ProviderOverride | None = Field(
        default=None,
        description=(
            "Force this one run to use a specific LLM backend instead of the project's default "
            "auto-selected one (see app/services/ai_generation.py's get_active_provider). "
            "'claude_agent_sdk' requires the project to have a connected GitHub repository; any other "
            "value requires that provider's own API key to actually be configured in the backend's "
            "environment, or the run fails with a clear error naming which one."
        ),
    )
    model_override: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Force this one run to use a specific model within the chosen provider (e.g. "
            "'claude-opus-5' for anthropic/claude_agent_sdk, or a specific repo id for huggingface) "
            "instead of that provider's configured default. Ignored if provider_override is left on "
            "the project default. An invalid/unavailable model name fails the run with that provider's "
            "own error, exactly as a hand-edited configuration default would."
        ),
    )

    @field_validator("input_context")
    @classmethod
    def _input_not_too_large(cls, value: dict[str, Any]) -> dict[str, Any]:
        total = sum(len(v) for v in value.values() if isinstance(v, str))
        if total > MAX_INPUT_CHARS:
            raise ValueError(
                f"The input is too long ({total:,} characters; the maximum is {MAX_INPUT_CHARS:,}, "
                "roughly 30,000 words). Split it, or remove repeated sections, and try again."
            )
        return value


class ProviderOptionRead(BaseModel):
    """One row of GET /agent-runs/providers — what the frontend's LLM
    dropdown (see provider_override above) actually offers for this
    backend/project, built from real config instead of a hardcoded list a
    user has no way to tell apart from what's actually usable."""

    value: ProviderOverride
    label: str
    # The real configured model name (e.g. "claude-sonnet-5",
    # "Qwen/Qwen2.5-Coder-32B-Instruct") — None only for claude_agent_sdk,
    # which has no single "model" setting of its own (it's a full agent
    # session, not a text-completion call).
    model: str | None
    # Whether this option would actually work right now if picked — an API
    # key being set (or, for claude_agent_sdk, the flag being on AND, when
    # project_id was given, that project having a connected repository).
    # Still offered even when False (never silently hidden), with
    # unavailable_reason explaining why, so a user isn't left guessing why
    # a run failed after picking it.
    configured: bool
    unavailable_reason: str | None = None
    # Curated, known-good model ids for this provider (e.g. the Claude
    # family for anthropic/claude_agent_sdk) — suggestions, not a closed
    # list: the frontend still lets a user type any model id via
    # model_override, since providers like Hugging Face/OpenRouter/NVIDIA
    # have far too large a catalog to enumerate here. Empty for a provider
    # with no curated suggestions (the user types the id freehand).
    available_models: list[str] = []


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
