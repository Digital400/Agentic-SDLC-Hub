from __future__ import annotations

import uuid

from sqlalchemy import JSON, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ArtifactStatus


class Artifact(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """The deliverable a workflow node produces (e.g. a requirements doc).

    An artifact is a container: its actual content lives in
    `ArtifactVersion` rows (one per draft/edit), and `current_version`
    always points at the latest one. `status` is the artifact's own review
    lifecycle (see `ArtifactStatus`) — related to, but independent of, its
    owning `WorkflowNode`'s broader `WorkflowStatus`.
    """

    __tablename__ = "artifacts"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    workflow_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_nodes.id", ondelete="CASCADE"), nullable=False
    )
    artifact_type: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ArtifactStatus] = mapped_column(
        Enum(ArtifactStatus, native_enum=False, length=20, validate_strings=True),
        default=ArtifactStatus.DRAFT,
        nullable=False,
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

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
    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_id])
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

    `content_markdown` is the canonical human-readable content;
    `content_json` is an optional structured form (e.g. for a
    machine-editable form of the same artifact) — not every version has
    one. There is no agent-authorship link yet: AI drafting isn't wired up
    (see docs/mvp-plan.md), so every version is human-created for now via
    `created_by`. Re-add an optional agent-run link when that's built.
    """

    __tablename__ = "artifact_versions"
    __table_args__ = (UniqueConstraint("artifact_id", "version_number"),)

    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    content_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    artifact: Mapped["Artifact"] = relationship(
        "Artifact", back_populates="versions", foreign_keys=[artifact_id]
    )
    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_id])
    reviews: Mapped[list["Review"]] = relationship(
        "Review", back_populates="artifact_version", cascade="all, delete-orphan"
    )
