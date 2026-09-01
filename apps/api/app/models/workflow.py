from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import WorkflowStatus


class WorkflowNode(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One stage of a project's workflow graph.

    Generated once per project from a workflow template (e.g.
    `workflows/sdlc-workflow.json`) — see
    `app/services/workflow_templates.py`. The template's static fields
    (name, agent_key, allowed_actions, ...) are copied in at generation
    time so a project's graph stays stable even if the template evolves
    later; `status` is the only field that changes as the project runs.
    """

    __tablename__ = "workflow_nodes"
    __table_args__ = (UniqueConstraint("project_id", "node_key"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    # Matches the template node's `id` (e.g. "hld"). Stable within a project.
    node_key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    agent_key: Mapped[str] = mapped_column(String(100), nullable=False)
    required_inputs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    output_artifact_type: Mapped[str] = mapped_column(String(100), nullable=False)
    requires_human_approval: Mapped[bool] = mapped_column(Boolean, nullable=False)
    allowed_actions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Which of this node's required_inputs this stage directly depends on
    # the DETAILED content of — see app/services/ai_generation.py's
    # build_prioritized_context, which uses an approved input's
    # agent_context_summary by default and only escalates to full content
    # for an artifact_type listed here (or when the run's own action is
    # IMPROVE/VALIDATE — see generate()'s docstring for those two other
    # cases). Empty by default: most stages work fine from a summary.
    full_content_artifact_types: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    status: Mapped[WorkflowStatus] = mapped_column(
        Enum(WorkflowStatus, native_enum=False, length=30, validate_strings=True),
        default=WorkflowStatus.LOCKED,
        nullable=False,
    )
    # Why this node is BLOCKED — set whenever GraphEngineService sets that
    # status (a rejected review, or any other hard stop), cleared when the
    # node moves off BLOCKED. Null the rest of the time.
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Reason given for the most recent manual override (see
    # GraphEngineService.manual_override) — the full history of who/when
    # lives in AuditLog; this is just the latest one, visible on the node
    # itself without joining out to the audit trail.
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A `## <heading>` this stage's artifact must have, with non-empty
    # content, before a review can approve it (see
    # GraphEngineService.validate_evidence_requirement) — e.g. "Test
    # Evidence" on Testing. Null means no such requirement; most stages
    # have none.
    required_evidence_section: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Token budgets for this stage's agent calls — see
    # app/services/token_budget.py's TokenBudgetService, which prioritizes
    # and fits an agent run's context into context_token_budget, and
    # app/services/ai_generation.py, which caps the model's response at
    # output_token_budget. Copied in from the workflow template at
    # generation time (see workflow_templates.py), same as every other
    # per-node config field — set here so a heavier stage (e.g.
    # Implementation) can carry a larger budget than a lighter one (e.g.
    # Requirement Intake) without a code change.
    context_token_budget: Mapped[int] = mapped_column(Integer, default=8000, nullable=False)
    output_token_budget: Mapped[int] = mapped_column(Integer, default=2048, nullable=False)

    # RAG tuning for this stage — see app/services/retrieval.py.
    # rag_top_k caps how many chunks are even considered before token
    # budgeting; max_rag_tokens is a separate, dedicated cap on how many
    # tokens' worth of those chunks retrieval actually keeps (independent
    # of context_token_budget above, which governs the whole prompt, not
    # just the RAG slice of it) — a stage that leans harder on retrieved
    # knowledge (e.g. Implementation citing coding standards) can carry a
    # bigger allowance than one that mostly works from upstream artifacts.
    rag_top_k: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    max_rag_tokens: Mapped[int] = mapped_column(Integer, default=2000, nullable=False)

    # Display order (matches the template's node order) and canvas position
    # for the React Flow rendering of this project's graph.
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    position_x: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    position_y: Mapped[float] = mapped_column(Float, default=0, nullable=False)

    project: Mapped["Project"] = relationship("Project", back_populates="workflow_nodes")
    artifacts: Mapped[list["Artifact"]] = relationship(
        "Artifact", back_populates="workflow_node", cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        "AgentRun", back_populates="workflow_node", cascade="all, delete-orphan"
    )
    outgoing_edges: Mapped[list["WorkflowEdge"]] = relationship(
        "WorkflowEdge",
        back_populates="source_node",
        foreign_keys="WorkflowEdge.source_node_id",
        cascade="all, delete-orphan",
    )
    incoming_edges: Mapped[list["WorkflowEdge"]] = relationship(
        "WorkflowEdge",
        back_populates="target_node",
        foreign_keys="WorkflowEdge.target_node_id",
        cascade="all, delete-orphan",
    )


class WorkflowEdge(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An allowed transition between two of a project's workflow nodes.

    More than one outgoing edge from the same node models a branch (e.g.
    Testing's forward path to Infrastructure vs. its rework path back to
    Implementation) — see `label` for which is which.
    """

    __tablename__ = "workflow_edges"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_nodes.id", ondelete="CASCADE"), nullable=False
    )
    target_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_nodes.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="workflow_edges")
    source_node: Mapped["WorkflowNode"] = relationship(
        "WorkflowNode", back_populates="outgoing_edges", foreign_keys=[source_node_id]
    )
    target_node: Mapped["WorkflowNode"] = relationship(
        "WorkflowNode", back_populates="incoming_edges", foreign_keys=[target_node_id]
    )
