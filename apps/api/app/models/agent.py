from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AgentPromptRole, AgentRunStatus, LoopStatus, LoopStepType


class AgentDefinition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Configuration for one agent (e.g. "hld-agent").

    `agent_key` matches a workflow node's `agent_key` field, connecting a
    stage to the agent responsible for drafting/assisting it. No real model
    calls happen yet — `model_name` is a placeholder for when they do.
    """

    __tablename__ = "agent_definitions"

    agent_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    prompts: Mapped[list["AgentPrompt"]] = relationship(
        "AgentPrompt", back_populates="agent_definition", cascade="all, delete-orphan", order_by="AgentPrompt.version"
    )
    runs: Mapped[list["AgentRun"]] = relationship(
        "AgentRun", back_populates="agent_definition", cascade="all, delete-orphan"
    )


class AgentPrompt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One version of a prompt for one of an agent's roles — the Prompt
    Library's core entity.

    Mirrors the draft/improve/validate roles from the product's AI agent
    principle. Versioned the same way artifacts are (see Artifact /
    ArtifactVersion): a new version is a new immutable row, and exactly one
    version per (agent_definition, role) is `is_active` at a time — see
    `POST /prompts/{id}/activate`.
    """

    __tablename__ = "agent_prompts"
    __table_args__ = (UniqueConstraint("agent_definition_id", "role", "version"),)

    agent_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_definitions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[AgentPromptRole] = mapped_column(
        Enum(AgentPromptRole, native_enum=False, length=20, validate_strings=True), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # The workflow stage (WorkflowNode.node_key, e.g. "hld") this prompt is
    # written for. Denormalized from the agent's own association with a
    # stage rather than looked up, since one agent could in principle serve
    # more than one stage.
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    output_format: Mapped[str] = mapped_column(Text, nullable=False)
    # Criteria the agent should self-check before finalizing output — the
    # agent-side counterpart to the human reviewer checklist in the review
    # gate UI.
    validation_checklist: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    agent_definition: Mapped["AgentDefinition"] = relationship("AgentDefinition", back_populates="prompts")
    runs: Mapped[list["AgentRun"]] = relationship("AgentRun", back_populates="agent_prompt")


class AgentRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One invocation of an agent against a specific workflow node.

    No real AI call is wired up yet (see docs/mvp-plan.md) —
    `app/services/mock_agent.py` generates deterministic placeholder output
    based on `agent_key` alone. `token_usage`/`cost` are placeholders for
    the same reason: present in the shape a real model call will need, but
    not meaningful numbers yet. `input_context` remains for freeform extra
    context (e.g. a stakeholder request with no prior artifact);
    `input_artifact_ids` is the structured list of artifacts actually used.
    """

    __tablename__ = "agent_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    workflow_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_nodes.id", ondelete="CASCADE"), nullable=False
    )
    agent_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_definitions.id"), nullable=False
    )
    agent_prompt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_prompts.id"), nullable=True)
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    action: Mapped[AgentPromptRole] = mapped_column(
        Enum(AgentPromptRole, native_enum=False, length=20, validate_strings=True), nullable=False
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        Enum(AgentRunStatus, native_enum=False, length=20, validate_strings=True),
        default=AgentRunStatus.PENDING,
        nullable=False,
    )
    input_context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Artifact ids actually used as input for this run (as opposed to
    # input_context's freeform notes) — stored as strings since JSON has no
    # native UUID representation.
    input_artifact_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set once the output has actually been saved into an artifact draft
    # (see POST /agent-runs/{id}/save-to-artifact) — a completed run's
    # output isn't automatically applied, so this can be null even for a
    # COMPLETED run.
    output_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Placeholders — see class docstring. Shape matches what a real model
    # call will report; the numbers themselves are not meaningful yet.
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    # --- Token Budget Service (see app/services/token_budget.py) ------------
    #
    # The node's budgets at the time this run executed (a node's config can
    # change later; this is what actually applied here). estimated_context_
    # tokens is computed BEFORE the model call, from the assembled/fitted
    # context; token_usage.prompt_tokens above is the ACTUAL count the
    # provider reports back afterward — comparing the two is how you'd spot
    # the estimate heuristic drifting from reality. token_budget_report is
    # the full per-block breakdown (included/truncated/dropped) behind that
    # estimate — see TokenBudgetResult.to_report_dict.
    context_token_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_token_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_context_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_budget_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Knowledge Base chunks retrieved for this run's context (see
    # app/services/retrieval.py), captured at run time so the Agent Run
    # Detail UI can show what was cited without re-running retrieval.
    # Each entry: {source_id, source_title, chunk_id, chunk_index, snippet,
    # similarity}. Empty list (not null) means retrieval ran and found
    # nothing above the relevance threshold — the run still proceeded on
    # project context alone, per the "don't force irrelevant knowledge"
    # rule; null means retrieval didn't run at all (e.g. an older run).
    retrieved_sources: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    # Agent Context Builder rule 5 ("store context snapshot with AgentRun
    # for audit/debugging") — exactly which Project Engineering Setup
    # fields were actually included for this run (see
    # app/services/agent_context_builder.py's EngineeringSetupContext.snapshot),
    # not the whole setup — a project with no engineering setup, or a run
    # predating this feature, leaves this null.
    engineering_setup_context_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Loop Engine state (see app/services/loop_engine.py) ---------------
    #
    # Only DRAFT-action runs currently go through the loop (see
    # app/api/routes/agent_runs.py) — a VALIDATE or IMPROVE run is already a
    # single well-defined human-triggered step, so these stay at their
    # NOT_STARTED/0/None defaults for those runs. The full per-step trail
    # lives in `loop_events`; these columns are just the latest snapshot, so
    # the run's own status is visible without joining out to it.
    loop_status: Mapped[LoopStatus] = mapped_column(
        Enum(LoopStatus, native_enum=False, length=30, validate_strings=True),
        default=LoopStatus.NOT_STARTED,
        nullable=False,
    )
    loop_current_step: Mapped[LoopStepType | None] = mapped_column(
        Enum(LoopStepType, native_enum=False, length=20, validate_strings=True), nullable=True
    )
    loop_iteration: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    loop_max_iterations: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loop_quality_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The latest VALIDATE step's score/critical issues — i.e. the ones that
    # produced loop_status's final decision. loop_validation_result is the
    # full structured ValidatorResult (see
    # app/services/validator_agent.py): quality/completeness/clarity/
    # risk_coverage scores, critical_issues, suggestions, and
    # approval_recommendation — this is what the Agent Run Detail API
    # shows (see AgentRunRead).
    loop_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    loop_validation_issues: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    loop_validation_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    project: Mapped["Project"] = relationship("Project")
    workflow_node: Mapped["WorkflowNode"] = relationship("WorkflowNode", back_populates="agent_runs")
    agent_definition: Mapped["AgentDefinition"] = relationship("AgentDefinition", back_populates="runs")
    agent_prompt: Mapped["AgentPrompt | None"] = relationship("AgentPrompt", back_populates="runs")
    triggered_by_user: Mapped["User | None"] = relationship("User")
    output_artifact: Mapped["Artifact | None"] = relationship("Artifact", foreign_keys=[output_artifact_id])
    loop_events: Mapped[list["AgentRunLoopEvent"]] = relationship(
        "AgentRunLoopEvent",
        back_populates="agent_run",
        cascade="all, delete-orphan",
        order_by="AgentRunLoopEvent.created_at",
    )


class AgentRunLoopEvent(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One step of one iteration of an agent run's loop — the full
    execution history behind AgentRun's loop_* summary columns, and what
    "improvement history" means for this run (its GENERATE_DRAFT/IMPROVE
    rows in iteration order). Append-only: nothing ever updates an existing
    row, matching AuditLog's own immutability pattern.
    """

    __tablename__ = "agent_run_loop_events"

    agent_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    # 0 for the once-per-run PLAN/RETRIEVE_CONTEXT steps; 1-based for the
    # repeating GENERATE_DRAFT/IMPROVE/VALIDATE cycle; the final
    # READY_FOR_REVIEW row carries whichever iteration the loop stopped on.
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    step: Mapped[LoopStepType] = mapped_column(
        Enum(LoopStepType, native_enum=False, length=20, validate_strings=True), nullable=False
    )
    # Set only on VALIDATE (and echoed onto the final READY_FOR_REVIEW row).
    # validation_result is the full structured ValidatorResult (see
    # app/services/validator_agent.py); validation_issues is just its
    # critical_issues list, kept alongside for a quick glance without
    # unpacking the full dict.
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_issues: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    validation_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The generated content at this step — set on GENERATE_DRAFT/IMPROVE
    # only, so "improvement history" (this run's content over time) can be
    # reconstructed without re-deriving it from token counts.
    content_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Freeform: the plan text, a retrieval summary, why the loop stopped, ...
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    agent_run: Mapped["AgentRun"] = relationship("AgentRun", back_populates="loop_events")
