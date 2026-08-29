"""Artifact endpoints.

Covers: create an artifact, get one by id, create/list its versions,
edit its current draft content in place, and submit it for review. "List
artifacts by project" lives in app/api/routes/projects.py instead, next to
that resource's other sub-lists (workflow-nodes) — see that file.

No AI/agent drafting is wired up here (see docs/mvp-plan.md) — every
version is created by a human via `created_by_id`.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Artifact, ArtifactStatus, ArtifactVersion, Project, User, WorkflowNode
from app.schemas.artifact import (
    ArtifactContentUpdate,
    ArtifactCreate,
    ArtifactRead,
    ArtifactVersionCreate,
    ArtifactVersionRead,
)
from app.services.audit import record_audit_log

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


def _get_artifact_or_404(db: Session, artifact_id: uuid.UUID) -> Artifact:
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Artifact {artifact_id} not found")
    return artifact


def _get_user_or_400(db: Session, user_id: uuid.UUID, field_name: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{field_name} {user_id} does not match an existing user")
    return user


# 1. Create artifact -----------------------------------------------------------


@router.post("", response_model=ArtifactRead, status_code=status.HTTP_201_CREATED)
def create_artifact(payload: ArtifactCreate, db: Session = Depends(get_db)) -> ArtifactRead:
    """Create an artifact container — no content yet.

    Content is added afterwards via `POST /artifacts/{id}/versions`; this
    matches the product model where an artifact is a stable container and
    its actual content lives in immutable `ArtifactVersion` rows.
    """
    project = db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"project_id {payload.project_id} does not match an existing project")

    node = (
        db.query(WorkflowNode)
        .filter(WorkflowNode.id == payload.workflow_node_id, WorkflowNode.project_id == payload.project_id)
        .first()
    )
    if node is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"workflow_node_id {payload.workflow_node_id} is not a workflow node of project {payload.project_id}",
        )

    creator = _get_user_or_400(db, payload.created_by_id, "created_by_id")

    artifact = Artifact(
        project=project,
        workflow_node=node,
        artifact_type=payload.artifact_type,
        title=payload.title,
        status=ArtifactStatus.DRAFT,
        created_by=creator,
    )
    db.add(artifact)
    db.flush()

    record_audit_log(
        db,
        project_id=project.id,
        actor_user_id=creator.id,
        action="artifact.created",
        entity_type="Artifact",
        entity_id=artifact.id,
        extra_data={"workflow_node": node.node_key, "artifact_type": artifact.artifact_type},
    )

    db.commit()
    db.refresh(artifact)
    return ArtifactRead.from_orm_artifact(artifact)


# 2. Get artifact by id ---------------------------------------------------------


@router.get("/{artifact_id}", response_model=ArtifactRead)
def get_artifact(artifact_id: uuid.UUID, db: Session = Depends(get_db)) -> ArtifactRead:
    return ArtifactRead.from_orm_artifact(_get_artifact_or_404(db, artifact_id))


# 4. Create artifact version -----------------------------------------------------


@router.post("/{artifact_id}/versions", response_model=ArtifactVersionRead, status_code=status.HTTP_201_CREATED)
def create_artifact_version(
    artifact_id: uuid.UUID, payload: ArtifactVersionCreate, db: Session = Depends(get_db)
) -> ArtifactVersionRead:
    """Create a brand-new immutable version and make it current.

    Any existing review verdict (NEEDS_CHANGES/REJECTED) no longer applies
    to this new, unreviewed content, so the artifact's status resets to
    DRAFT — even if it was previously APPROVED. Use `PATCH /artifacts/{id}`
    instead if you just want to keep editing the current draft in place.
    """
    artifact = _get_artifact_or_404(db, artifact_id)
    creator = _get_user_or_400(db, payload.created_by_id, "created_by_id")

    last_version_number = (
        db.query(ArtifactVersion.version_number)
        .filter(ArtifactVersion.artifact_id == artifact.id)
        .order_by(ArtifactVersion.version_number.desc())
        .limit(1)
        .scalar()
    )
    next_version_number = (last_version_number or 0) + 1

    version = ArtifactVersion(
        artifact=artifact,
        version_number=next_version_number,
        content_markdown=payload.content_markdown,
        content_json=payload.content_json,
        change_summary=payload.change_summary,
        created_by=creator,
    )
    db.add(version)
    db.flush()

    previous_status = artifact.status
    artifact.current_version = version
    artifact.status = ArtifactStatus.DRAFT

    record_audit_log(
        db,
        project_id=artifact.project_id,
        actor_user_id=creator.id,
        action="artifact_version.created",
        entity_type="ArtifactVersion",
        entity_id=version.id,
        extra_data={
            "artifact_id": str(artifact.id),
            "version_number": next_version_number,
            "artifact_status": {"from": previous_status.value, "to": ArtifactStatus.DRAFT.value},
        },
    )

    db.commit()
    db.refresh(version)
    return ArtifactVersionRead.from_orm_version(version)


# 5. Get artifact version history --------------------------------------------------


@router.get("/{artifact_id}/versions", response_model=list[ArtifactVersionRead])
def list_artifact_versions(artifact_id: uuid.UUID, db: Session = Depends(get_db)) -> list[ArtifactVersionRead]:
    _get_artifact_or_404(db, artifact_id)
    versions = (
        db.query(ArtifactVersion)
        .filter(ArtifactVersion.artifact_id == artifact_id)
        .order_by(ArtifactVersion.version_number)
        .all()
    )
    return [ArtifactVersionRead.from_orm_version(v) for v in versions]


# 6. Update artifact content ------------------------------------------------------


@router.patch("/{artifact_id}", response_model=ArtifactVersionRead)
def update_artifact_content(
    artifact_id: uuid.UUID, payload: ArtifactContentUpdate, db: Session = Depends(get_db)
) -> ArtifactVersionRead:
    """Edit the CURRENT version's content in place — no new version number.

    Only allowed while the artifact is still DRAFT: once it's submitted
    for review (or beyond), further changes must go through
    `POST /artifacts/{id}/versions` so the edit is captured as its own
    immutable, attributable snapshot rather than silently rewriting
    content a reviewer may already be looking at.
    """
    artifact = _get_artifact_or_404(db, artifact_id)

    if artifact.status != ArtifactStatus.DRAFT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot edit content in place while artifact status is {artifact.status.value}; "
            "create a new version instead.",
        )
    if artifact.current_version is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Artifact has no version yet; create one first.")

    version = artifact.current_version
    version.content_markdown = payload.content_markdown
    version.content_json = payload.content_json
    if payload.change_summary is not None:
        version.change_summary = payload.change_summary

    record_audit_log(
        db,
        project_id=artifact.project_id,
        action="artifact_version.content_updated",
        entity_type="ArtifactVersion",
        entity_id=version.id,
        extra_data={"artifact_id": str(artifact.id), "version_number": version.version_number},
    )

    db.commit()
    db.refresh(version)
    return ArtifactVersionRead.from_orm_version(version)


# 7. Mark artifact as ready for review ----------------------------------------------


@router.post("/{artifact_id}/submit-for-review", response_model=ArtifactRead)
def submit_artifact_for_review(artifact_id: uuid.UUID, db: Session = Depends(get_db)) -> ArtifactRead:
    artifact = _get_artifact_or_404(db, artifact_id)

    if artifact.current_version_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Artifact has no version yet; create one before submitting it.")

    if artifact.status not in (ArtifactStatus.DRAFT, ArtifactStatus.NEEDS_CHANGES):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot submit for review from status {artifact.status.value}.",
        )

    previous_status = artifact.status
    artifact.status = ArtifactStatus.READY_FOR_REVIEW

    record_audit_log(
        db,
        project_id=artifact.project_id,
        action="artifact.submitted_for_review",
        entity_type="Artifact",
        entity_id=artifact.id,
        extra_data={"from": previous_status.value, "to": ArtifactStatus.READY_FOR_REVIEW.value},
    )

    db.commit()
    db.refresh(artifact)
    return ArtifactRead.from_orm_artifact(artifact)
