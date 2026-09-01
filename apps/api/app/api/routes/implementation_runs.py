"""Implementation Agent execution endpoints.

Covers: start a run for one approved ImplementationTask (requirement 1's
three gates enforced here — LLD approved, Implementation Plan approved,
GitHub repo snapshot exists), fetch one run, record a human's
accept/reject review decision on a completed run's proposed diff, and —
only once ACCEPTED — create a real GitHub pull request from it.

HARD RULE: generating a run and Accepting/Rejecting it never itself writes
to GitHub in any way. A real write only happens through
`create_pull_request` below, which is only reachable once
`review_status == ACCEPTED`, and even then only ever creates a fresh
feature branch and commits/PRs against that — never this repository's
default branch (see app/services/github_integration.py's write methods,
none of which is ever called here with anything else).
"""

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.routes.github_integration import decrypt_repository_token
from app.core.database import get_db
from app.models import (
    ImplementationRun,
    ImplementationRunReviewStatus,
    ImplementationRunStatus,
    ImplementationTask,
    ImplementationTaskStatus,
    Project,
    PullRequestLink,
    Repository,
    RepositorySnapshot,
    User,
    WorkflowNode,
)
from app.schemas.implementation_run import (
    CreatePullRequestRequest,
    ImplementationRunRead,
    ReviewImplementationRunRequest,
    StartImplementationRunRequest,
)
from app.services.audit import record_audit_log
from app.services import github_integration as github_api
from app.services.github_integration import GitHubIntegrationError
from app.services.graph_engine import GraphEngineService
from app.services.implementation_agent import SUPPORTED_AREAS, run_implementation_agent
from app.services.permissions import require_can_edit_stage
from app.services.repo_context_builder import RepoContextBuilderService
from app.services.story_export import find_related_story

router = APIRouter(prefix="/implementation-runs", tags=["implementation-runs"])


def _get_run_or_404(db: Session, run_id: uuid.UUID) -> ImplementationRun:
    run = db.get(ImplementationRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Implementation run {run_id} not found")
    return run


def _get_pull_request_link(db: Session, run_id: uuid.UUID) -> PullRequestLink | None:
    return db.query(PullRequestLink).filter(PullRequestLink.implementation_run_id == run_id).first()




_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, *, max_len: int = 40) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return (slug or "task")[:max_len]


def _derive_project_key(project_name: str) -> str:
    """No persisted project-key field exists anywhere in this codebase
    (checked) — this is a disclosed, deterministic heuristic used only for
    display (PR titles/commit messages), never stored as if it were a real
    identifier. Multi-word names take up to the first 4 words' initials
    ("Agentic SDLC Hub" -> "ASH"); a single word takes its first 6
    characters, both uppercased."""
    words = re.findall(r"[A-Za-z0-9]+", project_name)
    if len(words) >= 2:
        return "".join(w[0] for w in words[:4]).upper()
    if words:
        return words[0][:6].upper()
    return "PROJ"


@router.post("", response_model=ImplementationRunRead, status_code=status.HTTP_201_CREATED)
def start_implementation_run(payload: StartImplementationRunRequest, db: Session = Depends(get_db)) -> ImplementationRunRead:
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
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user"
        )

    implementation_node = (
        db.query(WorkflowNode)
        .filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == "implementation")
        .first()
    )
    if implementation_node is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Project {project.id}'s workflow has no implementation stage.")
    require_can_edit_stage(triggered_by, implementation_node.node_key)

    if task.area not in SUPPORTED_AREAS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"No {task.area.value} Implementation Agent is available yet — only "
            f"{', '.join(sorted(a.value for a in SUPPORTED_AREAS))} are implemented.",
        )

    # Requirement 1, gates 1 & 2 — LLD approved + Implementation Plan
    # approved. `implementation`'s own required_inputs already lists both
    # artifact types (see workflows/sdlc-workflow.json), so this one call
    # enforces both, exactly like generate_implementation_plan's own gate.
    graph_engine = GraphEngineService(db)
    validation = graph_engine.validate_can_run(project=project, node=implementation_node, freeform_context={})
    if not validation.can_run:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot start — " + "; ".join(validation.reasons) + ".")

    # Requirement 1, gate 3 — a GitHub repo snapshot exists. Not an
    # artifact type, so not part of required_inputs; resolved the same way
    # preview_repo_context already does.
    repository = db.query(Repository).filter(Repository.project_id == project.id).order_by(Repository.created_at.desc()).first()
    if repository is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot start — connect a GitHub repository first.")
    snapshot = (
        db.query(RepositorySnapshot)
        .filter(RepositorySnapshot.repository_id == repository.id)
        .order_by(RepositorySnapshot.created_at.desc())
        .first()
    )
    if snapshot is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot start — create a repository snapshot first.")

    lld_summary = validation.approved_artifact_summaries.get("lld_document", "")
    story_backlog_content = validation.approved_artifact_content.get("story_backlog", "")
    story = find_related_story(story_backlog_content, task.linked_story)

    try:
        github_token = decrypt_repository_token(repository)
    except HTTPException:
        github_token = None  # degrades to summary-only context, same as preview_repo_context

    run = ImplementationRun(
        project_id=project.id,
        implementation_task_id=task.id,
        repository_snapshot_id=snapshot.id,
        triggered_by_user_id=triggered_by.id,
        agent_type=task.assigned_agent_type,
        status=ImplementationRunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    task.status = ImplementationTaskStatus.IN_PROGRESS
    db.flush()

    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="implementation_run.started",
        entity_type="ImplementationRun", entity_id=run.id,
        extra_data={"implementation_task_id": str(task.id), "area": task.area.value, "agent_type": task.assigned_agent_type},
    )

    try:
        repo_context = RepoContextBuilderService(db).build(task=task, snapshot=snapshot, github_token=github_token)
        result = run_implementation_agent(task=task, repo_context=repo_context, story=story, lld_summary=lld_summary)
    except Exception as exc:  # noqa: BLE001 — anything unexpected fails this run cleanly, never a bare 500
        run.status = ImplementationRunStatus.FAILED
        run.error_message = str(exc)
        run.completed_at = datetime.now(timezone.utc)
        task.status = ImplementationTaskStatus.PENDING  # retryable
        record_audit_log(
            db, project_id=project.id, actor_user_id=triggered_by.id, action="implementation_run.failed",
            entity_type="ImplementationRun", entity_id=run.id, extra_data={"error": str(exc)},
        )
        db.commit()
        db.refresh(run)
        return ImplementationRunRead.from_orm_run(run)

    run.proposed_file_changes = [
        {"path": c.path, "change_type": c.change_type, "summary": c.summary, "after_content": c.after_content}
        for c in result.proposed_file_changes
    ]
    run.diff_text = result.diff_text
    run.explanation = result.explanation
    run.test_command = result.test_command
    run.risks = result.risks
    run.used_mock = result.used_mock
    run.token_usage = {
        "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens, "total_tokens": result.total_tokens,
    }
    run.cost = result.cost
    run.status = ImplementationRunStatus.COMPLETED
    run.review_status = ImplementationRunReviewStatus.PENDING_REVIEW
    run.completed_at = datetime.now(timezone.utc)
    # task.status stays IN_PROGRESS — it only reaches COMPLETED once a PR
    # is actually created and merged (still outside this app's control —
    # merging is a separate, human GitHub action this codebase never takes).

    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="implementation_run.completed",
        entity_type="ImplementationRun", entity_id=run.id,
        extra_data={
            "implementation_task_id": str(task.id), "used_mock": result.used_mock,
            "file_count": len(result.proposed_file_changes), "token_usage": run.token_usage,
        },
    )

    db.commit()
    db.refresh(run)
    return ImplementationRunRead.from_orm_run(run)


@router.get("/{run_id}", response_model=ImplementationRunRead)
def get_implementation_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> ImplementationRunRead:
    run = _get_run_or_404(db, run_id)
    return ImplementationRunRead.from_orm_run(run, pull_request=_get_pull_request_link(db, run.id))


@router.post("/{run_id}/review", response_model=ImplementationRunRead)
def review_implementation_run(
    run_id: uuid.UUID, payload: ReviewImplementationRunRequest, db: Session = Depends(get_db)
) -> ImplementationRunRead:
    run = _get_run_or_404(db, run_id)
    if run.status != ImplementationRunStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Run {run_id} is {run.status.value}, not COMPLETED — nothing to review yet.")

    reviewer = db.get(User, payload.reviewed_by_user_id)
    if reviewer is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"reviewed_by_user_id {payload.reviewed_by_user_id} does not match an existing user")

    run.review_status = (
        ImplementationRunReviewStatus.ACCEPTED if payload.decision == "ACCEPTED" else ImplementationRunReviewStatus.REJECTED
    )
    run.reviewed_by_user_id = reviewer.id
    run.reviewed_at = datetime.now(timezone.utc)
    run.review_comment = payload.comment

    # SECURITY / SCOPE: neither branch below touches GitHub in any way —
    # see module docstring. Accepting only records a human's sign-off; a
    # real write only ever happens via create_pull_request below.
    record_audit_log(
        db, project_id=run.project_id, actor_user_id=reviewer.id,
        action="implementation_run.accepted" if payload.decision == "ACCEPTED" else "implementation_run.rejected",
        entity_type="ImplementationRun", entity_id=run.id, extra_data={"comment": payload.comment},
    )

    db.commit()
    db.refresh(run)
    return ImplementationRunRead.from_orm_run(run)


# --- Create PR from an accepted run (requirements 1-8 of the GitHub branch/PR task) ---


def _pr_body(*, project, task, run: ImplementationRun) -> str:
    risk_lines = [f"- {r}" for r in run.risks] if run.risks else ["- (none noted)"]
    parts = [
        run.explanation or "(no explanation provided)",
        "",
        f"**Test command:** `{run.test_command}`" if run.test_command else "",
        "",
        "**Risks:**",
        *risk_lines,
        "",
        "---",
        f"_Opened by the Implementation Agent — project `{project.id}`, task `{task.id}`, run `{run.id}`._",
    ]
    return "\n".join(p for p in parts if p is not None and p != "")


@router.post("/{run_id}/create-pull-request", response_model=ImplementationRunRead, status_code=status.HTTP_201_CREATED)
def create_pull_request(
    run_id: uuid.UUID, payload: CreatePullRequestRequest, db: Session = Depends(get_db)
) -> ImplementationRunRead:
    run = _get_run_or_404(db, run_id)
    task = db.get(ImplementationTask, run.implementation_task_id)
    if task is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Run {run_id}'s implementation task no longer exists.")
    project = db.get(Project, run.project_id)
    if project is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Run {run_id}'s project no longer exists.")

    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")

    implementation_node = (
        db.query(WorkflowNode).filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == "implementation").first()
    )
    if implementation_node is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Project {project.id}'s workflow has no implementation stage.")
    require_can_edit_stage(triggered_by, implementation_node.node_key)

    # RULE: user must approve the patch before PR creation.
    if run.status != ImplementationRunStatus.COMPLETED or run.review_status != ImplementationRunReviewStatus.ACCEPTED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Run {run_id} must be COMPLETED and ACCEPTED before a pull request can be created "
            f"(currently status={run.status.value}, review_status={run.review_status.value}).",
        )
    if _get_pull_request_link(db, run.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A pull request already exists for run {run_id}.")

    repository = db.query(Repository).filter(Repository.project_id == project.id).order_by(Repository.created_at.desc()).first()
    if repository is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot create a pull request — no GitHub repository is configured.")

    try:
        github_token = decrypt_repository_token(repository)
    except HTTPException as exc:
        # Unlike the run-generation path, a real write cannot silently
        # degrade to "no token" — surface the failure plainly.
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot create a pull request — " + str(exc.detail)) from exc

    base_branch = payload.base_branch or repository.default_branch
    if not base_branch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No base_branch given and this repository has no known default branch.")

    branch_name = f"agent/{_slugify(task.title)}-{str(run.id)[:8]}"
    # RULE: never push to main — defense in depth; unreachable by
    # construction since branch_name always includes a run-id suffix, but
    # checked explicitly rather than trusted implicitly.
    if branch_name == repository.default_branch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Refusing to use the repository's default branch as the feature branch.")

    project_key = _derive_project_key(project.name)
    commit_message = f"[{project_key}] {task.title}"
    pr_title = f"[{project_key}] {task.title}"[:255]

    try:
        github_api.create_branch(github_token, repository.owner, repository.name, new_branch=branch_name, base_ref=base_branch)
        for change in run.proposed_file_changes:
            path, change_type = change["path"], change["change_type"]
            if change_type == "delete":
                sha = github_api.get_file_sha(github_token, repository.owner, repository.name, path, branch_name)
                if sha is None:
                    continue  # nothing to delete — already absent on this branch
                github_api.delete_file(
                    github_token, repository.owner, repository.name, path,
                    message=commit_message, branch=branch_name, sha=sha,
                )
            else:
                sha = github_api.get_file_sha(github_token, repository.owner, repository.name, path, branch_name)
                github_api.create_or_update_file(
                    github_token, repository.owner, repository.name, path,
                    content=change.get("after_content") or "", message=commit_message, branch=branch_name, sha=sha,
                )
        pr = github_api.create_pull_request(
            github_token, repository.owner, repository.name,
            title=pr_title, head=branch_name, base=base_branch, body=_pr_body(project=project, task=task, run=run),
        )
    except GitHubIntegrationError as exc:
        # SCOPE: the branch (and any commits already made to it) may still
        # exist on GitHub — no automatic rollback. Recorded so a human can
        # find and clean it up; no PullRequestLink is persisted.
        record_audit_log(
            db, project_id=project.id, actor_user_id=triggered_by.id, action="implementation_run.pr_creation_failed",
            entity_type="ImplementationRun", entity_id=run.id, extra_data={"branch_name": branch_name, "error": str(exc)},
        )
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"GitHub pull request creation failed: {exc}") from exc

    link = PullRequestLink(
        project_id=project.id,
        workflow_node_id=implementation_node.id,
        implementation_task_id=task.id,
        implementation_run_id=run.id,
        repository_id=repository.id,
        branch_name=branch_name,
        base_branch=base_branch,
        pr_number=pr.number,
        pr_url=pr.html_url,
        created_by_agent=True,
        triggered_by_user_id=triggered_by.id,
        commit_message=commit_message,
    )
    db.add(link)
    db.flush()

    # SECURITY: never the token — only non-secret PR metadata.
    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="implementation_run.pr_created",
        entity_type="PullRequestLink", entity_id=link.id,
        extra_data={
            "owner": repository.owner, "name": repository.name, "branch_name": branch_name, "base_branch": base_branch,
            "pr_number": pr.number, "pr_url": pr.html_url,
        },
    )

    db.commit()
    db.refresh(run)
    db.refresh(link)
    return ImplementationRunRead.from_orm_run(run, pull_request=link)
