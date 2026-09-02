from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import SprintStatus


class Sprint(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A Scrum sprint for a project — see app/models/sprint_story.py for
    which stories are in it (a dedicated join table, not just
    Story.sprint_id — see that column's own docstring for why both
    exist). Not an AI-drafted concept: Sprint Planning/Release Planning
    are real project-management actions a human takes, rendered as a
    deterministic Markdown summary through the same generic Artifact
    pipeline every other stage uses (see app/services/sprint_export.py
    and workflows/scrum-story-lanes-workflow.json's sprint_planning/
    release_planning nodes)."""

    __tablename__ = "sprints"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    goal: Mapped[str] = mapped_column(Text, default="", nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # A human's estimate of how many story points this sprint's team can
    # take on — advisory, checked against the sum of planned_points across
    # this sprint's SprintStory rows on the sprint board, never enforced
    # as a hard cap (a team may knowingly over/under-commit).
    capacity_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[SprintStatus] = mapped_column(
        Enum(SprintStatus, native_enum=False, length=20, validate_strings=True),
        default=SprintStatus.PLANNED,
        nullable=False,
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    project: Mapped["Project"] = relationship("Project")
    created_by: Mapped["User"] = relationship("User")
    # Denormalized "current sprint" pointer on Story (Story.sprint_id) —
    # kept for quick reads (e.g. sprint_export.py's render) — coexists
    # with sprint_stories below, the real source of truth/history.
    stories: Mapped[list["Story"]] = relationship("Story", back_populates="sprint")
    sprint_stories: Mapped[list["SprintStory"]] = relationship(
        "SprintStory", back_populates="sprint", order_by="SprintStory.created_at"
    )
