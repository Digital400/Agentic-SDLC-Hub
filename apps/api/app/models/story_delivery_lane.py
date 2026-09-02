from __future__ import annotations

import uuid

from sqlalchemy import Enum, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import StoryDeliveryLaneStatus


class StoryDeliveryLane(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One story's own delivery lane — a dedicated, self-contained graph
    of StoryDeliveryNode/StoryDeliveryEdge rows, materialized once by
    app/services/story_delivery.py's create_story_delivery_lane (called
    from POST /stories/{id}/lane).

    Deliberately NOT built on the project-level WorkflowNode/WorkflowEdge
    graph engine (see app/services/graph_engine.py) — this is a separate,
    purpose-built model for per-story delivery tracking; the MVP
    project-level workflow (Requirement Intake through Maintenance/Sprint
    Planning/Release Planning) is untouched by this table and keeps using
    WorkflowNode/WorkflowEdge exactly as it always has.

    ONE LANE PER STORY: `story_id` is unique — a second
    POST /stories/{id}/lane call for the same story is rejected (see the
    route), not silently creating a second lane.
    """

    __tablename__ = "story_delivery_lanes"
    __table_args__ = (UniqueConstraint("story_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    story_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), nullable=False)
    # use_alter: story_delivery_lanes -> story_delivery_nodes ->
    # story_delivery_lanes is a two-table FK cycle (a lane points at its
    # current node; a node points back at its owning lane) — same
    # technique app/models/artifact.py's current_version_id already uses.
    current_node_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("story_delivery_nodes.id", ondelete="SET NULL", use_alter=True, name="fk_story_delivery_lanes_current_node_id"),
        nullable=True,
    )
    status: Mapped[StoryDeliveryLaneStatus] = mapped_column(
        Enum(StoryDeliveryLaneStatus, native_enum=False, length=20, validate_strings=True),
        default=StoryDeliveryLaneStatus.ACTIVE,
        nullable=False,
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    project: Mapped["Project"] = relationship("Project")
    story: Mapped["Story"] = relationship("Story", back_populates="delivery_lane")
    current_node: Mapped["StoryDeliveryNode | None"] = relationship(
        "StoryDeliveryNode", foreign_keys=[current_node_id], post_update=True
    )
    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_id])
    nodes: Mapped[list["StoryDeliveryNode"]] = relationship(
        "StoryDeliveryNode",
        back_populates="lane",
        foreign_keys="StoryDeliveryNode.lane_id",
        order_by="StoryDeliveryNode.order_index",
    )
    edges: Mapped[list["StoryDeliveryEdge"]] = relationship("StoryDeliveryEdge", back_populates="lane")
