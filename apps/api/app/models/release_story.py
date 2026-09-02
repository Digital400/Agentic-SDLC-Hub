from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ReleaseStory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One story's membership in one Release — see app/models/release.py.
    A story can only be added while its own delivery lane has reached
    RELEASE_READY (requirement 2, enforced in
    app/api/routes/releases.py's add_story_to_release, not here); this
    table doesn't re-check that afterward, so a release keeps its
    selected stories even if something about a story's lane changes
    later — release note generation re-reads each story's current state
    fresh every time regardless.

    Hard-deleted on removal (unlike SprintStory's soft-delete): a release
    is a lightweight, still-DRAFT curation step until approved — no
    membership history needs preserving the way a sprint's does."""

    __tablename__ = "release_stories"
    __table_args__ = (UniqueConstraint("release_id", "story_id"),)

    release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("releases.id", ondelete="CASCADE"), nullable=False)
    story_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), nullable=False)
    added_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    release: Mapped["Release"] = relationship("Release", back_populates="release_stories")
    story: Mapped["Story"] = relationship("Story")
    added_by: Mapped["User"] = relationship("User", foreign_keys=[added_by_id])
