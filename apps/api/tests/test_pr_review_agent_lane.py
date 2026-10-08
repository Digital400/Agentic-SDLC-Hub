"""GitHub PR Review Agent for each story lane.

Covers the additive pieces built on top of the already-working PR review
run (see test_story_pr_and_review_hardening.py for that baseline):

1. Precondition — "Story LLD exists" hard-blocks a story-scoped run.
2. `unrelated_changes` is a distinct output, persisted and readable.
3. Completing a PR Review Agent run auto-completes the lane's
   PR_REVIEW_AGENT node and unlocks HUMAN_CODE_REVIEW.
4. "Mark Human Review Complete" — HUMAN_CODE_REVIEW is Tech-Lead-gated,
   same as LLD_REVIEW.
5. REQUEST_CHANGES -> send the lane back to Implementation (or
   Implementation Plan) for rework, re-locking everything after it.
"""

import uuid

import pytest

from app.api.routes.implementation_runs import create_pull_request
from app.api.routes.pr_review_runs import send_pr_review_back_for_rework, start_pr_review_run
from app.api.routes.stories import update_lane_node_status
from app.models import (
    PRReviewRecommendation,
    PRReviewRun,
    StoryArtifact,
    StoryDeliveryNodeStatus,
    User,
    UserRole,
)
from app.schemas.implementation_run import CreatePullRequestRequest
from app.schemas.pr_review_run import SendBackForReworkRequest, StartPRReviewRunRequest
from app.schemas.story import UpdateLaneNodeStatusRequest
from app.services.story_lld_agent import STORY_LLD_ARTIFACT_TYPE

from tests.test_story_pr_and_review_hardening import _force_mock_providers, _mock_github, _story_lld_agent, _story_with_accepted_run, _tech_lead  # noqa: F401


def test_pr_review_blocked_without_story_lld(db, project, actor, monkeypatch):
    story_row, lane, nodes, task, run = _story_with_accepted_run(db, project, actor)
    _mock_github(monkeypatch)
    create_pull_request(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)

    # Remove the Story LLD artifact _story_with_accepted_run drafted —
    # simulates a lane that somehow reached PR review without one.
    db.query(StoryArtifact).filter(
        StoryArtifact.story_id == story_row.id, StoryArtifact.artifact_type == STORY_LLD_ARTIFACT_TYPE
    ).delete()
    db.flush()

    with pytest.raises(Exception) as exc_info:
        start_pr_review_run(StartPRReviewRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    assert "Story LLD" in str(exc_info.value.detail)


def test_pr_review_completion_advances_lane_and_gates_human_review(db, project, actor, monkeypatch):
    story_row, lane, nodes, task, run = _story_with_accepted_run(db, project, actor)
    _mock_github(monkeypatch)
    create_pull_request(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)

    result = start_pr_review_run(StartPRReviewRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    assert result.status.value == "COMPLETED"
    # The mock diff always contains the implementation-agent TODO scaffold
    # -> heuristic critical finding -> REQUEST_CHANGES, never APPROVE.
    assert result.overall_recommendation == PRReviewRecommendation.REQUEST_CHANGES
    assert result.unrelated_changes == []  # heuristic never invents an issue it can't check

    db.refresh(nodes["PR_REVIEW_AGENT"])
    db.refresh(nodes["HUMAN_CODE_REVIEW"])
    assert nodes["PR_REVIEW_AGENT"].status == StoryDeliveryNodeStatus.COMPLETED
    assert nodes["HUMAN_CODE_REVIEW"].status == StoryDeliveryNodeStatus.READY

    non_tech_lead = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(non_tech_lead)
    db.flush()
    with pytest.raises(Exception) as exc_info:
        update_lane_node_status(
            nodes["HUMAN_CODE_REVIEW"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=non_tech_lead.id), db
        )
    assert exc_info.value.status_code == 403

    tech_lead = _tech_lead(db)
    updated = update_lane_node_status(
        nodes["HUMAN_CODE_REVIEW"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db
    )
    assert updated.status == StoryDeliveryNodeStatus.COMPLETED
    db.refresh(nodes["TESTING"])
    assert nodes["TESTING"].status == StoryDeliveryNodeStatus.READY


def test_request_changes_sends_lane_back_for_rework(db, project, actor, monkeypatch):
    story_row, lane, nodes, task, run = _story_with_accepted_run(db, project, actor)
    _mock_github(monkeypatch)
    create_pull_request(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)
    result = start_pr_review_run(StartPRReviewRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    assert result.overall_recommendation == PRReviewRecommendation.REQUEST_CHANGES

    updated = send_pr_review_back_for_rework(
        result.id, SendBackForReworkRequest(triggered_by_user_id=actor.id, target_node_key="IMPLEMENTATION"), db
    )
    assert updated.id == result.id

    db.refresh(lane)
    for key in ("IMPLEMENTATION",):
        node = db.get(type(nodes[key]), nodes[key].id)
        assert node.status == StoryDeliveryNodeStatus.READY
    for key in ("TEST_SCENARIOS", "PULL_REQUEST", "PR_REVIEW_AGENT", "HUMAN_CODE_REVIEW", "TESTING", "QA_APPROVAL", "RELEASE_READY"):
        node = db.get(type(nodes[key]), nodes[key].id)
        assert node.status == StoryDeliveryNodeStatus.LOCKED
    assert lane.current_node_id == nodes["IMPLEMENTATION"].id


def test_send_back_for_rework_requires_request_changes_recommendation(db, project, actor, monkeypatch):
    story_row, lane, nodes, task, run = _story_with_accepted_run(db, project, actor)
    _mock_github(monkeypatch)
    create_pull_request(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)
    result = start_pr_review_run(StartPRReviewRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)

    run_row = db.get(PRReviewRun, result.id)
    run_row.overall_recommendation = PRReviewRecommendation.COMMENT_ONLY
    db.flush()

    with pytest.raises(Exception) as exc_info:
        send_pr_review_back_for_rework(
            result.id, SendBackForReworkRequest(triggered_by_user_id=actor.id, target_node_key="IMPLEMENTATION"), db
        )
    assert exc_info.value.status_code == 409
