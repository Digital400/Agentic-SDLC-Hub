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
    PRReviewRecommendation,
    PRReviewRun,
    PRReviewRunStatus,
    Project,
    PullRequestLink,
    Story,
    StoryArtifact,
    StoryDeliveryNode,
    StoryDeliveryNodeStatus,
    TestRun,
    TestRunStatus,
    User,
    WorkflowNode,
    WorkflowStatus,
)
from app.schemas.pr_review_run import (
    PostedCommentResultRead,
    PostPRReviewCommentsRequest,
    PostPRReviewCommentsResponse,
    PRReviewRunRead,
    SendBackForReworkRequest,
    StartPRReviewRunRequest,
)
from app.services.audit import record_audit_log
from app.services import github_integration as github_api
from app.services.github_integration import GitHubIntegrationError
from app.services.permissions import require_can_edit_stage
from app.services.pr_review_agent import run_pr_review_agent
from app.services.retrieval import retrieve_relevant_chunks
from app.services.story_delivery import StoryDeliveryError, advance_lane, send_lane_back_for_rework
from app.services.story_export import find_related_story
from app.services.story_lld_agent import STORY_LLD_ARTIFACT_TYPE
from app.services.story_implementation_plan_agent import STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE
from app.services.story_test_scenarios_agent import STORY_TEST_SCENARIOS_ARTIFACT_TYPE

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


def _virtual_pr_review_node(project: Project) -> WorkflowNode:
    """HARDENING FIX — a story-scoped task has no WorkflowNode row for the
    pr_review stage (lanes are materialized entirely in StoryDeliveryNode;
    see app/services/story_delivery.py), so the previous unconditional
    `WorkflowNode.query(...).filter(story_id == task.story_id)` lookup
    always returned None for one, 400ing every story-level PR review
    before it could ever run. A transient, never-persisted WorkflowNode
    stands in for _fetch_standards_chunks' `node` parameter instead — same
    reuse pattern as app/api/routes/implementation_runs.py's own
    _fetch_standards_chunks and app/services/story_lld_agent.py's module
    docstring."""
    return WorkflowNode(
        project_id=project.id, node_key="pr_review", name="PR Review",
        description="Coding standards retrieval for a PR review run.",
        agent_key="pr-review-agent", required_inputs=[], output_artifact_type="pr_review_report",
        requires_human_approval=False, allowed_actions=["draft"], status=WorkflowStatus.READY,
        order_index=0, position_x=0, position_y=0,
    )


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

    # HARDENING FIX — see _virtual_pr_review_node's docstring: a
    # story-scoped task has no real WorkflowNode row for this stage, so
    # this lookup is skipped entirely for one (only the project-level
    # path still requires and looks up a real node).
    pr_review_node: WorkflowNode | None = None
    if task.story_id is None:
        pr_review_node = (
            db.query(WorkflowNode)
            .filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == "pr_review", WorkflowNode.story_id.is_(None))
            .first()
        )
        if pr_review_node is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Project {project.id}'s workflow has no pr_review stage.")
    require_can_edit_stage(triggered_by, "pr_review")
    standards_lookup_node = pr_review_node if pr_review_node is not None else _virtual_pr_review_node(project)

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

    # Preconditions 3/4 — "Story LLD exists" (hard) / "test scenarios if
    # available" (soft). For a story-scoped task the LLD/plan/scenarios
    # live as StoryArtifacts on the story's own delivery lane, not the
    # project-level lld_document Artifact — mirror that scoping here.
    implementation_plan_summary, test_scenarios_summary = "", ""
    if task.story_id is not None:
        story_lld_artifact = (
            db.query(StoryArtifact)
            .filter(StoryArtifact.story_id == task.story_id, StoryArtifact.artifact_type == STORY_LLD_ARTIFACT_TYPE)
            .order_by(StoryArtifact.version_number.desc())
            .first()
        )
        if story_lld_artifact is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Cannot start a PR review — this story has no approved Story LLD yet."
            )
        lld_summary = story_lld_artifact.content_markdown

        plan_artifact = (
            db.query(StoryArtifact)
            .filter(StoryArtifact.story_id == task.story_id, StoryArtifact.artifact_type == STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE)
            .order_by(StoryArtifact.version_number.desc())
            .first()
        )
        if plan_artifact is not None:
            implementation_plan_summary = plan_artifact.content_markdown

        scenarios_artifact = (
            db.query(StoryArtifact)
            .filter(StoryArtifact.story_id == task.story_id, StoryArtifact.artifact_type == STORY_TEST_SCENARIOS_ARTIFACT_TYPE)
            .order_by(StoryArtifact.version_number.desc())
            .first()
        )
        if scenarios_artifact is not None:
            test_scenarios_summary = scenarios_artifact.content_markdown
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

    jira_issue_key = pr_link.jira_issue_key or ""

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

    standards_chunks = _fetch_standards_chunks(db, project, standards_lookup_node, task)
    test_summary, test_fail_count, test_bugs_found = _latest_test_summary(db, task.id)

    run = PRReviewRun(
        project_id=project.id,
        workflow_node_id=pr_review_node.id if pr_review_node is not None else None,
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
            jira_issue_key=jira_issue_key, implementation_plan_summary=implementation_plan_summary,
            test_scenarios_summary=test_scenarios_summary,
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
    run.unrelated_changes = result.unrelated_changes
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

    # Story lane wiring — PR_REVIEW_AGENT is a plain (non-approval) node,
    # so the agent actually running it IS its completion, same
    # "drafting-completes-its-own-node" pattern STORY_LLD uses (unlike
    # IMPLEMENTATION_PLAN/TEST_SCENARIOS, whose review is a separate human
    # action). This unlocks HUMAN_CODE_REVIEW next.
    if task.story_id is not None:
        lane_node = (
            db.query(StoryDeliveryNode)
            .join(StoryDeliveryNode.lane)
            .filter(StoryDeliveryNode.node_key == "PR_REVIEW_AGENT", StoryDeliveryNode.lane.has(story_id=task.story_id))
            .first()
        )
        if lane_node is not None and lane_node.status != StoryDeliveryNodeStatus.COMPLETED:
            lane_node.status = StoryDeliveryNodeStatus.COMPLETED
            lane_node.completed_at = datetime.now(timezone.utc)
            db.flush()
            advance_lane(db, lane=lane_node.lane, completed_node=lane_node)

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


@router.post("/{run_id}/send-back-for-rework", response_model=PRReviewRunRead)
def send_pr_review_back_for_rework(
    run_id: uuid.UUID, payload: SendBackForReworkRequest, db: Session = Depends(get_db)
) -> PRReviewRunRead:
    """Rule — "if recommendation is REQUEST_CHANGES, lane returns to Code
    Implementation or Implementation Plan update." Story-scoped only: a
    project-level PR review has no lane to send back. See
    app/services/story_delivery.py's send_lane_back_for_rework for what
    "returns to" actually does to the lane's nodes."""
    run = _get_run_or_404(db, run_id)
    if run.overall_recommendation != PRReviewRecommendation.REQUEST_CHANGES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Run {run_id}'s recommendation is {run.overall_recommendation}, not REQUEST_CHANGES — nothing to send back.",
        )
    if run.story_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This PR review run has no story lane to send back.")

    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")
    require_can_edit_stage(triggered_by, "pr_review")

    story = db.get(Story, run.story_id)
    if story is None or story.delivery_lane is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Story {run.story_id} has no delivery lane.")

    try:
        target_node = send_lane_back_for_rework(
            db, lane=story.delivery_lane, target_node_key=payload.target_node_key, actor=triggered_by
        )
    except StoryDeliveryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    record_audit_log(
        db, project_id=run.project_id, actor_user_id=triggered_by.id, action="pr_review_run.sent_back_for_rework",
        entity_type="PRReviewRun", entity_id=run.id,
        extra_data={"target_node_key": target_node.node_key, "lane_id": str(story.delivery_lane.id)},
    )

    db.commit()
    db.refresh(run)
    return PRReviewRunRead.from_orm_run(run)
