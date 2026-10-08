from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ConfluencePageLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One real Confluence page published from an internal artifact — see
    app/services/confluence_publish_preview.py's CONFLUENCE_ARTIFACT_TYPES
    (the fixed set of seven project-level publishable kinds) and
    app/api/routes/confluence_integration.py's `/confluence/publish` (the
    only place a project-level row here is ever created or updated —
    always from an explicit, human-selected artifact_types list, never
    automatically).

    STORY-SCOPED PUBLISHING: `story_id`/`story_artifact_id` are set only
    for a page published from one story's own StoryArtifact (e.g. its
    Story LLD — see app/services/story_confluence_publish.py) — the
    per-story sibling of the project-level flow above, same nullable-
    relaxation precedent this session has already applied to
    PullRequestLink/PRReviewRun/TestRun for story scoping. `artifact_id`/
    `artifact_version_id` are null for exactly these rows (a StoryArtifact
    is a different table, not an Artifact/ArtifactVersion).

    IDENTITY (rule — one page per artifact kind, now per story too): a
    project-level row (story_id IS NULL) is unique per (space,
    artifact_type) — the original guarantee, preserved via a partial
    index rather than a plain UniqueConstraint (Postgres treats every
    NULL as distinct in an ordinary unique index/constraint, which would
    otherwise silently stop enforcing "one page per artifact kind" the
    moment story_id existed as a column at all). A story-scoped row is
    unique per (space, artifact_type, story_id). Either way, a second
    publish updates this same row's page (requirement 7) rather than
    creating a duplicate — see confluence_page_version, which tracks
    Confluence's own page version number so the next update PUTs
    version+1.
    """

    __tablename__ = "confluence_page_links"
    __table_args__ = (
        Index(
            "uq_confluence_page_links_project_scope", "confluence_space_link_id", "artifact_type",
            unique=True, postgresql_where=text("story_id IS NULL"),
        ),
        Index(
            "uq_confluence_page_links_story_scope", "confluence_space_link_id", "artifact_type", "story_id",
            unique=True, postgresql_where=text("story_id IS NOT NULL"),
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    confluence_space_link_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("confluence_space_links.id", ondelete="CASCADE"), nullable=False
    )
    # e.g. "hld_document" — see CONFLUENCE_ARTIFACT_TYPES; "deployment_record"
    # is the disclosed stand-in for "Release Notes" (see that module's
    # docstring — no dedicated release-notes artifact type exists here).
    # For a story-scoped row this is a StoryArtifact artifact_type instead
    # (e.g. "story_lld" — see app/services/story_lld_agent.py).
    artifact_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # Null for a story-scoped row — see story_artifact_id below instead.
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=True)
    # Which version was actually published — the comparison point
    # build_confluence_publish_preview uses to detect "update available"
    # when a newer version is later approved. Null for a story-scoped row.
    artifact_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifact_versions.id", ondelete="CASCADE"), nullable=True
    )
    # Set only for a story-scoped page — see this class's own docstring.
    story_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), nullable=True)
    story_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("story_artifacts.id", ondelete="CASCADE"), nullable=True
    )

    confluence_page_id: Mapped[str] = mapped_column(String(100), nullable=False)
    confluence_page_url: Mapped[str] = mapped_column(String(500), nullable=False)
    confluence_page_title: Mapped[str] = mapped_column(String(255), nullable=False)
    confluence_page_version: Mapped[int] = mapped_column(Integer, nullable=False)

    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    project: Mapped["Project"] = relationship("Project")
    confluence_space_link: Mapped["ConfluenceSpaceLink"] = relationship("ConfluenceSpaceLink")
    artifact: Mapped["Artifact | None"] = relationship("Artifact", foreign_keys=[artifact_id])
    artifact_version: Mapped["ArtifactVersion | None"] = relationship("ArtifactVersion", foreign_keys=[artifact_version_id])
    story: Mapped["Story | None"] = relationship("Story")
    story_artifact: Mapped["StoryArtifact | None"] = relationship("StoryArtifact")
    triggered_by: Mapped["User | None"] = relationship("User", foreign_keys=[triggered_by_user_id])
