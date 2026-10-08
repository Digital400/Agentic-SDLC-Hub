"""The final Done gate for a story delivery lane — the one comprehensive
check run immediately before RELEASE_READY is allowed to complete (see
app/api/routes/stories.py's update_lane_node_status).

WHY A SEPARATE, EXPLICIT CHECK: most of these 9 conditions are already
*implied* by the lane's own strict forward-only node sequence (see
app/services/story_delivery.py's DEFAULT_STORY_DELIVERY_NODES) — e.g.
LLD_REVIEW must complete before RELEASE_READY is even reachable. But
"implied by reachability" is not the same as "explicitly verified at the
moment of completion," and at least two of the 9 (Jira sync, "no
unresolved critical findings") have no dedicated lane node at all. Rather
than trust that every earlier gate in the lane was airtight, this module
re-checks all 9 conditions directly against their source of truth, one
last time, right before a story is allowed to become DONE.

Returns a list of human-readable reasons the story is NOT ready — an
empty list means every gate passes and RELEASE_READY may complete.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import (
    PRReviewRun,
    PRReviewRunStatus,
    PullRequestLink,
    Story,
    StoryArtifact,
    StoryDeliveryLane,
    StoryDeliveryNodeStatus,
    StoryJiraSyncStatus,
    StoryTestExecution,
    StoryTestExecutionQaDecision,
    StoryTestExecutionStatus,
)
from app.services.story_implementation_plan_agent import STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE
from app.services.story_test_scenarios_agent import STORY_TEST_SCENARIOS_ARTIFACT_TYPE


def _latest_story_artifact(db: Session, story_id, artifact_type: str) -> StoryArtifact | None:
    return (
        db.query(StoryArtifact)
        .filter(StoryArtifact.story_id == story_id, StoryArtifact.artifact_type == artifact_type)
        .order_by(StoryArtifact.version_number.desc())
        .first()
    )


def _latest_pr_review(db: Session, story_id) -> PRReviewRun | None:
    return (
        db.query(PRReviewRun)
        .filter(PRReviewRun.story_id == story_id, PRReviewRun.status == PRReviewRunStatus.COMPLETED)
        .order_by(PRReviewRun.completed_at.desc())
        .first()
    )


def _latest_test_execution(db: Session, story_id) -> StoryTestExecution | None:
    return (
        db.query(StoryTestExecution)
        .filter(StoryTestExecution.story_id == story_id)
        .order_by(StoryTestExecution.created_at.desc())
        .first()
    )


def evaluate_story_done_gate(db: Session, *, story: Story, lane: StoryDeliveryLane) -> list[str]:
    reasons: list[str] = []

    # 1. Jira story is synced.
    if story.jira_sync_status != StoryJiraSyncStatus.SYNCED or not story.jira_issue_key:
        reasons.append("Jira story is not synced yet.")

    nodes = {n.node_key: n for n in lane.nodes}

    # 2. Story LLD is approved — LLD_REVIEW is that approval gate.
    lld_review = nodes.get("LLD_REVIEW")
    if lld_review is None or lld_review.status != StoryDeliveryNodeStatus.COMPLETED:
        reasons.append("Story LLD is not approved yet.")

    # 3. Implementation Plan exists.
    if _latest_story_artifact(db, story.id, STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE) is None:
        reasons.append("Implementation Plan does not exist yet.")

    # 4. Test Scenarios exist.
    if _latest_story_artifact(db, story.id, STORY_TEST_SCENARIOS_ARTIFACT_TYPE) is None:
        reasons.append("Test Scenarios do not exist yet.")

    # 5. GitHub PR exists.
    pr_link = db.query(PullRequestLink).filter(PullRequestLink.lane_id == lane.id).order_by(PullRequestLink.created_at.desc()).first()
    if pr_link is None:
        reasons.append("No GitHub PR exists yet.")

    # 6. PR Review completed. 9. No unresolved critical findings.
    pr_review = _latest_pr_review(db, story.id)
    if pr_review is None:
        reasons.append("PR Review has not completed yet.")
    elif len(pr_review.critical_findings) > 0:
        reasons.append(f"PR Review has {len(pr_review.critical_findings)} unresolved critical finding(s).")

    # 7. Testing passed. 8. QA approved.
    execution = _latest_test_execution(db, story.id)
    if execution is None or execution.status not in (StoryTestExecutionStatus.PASSED, StoryTestExecutionStatus.QA_APPROVED):
        reasons.append("Testing has not passed yet.")
    if execution is None or execution.qa_decision != StoryTestExecutionQaDecision.APPROVED:
        reasons.append("QA has not approved yet.")

    return reasons
