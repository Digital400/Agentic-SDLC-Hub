"""Direct ImplementationTask endpoints: assigning which of a project's
(possibly several) connected repositories a task's code changes target
(see app/models/repository.py's multi-repo support and
app/api/routes/implementation_runs.py's `_resolve_repository_for_task`,
which is what actually reads this field back at run time), and reopening a
mistakenly-completed task.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import (
    ImplementationRun,
    ImplementationRunReviewStatus,
    ImplementationTask,
    ImplementationTaskStatus,
    Repository,
    User,
)
from app.schemas.implementation_task import ImplementationTaskRead, ReopenImplementationTaskRequest, UpdateImplementationTaskRepositoryRequest
from app.services.audit import record_audit_log

router = APIRouter(prefix="/implementation-tasks", tags=["implementation-tasks"])


def _get_task_or_404(db: Session, task_id: uuid.UUID) -> ImplementationTask:
    task = db.get(ImplementationTask, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Implementation task {task_id} not found")
    return task


@router.patch("/{task_id}/repository", response_model=ImplementationTaskRead)
def update_task_repository(
    task_id: uuid.UUID, payload: UpdateImplementationTaskRepositoryRequest, db: Session = Depends(get_db)
) -> ImplementationTask:
    task = _get_task_or_404(db, task_id)

    if payload.repository_id is not None:
        repository = db.get(Repository, payload.repository_id)
        if repository is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"repository_id {payload.repository_id} does not match an existing repository")
        if repository.project_id != task.project_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That repository is not connected to this task's project.")

    task.repository_id = payload.repository_id
    db.flush()

    record_audit_log(
        db, project_id=task.project_id, action="implementation_task.repository_assigned",
        entity_type="ImplementationTask", entity_id=task.id,
        extra_data={"repository_id": str(payload.repository_id) if payload.repository_id else None},
    )

    db.commit()
    db.refresh(task)
    return task


@router.post("/{task_id}/reopen", response_model=ImplementationTaskRead)
def reopen_task(task_id: uuid.UUID, payload: ReopenImplementationTaskRequest, db: Session = Depends(get_db)) -> ImplementationTask:
    """Undoes a mistaken COMPLETED status — most commonly, the wrong (e.g.
    already-merged, unrelated) pull request was registered against this
    task via POST /implementation-runs/register-pull-request, which flips a
    task straight to COMPLETED with no review step a human could catch
    first. Puts the task back to PENDING — so it becomes "the current task"
    again for a full-stack-per-story sequence (see
    app/api/routes/stories.py's _current_story_task) — and REJECTs its
    latest Accepted run so a fresh one (or a correct registration) is what
    gets accepted next.

    SCOPE: does not touch the superseded run's PullRequestLink row — it
    stays exactly as it was (this app has no live sync with GitHub's own PR
    state; see PullRequestLink.status's own docstring). If that PR is still
    recorded as OPEN and this is a story-scoped task, a later in-app
    "Create Pull Request" for a sibling task could still find it via
    _get_open_task_pull_request — registering the CORRECT pull request
    again here is the safe way to continue, not the in-app agent path.
    """
    task = _get_task_or_404(db, task_id)
    if task.status != ImplementationTaskStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Task {task.id} is {task.status.value}, not COMPLETED — nothing to reopen.")

    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")

    latest_accepted_run = (
        db.query(ImplementationRun)
        .filter(ImplementationRun.implementation_task_id == task.id, ImplementationRun.review_status == ImplementationRunReviewStatus.ACCEPTED)
        .order_by(ImplementationRun.created_at.desc())
        .first()
    )
    if latest_accepted_run is not None:
        latest_accepted_run.review_status = ImplementationRunReviewStatus.REJECTED
        latest_accepted_run.reviewed_by_user_id = triggered_by.id
        latest_accepted_run.reviewed_at = datetime.now(timezone.utc)
        latest_accepted_run.review_comment = payload.reason or "Reopened — this run (and any pull request registered against it) was incorrect."

    task.status = ImplementationTaskStatus.PENDING
    db.flush()

    record_audit_log(
        db, project_id=task.project_id, actor_user_id=triggered_by.id, action="implementation_task.reopened",
        entity_type="ImplementationTask", entity_id=task.id,
        extra_data={"reason": payload.reason, "superseded_run_id": str(latest_accepted_run.id) if latest_accepted_run else None},
    )

    db.commit()
    db.refresh(task)
    return task
