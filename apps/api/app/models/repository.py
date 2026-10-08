from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import RepositoryFileEntryType


class Repository(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One project's configured GitHub repository — see
    app/services/github_integration.py's GitHubIntegrationService (the
    read-only foundation: branches, tree, file contents, snapshots) and
    app/models/integration_connection.py (which credential this repo's
    calls authenticate with).

    Project-scoped, unlike `IntegrationConnection`/`Integration` (org-wide)
    — one company GitHub connection can back a different repo per project.
    """

    __tablename__ = "repositories"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("integration_connections.id"), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Populated/refreshed from GitHub's own GET /repos/{owner}/{repo}
    # response at connect time and on every snapshot — not user-entered.
    default_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_private: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Multi-repo support — a project may now have more than one Repository
    # row (e.g. a separate frontend/backend/infra repo). Exactly one repo
    # per project is the implicit default a task uses when it doesn't name
    # one explicitly (see ImplementationTask.repository_id); enforced in
    # application code (set_primary_repository in app/api/routes/
    # github_integration.py unsets every sibling before setting this one),
    # not a DB constraint — same pattern this codebase already uses for
    # other "exactly one active X" cases.
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    project: Mapped["Project"] = relationship("Project", back_populates="repositories")
    connection: Mapped["IntegrationConnection"] = relationship("IntegrationConnection")
    snapshots: Mapped[list["RepositorySnapshot"]] = relationship(
        "RepositorySnapshot", back_populates="repository", cascade="all, delete-orphan", order_by="RepositorySnapshot.created_at.desc()"
    )


class RepositorySnapshot(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One read-only, point-in-time pull of a Repository's file tree — see
    GitHubIntegrationService.get_repository_tree. Immutable once created,
    like ArtifactVersion/AuditLog — a repo's history of scans, not a live
    mirror. Contains no file *content*, only the tree's own metadata; see
    RepositoryFileIndex for the per-file entries this scan found.
    """

    __tablename__ = "repository_snapshots"

    repository_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    # The branch/tag/sha this snapshot was taken against.
    ref: Mapped[str] = mapped_column(String(255), nullable=False)
    # The actual commit sha `ref` resolved to at scan time — kept even when
    # `ref` is itself a branch name, so a later branch update doesn't make
    # this snapshot's own point-in-time meaning ambiguous.
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # GitHub's own Git Trees API sets this true when a repo has more
    # entries than one recursive call can return — surfaced honestly
    # rather than silently presenting a partial scan as complete.
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    triggered_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    repository: Mapped["Repository"] = relationship("Repository", back_populates="snapshots")
    triggered_by: Mapped["User | None"] = relationship("User", foreign_keys=[triggered_by_id])
    files: Mapped[list["RepositoryFileIndex"]] = relationship(
        "RepositoryFileIndex", back_populates="snapshot", cascade="all, delete-orphan", order_by="RepositoryFileIndex.path"
    )


class RepositoryFileIndex(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One file/directory entry found by a RepositorySnapshot — the
    "repository scan" index itself. No content here (see
    GitHubIntegrationService.read_file for reading one file's content
    on demand) — this is the tree's shape, not its contents."""

    __tablename__ = "repository_file_index"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repository_snapshots.id", ondelete="CASCADE"), nullable=False)
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    entry_type: Mapped[RepositoryFileEntryType] = mapped_column(
        Enum(RepositoryFileEntryType, native_enum=False, length=10, validate_strings=True), nullable=False
    )
    # Null for directories — GitHub's tree API only reports size for blobs.
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha: Mapped[str] = mapped_column(String(64), nullable=False)

    snapshot: Mapped["RepositorySnapshot"] = relationship("RepositorySnapshot", back_populates="files")
