from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import PRReviewRecommendation, PRReviewRunStatus


class PRReviewRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One PR Review Agent execution against an already-created GitHub
    pull request — see app/services/pr_review_agent.py.

    GATE (requirement 1): starting a run requires the task's latest
    ImplementationRun to be ACCEPTED, a PullRequestLink to exist for it
    (a real GitHub PR — stricter than TestRun's "PR or diff" either/or),
    and a non-empty diff. Same reason TestRun bypasses
    GraphEngineService.validate_can_run against the `pr_review` node: that
    node's `required_inputs` still names a `code_change` Artifact type
    nothing in this codebase ever produces.

    SCOPE: fully bespoke — no Artifact/Review rows exist for this run,
    unlike TestRun's reuse of the generic QA-review mechanism. This task
    names no artifact type or review gate; "human reviewer decides final
    approval" is the real GitHub PR review, external to this app.
    `overall_recommendation` is advisory input to that human, never an
    approval this app grants on GitHub's behalf.

    RULE: nothing in this model, the service that populates it, or the
    routes that expose it ever merges a PR — there is no merge method
    anywhere in app/services/github_integration.py, by construction.
    """

    __tablename__ = "pr_review_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    # Null for a story-scoped run — see PullRequestLink.workflow_node_id's
    # own docstring for the same reasoning.
    workflow_node_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_nodes.id", ondelete="CASCADE"), nullable=True)
    implementation_task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("implementation_tasks.id", ondelete="CASCADE"), nullable=False
    )
    implementation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("implementation_runs.id", ondelete="CASCADE"), nullable=False
    )
    pull_request_link_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pull_request_links.id", ondelete="CASCADE"), nullable=False
    )
    # Set only for a run inside a per-story delivery lane — see
    # app/models/story.py.
    story_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), nullable=True)
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    status: Mapped[PRReviewRunStatus] = mapped_column(
        Enum(PRReviewRunStatus, native_enum=False, length=20, validate_strings=True),
        default=PRReviewRunStatus.PENDING,
        nullable=False,
    )
    # Null until COMPLETED — never defaults to APPROVE (see
    # pr_review_agent.py: neither the heuristic nor the coercion of an
    # invalid real-AI value ever picks APPROVE on its own).
    overall_recommendation: Mapped[PRReviewRecommendation | None] = mapped_column(
        Enum(PRReviewRecommendation, native_enum=False, length=20, validate_strings=True), nullable=True
    )

    # --- Output (requirement 4) -----------------------------------------
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    critical_findings: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    major_findings: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    minor_findings: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    missing_tests: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Review check 4 ("are there unrelated file changes?") — a distinct
    # output, not folded into findings, so a REQUEST_CHANGES send-back or
    # the UI can act on it directly. See pr_review_agent.py.
    unrelated_changes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # The agent's proposal — {"file","body"}. Requirement "comments must be
    # editable before posting" is a frontend concern: the UI holds edit
    # state and sends final text to /post-comments, this column is never
    # mutated by that action.
    suggested_comments: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final_reviewer_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Appended to by every /post-comments call — {"file","body",
    # "github_comment_id","github_comment_url","posted_at"} — never
    # replaced, a running record of what was actually posted to GitHub.
    posted_comments: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)

    used_mock: Mapped[bool] = mapped_column(default=True, nullable=False)
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship("Project")
    workflow_node: Mapped["WorkflowNode | None"] = relationship("WorkflowNode")
    implementation_task: Mapped["ImplementationTask"] = relationship("ImplementationTask")
    implementation_run: Mapped["ImplementationRun"] = relationship("ImplementationRun")
    pull_request_link: Mapped["PullRequestLink"] = relationship("PullRequestLink")
    story: Mapped["Story | None"] = relationship("Story")
    triggered_by: Mapped["User | None"] = relationship("User", foreign_keys=[triggered_by_user_id])
