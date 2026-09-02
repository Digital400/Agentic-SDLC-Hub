from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import StoryJiraSyncStatus, StoryStatus, StoryType


class Story(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One real, persisted story — the durable counterpart to
    app/services/story_export.py's `Story` dataclass (which is still the
    only thing that ever *parses* a story_backlog artifact; this table is
    populated FROM that parse, once, per app/api/routes/stories.py's
    sync-from-backlog action, never generated independently).

    WHY THIS EXISTS: everything the product needs beyond drafting text —
    assigning an owner, syncing to Jira, adding to a sprint, and above all
    creating a per-story delivery lane (see
    app/services/story_delivery.py's StoryDeliveryLane/StoryDeliveryNode —
    a dedicated graph per story, not the project-level WorkflowNode graph
    engine) — needs a real, stable id to hang state off of. A
    freshly-reparsed dataclass has none.

    IDENTITY: `(project_id, title)` is unique, matching how every other
    part of this codebase already identifies "which story" purely by
    title (app/services/jira_push_preview.py's `_story_items`,
    app/models/implementation_task.py's `linked_story` string) — a Story
    row's title is expected to exactly match its origin backlog entry's
    title, so the existing Jira push flow keeps working against this
    table unmodified.
    """

    __tablename__ = "stories"
    __table_args__ = (UniqueConstraint("project_id", "title"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    # The story_backlog ArtifactVersion this row was parsed from, when it
    # came from a sync-from-backlog action — null for a story created
    # directly via POST /stories (no backlog document to trace back to).
    # use_alter: stories -> artifact_versions -> artifacts ->
    # workflow_nodes -> stories (via WorkflowNode.story_id, from an
    # earlier lane implementation this table's own docstring below
    # discusses) is a genuine FK cycle across four tables — same
    # technique app/models/artifact.py's own current_version_id already
    # uses to break its own circular FK with ArtifactVersion.
    source_artifact_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifact_versions.id", ondelete="CASCADE", use_alter=True, name="fk_stories_source_artifact_version_id"),
        nullable=True,
    )
    # A free-text description, distinct from user_story below — set
    # directly via POST /stories, or left blank for a story synced from a
    # backlog (which only ever has a User Story, not a separate summary).
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    story_type: Mapped[StoryType] = mapped_column(
        Enum(StoryType, native_enum=False, length=20, validate_strings=True), nullable=False
    )
    status: Mapped[StoryStatus] = mapped_column(
        Enum(StoryStatus, native_enum=False, length=20, validate_strings=True),
        default=StoryStatus.PENDING,
        nullable=False,
    )

    # Mirrors app/services/story_export.py's Story dataclass fields exactly.
    epic: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    feature: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    user_story: Mapped[str] = mapped_column(Text, default="", nullable=False)
    priority: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    dependencies: Mapped[str] = mapped_column(Text, default="", nullable=False)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    definition_of_done: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    # Set at sync-from-backlog time — preferably straight from the agent's
    # own "Suggested Owner Role" field (see app/services/story_export.py),
    # falling back to a keyword heuristic over the story's own text (same
    # _infer_area reuse as app/services/story_lane_templates.py's lane
    # ImplementationTask) only when the agent didn't state one. Always a
    # suggestion, never a hard assignment — a human can still assign any
    # owner via owner_user_id below regardless of what's suggested here.
    suggested_owner_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Editable via PATCH /stories/{id} — seeded from the agent's own
    # "Story Points Estimate" at sync time when it parses as a plain
    # integer, but a human estimate is always the field of record; this
    # app never recomputes it once set.
    story_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The remaining fields below all come straight from the agent's output
    # (see app/services/story_export.py's Story dataclass) — set once at
    # sync time, editable afterward like every other copied backlog field.
    business_value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    technical_areas: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    jira_issue_type: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    suggested_subtasks: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    release_readiness_criteria: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # The real Jira issue key once synced (e.g. "PROJ-123") — kept in sync
    # by POST /jira/push whenever it successfully creates a STORY issue
    # for this title (see app/api/routes/jira_integration.py). Distinct
    # from jira_issue_type above (the *kind* of issue to create — Story/
    # Task/Sub-task — stated by the agent before any push ever happens).
    jira_issue_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Requirement 6 — Jira's own numeric/opaque issue id and the browsable
    # issue URL, stored alongside the key so a UI never has to re-derive
    # or look them up from JiraIssueLink to render "Open Jira". Set
    # together with jira_issue_key by sync_story_to_jira
    # (app/services/story_jira_sync.py) — never independently.
    jira_issue_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    jira_issue_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Requirement 1 — this story's own Jira sync state machine, distinct
    # from a JiraIssueLink's live jira_status (Jira's own workflow
    # column). See app/models/enums.py's StoryJiraSyncStatus.
    jira_sync_status: Mapped[StoryJiraSyncStatus] = mapped_column(
        Enum(StoryJiraSyncStatus, native_enum=False, length=20, validate_strings=True),
        default=StoryJiraSyncStatus.NOT_SYNCED,
        nullable=False,
    )

    # Denormalized "current owner" for quick reads — the field of record
    # for who owns this story right now. app/models/story_assignee.py
    # holds the full assignment history (one row per assign/unassign
    # event); POST /stories/{id}/assign writes both in the same call.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    sprint_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sprints.id", ondelete="SET NULL"), nullable=True)
    # Set once POST /stories/{id}/lane succeeds — see
    # app/services/story_delivery.py. Null until then.
    lane_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    project: Mapped["Project"] = relationship("Project")
    source_artifact_version: Mapped["ArtifactVersion | None"] = relationship("ArtifactVersion")
    owner: Mapped["User | None"] = relationship("User", foreign_keys=[owner_user_id])
    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_id])
    sprint: Mapped["Sprint | None"] = relationship("Sprint", back_populates="stories")
    assignees: Mapped[list["StoryAssignee"]] = relationship(
        "StoryAssignee", back_populates="story", order_by="StoryAssignee.assigned_at"
    )
    delivery_lane: Mapped["StoryDeliveryLane | None"] = relationship("StoryDeliveryLane", back_populates="story", uselist=False)
