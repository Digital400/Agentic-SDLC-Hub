"""Story Testing stage — StoryTestExecution.

Covers the preconditions (Test Scenarios exist, PR/diff exists, no
unresolved PR-review critical findings), the six capabilities (manual
result entry, agent checklist, CodeRunner log attachment, pass/fail per
scenario, bug suggestions, QA approval), and the two rules: "if testing
fails, lane goes back to Code Implementation" and "story cannot be DONE
until QA approval."
"""

import uuid

import pytest

from app.api.routes.implementation_runs import create_pull_request
from app.api.routes.pr_review_runs import start_pr_review_run
from app.api.routes.stories import draft_story_test_scenarios, update_lane_node_status
from app.api.routes.story_test_executions import (
    attach_code_run,
    generate_checklist,
    qa_approve_execution,
    record_results,
    start_test_execution,
)
from app.models import (
    AgentDefinition,
    CodeRun,
    CodeRunStatus,
    StoryDeliveryNodeStatus,
    StoryTestExecutionQaDecision,
    StoryTestExecutionStatus,
    User,
    UserRole,
)
from app.schemas.implementation_run import CreatePullRequestRequest
from app.schemas.pr_review_run import StartPRReviewRunRequest
from app.schemas.story import DraftStoryTestScenariosRequest, UpdateLaneNodeStatusRequest
from app.schemas.story_test_execution import (
    AttachCodeRunLogRequest,
    GenerateChecklistRequest,
    QaApproveRequest,
    RecordTestResultsRequest,
    StartStoryTestExecutionRequest,
)

from app.services.story_test_scenarios_agent import STORY_TEST_SCENARIOS_AGENT_KEY
from tests.conftest import make_agent_prompt
from tests.test_story_pr_and_review_hardening import (  # noqa: F401
    _force_mock_providers,
    _mock_github,
    _story_lld_agent,
    _story_with_accepted_run,
    _tech_lead,
)


@pytest.fixture(autouse=True)
def _story_test_scenarios_agent(db):
    agent = AgentDefinition(agent_key=STORY_TEST_SCENARIOS_AGENT_KEY, name="Story Test Scenarios Agent", model_name="mock")
    db.add(agent)
    db.flush()
    make_agent_prompt(db, stage="story_test_scenarios", agent=agent)


def _qa(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="QA", role=UserRole.QA)
    db.add(user)
    db.flush()
    return user


def _story_ready_for_testing(db, project, actor, monkeypatch, *, title: str = "Add reset endpoint"):
    """Drives a story through PR creation + a clean (no-critical-findings)
    PR review, then drafts Test Scenarios — everything Testing's own
    preconditions require."""
    story_row, lane, nodes, task, run = _story_with_accepted_run(db, project, actor, title=title)
    _mock_github(monkeypatch)
    create_pull_request(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)

    # IMPLEMENTATION must complete before TEST_SCENARIOS unlocks —
    # _story_with_accepted_run only accepts the ImplementationRun, it
    # doesn't complete the lane node itself.
    update_lane_node_status(nodes["IMPLEMENTATION"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)

    # TEST_SCENARIOS sits before PULL_REQUEST/PR_REVIEW_AGENT in the lane
    # sequence but _story_with_accepted_run doesn't draft it — do that now
    # so testing's own "Test Scenarios exist" precondition is satisfiable.
    draft_story_test_scenarios(nodes["TEST_SCENARIOS"].id, DraftStoryTestScenariosRequest(triggered_by_user_id=actor.id), db)
    update_lane_node_status(nodes["TEST_SCENARIOS"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)
    update_lane_node_status(nodes["PULL_REQUEST"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)

    # PR_REVIEW_AGENT auto-completes once its run finishes (see
    # app/api/routes/pr_review_runs.py) — the mock heuristic always flags
    # the implementation-agent TODO scaffold as a critical finding, so
    # clear it here to simulate a clean review (this helper is building
    # the "testing is reachable" happy path, not testing PR review itself).
    from app.models import PRReviewRun

    start_pr_review_run(StartPRReviewRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    review_run = db.query(PRReviewRun).filter(PRReviewRun.implementation_task_id == task.id).order_by(PRReviewRun.completed_at.desc()).first()
    review_run.critical_findings = []
    db.flush()

    tech_lead = _tech_lead(db)
    update_lane_node_status(nodes["HUMAN_CODE_REVIEW"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db)

    return story_row, lane, nodes, task, run


def test_testing_blocked_without_test_scenarios(db, project, actor, monkeypatch):
    story_row, lane, nodes, task, run = _story_with_accepted_run(db, project, actor)
    _mock_github(monkeypatch)
    create_pull_request(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)

    with pytest.raises(Exception) as exc_info:
        start_test_execution(StartStoryTestExecutionRequest(story_id=story_row.id, triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "Test Scenarios" in str(exc_info.value.detail)


def test_testing_blocked_by_unresolved_critical_findings(db, project, actor, monkeypatch):
    story_row, lane, nodes, task, run = _story_with_accepted_run(db, project, actor)
    _mock_github(monkeypatch)
    create_pull_request(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)
    update_lane_node_status(nodes["IMPLEMENTATION"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)
    draft_story_test_scenarios(nodes["TEST_SCENARIOS"].id, DraftStoryTestScenariosRequest(triggered_by_user_id=actor.id), db)
    update_lane_node_status(nodes["TEST_SCENARIOS"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)

    # The mock diff always contains the implementation-agent TODO scaffold
    # -> heuristic critical finding -> REQUEST_CHANGES with a critical
    # finding present.
    start_pr_review_run(StartPRReviewRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)

    with pytest.raises(Exception) as exc_info:
        start_test_execution(StartStoryTestExecutionRequest(story_id=story_row.id, triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert "critical finding" in str(exc_info.value.detail)


def test_full_testing_flow_pass_and_qa_approval(db, project, actor, monkeypatch):
    story_row, lane, nodes, task, run = _story_ready_for_testing(db, project, actor, monkeypatch)

    execution = start_test_execution(StartStoryTestExecutionRequest(story_id=story_row.id, triggered_by_user_id=actor.id), db)
    assert execution.status == StoryTestExecutionStatus.IN_PROGRESS
    db.refresh(nodes["TESTING"])
    assert nodes["TESTING"].status == StoryDeliveryNodeStatus.IN_PROGRESS

    # Requirement 2 — agent-generated checklist.
    execution = generate_checklist(execution.id, GenerateChecklistRequest(triggered_by_user_id=actor.id), db)
    assert isinstance(execution.agent_checklist, list)

    # Requirement 3 — automated test log attachment from CodeRunner.
    from app.models import PullRequestLink

    pr_link = db.query(PullRequestLink).filter(PullRequestLink.implementation_run_id == run.id).first()
    code_run = CodeRun(
        project_id=project.id, story_id=story_row.id, lane_id=lane.id, repository_id=pr_link.repository_id,
        branch_name="story/x", status=CodeRunStatus.PUSHED, logs=[{"level": "INFO", "message": "tests passed"}],
    )
    db.add(code_run)
    db.flush()
    execution = attach_code_run(execution.id, AttachCodeRunLogRequest(triggered_by_user_id=actor.id, code_run_id=code_run.id), db)
    assert any("coderun:" in u for u in execution.evidence_urls)

    # Requirements 1/4/5 — manual pass/fail per scenario + bug suggestion.
    execution = record_results(
        execution.id,
        RecordTestResultsRequest(
            triggered_by_user_id=actor.id,
            results=[{"scenario": "Login succeeds", "status": "PASS", "notes": "ok"}],
            evidence_urls=["https://ci.example.com/run/1"],
            bugs_found=[],
        ),
        db,
    )
    assert execution.status == StoryTestExecutionStatus.PASSED

    # Requirement 6 — QA approval, non-QA rejected first.
    dev = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(dev)
    db.flush()
    with pytest.raises(Exception) as exc_info:
        qa_approve_execution(execution.id, QaApproveRequest(actor_user_id=dev.id, decision="APPROVED"), db)
    assert exc_info.value.status_code == 403

    qa = _qa(db)
    execution = qa_approve_execution(execution.id, QaApproveRequest(actor_user_id=qa.id, decision="APPROVED"), db)
    assert execution.status == StoryTestExecutionStatus.QA_APPROVED
    assert execution.qa_decision == StoryTestExecutionQaDecision.APPROVED

    db.refresh(nodes["QA_APPROVAL"])
    db.refresh(nodes["RELEASE_READY"])
    assert nodes["QA_APPROVAL"].status == StoryDeliveryNodeStatus.COMPLETED
    assert nodes["RELEASE_READY"].status == StoryDeliveryNodeStatus.READY  # story still not DONE — RELEASE_READY not completed yet


def test_failed_result_sends_lane_back_to_implementation(db, project, actor, monkeypatch):
    story_row, lane, nodes, task, run = _story_ready_for_testing(db, project, actor, monkeypatch)
    execution = start_test_execution(StartStoryTestExecutionRequest(story_id=story_row.id, triggered_by_user_id=actor.id), db)

    execution = record_results(
        execution.id,
        RecordTestResultsRequest(
            triggered_by_user_id=actor.id,
            results=[{"scenario": "Login succeeds", "status": "FAIL", "notes": "500 error"}],
            evidence_urls=[], bugs_found=["Login throws 500 on valid credentials"],
        ),
        db,
    )
    assert execution.status == StoryTestExecutionStatus.FAILED
    assert execution.bugs_found == ["Login throws 500 on valid credentials"]

    db.refresh(lane)
    for key in ("IMPLEMENTATION",):
        node = db.get(type(nodes[key]), nodes[key].id)
        assert node.status == StoryDeliveryNodeStatus.READY
    for key in ("TEST_SCENARIOS", "PULL_REQUEST", "PR_REVIEW_AGENT", "HUMAN_CODE_REVIEW", "TESTING", "QA_APPROVAL", "RELEASE_READY"):
        node = db.get(type(nodes[key]), nodes[key].id)
        assert node.status == StoryDeliveryNodeStatus.LOCKED
    assert lane.current_node_id == nodes["IMPLEMENTATION"].id


def test_qa_rejection_also_sends_lane_back(db, project, actor, monkeypatch):
    story_row, lane, nodes, task, run = _story_ready_for_testing(db, project, actor, monkeypatch)
    execution = start_test_execution(StartStoryTestExecutionRequest(story_id=story_row.id, triggered_by_user_id=actor.id), db)
    execution = record_results(
        execution.id,
        RecordTestResultsRequest(
            triggered_by_user_id=actor.id, results=[{"scenario": "Login succeeds", "status": "PASS", "notes": ""}],
        ),
        db,
    )
    assert execution.status == StoryTestExecutionStatus.PASSED

    qa = _qa(db)
    execution = qa_approve_execution(execution.id, QaApproveRequest(actor_user_id=qa.id, decision="REJECTED", reason="Missed a case"), db)
    assert execution.status == StoryTestExecutionStatus.FAILED
    assert execution.qa_decision == StoryTestExecutionQaDecision.REJECTED

    db.refresh(lane)
    assert lane.current_node_id == nodes["IMPLEMENTATION"].id
