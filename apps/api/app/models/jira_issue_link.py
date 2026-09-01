from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import JiraSourceType


class JiraIssueLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One real Jira issue created from an internal record — Epic (grouped
    from Story.epic), Story, ImplementationTask (-> Sub-task), or a single
    TestRun.bugs_found entry (-> Bug). See
    app/services/jira_push_preview.py (what's offered, with validation and
    already-linked detection) and app/api/routes/jira_integration.py's
    `/jira/push` (the only place a row here is ever created — always from
    an explicit, human-selected item, never automatically).

    DUPLICATE PREVENTION (rule): the unique constraint below is the real
    enforcement — (jira_project_link_id, source_type, source_key) can
    exist at most once, so the same internal item can never be pushed
    twice even if the same selection is submitted again.

    `source_key` is a stable identifier for the internal item, not a
    database id in every case: the raw epic name string for EPIC, the
    story title for STORY, the ImplementationTask id (as text) for
    IMPLEMENTATION_TASK, or "{test_run_id}:{bug_index}" for TESTING_BUG —
    see app/models/test_run.py's `bugs_found` (a plain string list, not
    individually id'd; the index is stable because that list is written
    once at run completion and never mutated afterward).
    """

    __tablename__ = "jira_issue_links"
    __table_args__ = (UniqueConstraint("jira_project_link_id", "source_type", "source_key"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    jira_project_link_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jira_project_links.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[JiraSourceType] = mapped_column(
        Enum(JiraSourceType, native_enum=False, length=30, validate_strings=True), nullable=False
    )
    source_key: Mapped[str] = mapped_column(String(500), nullable=False)
    source_label: Mapped[str] = mapped_column(String(500), nullable=False)

    jira_issue_key: Mapped[str] = mapped_column(String(50), nullable=False)
    jira_issue_type: Mapped[str] = mapped_column(String(50), nullable=False)
    jira_issue_url: Mapped[str] = mapped_column(String(500), nullable=False)
    parent_jira_issue_key: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Requirement 8 — populated by POST /jira/sync-status, never by a
    # push itself (a freshly created issue's status is whatever Jira's
    # default workflow start state is; this app doesn't assume or fake it).
    jira_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    project: Mapped["Project"] = relationship("Project")
    jira_project_link: Mapped["JiraProjectLink"] = relationship("JiraProjectLink")
    triggered_by: Mapped["User | None"] = relationship("User", foreign_keys=[triggered_by_user_id])
