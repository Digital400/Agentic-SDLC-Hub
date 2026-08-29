from __future__ import annotations

import uuid

from sqlalchemy import Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ProjectRole, ProjectStatus


class Project(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A single piece of work run through an SDLC workflow."""

    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Identifies which workflow template (and version of it) this project's
    # WorkflowNode/WorkflowEdge rows were generated from. Kept even though
    # the template file can change later, so a project's graph stays
    # reproducible/explainable.
    workflow_template_id: Mapped[str] = mapped_column(String(100), nullable=False)
    workflow_template_version: Mapped[str] = mapped_column(String(50), nullable=False)

    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, native_enum=False, length=20, validate_strings=True),
        default=ProjectStatus.ACTIVE,
        nullable=False,
    )

    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_id])
    members: Mapped[list["ProjectMember"]] = relationship(
        "ProjectMember", back_populates="project", cascade="all, delete-orphan"
    )
    workflow_nodes: Mapped[list["WorkflowNode"]] = relationship(
        "WorkflowNode",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="WorkflowNode.order_index",
    )
    workflow_edges: Mapped[list["WorkflowEdge"]] = relationship(
        "WorkflowEdge", back_populates="project", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        "Artifact", back_populates="project", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog", back_populates="project", cascade="all, delete-orphan"
    )


class ProjectMember(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A user's membership + role on a specific project."""

    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[ProjectRole] = mapped_column(
        Enum(ProjectRole, native_enum=False, length=20, validate_strings=True), nullable=False
    )

    project: Mapped["Project"] = relationship("Project", back_populates="members")
    user: Mapped["User"] = relationship("User", back_populates="project_memberships")
