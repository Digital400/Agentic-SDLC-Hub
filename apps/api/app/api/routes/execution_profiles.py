"""ProjectExecutionProfile endpoints — see
app/services/execution_profile_service.py for the propose/approve/reject
logic and app/services/execution_profile_detection.py for the safe,
read-only repository detection behind the EXISTING_REPOSITORY flow.

Covers: list approved company stack/templates (NEW_PROJECT flow input),
propose a profile from detection or from a template, list a project's full
version/audit history, fetch its active approved profile, and
approve/reject a proposed version (Project Owner only).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.routes.github_integration import decrypt_repository_token
from app.core.database import get_db
from app.models import Project, ProjectExecutionProfile, RepositorySnapshot, User
from app.schemas.project_execution_profile import (
    ApproveExecutionProfileRequest,
    ExecutionProfileTemplateRead,
    ProjectExecutionProfileRead,
    ProposeFromDetectionRequest,
    ProposeFromTemplateRequest,
    RejectExecutionProfileRequest,
)
from app.services.execution_profile_service import ExecutionProfileError, ProjectExecutionProfileService
from app.services.execution_profile_templates import ExecutionProfileTemplateError, list_approved_templates
from app.services.permissions import require_can_approve_execution_profile

router = APIRouter(tags=["execution-profiles"])


def _get_project_or_400(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"project_id {project_id} does not match an existing project")
    return project


def _get_user_or_400(db: Session, user_id: uuid.UUID, *, field_name: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{field_name} {user_id} does not match an existing user")
    return user


def _get_profile_or_404(db: Session, profile_id: uuid.UUID) -> ProjectExecutionProfile:
    profile = db.get(ProjectExecutionProfile, profile_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Project execution profile {profile_id} not found")
    return profile


# --- Approved company stack/template catalog (NEW_PROJECT flow input) -----------------


@router.get("/execution-profile-templates", response_model=list[ExecutionProfileTemplateRead])
def list_execution_profile_templates() -> list[ExecutionProfileTemplateRead]:
    return [
        ExecutionProfileTemplateRead(
            template_key=t.template_key, name=t.name, description=t.description, languages=t.languages,
            frameworks=t.frameworks, package_manager=t.package_manager, runtime_image=t.runtime_image,
        )
        for t in list_approved_templates()
    ]


# --- Propose ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/execution-profiles/detect",
    response_model=ProjectExecutionProfileRead,
    status_code=status.HTTP_201_CREATED,
)
def propose_execution_profile_from_detection(
    project_id: uuid.UUID, payload: ProposeFromDetectionRequest, db: Session = Depends(get_db)
) -> ProjectExecutionProfileRead:
    """EXISTING_REPOSITORY flow — "detect and propose the profile." Safe,
    read-only detection only (see execution_profile_detection.py's HARD
    RULE); the resulting profile is PENDING_APPROVAL, never auto-active."""
    project = _get_project_or_400(db, project_id)
    triggered_by = _get_user_or_400(db, payload.triggered_by_user_id, field_name="triggered_by_user_id")

    snapshot = db.get(RepositorySnapshot, payload.repository_snapshot_id)
    if snapshot is None or snapshot.repository.project_id != project.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"repository_snapshot_id {payload.repository_snapshot_id} does not match a snapshot of a repository belonging to project {project_id}",
        )

    github_token = payload.github_token
    if github_token is None:
        try:
            github_token = decrypt_repository_token(snapshot.repository)
        except HTTPException:
            github_token = None  # degrades to presence-only detection signals, same as repo_context_builder.py's own fallback

    profile = ProjectExecutionProfileService(db).propose_from_detection(
        project=project, snapshot=snapshot, triggered_by=triggered_by, github_token=github_token,
    )
    db.commit()
    db.refresh(profile)
    return ProjectExecutionProfileRead.model_validate(profile)


@router.post(
    "/projects/{project_id}/execution-profiles/from-template",
    response_model=ProjectExecutionProfileRead,
    status_code=status.HTTP_201_CREATED,
)
def propose_execution_profile_from_template(
    project_id: uuid.UUID, payload: ProposeFromTemplateRequest, db: Session = Depends(get_db)
) -> ProjectExecutionProfileRead:
    """NEW_PROJECT flow — "let the user select an approved company
    stack/template" then "generate a proposed profile." Rejects any
    template_key not in the approved catalog with 400 (never silently
    substitutes a close match)."""
    project = _get_project_or_400(db, project_id)
    triggered_by = _get_user_or_400(db, payload.triggered_by_user_id, field_name="triggered_by_user_id")

    try:
        profile = ProjectExecutionProfileService(db).propose_from_template(
            project=project, template_key=payload.template_key, triggered_by=triggered_by,
            default_branch_override=payload.default_branch,
        )
    except ExecutionProfileTemplateError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    db.commit()
    db.refresh(profile)
    return ProjectExecutionProfileRead.model_validate(profile)


# --- Read ------------------------------------------------------------------------------


@router.get("/projects/{project_id}/execution-profiles", response_model=list[ProjectExecutionProfileRead])
def list_execution_profiles(project_id: uuid.UUID, db: Session = Depends(get_db)) -> list[ProjectExecutionProfileRead]:
    """Full version/audit history for this project — newest first. Every
    DRAFT/PENDING_APPROVAL/APPROVED/REJECTED/SUPERSEDED version, immutable
    once created (see the model's versioning note)."""
    _get_project_or_400(db, project_id)
    profiles = ProjectExecutionProfileService(db).list_versions(project_id)
    return [ProjectExecutionProfileRead.model_validate(p) for p in profiles]


@router.get("/projects/{project_id}/execution-profiles/active", response_model=ProjectExecutionProfileRead)
def get_active_execution_profile(project_id: uuid.UUID, db: Session = Depends(get_db)) -> ProjectExecutionProfileRead:
    _get_project_or_400(db, project_id)
    profile = ProjectExecutionProfileService(db).get_active_profile(project_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Project {project_id} has no approved, active execution profile yet.")
    return ProjectExecutionProfileRead.model_validate(profile)


@router.get("/execution-profiles/{profile_id}", response_model=ProjectExecutionProfileRead)
def get_execution_profile(profile_id: uuid.UUID, db: Session = Depends(get_db)) -> ProjectExecutionProfileRead:
    return ProjectExecutionProfileRead.model_validate(_get_profile_or_404(db, profile_id))


# --- Approval lifecycle (Project Owner only) --------------------------------------------


@router.post("/execution-profiles/{profile_id}/approve", response_model=ProjectExecutionProfileRead)
def approve_execution_profile(
    profile_id: uuid.UUID, payload: ApproveExecutionProfileRequest, db: Session = Depends(get_db)
) -> ProjectExecutionProfileRead:
    profile = _get_profile_or_404(db, profile_id)
    approver = _get_user_or_400(db, payload.approved_by_user_id, field_name="approved_by_user_id")
    require_can_approve_execution_profile(approver)

    try:
        ProjectExecutionProfileService(db).approve(profile=profile, approved_by=approver)
    except ExecutionProfileError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    db.commit()
    db.refresh(profile)
    return ProjectExecutionProfileRead.model_validate(profile)


@router.post("/execution-profiles/{profile_id}/reject", response_model=ProjectExecutionProfileRead)
def reject_execution_profile(
    profile_id: uuid.UUID, payload: RejectExecutionProfileRequest, db: Session = Depends(get_db)
) -> ProjectExecutionProfileRead:
    profile = _get_profile_or_404(db, profile_id)
    rejecter = _get_user_or_400(db, payload.rejected_by_user_id, field_name="rejected_by_user_id")
    require_can_approve_execution_profile(rejecter)

    try:
        ProjectExecutionProfileService(db).reject(profile=profile, rejected_by=rejecter, reason=payload.reason)
    except ExecutionProfileError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    db.commit()
    db.refresh(profile)
    return ProjectExecutionProfileRead.model_validate(profile)
