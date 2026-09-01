from __future__ import annotations

import uuid

from sqlalchemy import JSON, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ImplementationTaskArea, ImplementationTaskRiskLevel, ImplementationTaskStatus


class ImplementationTask(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One unit of work in an Implementation Plan — see
    app/services/implementation_planner.py, which generates these from an
    approved LLD document, and app/models/workflow.py's `implementation_planning`
    stage, whose artifact (`implementation_plan`) these rows belong to.

    Individually queryable/statused (by area, by status) rather than an
    opaque JSON blob on the artifact — this is a genuinely new persistence
    pattern for this codebase (every prior "parse a stage's markdown into
    structured items" case, e.g. app/services/story_export.py's `Story`,
    stays fully ephemeral/computed-on-read) because, unlike those, these
    rows need individual state and assignment, not just a read-only report.

    Regenerating the plan (see the generate endpoint) replaces this node's
    entire task set — delete then reinsert — rather than diffing/versioning
    individual tasks; the plan artifact's own version history is what
    preserves prior generations.
    """

    __tablename__ = "implementation_tasks"

    # Denormalized alongside artifact_id/artifact_version_id, matching
    # Artifact's own convention — lets "all tasks for this project" /
    # "...for this stage" be queried directly without joining through the
    # artifact.
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    workflow_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_nodes.id", ondelete="CASCADE"), nullable=False
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False)
    # Which generation produced this row — the artifact's version history
    # is the record of prior plans; this ties a task set to one of them.
    artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifact_versions.id", ondelete="CASCADE"), nullable=False
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # A story title from the approved story_backlog — referenced by string,
    # not a hard FK, matching how Story Crafting's own "Dependencies" field
    # already references other story titles (no `Story` table exists
    # anywhere in this codebase — see app/services/story_export.py).
    linked_story: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # One of the approved LLD's 15 section headings (see
    # app/db/seed.py's RICH_DEFAULT_PROMPTS["lld"]) this task derives from.
    linked_lld_section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    area: Mapped[ImplementationTaskArea] = mapped_column(
        Enum(ImplementationTaskArea, native_enum=False, length=20, validate_strings=True), nullable=False
    )
    expected_paths: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Other tasks this one depends on, referenced by title (task rows have
    # no stable cross-generation id to reference instead — see the
    # replace-on-regenerate note in the class docstring).
    dependencies: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    test_expectation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    risk_level: Mapped[ImplementationTaskRiskLevel] = mapped_column(
        Enum(ImplementationTaskRiskLevel, native_enum=False, length=10, validate_strings=True),
        default=ImplementationTaskRiskLevel.MEDIUM,
        nullable=False,
    )
    # Derived 1:1 from `area` today (see
    # app/services/implementation_planner.py's AREA_TO_AGENT_TYPE) — a
    # plain string, not an FK to AgentDefinition, since no real per-area
    # coding agent exists yet; forward-compatible with one once it does.
    assigned_agent_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[ImplementationTaskStatus] = mapped_column(
        Enum(ImplementationTaskStatus, native_enum=False, length=20, validate_strings=True),
        default=ImplementationTaskStatus.PENDING,
        nullable=False,
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)

    project: Mapped["Project"] = relationship("Project")
    workflow_node: Mapped["WorkflowNode"] = relationship("WorkflowNode")
    artifact: Mapped["Artifact"] = relationship("Artifact")
    artifact_version: Mapped["ArtifactVersion"] = relationship("ArtifactVersion")
