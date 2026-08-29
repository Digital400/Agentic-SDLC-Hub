from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
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
    """One version of a prompt template for one of an agent's roles.

    Mirrors the draft/improve/validate roles from the product's AI agent
    principle. Versioned so prompt changes are auditable and a run can
    record exactly which prompt version produced its output.
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
    template: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    agent_definition: Mapped["AgentDefinition"] = relationship("AgentDefinition", back_populates="prompts")
    runs: Mapped[list["AgentRun"]] = relationship("AgentRun", back_populates="agent_prompt")


class AgentRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One invocation of an agent against a specific workflow node.

    `input_context`/`output_text` are stubs for now — no real AI call is
    wired up yet. `input_context` is also where future RAG-retrieved
    context would be recorded, once that exists.
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
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship("Project")
    workflow_node: Mapped["WorkflowNode"] = relationship("WorkflowNode", back_populates="agent_runs")
    agent_definition: Mapped["AgentDefinition"] = relationship("AgentDefinition", back_populates="runs")
    agent_prompt: Mapped["AgentPrompt | None"] = relationship("AgentPrompt", back_populates="runs")
    triggered_by_user: Mapped["User | None"] = relationship("User")
    # No link to ArtifactVersion yet — AI drafting isn't wired up (see
    # docs/mvp-plan.md); every version is human-created for now. Re-add
    # once an agent can actually produce one.
