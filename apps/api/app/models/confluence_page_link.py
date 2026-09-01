from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ConfluencePageLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One real Confluence page published from an internal artifact — see
    app/services/confluence_publish_preview.py's CONFLUENCE_ARTIFACT_TYPES
    (the fixed set of seven publishable kinds) and
    app/api/routes/confluence_integration.py's `/confluence/publish` (the
    only place a row here is ever created or updated — always from an
    explicit, human-selected artifact_types list, never automatically).

    IDENTITY (rule — one page per artifact kind): the unique constraint
    below means at most one ConfluencePageLink can exist per
    (space, artifact_type). A second publish of the same artifact_type
    updates this same row's page (requirement 7) rather than creating a
    duplicate — see confluence_page_version, which tracks Confluence's own
    page version number so the next update PUTs version+1.
    """

    __tablename__ = "confluence_page_links"
    __table_args__ = (UniqueConstraint("confluence_space_link_id", "artifact_type"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    confluence_space_link_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("confluence_space_links.id", ondelete="CASCADE"), nullable=False
    )
    # e.g. "hld_document" — see CONFLUENCE_ARTIFACT_TYPES; "deployment_record"
    # is the disclosed stand-in for "Release Notes" (see that module's
    # docstring — no dedicated release-notes artifact type exists here).
    artifact_type: Mapped[str] = mapped_column(String(100), nullable=False)
    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False)
    # Which version was actually published — the comparison point
    # build_confluence_publish_preview uses to detect "update available"
    # when a newer version is later approved.
    artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifact_versions.id", ondelete="CASCADE"), nullable=False
    )

    confluence_page_id: Mapped[str] = mapped_column(String(100), nullable=False)
    confluence_page_url: Mapped[str] = mapped_column(String(500), nullable=False)
    confluence_page_title: Mapped[str] = mapped_column(String(255), nullable=False)
    confluence_page_version: Mapped[int] = mapped_column(Integer, nullable=False)

    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    project: Mapped["Project"] = relationship("Project")
    confluence_space_link: Mapped["ConfluenceSpaceLink"] = relationship("ConfluenceSpaceLink")
    artifact: Mapped["Artifact"] = relationship("Artifact", foreign_keys=[artifact_id])
    artifact_version: Mapped["ArtifactVersion"] = relationship("ArtifactVersion", foreign_keys=[artifact_version_id])
    triggered_by: Mapped["User | None"] = relationship("User", foreign_keys=[triggered_by_user_id])
