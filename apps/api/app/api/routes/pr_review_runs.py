"""PR Review Agent execution endpoints.

Covers: start a PR Review Agent run for a task with a latest ACCEPTED
ImplementationRun that already has a created PR (requirement 1's gate —
stricter than Testing's "PR or diff" either/or, since this feature
reviews the PR itself), fetch one run, and post a human-selected/edited
subset of its suggested comments to the real GitHub PR.

SCOPE: fully bespoke — no Artifact/Review rows exist for this feature.
"Human reviewer decides final approval" is the real GitHub PR review,
external to this app. No merge call exists anywhere in
app/services/github_integration.py, by construction — "must not merge"
is enforced structurally, not by a runtime check here.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.routes.github_integration import decrypt_repository_token
from app.core.database import get_db
from app.models import (
    Artifact,
    ArtifactStatus,
    ImplementationRun,
    ImplementationRunReviewStatus,
    ImplementationTask,
    KnowledgeContentType,
    PRReviewRun,
    PRReviewRunStatus,
    Project,
    PullRequestLink,
    TestRun,
    TestRunStatus,
    User,
    WorkflowNode,
)
from app.schemas.pr_review_run import (
    PostedCommentResultRead,
    PostPRReviewCommentsRequest,
    PostPRReviewCommentsResponse,
    PRReviewRunRead,
    StartPRReviewRunRequest,
)
from app.services.audit import record_audit_log
from app.services import github_integration as github_api
from app.services.github_integration import GitHubIntegrationError
from app.services.permissions import require_can_edit_stage
from app.services.pr_review_agent import run_pr_review_agent
from app.services.retrieval import retrieve_relevant_chunks
from app.services.story_export import find_related_story

router = APIRouter(prefix="/pr-review-runs", tags=["pr-review-runs"])

_STANDARDS_CONTENT_TYPES = [KnowledgeContentType.COMPANY_STANDARD, KnowledgeContentType.ARCHITECTURE_RULE]


def _get_run_or_404(db: Session, run_id: uuid.UUID) -> PRReviewRun:
    run = db.get(PRReviewRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"PR review run {run_id} not found")
    return run


def _fetch_standards_chunks(db: Session, project: Project, node: WorkflowNode, task: ImplementationTask):
    try:
        return retrieve_relevant_chunks(
            db, project=project, node=node, freeform_context={"query": f"{task.title} {task.description}"},
            approved_inputs={}, content_types=_STANDARDS_CONTENT_TYPES, top_k=5,
        )
    except Exception:  # noqa: BLE001 — RAG input is additive, never blocks a run
        return []


def _latest_test_summary(db: Session, task_id: uuid.UUID) -> tuple[str, int | None, list[str]]:
    """Best-effort — a task may not have been tested yet (requirement 2's
    "test results if available")."""
    test_run = (
        db.query(TestRun)
        .filter(TestRun.implementation_task_id == task_id, TestRun.status == TestRunStatus.COMPLETED)
        .order_by(TestRun.completed_at.desc())
        .first()
    )
    if test_run is None:
        return "", None, []
    summary = f"Pass: {test_run.pass_count} · Fail: {test_run.fail_count}. Bugs found: {'; '.join(test_run.bugs_found) or 'none'}."
    return summary, test_run.fail_count, test_run.bugs_found


@router.post("", response_model=PRReviewRunRead, status_code=status.HTTP_201_CREATED)
def start_pr_review_run(payload: StartPRReviewRunRequest, db: Session = Depends(get_db)) -> PRReviewRunRead:
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

    pr_review_node = (
        db.query(WorkflowNode)
        .filter(
            WorkflowNode.project_id == project.id,
            WorkflowNode.node_key == "pr_review",
            WorkflowNode.story_id == task.story_id,
        )
        .first()
    )
    if pr_review_node is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Project {project.id}'s workflow has no pr_review stage.")
    require_can_edit_stage(triggered_by, pr_review_node.node_key)

    # Requirement 1's gate — an accepted run AND a created PR (stricter
    # than Testing's either/or) AND a non-empty diff.
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
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot start a PR review — accept an implementation run for this task first.")

    pr_link = db.query(PullRequestLink).filter(PullRequestLink.implementation_run_id == implementation_run.id).first()
    if pr_link is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot start a PR review — create a pull request for this run first.")
    if not implementation_run.diff_text.strip():
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot start a PR review — this run has no diff.")

    # Best-effort context (requirement 2) — none of this blocks a run.
    repository = pr_link.repository
    pr_title, pr_body = "", ""
    try:
        token = decrypt_repository_token(repository)
        pr_detail = github_api.get_pull_request(token, repository.owner, repository.name, pr_link.pr_number)
        pr_title, pr_body = pr_detail.title, pr_detail.body
    except (HTTPException, GitHubIntegrationError):
        pass  # a read degrading to "" is fine; the diff/task fields still carry the review

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

    story_backlog_artifact = (
        db.query(Artifact)
        .filter(Artifact.project_id == project.id, Artifact.artifact_type == "story_backlog", Artifact.status == ArtifactStatus.APPROVED)
        .order_by(Artifact.updated_at.desc())
        .first()
    )
    story_backlog_content = ""
    if story_backlog_artifact is not None and story_backlog_artifact.current_version is not None:
        story_backlog_content = story_backlog_artifact.current_version.content_markdown
    story = find_related_story(story_backlog_content, task.linked_story)

    standards_chunks = _fetch_standards_chunks(db, project, pr_review_node, task)
    test_summary, test_fail_count, test_bugs_found = _latest_test_summary(db, task.id)

    run = PRReviewRun(
        project_id=project.id,
        workflow_node_id=pr_review_node.id,
        implementation_task_id=task.id,
        implementation_run_id=implementation_run.id,
        pull_request_link_id=pr_link.id,
        story_id=task.story_id,
        triggered_by_user_id=triggered_by.id,
        status=PRReviewRunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()

    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="pr_review_run.started",
        entity_type="PRReviewRun", entity_id=run.id,
        extra_data={"implementation_task_id": str(task.id), "pr_number": pr_link.pr_number},
    )

    try:
        result = run_pr_review_agent(
            task=task, pr_title=pr_title, pr_body=pr_body, diff_text=implementation_run.diff_text,
            lld_summary=lld_summary, story=story, standards_chunks=standards_chunks,
            test_summary=test_summary, test_fail_count=test_fail_count, test_bugs_found=test_bugs_found,
        )
    except Exception as exc:  # noqa: BLE001 — anything unexpected fails this run cleanly, never a bare 500
        run.status = PRReviewRunStatus.FAILED
        run.error_message = str(exc)
        run.completed_at = datetime.now(timezone.utc)
        record_audit_log(
            db, project_id=project.id, actor_user_id=triggered_by.id, action="pr_review_run.failed",
            entity_type="PRReviewRun", entity_id=run.id, extra_data={"error": str(exc)},
        )
        db.commit()
        db.refresh(run)
        return PRReviewRunRead.from_orm_run(run)

    run.overall_recommendation = result.overall_recommendation
    run.summary = result.summary
    run.critical_findings = [{"file": f.file, "detail": f.detail} for f in result.critical_findings]
    run.major_findings = [{"file": f.file, "detail": f.detail} for f in result.major_findings]
    run.minor_findings = [{"file": f.file, "detail": f.detail} for f in result.minor_findings]
    run.missing_tests = result.missing_tests
    run.suggested_comments = [{"file": c.file, "body": c.body} for c in result.suggested_comments]
    run.risk_score = result.risk_score
    run.final_reviewer_note = result.final_reviewer_note
    run.used_mock = result.used_mock
    run.token_usage = {
        "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens, "total_tokens": result.total_tokens,
    }
    run.cost = result.cost
    run.status = PRReviewRunStatus.COMPLETED
    run.completed_at = datetime.now(timezone.utc)

    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="pr_review_run.completed",
        entity_type="PRReviewRun", entity_id=run.id,
        extra_data={
            "implementation_task_id": str(task.id), "used_mock": result.used_mock,
            "overall_recommendation": result.overall_recommendation, "risk_score": result.risk_score,
            "critical_count": len(result.critical_findings), "major_count": len(result.major_findings),
        },
    )

    db.commit()
    db.refresh(run)
    return PRReviewRunRead.from_orm_run(run)


@router.get("/{run_id}", response_model=PRReviewRunRead)
def get_pr_review_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> PRReviewRunRead:
    return PRReviewRunRead.from_orm_run(_get_run_or_404(db, run_id))


@router.post("/{run_id}/post-comments", response_model=PostPRReviewCommentsResponse)
def post_pr_review_comments(
    run_id: uuid.UUID, payload: PostPRReviewCommentsRequest, db: Session = Depends(get_db)
) -> PostPRReviewCommentsResponse:
    run = _get_run_or_404(db, run_id)
    if run.status != PRReviewRunStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Run {run_id} is {run.status.value}, not COMPLETED — nothing to post yet.")

    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")
    require_can_edit_stage(triggered_by, "pr_review")

    pr_link = run.pull_request_link
    repository = pr_link.repository
    # A real write cannot silently degrade to "no token" — same policy
    # app/api/routes/implementation_runs.py's create_pull_request already established.
    try:
        token = decrypt_repository_token(repository)
    except HTTPException as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot post comments — " + str(exc.detail)) from exc

    results: list[PostedCommentResultRead] = []
    newly_posted: list[dict] = []
    for item in payload.comments:
        try:
            comment = github_api.create_issue_comment(token, repository.owner, repository.name, pr_link.pr_number, body=item.body)
        except GitHubIntegrationError as exc:
            results.append(PostedCommentResultRead(file=item.file, body=item.body, status="failed", error=str(exc)))
            continue
        posted_at = datetime.now(timezone.utc).isoformat()
        newly_posted.append(
            {"file": item.file, "body": item.body, "github_comment_id": comment.id, "github_comment_url": comment.html_url, "posted_at": posted_at}
        )
        results.append(
            PostedCommentResultRead(file=item.file, body=item.body, status="posted", github_comment_id=comment.id, github_comment_url=comment.html_url)
        )

    run.posted_comments = [*run.posted_comments, *newly_posted]

    # SECURITY: never the token, never full comment bodies — just counts
    # and file names, matching this session's audit-content discipline.
    record_audit_log(
        db, project_id=run.project_id, actor_user_id=triggered_by.id, action="pr_review_run.comments_posted",
        entity_type="PRReviewRun", entity_id=run.id,
        extra_data={
            "pr_number": pr_link.pr_number, "posted_count": len(newly_posted), "failed_count": len(results) - len(newly_posted),
            "files": [c["file"] for c in newly_posted],
        },
    )

    db.commit()
    db.refresh(run)
    return PostPRReviewCommentsResponse(run=PRReviewRunRead.from_orm_run(run), results=results)
