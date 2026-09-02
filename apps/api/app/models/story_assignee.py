from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StoryAssignee(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One assignment event for a Story — history-preserving, unlike
    Story.owner_user_id (a plain denormalized "current owner" column kept
    in sync alongside this table by POST /stories/{id}/assign).

    "Current owner" = the row with `unassigned_at IS NULL` for that
    story; re-assigning closes the previous row (sets its
    `unassigned_at`) rather than deleting it, so who owned a story and
    when is never lost.
    """

    __tablename__ = "story_assignees"

    story_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    assigned_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    unassigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    story: Mapped["Story"] = relationship("Story", back_populates="assignees")
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    assigned_by: Mapped["User | None"] = relationship("User", foreign_keys=[assigned_by_id])
