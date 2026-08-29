from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AgentPromptRole, AgentRunStatus


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
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship("Project")
    workflow_node: Mapped["WorkflowNode"] = relationship("WorkflowNode", back_populates="agent_runs")
    agent_definition: Mapped["AgentDefinition"] = relationship("AgentDefinition", back_populates="runs")
    agent_prompt: Mapped["AgentPrompt | None"] = relationship("AgentPrompt", back_populates="runs")
    triggered_by_user: Mapped["User | None"] = relationship("User")
    output_artifact: Mapped["Artifact | None"] = relationship("Artifact", foreign_keys=[output_artifact_id])
