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
