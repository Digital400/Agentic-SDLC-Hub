"""Story Testing stage — StoryTestExecution, the lane's TESTING-stage
record of record. See app/models/story_test_execution.py for the model's
full docstring (gates, rules, and the honest scope note on
`agent_checklist`).

This module owns every state transition on a StoryTestExecution row:
starting one (preconditions 1-3), recording manual per-scenario results
(requirement 4, "pass/fail per scenario") with the automatic status
rollup and the "testing fails -> lane goes back to Code Implementation"
rule, generating the agent checklist (requirement 2), attaching a
CodeRun's log as evidence (requirement 3), and QA's own approve/reject
decision (requirement 6).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import (
    CodeRun,
    ImplementationRun,
    ImplementationRunReviewStatus,
    ImplementationTask,
    PRReviewRun,
    PRReviewRunStatus,
    PullRequestLink,
    Story,
    StoryActivityLog,
    StoryArtifact,
    StoryDeliveryLane,
    StoryDeliveryNode,
    StoryDeliveryNodeStatus,
    StoryTestExecution,
    StoryTestExecutionQaDecision,
    StoryTestExecutionStatus,
    User,
)
from app.services.audit import record_audit_log
from app.services.story_delivery import advance_lane, send_lane_back_for_rework
from app.services.story_test_scenarios_agent import STORY_TEST_SCENARIOS_ARTIFACT_TYPE

_VALID_RESULT_STATUSES = {"PASS", "FAIL", "BLOCKED"}


class StoryTestExecutionError(Exception):
    """Raised when a StoryTestExecution action can't proceed."""


def _log_story_activity(
    db: Session, *, story: Story, lane: StoryDeliveryLane | None, node: StoryDeliveryNode | None,
    action: str, actor: User | None, details: dict | None = None,
) -> None:
    """Writes a StoryActivityLog row — additive alongside the generic
    AuditLog (see record_audit_log calls throughout this module), same
    small helper app/api/routes/stories.py's own _log_story_activity
    duplicates for the same reason (no shared cross-module import of a
    private helper)."""
    db.add(
        StoryActivityLog(
            story_id=story.id, lane_id=lane.id if lane is not None else None, node_id=node.id if node is not None else None,
            action=action, actor_user_id=actor.id if actor is not None else None, details=details,
        )
    )


def _latest_test_scenarios(db: Session, story_id: uuid.UUID) -> StoryArtifact | None:
    return (
        db.query(StoryArtifact)
        .filter(StoryArtifact.story_id == story_id, StoryArtifact.artifact_type == STORY_TEST_SCENARIOS_ARTIFACT_TYPE)
        .order_by(StoryArtifact.version_number.desc())
        .first()
    )


def _latest_pr_link(db: Session, lane_id: uuid.UUID) -> PullRequestLink | None:
    return (
        db.query(PullRequestLink)
        .filter(PullRequestLink.lane_id == lane_id)
        .order_by(PullRequestLink.created_at.desc())
        .first()
    )


def _latest_accepted_diff(db: Session, story_id: uuid.UUID) -> str:
    task = db.query(ImplementationTask).filter(ImplementationTask.story_id == story_id).first()
    if task is None:
        return ""
    run = (
        db.query(ImplementationRun)
        .filter(ImplementationRun.implementation_task_id == task.id, ImplementationRun.review_status == ImplementationRunReviewStatus.ACCEPTED)
        .order_by(ImplementationRun.completed_at.desc())
        .first()
    )
    return run.diff_text if run is not None else ""


def _latest_pr_review_critical_count(db: Session, story_id: uuid.UUID) -> int:
    run = (
        db.query(PRReviewRun)
        .filter(PRReviewRun.story_id == story_id, PRReviewRun.status == PRReviewRunStatus.COMPLETED)
        .order_by(PRReviewRun.completed_at.desc())
        .first()
    )
    if run is None:
        return 0  # soft — a PR review run isn't required to exist yet to start testing
    return len(run.critical_findings)


def start_story_test_execution(db: Session, *, story: Story, lane: StoryDeliveryLane, actor: User) -> StoryTestExecution:
    """Preconditions 1-3. Raises StoryTestExecutionError (-> 409) if any
    fails."""
    scenarios = _latest_test_scenarios(db, story.id)
    if scenarios is None:
        raise StoryTestExecutionError("Cannot start testing — this story has no Test Scenarios drafted yet.")

    pr_link = _latest_pr_link(db, lane.id)
    diff_text = "" if pr_link is not None else _latest_accepted_diff(db, story.id)
    if pr_link is None and not diff_text.strip():
        raise StoryTestExecutionError("Cannot start testing — no GitHub PR and no accepted implementation diff exist yet.")

    critical_count = _latest_pr_review_critical_count(db, story.id)
    if critical_count > 0:
        raise StoryTestExecutionError(
            f"Cannot start testing — the latest PR review has {critical_count} unresolved critical finding(s)."
        )

    execution = StoryTestExecution(
        story_id=story.id,
        lane_id=lane.id,
        test_scenario_artifact_id=scenarios.id,
        pull_request_link_id=pr_link.id if pr_link is not None else None,
        executed_by_user_id=actor.id,
        status=StoryTestExecutionStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
    )
    db.add(execution)
    db.flush()

    testing_node = next((n for n in lane.nodes if n.node_key == "TESTING"), None)
    if testing_node is not None and testing_node.status == StoryDeliveryNodeStatus.READY:
        testing_node.status = StoryDeliveryNodeStatus.IN_PROGRESS
        testing_node.started_at = testing_node.started_at or datetime.now(timezone.utc)
        db.flush()

    _log_story_activity(
        db, story=story, lane=lane, node=testing_node, action="story_test_execution.started", actor=actor,
        details={"execution_id": str(execution.id), "test_scenario_artifact_id": str(scenarios.id)},
    )
    record_audit_log(
        db, project_id=story.project_id, actor_user_id=actor.id, action="story_test_execution.started",
        entity_type="StoryTestExecution", entity_id=execution.id, extra_data={"story_id": str(story.id)},
    )
    return execution


def generate_agent_checklist(db: Session, *, execution: StoryTestExecution, actor: User) -> StoryTestExecution:
    """Requirement 2, "agent-generated testing checklist" — deterministic
    derivation from the approved Test Scenarios document (see the model
    docstring's HONESTY note: no real-AI branch for this specific step)."""
    scenarios = db.get(StoryArtifact, execution.test_scenario_artifact_id) if execution.test_scenario_artifact_id else None
    content = scenarios.content_markdown if scenarios is not None else ""

    checklist: list[dict] = []
    for line in content.splitlines():
        stripped = line.strip().lstrip("-*").strip()
        # Only bullet/numbered lines under a scenario section — skip
        # headings ("#") and blank lines.
        if not stripped or line.strip().startswith("#"):
            continue
        if line.strip().startswith(("-", "*")) or (stripped[:1].isdigit() and "." in stripped[:4]):
            checklist.append({"item": stripped, "done": False})

    execution.agent_checklist = checklist
    execution.used_mock = True
    db.flush()

    _log_story_activity(
        db, story=execution.story, lane=execution.lane, node=None, action="story_test_execution.checklist_generated",
        actor=actor, details={"execution_id": str(execution.id), "item_count": len(checklist)},
    )
    return execution


def attach_code_run_log(db: Session, *, execution: StoryTestExecution, code_run: CodeRun, actor: User) -> StoryTestExecution:
    """Requirement 3, "automated test log attachment from CodeRunner" —
    references (never copies wholesale) a CodeRun's own logs, same
    disclosure discipline CodeRunnerService._log already applies (no
    secret ever ends up in this list, since it never leaves CodeRun.logs
    itself)."""
    if code_run.story_id != execution.story_id:
        raise StoryTestExecutionError("This code run belongs to a different story.")

    log_lines = [f"[{entry.get('level', 'INFO')}] {entry.get('message', '')}" for entry in code_run.logs]
    reference = f"coderun:{code_run.id} status={code_run.status.value} log_entries={len(code_run.logs)}"
    execution.evidence_urls = [*execution.evidence_urls, reference]
    if log_lines:
        execution.evidence_urls = [*execution.evidence_urls, *[f"coderun:{code_run.id}: {line}" for line in log_lines[-10:]]]
    db.flush()

    _log_story_activity(
        db, story=execution.story, lane=execution.lane, node=None, action="story_test_execution.code_run_log_attached",
        actor=actor, details={"execution_id": str(execution.id), "code_run_id": str(code_run.id)},
    )
    return execution


def _recompute_status(results: list[dict]) -> StoryTestExecutionStatus:
    if not results:
        return StoryTestExecutionStatus.IN_PROGRESS
    statuses = {str(r.get("status", "")).upper() for r in results}
    if "FAIL" in statuses:
        return StoryTestExecutionStatus.FAILED
    if "BLOCKED" in statuses:
        return StoryTestExecutionStatus.BLOCKED
    if statuses == {"PASS"}:
        return StoryTestExecutionStatus.PASSED
    return StoryTestExecutionStatus.IN_PROGRESS


def record_manual_results(
    db: Session, *, execution: StoryTestExecution, results: list[dict], evidence_urls: list[str], bugs_found: list[str], actor: User
) -> StoryTestExecution:
    """Requirements 1/4/5 — manual per-scenario entry, evidence, and bug
    suggestions. Rule — "if testing fails, lane goes back to Code
    Implementation": recomputing to FAILED triggers
    send_lane_back_for_rework(target_node_key="IMPLEMENTATION")."""
    for r in results:
        status_value = str(r.get("status", "")).upper()
        if status_value not in _VALID_RESULT_STATUSES:
            raise StoryTestExecutionError(f"Invalid result status '{r.get('status')}' — must be one of {sorted(_VALID_RESULT_STATUSES)}.")

    execution.results_json = results
    if evidence_urls:
        execution.evidence_urls = [*execution.evidence_urls, *evidence_urls]
    if bugs_found:
        execution.bugs_found = [*execution.bugs_found, *bugs_found]

    new_status = _recompute_status(results)
    execution.status = new_status
    if new_status in (StoryTestExecutionStatus.PASSED, StoryTestExecutionStatus.FAILED):
        execution.completed_at = datetime.now(timezone.utc)
    db.flush()

    record_audit_log(
        db, project_id=execution.story.project_id, actor_user_id=actor.id, action="story_test_execution.results_recorded",
        entity_type="StoryTestExecution", entity_id=execution.id,
        extra_data={"status": new_status.value, "pass_count": sum(1 for r in results if str(r.get("status", "")).upper() == "PASS"),
                    "fail_count": sum(1 for r in results if str(r.get("status", "")).upper() == "FAIL")},
    )
    _log_story_activity(
        db, story=execution.story, lane=execution.lane, node=None, action="story_test_execution.results_recorded",
        actor=actor, details={"execution_id": str(execution.id), "status": new_status.value},
    )

    if new_status == StoryTestExecutionStatus.FAILED and execution.lane is not None:
        send_lane_back_for_rework(db, lane=execution.lane, target_node_key="IMPLEMENTATION", actor=actor)
        record_audit_log(
            db, project_id=execution.story.project_id, actor_user_id=actor.id,
            action="story_test_execution.failed_sent_back_for_rework", entity_type="StoryTestExecution", entity_id=execution.id,
        )

    return execution


def qa_approve(
    db: Session, *, execution: StoryTestExecution, decision: str, actor: User, reason: str = ""
) -> StoryTestExecution:
    """Requirement 6, QA approval — the same generic-endpoint-completes-
    the-node convention as every other lane review gate, but expressed as
    a dedicated action here since a StoryTestExecution, not just a
    StoryDeliveryNode status, needs updating. APPROVED completes
    QA_APPROVAL directly (mirrors PR_REVIEW_AGENT's own auto-completion
    in app/api/routes/pr_review_runs.py); REJECTED is a test failure —
    same rework rule as record_manual_results."""
    try:
        qa_decision = StoryTestExecutionQaDecision(decision)
    except ValueError as exc:
        raise StoryTestExecutionError(f"Invalid decision '{decision}' — must be APPROVED or REJECTED.") from exc
    if qa_decision == StoryTestExecutionQaDecision.PENDING:
        raise StoryTestExecutionError("decision must be APPROVED or REJECTED, not PENDING.")

    execution.qa_decision = qa_decision
    execution.qa_decision_reason = reason
    execution.qa_decided_by_user_id = actor.id
    execution.completed_at = execution.completed_at or datetime.now(timezone.utc)

    if qa_decision == StoryTestExecutionQaDecision.APPROVED:
        execution.status = StoryTestExecutionStatus.QA_APPROVED
        db.flush()
        lane = execution.lane
        qa_node = next((n for n in lane.nodes if n.node_key == "QA_APPROVAL"), None) if lane is not None else None
        if qa_node is not None and qa_node.status != StoryDeliveryNodeStatus.COMPLETED:
            qa_node.status = StoryDeliveryNodeStatus.COMPLETED
            qa_node.completed_at = datetime.now(timezone.utc)
            db.flush()
            advance_lane(db, lane=lane, completed_node=qa_node)
        record_audit_log(
            db, project_id=execution.story.project_id, actor_user_id=actor.id, action="story_test_execution.qa_approved",
            entity_type="StoryTestExecution", entity_id=execution.id,
        )
        _log_story_activity(
            db, story=execution.story, lane=execution.lane, node=qa_node, action="story_test_execution.qa_approved",
            actor=actor, details={"execution_id": str(execution.id)},
        )
    else:
        execution.status = StoryTestExecutionStatus.FAILED
        db.flush()
        record_audit_log(
            db, project_id=execution.story.project_id, actor_user_id=actor.id, action="story_test_execution.qa_rejected",
            entity_type="StoryTestExecution", entity_id=execution.id, extra_data={"reason": reason},
        )
        _log_story_activity(
            db, story=execution.story, lane=execution.lane, node=None, action="story_test_execution.qa_rejected",
            actor=actor, details={"execution_id": str(execution.id), "reason": reason},
        )
        if execution.lane is not None:
            send_lane_back_for_rework(db, lane=execution.lane, target_node_key="IMPLEMENTATION", actor=actor)
            record_audit_log(
                db, project_id=execution.story.project_id, actor_user_id=actor.id,
                action="story_test_execution.failed_sent_back_for_rework", entity_type="StoryTestExecution", entity_id=execution.id,
            )

    return execution
