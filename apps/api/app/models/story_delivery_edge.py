from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StoryDeliveryEdge(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One directed edge between two nodes in the same story delivery
    lane — see app/models/story_delivery_node.py. The default lane is a
    straight sequence (no fan-out/rework edges, unlike the project-level
    WorkflowEdge), but this is still its own table rather than an
    implicit `order_index + 1` relationship, so a lane's shape can be
    inspected/extended later without a schema change."""

    __tablename__ = "story_delivery_edges"

    lane_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("story_delivery_lanes.id", ondelete="CASCADE"), nullable=False)
    source_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("story_delivery_nodes.id", ondelete="CASCADE"), nullable=False)
    target_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("story_delivery_nodes.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)

    lane: Mapped["StoryDeliveryLane"] = relationship("StoryDeliveryLane", back_populates="edges")
    source_node: Mapped["StoryDeliveryNode"] = relationship(
        "StoryDeliveryNode", back_populates="outgoing_edges", foreign_keys=[source_node_id]
    )
    target_node: Mapped["StoryDeliveryNode"] = relationship(
        "StoryDeliveryNode", back_populates="incoming_edges", foreign_keys=[target_node_id]
    )
