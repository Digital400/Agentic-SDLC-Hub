from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import StoryTestExecutionQaDecision, StoryTestExecutionStatus


class StoryTestExecution(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One execution pass of a story's Testing stage — see
    app/services/story_test_execution.py.

    Distinct from TestRun (app/models/test_run.py, the AI Testing Agent's
    own execution of one implementation task): a StoryTestExecution is
    the story lane's TESTING-stage record of record — it tracks manual
    result entry against the story's own approved Test Scenarios, an
    agent-generated checklist, automated log attachments pulled from a
    CodeRun, and QA's own approve/reject decision, none of which TestRun
    models. A story may still separately have TestRun rows (e.g. from an
    earlier turn's project-level testing agent); this model does not
    replace or read from them.

    GATES (preconditions, enforced by start_story_test_execution):
    1. The story's Test Scenarios (StoryArtifact, story_test_scenarios)
       must exist.
    2. A GitHub PR (PullRequestLink) or a non-empty implementation diff
       must exist.
    3. The latest completed PRReviewRun for this story must have no
       unresolved critical findings — this codebase has no per-finding
       resolution tracking, so "unresolved" means simply "present": any
       critical finding on the latest run blocks testing from starting.

    HONESTY: `agent_checklist` is generated deterministically from the
    approved Test Scenarios document's own bullet points (see
    generate_agent_checklist) — there is no separate real-AI path for
    this specific step, since a checklist item here is just one already-
    approved scenario restated as a checkable step, not something an LLM
    needs to invent from scratch.

    RULE ("if testing fails, lane goes back to Code Implementation"):
    whenever `status` rolls up to FAILED (a manual result entry recording
    a FAIL, or a QA rejection), record_manual_results/qa_approve call
    app/services/story_delivery.py's send_lane_back_for_rework(
    target_node_key="IMPLEMENTATION") — the same mechanism the PR Review
    Agent's REQUEST_CHANGES rule already uses.

    RULE ("Story cannot be DONE until QA approval"): already structurally
    true — RELEASE_READY is LOCKED until QA_APPROVAL completes (see
    app/services/story_delivery.py's DEFAULT_STORY_DELIVERY_NODES
    sequence); qa_approve's APPROVED path is what completes QA_APPROVAL.
    """

    __tablename__ = "story_test_executions"

    story_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), nullable=False)
    lane_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("story_delivery_lanes.id", ondelete="CASCADE"), nullable=True)
    test_scenario_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("story_artifacts.id", ondelete="SET NULL"), nullable=True
    )
    pull_request_link_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pull_request_links.id", ondelete="SET NULL"), nullable=True
    )
    executed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    status: Mapped[StoryTestExecutionStatus] = mapped_column(
        Enum(StoryTestExecutionStatus, native_enum=False, length=20, validate_strings=True),
        default=StoryTestExecutionStatus.NOT_STARTED,
        nullable=False,
    )

    # --- Output (requirements 1-5) --------------------------------------
    # Manual/per-scenario entry — [{"scenario": str, "status": "PASS"|
    # "FAIL"|"BLOCKED", "notes": str}, ...].
    results_json: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    # Agent-generated checklist — [{"item": str, "done": bool}, ...].
    agent_checklist: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    # Free-text evidence references — manual links plus one entry per
    # attach_code_run_log call (a CodeRun's own logs are never copied
    # wholesale here, just referenced by id/status).
    evidence_urls: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Requirement 5, "bug creation suggestion" — free-text suggestions,
    # same shape as TestRun.bugs_found; this app creates no real ticket
    # anywhere from this list (no bug tracker integration exists) — it is
    # a suggestion a human acts on, not an automated bug filing.
    bugs_found: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    qa_decision: Mapped[StoryTestExecutionQaDecision] = mapped_column(
        Enum(StoryTestExecutionQaDecision, native_enum=False, length=20, validate_strings=True),
        default=StoryTestExecutionQaDecision.PENDING,
        nullable=False,
    )
    qa_decision_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    qa_decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    used_mock: Mapped[bool] = mapped_column(default=True, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    story: Mapped["Story"] = relationship("Story")
    lane: Mapped["StoryDeliveryLane | None"] = relationship("StoryDeliveryLane")
    test_scenario_artifact: Mapped["StoryArtifact | None"] = relationship("StoryArtifact")
    pull_request_link: Mapped["PullRequestLink | None"] = relationship("PullRequestLink")
    executed_by: Mapped["User | None"] = relationship("User", foreign_keys=[executed_by_user_id])
    qa_decided_by: Mapped["User | None"] = relationship("User", foreign_keys=[qa_decided_by_user_id])
