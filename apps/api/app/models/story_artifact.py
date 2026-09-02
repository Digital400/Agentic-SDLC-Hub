from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StoryArtifact(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A document produced inside a story's own delivery lane (e.g. its
    Story LLD, a PR link summary, a test report) — the per-lane
    counterpart to the project-level Artifact/ArtifactVersion model,
    deliberately separate from it for the same reason
    StoryDeliveryLane/Node/Edge are separate from WorkflowNode/WorkflowEdge:
    this is a dedicated, self-contained per-story model, not a reuse of
    the project-level graph engine's own artifact pipeline.

    No version history table of its own (unlike ArtifactVersion) — a new
    StoryArtifact row per revision, ordered by `version_number`, is
    enough for this simpler per-lane use case; nothing here needs
    review-gate machinery.
    """

    __tablename__ = "story_artifacts"

    story_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), nullable=False)
    lane_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("story_delivery_lanes.id", ondelete="CASCADE"), nullable=True)
    node_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("story_delivery_nodes.id", ondelete="SET NULL"), nullable=True)
    artifact_type: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content_markdown: Mapped[str] = mapped_column(Text, default="", nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    story: Mapped["Story"] = relationship("Story")
    lane: Mapped["StoryDeliveryLane | None"] = relationship("StoryDeliveryLane")
    node: Mapped["StoryDeliveryNode | None"] = relationship("StoryDeliveryNode")
    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_id])
