"""Testing Agent execution endpoints.

Covers: start a Testing Agent run for a task with a latest ACCEPTED
ImplementationRun (requirement 1/2's gate — a PR or an existing diff, not
the workflow JSON's stale `code_change` requirement — see
app/models/test_run.py's class docstring), and fetch one run. Branches for
a story-scoped task (see app/models/story_delivery_node.py's TESTING node)
exactly like app/api/routes/implementation_runs.py's own story branch —
project-level behavior is unchanged from before this feature.

QA GATE: for a project-level run, there is no accept/reject endpoint here
— completing a run opens a real Review against the `test_report` Artifact
it produces, through the existing generic mechanism
(app/api/routes/reviews.py). For a story-scoped run, QA approval is the
lane's own QA_APPROVAL node (role- and evidence-gated — see
app/api/routes/stories.py's update_lane_node_status). Nothing in this
module ever sets ArtifactStatus.APPROVED or ReviewStatus.APPROVED, or
completes a StoryDeliveryNode by itself.
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
    Story,
    StoryArtifact,
    StoryDeliveryNodeStatus,
    TestRun,
    TestRunStatus,
    User,
    WorkflowNode,
    WorkflowStatus,
)
from app.schemas.test_run import StartTestRunRequest, TestRunRead
from app.services.audit import record_audit_log
from app.services.graph_engine import GraphEngineService
from app.services.permissions import require_can_edit_stage
from app.services.retrieval import retrieve_relevant_chunks
from app.services.story_delivery import advance_lane
from app.services.story_export import Story as StoryDataclass
from app.services.story_lld_agent import STORY_LLD_ARTIFACT_TYPE
from app.services.testing_agent import (
    COVERAGE_IMPACT_PLACEHOLDER,
    STORY_TEST_REPORT_ARTIFACT_TYPE,
    is_test_path,
    render_test_report_markdown,
    run_testing_agent,
)

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


def _fetch_pattern_chunks_virtual(db: Session, project: Project, task: ImplementationTask):
    """Story-scoped equivalent of _fetch_pattern_chunks — no real
    WorkflowNode exists for a StoryDeliveryNode, so a transient,
    never-persisted one stands in (see app/services/story_lld_agent.py's
    module docstring for the same reuse pattern)."""
    virtual_node = WorkflowNode(
        project_id=project.id, node_key="testing", name="Testing", description="Testing pattern retrieval for a story-scoped run.",
        agent_key="testing-agent", required_inputs=[], output_artifact_type="story_test_report",
        requires_human_approval=True, allowed_actions=["draft"], status=WorkflowStatus.READY,
        order_index=0, position_x=0, position_y=0,
    )
    return _fetch_pattern_chunks(db, project, virtual_node, task)


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

    # Story-level testing workflow, requirement 1 — a story-scoped task
    # belongs to that story's own delivery lane, not the project-level
    # WorkflowNode graph. Branch once, here — project-level behavior below
    # this block is unchanged from before this feature.
    testing_node: WorkflowNode | None = None
    lane = None
    story_row: Story | None = None
    if task.story_id is not None:
        story_row = db.get(Story, task.story_id)
        if story_row is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Task {task.id}'s story no longer exists.")
        lane = story_row.delivery_lane
        if lane is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Story {story_row.id} has no delivery lane.")
        testing_lane_node = next((n for n in lane.nodes if n.node_key == "TESTING"), None)
        if testing_lane_node is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Lane {lane.id} is missing its TESTING node.")
        if testing_lane_node.status == StoryDeliveryNodeStatus.LOCKED:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Node {testing_lane_node.id} is LOCKED.")
        require_can_edit_stage(triggered_by, "testing")
    else:
        testing_node = (
            db.query(WorkflowNode)
            .filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == "testing", WorkflowNode.story_id.is_(None))
            .first()
        )
        if testing_node is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Project {project.id}'s workflow has no testing stage.")
        require_can_edit_stage(triggered_by, testing_node.node_key)

    reviewer = None
    if task.story_id is None:
        reviewer = db.get(User, payload.reviewer_id) if payload.reviewer_id else None
        if reviewer is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "reviewer_id is required and must match an existing user")

    # Requirement 2's gate — "implementation exists, or PR exists". A PR
    # always originates from an ImplementationRun in this codebase (see
    # app/api/routes/implementation_runs.py's create_pull_request), so
    # "an ImplementationRun exists for this task" already covers both
    # halves of that OR. Kept as an ACCEPTED lookup, same as the existing
    # project-level gate, for both modes — a run a human hasn't accepted
    # yet isn't "implementation" in the sense either rule means.
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

    # Best-effort context (requirement 4) — the approved LLD (project- or
    # story-scoped), existing test file paths, and RAG testing-pattern
    # chunks. None of these block a run.
    story_dataclass: StoryDataclass | None = None
    if story_row is not None:
        lld_content = ""
        story_lld_artifact = (
            db.query(StoryArtifact)
            .filter(StoryArtifact.story_id == story_row.id, StoryArtifact.artifact_type == STORY_LLD_ARTIFACT_TYPE)
            .order_by(StoryArtifact.version_number.desc())
            .first()
        )
        if story_lld_artifact is not None:
            lld_content = story_lld_artifact.content_markdown
        lld_summary = lld_content
        story_dataclass = StoryDataclass(
            title=story_row.title, epic=story_row.epic, feature=story_row.feature, user_story=story_row.user_story,
            priority=story_row.priority, dependencies=story_row.dependencies,
            acceptance_criteria=story_row.acceptance_criteria, definition_of_done=story_row.definition_of_done,
        )
    else:
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
    pattern_chunks = _fetch_pattern_chunks(db, project, testing_node, task) if testing_node is not None else _fetch_pattern_chunks_virtual(db, project, task)

    run = TestRun(
        project_id=project.id,
        workflow_node_id=testing_node.id if testing_node is not None else None,
        implementation_task_id=task.id,
        implementation_run_id=implementation_run.id,
        pull_request_link_id=pr_link.id if pr_link else None,
        story_id=task.story_id,
        lane_id=lane.id if lane is not None else None,
        triggered_by_user_id=triggered_by.id,
        agent_type=payload.agent_type,
        test_agent_key=payload.agent_type.value,
        status=TestRunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()

    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="test_run.started",
        entity_type="TestRun", entity_id=run.id,
        extra_data={
            "implementation_task_id": str(task.id), "agent_type": payload.agent_type.value,
            "story_id": str(task.story_id) if task.story_id else None, "lane_id": str(lane.id) if lane else None,
        },
    )

    try:
        result = run_testing_agent(
            task=task, agent_type=payload.agent_type, diff_text=implementation_run.diff_text, lld_summary=lld_summary,
            existing_test_paths=existing_test_paths, pattern_chunks=pattern_chunks, story=story_dataclass,
        )
        result.evidence_attachments = payload.evidence_attachments
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
    run.evidence_attachments = result.evidence_attachments

    report_markdown = render_test_report_markdown(result, task=task, agent_type=payload.agent_type)
    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="test_run.completed",
        entity_type="TestRun", entity_id=run.id,
        extra_data={
            "implementation_task_id": str(task.id), "used_mock": result.used_mock,
            "pass_count": result.pass_count, "fail_count": result.fail_count, "bug_count": len(result.bugs_found),
        },
    )

    if story_row is not None:
        # Story-level testing workflow, requirements 6 + 7 — a
        # story_test_report StoryArtifact, not a project-level
        # Artifact/Review. TESTING completes directly (mirrors
        # STORY_LLD's own auto-complete in app/services/story_lld_agent.py)
        # and unlocks QA_APPROVAL — that node's own role+evidence gate
        # (app/api/routes/stories.py's update_lane_node_status) is this
        # workflow's real QA approval, not a generic Review.
        testing_lane_node = next(n for n in lane.nodes if n.node_key == "TESTING")
        last_version_number = (
            db.query(StoryArtifact)
            .filter(StoryArtifact.story_id == story_row.id, StoryArtifact.artifact_type == STORY_TEST_REPORT_ARTIFACT_TYPE)
            .order_by(StoryArtifact.version_number.desc())
            .first()
        )
        next_version_number = (last_version_number.version_number if last_version_number else 0) + 1
        story_artifact = StoryArtifact(
            story_id=story_row.id, lane_id=lane.id, node_id=testing_lane_node.id,
            artifact_type=STORY_TEST_REPORT_ARTIFACT_TYPE, title=f"Test Report — {story_row.title}",
            content_markdown=report_markdown, version_number=next_version_number, created_by_id=triggered_by.id,
        )
        db.add(story_artifact)
        db.flush()
        run.story_artifact_id = story_artifact.id

        testing_lane_node.status = StoryDeliveryNodeStatus.COMPLETED
        testing_lane_node.completed_at = datetime.now(timezone.utc)
        db.flush()
        advance_lane(db, lane=lane, completed_node=testing_lane_node)

        record_audit_log(
            db, project_id=project.id, actor_user_id=triggered_by.id, action="story_test_report.created",
            entity_type="StoryArtifact", entity_id=story_artifact.id,
            extra_data={"story_id": str(story_row.id), "version_number": next_version_number},
        )
    else:
        # Requirements 5 + 6 — a real test_report Artifact, opened for QA
        # review through the existing generic mechanism (see module docstring).
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
    return TestRunRead.from_orm_run(run, review_id=get_review_id_for_test_run(db, run))


@router.get("/{run_id}", response_model=TestRunRead)
def get_test_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> TestRunRead:
    run = _get_run_or_404(db, run_id)
    return TestRunRead.from_orm_run(run, review_id=get_review_id_for_test_run(db, run))
