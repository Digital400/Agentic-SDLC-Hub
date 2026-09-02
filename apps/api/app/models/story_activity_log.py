from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StoryActivityLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One audited event on a Story or its delivery lane — a dedicated
    log for this module, separate from the generic app/models/audit.py's
    AuditLog used everywhere else in this codebase (both are written for
    every story-related mutation in app/api/routes/stories.py — this one
    additionally, not instead, so nothing that already reads AuditLog
    loses coverage): story.created, story.updated, story.assigned,
    story_lane.created, story_lane_node.status_changed.

    `created_at` (from TimestampMixin) is this event's timestamp — there
    is no meaningful `updated_at` for a log entry, but TimestampMixin is
    reused here rather than a bespoke single-column model, matching
    every other model in this codebase.
    """

    __tablename__ = "story_activity_logs"

    story_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), nullable=False)
    lane_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("story_delivery_lanes.id", ondelete="CASCADE"), nullable=True)
    node_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("story_delivery_nodes.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    story: Mapped["Story"] = relationship("Story")
    lane: Mapped["StoryDeliveryLane | None"] = relationship("StoryDeliveryLane")
    node: Mapped["StoryDeliveryNode | None"] = relationship("StoryDeliveryNode")
    actor: Mapped["User | None"] = relationship("User", foreign_keys=[actor_user_id])
