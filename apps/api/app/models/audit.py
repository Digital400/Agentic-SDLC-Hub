from __future__ import annotations

import uuid

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class AuditLog(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """An immutable record of something that happened, for traceability.

    Every entry is attributable to a human (`actor_user`) or an agent
    (`actor_agent_run`) — never invented after the fact. `entity_type` +
    `entity_id` point at whatever the event was about (an Artifact, a
    WorkflowNode, ...); kept as a loose reference rather than a FK since
    the set of auditable entity types is expected to grow.
    """

    __tablename__ = "audit_logs"

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    actor_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)

    # e.g. "project.created", "artifact.approved", "workflow_node.status_changed".
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    # Renamed from the more obvious "metadata" — that name is reserved by
    # SQLAlchemy's DeclarativeBase (Base.metadata) and can't be reused as a
    # column attribute.
    extra_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    project: Mapped["Project | None"] = relationship("Project", back_populates="audit_logs")
    actor_user: Mapped["User | None"] = relationship("User")
    actor_agent_run: Mapped["AgentRun | None"] = relationship("AgentRun")
