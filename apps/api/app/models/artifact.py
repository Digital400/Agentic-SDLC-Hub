from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import WorkflowStatus


class Artifact(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """The deliverable a workflow node produces (e.g. a requirements doc).

    An artifact is a container: its actual content lives in
    `ArtifactVersion` rows (one per draft/edit), and `current_version`
    always points at the latest one. `status` tracks the artifact's review
    lifecycle and mirrors `WorkflowStatus` so it lines up with the owning
    node's status.
    """

    __tablename__ = "artifacts"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    workflow_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_nodes.id", ondelete="CASCADE"), nullable=False
    )
    artifact_type: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[WorkflowStatus] = mapped_column(
        Enum(WorkflowStatus, native_enum=False, length=30, validate_strings=True),
        default=WorkflowStatus.NOT_STARTED,
        nullable=False,
    )

    # Nullable + use_alter: an artifact can exist before it has a version,
    # and ArtifactVersion.artifact_id points back at this table, so the two
    # tables have a circular FK. use_alter/post_update tell SQLAlchemy to
    # create this FK in a second ALTER TABLE step and to update it after
    # the row insert, breaking the ordering deadlock.
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifact_versions.id", ondelete="SET NULL", use_alter=True, name="fk_artifacts_current_version_id"),
        nullable=True,
    )

    project: Mapped["Project"] = relationship("Project", back_populates="artifacts")
    workflow_node: Mapped["WorkflowNode"] = relationship("WorkflowNode", back_populates="artifacts")
    versions: Mapped[list["ArtifactVersion"]] = relationship(
        "ArtifactVersion",
        back_populates="artifact",
        cascade="all, delete-orphan",
        foreign_keys="ArtifactVersion.artifact_id",
        order_by="ArtifactVersion.version_number",
    )
    current_version: Mapped["ArtifactVersion | None"] = relationship(
        "ArtifactVersion", foreign_keys=[current_version_id], post_update=True
    )


class ArtifactVersion(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One immutable snapshot of an artifact's content.

    Produced either by a human (`authored_by_user`) or by an agent run
    (`authored_by_agent_run`) — never both unset, per the check constraint
    below, since every version must be attributable to someone/something.
    """

    __tablename__ = "artifact_versions"
    __table_args__ = (
        UniqueConstraint("artifact_id", "version_number"),
        CheckConstraint(
            "authored_by_user_id IS NOT NULL OR authored_by_agent_run_id IS NOT NULL",
            # Short label — the naming convention in app/models/base.py adds
            # the "ck_<table>_" prefix itself; a fully-qualified name here
            # would get prefixed twice.
            name="has_author",
        ),
    )

    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_format: Mapped[str] = mapped_column(String(30), default="markdown", nullable=False)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    authored_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    authored_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True
    )

    artifact: Mapped["Artifact"] = relationship(
        "Artifact", back_populates="versions", foreign_keys=[artifact_id]
    )
    authored_by_user: Mapped["User | None"] = relationship("User", foreign_keys=[authored_by_user_id])
    authored_by_agent_run: Mapped["AgentRun | None"] = relationship(
        "AgentRun", back_populates="produced_versions", foreign_keys=[authored_by_agent_run_id]
    )
    reviews: Mapped[list["Review"]] = relationship(
        "Review", back_populates="artifact_version", cascade="all, delete-orphan"
    )
