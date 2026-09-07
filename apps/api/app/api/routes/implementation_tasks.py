"""Direct ImplementationTask endpoints — currently just the one action
that doesn't belong to any other router: assigning which of a project's
(possibly several) connected repositories a task's code changes target.
See app/models/repository.py's multi-repo support and
app/api/routes/implementation_runs.py's `_resolve_repository_for_task`,
which is what actually reads this field back at run time.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import ImplementationTask, Repository
from app.schemas.implementation_task import ImplementationTaskRead, UpdateImplementationTaskRepositoryRequest
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
