from __future__ import annotations

import uuid

from sqlalchemy import Enum, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import SprintStoryStatus


class SprintStory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One story's membership in one sprint — the real source of truth
    for sprint planning, distinct from Story.sprint_id (a denormalized
    "current sprint" pointer kept in sync alongside this table, the same
    convention as Story.owner_user_id / StoryAssignee from the story
    delivery lane work). A story can accumulate several SprintStory rows
    over its life (one per sprint it was ever planned into, including
    ones it was later removed from — see SprintStoryStatus.REMOVED) but
    at most one row per (sprint, story) pair.

    `planned_points` is this sprint's own estimate for the story — set
    when adding it, independent of (and not overwriting) Story's own
    `story_points`, since the same story can be re-estimated differently
    sprint to sprint. `assigned_owner_id` is likewise sprint-scoped and
    independent of Story.owner_user_id — a story's overall owner and who
    is actually driving it within one particular sprint aren't always
    the same person.
    """

    __tablename__ = "sprint_stories"
    __table_args__ = (UniqueConstraint("sprint_id", "story_id"),)

    sprint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sprints.id", ondelete="CASCADE"), nullable=False)
    story_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), nullable=False)
    planned_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assigned_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[SprintStoryStatus] = mapped_column(
        Enum(SprintStoryStatus, native_enum=False, length=20, validate_strings=True),
        default=SprintStoryStatus.PLANNED,
        nullable=False,
    )

    sprint: Mapped["Sprint"] = relationship("Sprint", back_populates="sprint_stories")
    story: Mapped["Story"] = relationship("Story")
    assigned_owner: Mapped["User | None"] = relationship("User", foreign_keys=[assigned_owner_id])
