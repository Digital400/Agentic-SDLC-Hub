from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ReleaseStatus


class Release(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A release built from story delivery lanes — see
    app/models/release_story.py for which stories are in it. Deliberately
    NOT tied to any one Sprint (see app/models/sprint.py's own
    release_planning stage, which stays a per-sprint Markdown summary
    through the generic Artifact pipeline): a Release is a free-standing,
    human-curated set of stories a human decides belong in the same
    shippable unit, each of which must independently have reached
    RELEASE_READY in its own delivery lane (requirement 2 — see
    app/api/routes/releases.py's _require_release_ready).

    APPROVAL GATE (requirement 7): `status` only ever reaches APPROVED
    through POST /releases/{id}/approve, which additionally requires
    non-empty `release_notes` and at least one story — never implied by
    just adding stories or generating notes.
    """

    __tablename__ = "releases"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[ReleaseStatus] = mapped_column(
        Enum(ReleaseStatus, native_enum=False, length=20, validate_strings=True),
        default=ReleaseStatus.DRAFT,
        nullable=False,
    )
    # Requirement 6 — generated from story summaries, PR summaries, test
    # reports, and known risks (see app/services/release_notes.py). Free
    # text; a human may also hand-edit it after generation via PATCH.
    release_notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship("Project")
    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_id])
    approved_by: Mapped["User | None"] = relationship("User", foreign_keys=[approved_by_id])
    release_stories: Mapped[list["ReleaseStory"]] = relationship(
        "ReleaseStory", back_populates="release", order_by="ReleaseStory.created_at"
    )
