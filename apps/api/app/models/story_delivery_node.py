from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import StoryDeliveryNodeStatus

# The default story delivery lane's node sequence — see
# app/services/story_delivery.py's DEFAULT_STORY_DELIVERY_NODES, the one
# place this exact list/order is defined; this constant exists here only
# so a node's `node_key` type hint has something concrete to reference.
STORY_DELIVERY_NODE_KEYS = (
    "STORY_READY",
    "STORY_LLD",
    "LLD_REVIEW",
    "IMPLEMENTATION",
    "PULL_REQUEST",
    "PR_REVIEW_AGENT",
    "HUMAN_CODE_REVIEW",
    "TESTING",
    "QA_APPROVAL",
    "RELEASE_READY",
)


class StoryDeliveryNode(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One stage in a story's delivery lane — see
    app/models/story_delivery_lane.py. Sequential by construction: `order_index`
    fixes the lane's linear order, and every node except the first starts
    LOCKED, unlocked into READY only once its immediate predecessor
    reaches COMPLETED (see app/services/story_delivery.py's
    advance_lane, called from PATCH /delivery-lane-nodes/{id}).
    """

    __tablename__ = "story_delivery_nodes"

    lane_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("story_delivery_lanes.id", ondelete="CASCADE"), nullable=False)
    node_key: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[StoryDeliveryNodeStatus] = mapped_column(
        Enum(StoryDeliveryNodeStatus, native_enum=False, length=20, validate_strings=True),
        default=StoryDeliveryNodeStatus.LOCKED,
        nullable=False,
    )
    # Who/what role this stage is meant for — advisory, same spirit as
    # Story.suggested_owner_role; not enforced against a real permission
    # table (no per-lane-node permission model exists — see
    # app/services/permissions.py's stage-keyed tables for the
    # project-level equivalent, unaffected by this table).
    assigned_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)

    lane: Mapped["StoryDeliveryLane"] = relationship(
        "StoryDeliveryLane", back_populates="nodes", foreign_keys=[lane_id]
    )
    assigned_user: Mapped["User | None"] = relationship("User", foreign_keys=[assigned_user_id])
    outgoing_edges: Mapped[list["StoryDeliveryEdge"]] = relationship(
        "StoryDeliveryEdge", back_populates="source_node", foreign_keys="StoryDeliveryEdge.source_node_id"
    )
    incoming_edges: Mapped[list["StoryDeliveryEdge"]] = relationship(
        "StoryDeliveryEdge", back_populates="target_node", foreign_keys="StoryDeliveryEdge.target_node_id"
    )
