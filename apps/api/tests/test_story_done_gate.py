"""The final Done gate — app/services/story_done_gate.py.

Covers each of the 9 rules individually (each one unmet blocks; all 9 met
together pass), the RELEASE_READY route-level 409, and the "when story
becomes DONE" side effects: Story.status, the lane's own COMPLETED
status, the completion audit log, and a best-effort Jira comment attempt.
"""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.routes.stories import create_story, create_story_lane, update_lane_node_status
from app.models import (
    AuditLog,
    PRReviewRecommendation,
    PRReviewRun,
    PRReviewRunStatus,
    PullRequestLink,
    PullRequestStatus,
    Story,
    StoryArtifact,
    StoryDeliveryLane,
    StoryDeliveryLaneStatus,
    StoryDeliveryNode,
    StoryDeliveryNodeStatus,
    StoryJiraSyncStatus,
    StoryStatus,
    StoryTestExecution,
    StoryTestExecutionQaDecision,
    StoryTestExecutionStatus,
    StoryType,
    User,
    UserRole,
)
from app.schemas.story import CreateStoryLaneRequest, StoryCreate, UpdateLaneNodeStatusRequest
from app.services.story_done_gate import evaluate_story_done_gate
from tests.conftest import make_approved_artifact, make_node


def _approved_project(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content="## Story: Placeholder\n")


def _fresh_lane(db, project, actor, *, title: str = "Add reset endpoint"):
    _approved_project(db, project, actor)
    story = create_story(
        StoryCreate(project_id=project.id, title=title, mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id),
        db,
    )
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    story_row = db.get(Story, story.id)
    lane = story_row.delivery_lane
    return story_row, lane


def _product_owner(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="PO", role=UserRole.PRODUCT_OWNER)
    db.add(user)
    db.flush()
    return user


def _satisfy_all_nine_gates(db, *, project, story, lane, actor, skip: str | None = None) -> None:
    """Fabricates everything evaluate_story_done_gate checks, directly
    via ORM — bypassing the sequential node-by-node PATCH flow entirely,
    since the gate function itself doesn't care about node reachability,
    only each rule's own source of truth. `skip` names one gate to leave
    unsatisfied (see the keys below) for the individual-rule tests."""
    nodes = {n.node_key: n for n in lane.nodes}

    if skip != "jira":
        story.jira_sync_status = StoryJiraSyncStatus.SYNCED
        story.jira_issue_key = "TEST-1"

    if skip != "lld":
        nodes["LLD_REVIEW"].status = StoryDeliveryNodeStatus.COMPLETED

    if skip != "plan":
        db.add(
            StoryArtifact(
                story_id=story.id, lane_id=lane.id, node_id=nodes["IMPLEMENTATION_PLAN"].id, artifact_type="story_implementation_plan",
                title="Plan", content_markdown="## Implementation Summary\nPlan.\n", version_number=1, created_by_id=actor.id,
            )
        )

    if skip != "scenarios":
        db.add(
            StoryArtifact(
                story_id=story.id, lane_id=lane.id, node_id=nodes["TEST_SCENARIOS"].id, artifact_type="story_test_scenarios",
                title="Scenarios", content_markdown="## Functional Test Scenarios\n- One\n", version_number=1, created_by_id=actor.id,
            )
        )

    pr_link = None
    if skip != "pr":
        pr_link = PullRequestLink(
            project_id=project.id, story_id=story.id, lane_id=lane.id,
            implementation_task_id=uuid.uuid4(), implementation_run_id=uuid.uuid4(), repository_id=uuid.uuid4(),
            branch_name="story/x", base_branch="main", pr_number=1, pr_url="https://github.com/x/y/pull/1",
            status=PullRequestStatus.OPEN, commit_message="test",
        )
        db.add(pr_link)
        db.flush()

    if skip != "pr_review" and pr_link is not None:
        db.add(
            PRReviewRun(
                project_id=project.id, story_id=story.id, implementation_task_id=uuid.uuid4(), implementation_run_id=uuid.uuid4(),
                pull_request_link_id=pr_link.id, status=PRReviewRunStatus.COMPLETED,
                overall_recommendation=PRReviewRecommendation.APPROVE,
                critical_findings=[{"file": "x.py", "detail": "bad"}] if skip == "unresolved_critical" else [],
                completed_at=datetime.now(timezone.utc),
            )
        )

    if skip not in ("testing", "qa_approval"):
        db.add(
            StoryTestExecution(
                story_id=story.id, lane_id=lane.id, executed_by_user_id=actor.id,
                status=StoryTestExecutionStatus.NOT_STARTED if skip == "testing" else StoryTestExecutionStatus.QA_APPROVED,
                qa_decision=StoryTestExecutionQaDecision.PENDING if skip == "qa_approval" else StoryTestExecutionQaDecision.APPROVED,
                completed_at=datetime.now(timezone.utc),
            )
        )
    elif skip == "testing":
        db.add(
            StoryTestExecution(
                story_id=story.id, lane_id=lane.id, executed_by_user_id=actor.id,
                status=StoryTestExecutionStatus.FAILED, qa_decision=StoryTestExecutionQaDecision.PENDING,
            )
        )
    elif skip == "qa_approval":
        db.add(
            StoryTestExecution(
                story_id=story.id, lane_id=lane.id, executed_by_user_id=actor.id,
                status=StoryTestExecutionStatus.PASSED, qa_decision=StoryTestExecutionQaDecision.PENDING,
            )
        )

    db.flush()


# --- Individual gate rules ---------------------------------------------------------------


@pytest.mark.parametrize(
    "skip,expected_fragment",
    [
        ("jira", "Jira story is not synced"),
        ("lld", "Story LLD is not approved"),
        ("plan", "Implementation Plan does not exist"),
        ("scenarios", "Test Scenarios do not exist"),
        ("pr", "No GitHub PR exists"),
        ("pr_review", "PR Review has not completed"),
        ("unresolved_critical", "unresolved critical finding"),
        ("testing", "Testing has not passed"),
        ("qa_approval", "QA has not approved"),
    ],
)
def test_each_gate_rule_blocks_when_unmet(db, project, actor, skip, expected_fragment):
    story, lane = _fresh_lane(db, project, actor, title=f"Story {skip}")
    _satisfy_all_nine_gates(db, project=project, story=story, lane=lane, actor=actor, skip=skip)

    reasons = evaluate_story_done_gate(db, story=story, lane=lane)

    assert any(expected_fragment in r for r in reasons), reasons


def test_all_nine_gates_satisfied_passes(db, project, actor):
    story, lane = _fresh_lane(db, project, actor)
    _satisfy_all_nine_gates(db, project=project, story=story, lane=lane, actor=actor)

    assert evaluate_story_done_gate(db, story=story, lane=lane) == []


# --- Route-level enforcement + DONE side effects -----------------------------------------


def test_release_ready_completion_blocked_with_combined_reasons(db, project, actor):
    story, lane = _fresh_lane(db, project, actor)
    # Nothing fabricated — every gate is unmet.
    nodes = {n.node_key: n for n in lane.nodes}
    release_ready = nodes["RELEASE_READY"]
    # Force it READY directly (bypassing the sequential unlock) so only
    # the Done gate itself is under test, not lane-reachability.
    release_ready.status = StoryDeliveryNodeStatus.READY
    db.flush()

    po = _product_owner(db)
    with pytest.raises(HTTPException) as exc_info:
        update_lane_node_status(release_ready.id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=po.id), db)
    assert exc_info.value.status_code == 409
    assert "Jira story is not synced" in str(exc_info.value.detail)
    assert "Story LLD is not approved" in str(exc_info.value.detail)

    assert db.get(Story, story.id).status != StoryStatus.DONE


def test_release_ready_completion_succeeds_and_marks_story_done_with_audit_log(db, project, actor):
    story, lane = _fresh_lane(db, project, actor)
    nodes = {n.node_key: n for n in lane.nodes}
    _satisfy_all_nine_gates(db, project=project, story=story, lane=lane, actor=actor)
    for key in ("STORY_LLD", "IMPLEMENTATION_PLAN", "IMPLEMENTATION", "TEST_SCENARIOS", "PULL_REQUEST", "PR_REVIEW_AGENT", "HUMAN_CODE_REVIEW", "TESTING", "QA_APPROVAL"):
        nodes[key].status = StoryDeliveryNodeStatus.COMPLETED
    nodes["RELEASE_READY"].status = StoryDeliveryNodeStatus.READY
    db.flush()

    po = _product_owner(db)
    updated = update_lane_node_status(nodes["RELEASE_READY"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=po.id), db)
    assert updated.status == StoryDeliveryNodeStatus.COMPLETED

    story_row = db.get(Story, story.id)
    assert story_row.status == StoryStatus.DONE

    lane_row = db.get(StoryDeliveryLane, lane.id)
    assert lane_row.status == StoryDeliveryLaneStatus.COMPLETED

    done_log = (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == "Story", AuditLog.entity_id == story_row.id, AuditLog.action == "story.done")
        .first()
    )
    assert done_log is not None
    assert done_log.extra_data["jira_issue_key"] == "TEST-1"
