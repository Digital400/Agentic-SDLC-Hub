"""Testing Agent execution endpoints.

Covers: start a Testing Agent run for a task with a latest ACCEPTED
ImplementationRun (requirement 1's gate — a PR or an existing diff, not
the workflow JSON's stale `code_change` requirement — see
app/models/test_run.py's class docstring), and fetch one run.

QA GATE: there is no accept/reject endpoint here. Completing a run opens
a real Review against the `test_report` Artifact it produces, through the
existing generic mechanism (app/api/routes/reviews.py) — the same one
every other stage's artifact already uses. Nothing in this module ever
sets ArtifactStatus.APPROVED or ReviewStatus.APPROVED.
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
    ImplementationRun,
    ImplementationRunReviewStatus,
    ImplementationTask,
    KnowledgeContentType,
    Project,
    PullRequestLink,
    Review,
    ReviewStatus,
    RepositoryFileEntryType,
    RepositoryFileIndex,
    TestRun,
    TestRunStatus,
    User,
    WorkflowNode,
)
from app.schemas.test_run import StartTestRunRequest, TestRunRead
from app.services.audit import record_audit_log
from app.services.graph_engine import GraphEngineService
from app.services.permissions import require_can_edit_stage
from app.services.retrieval import retrieve_relevant_chunks
from app.services.testing_agent import COVERAGE_IMPACT_PLACEHOLDER, is_test_path, render_test_report_markdown, run_testing_agent

router = APIRouter(prefix="/test-runs", tags=["test-runs"])

_TESTING_PATTERN_CONTENT_TYPES = [KnowledgeContentType.TESTING_STANDARD, KnowledgeContentType.PAST_ARTIFACT]
_MAX_EXISTING_TEST_PATHS = 15


def _get_run_or_404(db: Session, run_id: uuid.UUID) -> TestRun:
    run = db.get(TestRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Test run {run_id} not found")
    return run


def get_review_id_for_test_run(db: Session, run: TestRun) -> uuid.UUID | None:
    if run.artifact_version_id is None:
        return None
    review = (
        db.query(Review)
        .filter(Review.artifact_version_id == run.artifact_version_id)
        .order_by(Review.created_at.desc())
        .first()
    )
    return review.id if review else None


def _existing_test_paths(db: Session, snapshot_id: uuid.UUID | None) -> list[str]:
    if snapshot_id is None:
        return []
    rows = (
        db.query(RepositoryFileIndex)
        .filter(RepositoryFileIndex.snapshot_id == snapshot_id, RepositoryFileIndex.entry_type == RepositoryFileEntryType.FILE)
        .all()
    )
    return [r.path for r in rows if is_test_path(r.path)][:_MAX_EXISTING_TEST_PATHS]


def _fetch_pattern_chunks(db: Session, project: Project, node: WorkflowNode, task: ImplementationTask):
    try:
        return retrieve_relevant_chunks(
            db, project=project, node=node, freeform_context={"query": f"{task.title} {task.description}"},
            approved_inputs={}, content_types=_TESTING_PATTERN_CONTENT_TYPES, top_k=5,
        )
    except Exception:  # noqa: BLE001 — RAG input is additive, never blocks a run
        return []


@router.post("", response_model=TestRunRead, status_code=status.HTTP_201_CREATED)
def start_test_run(payload: StartTestRunRequest, db: Session = Depends(get_db)) -> TestRunRead:
    task = db.get(ImplementationTask, payload.implementation_task_id)
    if task is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"implementation_task_id {payload.implementation_task_id} does not match an existing implementation task",
        )
    project = db.get(Project, task.project_id)
    if project is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Task {task.id}'s project no longer exists.")

    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")
    reviewer = db.get(User, payload.reviewer_id)
    if reviewer is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"reviewer_id {payload.reviewer_id} does not match an existing user")

    testing_node = (
        db.query(WorkflowNode)
        .filter(
            WorkflowNode.project_id == project.id,
            WorkflowNode.node_key == "testing",
            WorkflowNode.story_id == task.story_id,
        )
        .first()
    )
    if testing_node is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Project {project.id}'s workflow has no testing stage.")
    require_can_edit_stage(triggered_by, testing_node.node_key)

    # Requirement 1's gate — see app/models/test_run.py's class docstring
    # for why this replaces GraphEngineService.validate_can_run here.
    implementation_run = (
        db.query(ImplementationRun)
        .filter(
            ImplementationRun.implementation_task_id == task.id,
            ImplementationRun.review_status == ImplementationRunReviewStatus.ACCEPTED,
        )
        .order_by(ImplementationRun.completed_at.desc())
        .first()
    )
    if implementation_run is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot start testing — accept an implementation run for this task first.")

    pr_link = (
        db.query(PullRequestLink).filter(PullRequestLink.implementation_run_id == implementation_run.id).first()
    )
    if pr_link is None and not implementation_run.diff_text.strip():
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot start testing — a created PR or an existing code diff is required.")

    # Best-effort context (requirement 2) — an approved LLD, existing test
    # file paths, and RAG testing-pattern chunks. None of these block a run.
    lld_artifact = (
        db.query(Artifact)
        .filter(Artifact.project_id == project.id, Artifact.artifact_type == "lld_document", Artifact.status == ArtifactStatus.APPROVED)
        .order_by(Artifact.updated_at.desc())
        .first()
    )
    lld_summary = ""
    if lld_artifact is not None and lld_artifact.current_version is not None:
        version = lld_artifact.current_version
        lld_summary = version.agent_context_summary or version.content_markdown
    existing_test_paths = _existing_test_paths(db, implementation_run.repository_snapshot_id)
    pattern_chunks = _fetch_pattern_chunks(db, project, testing_node, task)

    run = TestRun(
        project_id=project.id,
        workflow_node_id=testing_node.id,
        implementation_task_id=task.id,
        implementation_run_id=implementation_run.id,
        pull_request_link_id=pr_link.id if pr_link else None,
        story_id=task.story_id,
        triggered_by_user_id=triggered_by.id,
        agent_type=payload.agent_type,
        status=TestRunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()

    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="test_run.started",
        entity_type="TestRun", entity_id=run.id,
        extra_data={"implementation_task_id": str(task.id), "agent_type": payload.agent_type.value},
    )

    try:
        result = run_testing_agent(
            task=task, agent_type=payload.agent_type, diff_text=implementation_run.diff_text, lld_summary=lld_summary,
            existing_test_paths=existing_test_paths, pattern_chunks=pattern_chunks,
        )
    except Exception as exc:  # noqa: BLE001 — anything unexpected fails this run cleanly, never a bare 500
        run.status = TestRunStatus.FAILED
        run.error_message = str(exc)
        run.completed_at = datetime.now(timezone.utc)
        record_audit_log(
            db, project_id=project.id, actor_user_id=triggered_by.id, action="test_run.failed",
            entity_type="TestRun", entity_id=run.id, extra_data={"error": str(exc)},
        )
        db.commit()
        db.refresh(run)
        return TestRunRead.from_orm_run(run)

    run.test_plan = result.test_plan
    run.tests_to_add = [{"name": t.name, "description": t.description, "area": t.area} for t in result.tests_to_add]
    run.tests_executed = [{"name": t.name, "result": t.result, "notes": t.notes} for t in result.tests_executed]
    run.pass_count = result.pass_count
    run.fail_count = result.fail_count
    run.bugs_found = result.bugs_found
    run.suggested_fixes = result.suggested_fixes
    run.coverage_impact = dict(COVERAGE_IMPACT_PLACEHOLDER)
    run.used_mock = result.used_mock
    run.token_usage = {
        "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens, "total_tokens": result.total_tokens,
    }
    run.cost = result.cost
    run.status = TestRunStatus.COMPLETED
    run.completed_at = datetime.now(timezone.utc)

    # Requirements 5 + 6 — a real test_report Artifact, opened for QA
    # review through the existing generic mechanism (see module docstring).
    report_markdown = render_test_report_markdown(result, task=task, agent_type=payload.agent_type)
    artifact = (
        db.query(Artifact)
        .filter(Artifact.workflow_node_id == testing_node.id, Artifact.artifact_type == testing_node.output_artifact_type)
        .order_by(Artifact.created_at.desc())
        .first()
    )
    if artifact is None:
        artifact = Artifact(
            project_id=project.id, workflow_node=testing_node, artifact_type=testing_node.output_artifact_type,
            title=testing_node.name, status=ArtifactStatus.DRAFT, created_by=triggered_by,
        )
        db.add(artifact)
        db.flush()
        record_audit_log(
            db, project_id=project.id, actor_user_id=triggered_by.id, action="artifact.created",
            entity_type="Artifact", entity_id=artifact.id,
            extra_data={"workflow_node": testing_node.node_key, "artifact_type": artifact.artifact_type},
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
        artifact=artifact, version_number=next_version_number, content_markdown=report_markdown, created_by=triggered_by,
        change_summary=f"Generated by the {payload.agent_type.value} Test Agent.",
    )
    db.add(version)
    db.flush()

    artifact.current_version = version
    artifact.status = ArtifactStatus.READY_FOR_REVIEW
    run.artifact_id = artifact.id
    run.artifact_version_id = version.id

    graph_engine = GraphEngineService(db)
    graph_engine.mark_waiting_for_review(testing_node)

    review = Review(artifact_version=version, workflow_node=testing_node, reviewer_id=reviewer.id, status=ReviewStatus.PENDING)
    db.add(review)
    db.flush()

    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="test_run.completed",
        entity_type="TestRun", entity_id=run.id,
        extra_data={
            "implementation_task_id": str(task.id), "used_mock": result.used_mock,
            "pass_count": result.pass_count, "fail_count": result.fail_count, "bug_count": len(result.bugs_found),
        },
    )
    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="artifact_version.created",
        entity_type="ArtifactVersion", entity_id=version.id,
        extra_data={"artifact_id": str(artifact.id), "version_number": next_version_number},
    )
    record_audit_log(
        db, project_id=project.id, actor_user_id=reviewer.id, action="review.created",
        entity_type="Review", entity_id=review.id,
        extra_data={"artifact_id": str(artifact.id), "artifact_version_id": str(version.id)},
    )

    db.commit()
    db.refresh(run)
    return TestRunRead.from_orm_run(run, review_id=review.id)


@router.get("/{run_id}", response_model=TestRunRead)
def get_test_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> TestRunRead:
    run = _get_run_or_404(db, run_id)
    return TestRunRead.from_orm_run(run, review_id=get_review_id_for_test_run(db, run))
