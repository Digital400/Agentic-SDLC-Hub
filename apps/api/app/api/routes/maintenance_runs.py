"""Maintenance Agent execution endpoints.

Covers: start a repeatable, manually-triggered maintenance health report
for a project (requirement 6: "generate weekly maintenance report
manually" — nothing here runs on a schedule, every run is explicit), and
fetch one run. "List runs by project" lives in
app/api/routes/projects.py, next to that resource's other sub-lists.

GATE: a project must have an APPROVED deployment_record artifact before a
report can be generated — "released project summary" (requirement 3)
requires an actual release to summarize.

NO REVIEW (rule: "cannot change production; recommend actions only" —
see app/models/maintenance_run.py's class docstring): completing a run
sets the produced Artifact straight to APPROVED. There is no accept/
reject/approve endpoint here — nothing about this report needs approval.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import (
    Artifact,
    ArtifactStatus,
    ArtifactVersion,
    MaintenanceRun,
    MaintenanceRunStatus,
    Project,
    PullRequestLink,
    TestRun,
    TestRunStatus,
    User,
    WorkflowNode,
)
from app.schemas.maintenance_run import MaintenanceRunRead, StartMaintenanceRunRequest
from app.services.audit import record_audit_log
from app.services.maintenance_agent import MAINTENANCE_REPORT_ARTIFACT_TYPE, run_maintenance_agent
from app.services.permissions import require_can_edit_stage

router = APIRouter(prefix="/maintenance-runs", tags=["maintenance-runs"])


def _get_run_or_404(db: Session, run_id: uuid.UUID) -> MaintenanceRun:
    run = db.get(MaintenanceRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Maintenance run {run_id} not found")
    return run


def _open_bugs(db: Session, project_id: uuid.UUID) -> list[str]:
    """TestRun.bugs_found across this project's COMPLETED test runs — the
    same "open issues" framing app/services/jira_push_preview.py's
    _testing_bug_items already establishes (no dedicated Issue-tracking
    model exists in this codebase)."""
    runs = (
        db.query(TestRun)
        .filter(TestRun.project_id == project_id, TestRun.status == TestRunStatus.COMPLETED)
        .order_by(TestRun.completed_at.asc())
        .all()
    )
    return [bug for run in runs for bug in run.bugs_found]


def _pr_history(db: Session, project_id: uuid.UUID) -> list[str]:
    links = (
        db.query(PullRequestLink)
        .filter(PullRequestLink.project_id == project_id)
        .order_by(PullRequestLink.created_at.desc())
        .all()
    )
    return [f"PR #{link.pr_number} ({link.status.value}): {link.commit_message} — {link.pr_url}" for link in links]


def _test_summary(db: Session, project_id: uuid.UUID) -> str:
    runs = db.query(TestRun).filter(TestRun.project_id == project_id, TestRun.status == TestRunStatus.COMPLETED).all()
    if not runs:
        return "No completed test runs on record for this project yet."
    total_pass = sum(r.pass_count for r in runs)
    total_fail = sum(r.fail_count for r in runs)
    return f"{len(runs)} completed test run(s): {total_pass} passed, {total_fail} failed in total."


@router.post("", response_model=MaintenanceRunRead, status_code=status.HTTP_201_CREATED)
def start_maintenance_run(payload: StartMaintenanceRunRequest, db: Session = Depends(get_db)) -> MaintenanceRunRead:
    project = db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"project_id {payload.project_id} does not match an existing project")

    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")

    maintenance_node = (
        db.query(WorkflowNode).filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == "maintenance").first()
    )
    if maintenance_node is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Project {project.id}'s workflow has no maintenance stage.")
    require_can_edit_stage(triggered_by, maintenance_node.node_key)

    # GATE — "released project summary" requires an actual release.
    deployment_artifact = (
        db.query(Artifact)
        .filter(Artifact.project_id == project.id, Artifact.artifact_type == "deployment_record", Artifact.status == ArtifactStatus.APPROVED)
        .order_by(Artifact.updated_at.desc())
        .first()
    )
    if deployment_artifact is None or deployment_artifact.current_version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot generate a maintenance report — no approved release (deployment_record) exists yet.")

    released_version = deployment_artifact.current_version
    released_summary = released_version.agent_context_summary or released_version.content_markdown

    # Best-effort context (requirement 3) — none of these block the run.
    open_bugs = _open_bugs(db, project.id)
    pr_history = _pr_history(db, project.id)
    test_summary = _test_summary(db, project.id)

    run = MaintenanceRun(
        project_id=project.id,
        workflow_node_id=maintenance_node.id,
        triggered_by_user_id=triggered_by.id,
        status=MaintenanceRunStatus.RUNNING,
        error_logs_input=payload.error_logs,
        user_feedback_input=payload.user_feedback,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()

    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="maintenance_run.started",
        entity_type="MaintenanceRun", entity_id=run.id,
        extra_data={"has_error_logs": payload.error_logs is not None, "has_user_feedback": payload.user_feedback is not None},
    )

    try:
        result = run_maintenance_agent(
            released_summary=released_summary, open_bugs=open_bugs, pr_history=pr_history,
            test_summary=test_summary, error_logs=payload.error_logs, user_feedback=payload.user_feedback,
        )
    except Exception as exc:  # noqa: BLE001 — anything unexpected fails this run cleanly, never a bare 500
        run.status = MaintenanceRunStatus.FAILED
        run.error_message = str(exc)
        run.completed_at = datetime.now(timezone.utc)
        record_audit_log(
            db, project_id=project.id, actor_user_id=triggered_by.id, action="maintenance_run.failed",
            entity_type="MaintenanceRun", entity_id=run.id, extra_data={"error": str(exc)},
        )
        db.commit()
        db.refresh(run)
        return MaintenanceRunRead.from_orm_run(run)

    run.report_markdown = result.content_markdown
    run.used_mock = result.used_mock
    run.token_usage = {
        "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens, "total_tokens": result.total_tokens,
    }
    run.cost = result.cost
    run.status = MaintenanceRunStatus.COMPLETED
    run.completed_at = datetime.now(timezone.utc)

    # Requirement 2 — a real maintenance_report Artifact, create-or-reuse
    # + always-append-a-new-version, exactly like TestRun's pattern (see
    # app/api/routes/test_runs.py) — but no Review (rule: nothing here
    # needs approval).
    artifact = (
        db.query(Artifact)
        .filter(Artifact.workflow_node_id == maintenance_node.id, Artifact.artifact_type == MAINTENANCE_REPORT_ARTIFACT_TYPE)
        .order_by(Artifact.created_at.desc())
        .first()
    )
    if artifact is None:
        artifact = Artifact(
            project_id=project.id, workflow_node=maintenance_node, artifact_type=MAINTENANCE_REPORT_ARTIFACT_TYPE,
            title="Maintenance Report", status=ArtifactStatus.DRAFT, created_by=triggered_by,
        )
        db.add(artifact)
        db.flush()
        record_audit_log(
            db, project_id=project.id, actor_user_id=triggered_by.id, action="artifact.created",
            entity_type="Artifact", entity_id=artifact.id,
            extra_data={"workflow_node": maintenance_node.node_key, "artifact_type": artifact.artifact_type},
        )

    last_version_number = (
        db.query(ArtifactVersion.version_number)
        .filter(ArtifactVersion.artifact_id == artifact.id)
        .order_by(ArtifactVersion.version_number.desc())
        .limit(1)
        .scalar()
    )
    next_version_number = (last_version_number or 0) + 1
    version = ArtifactVersion(
        artifact=artifact, version_number=next_version_number, content_markdown=result.content_markdown, created_by=triggered_by,
        change_summary="Generated by the Maintenance Agent.",
    )
    db.add(version)
    db.flush()

    artifact.current_version = version
    # Rule: recommend actions only — nothing here needs a human approval
    # gate, so this goes straight to APPROVED, never READY_FOR_REVIEW.
    artifact.status = ArtifactStatus.APPROVED
    run.artifact_id = artifact.id
    run.artifact_version_id = version.id

    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="maintenance_run.completed",
        entity_type="MaintenanceRun", entity_id=run.id,
        extra_data={"used_mock": result.used_mock, "open_bug_count": len(open_bugs), "pr_count": len(pr_history)},
    )
    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="artifact_version.created",
        entity_type="ArtifactVersion", entity_id=version.id,
        extra_data={"artifact_id": str(artifact.id), "version_number": next_version_number},
    )

    db.commit()
    db.refresh(run)
    return MaintenanceRunRead.from_orm_run(run)


@router.get("/{run_id}", response_model=MaintenanceRunRead)
def get_maintenance_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> MaintenanceRunRead:
    return MaintenanceRunRead.from_orm_run(_get_run_or_404(db, run_id))
