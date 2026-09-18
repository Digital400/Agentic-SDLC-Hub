"""Developer Bridge endpoints (Phase 13) — the server half of
docs/architecture/developer-local-bridge.md. Gated entirely behind
Settings.DEVELOPER_BRIDGE_ENABLED (default False, per Phase 13's own
"keep the bridge feature disabled by default" requirement): every route
below returns 404 while the flag is off, matching the exact pattern
already used for Settings.STORY_GIT_PR_FLOW_V2_ENABLED
(app/api/routes/implementation_runs.py's create_pull_request_v2).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.models import BridgeDeviceAuthorizationStatus, BridgeSessionStatus
from app.services import developer_bridge as service

router = APIRouter(prefix="/bridge", tags=["developer-bridge"])


def _require_enabled() -> None:
    if not get_settings().DEVELOPER_BRIDGE_ENABLED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The Developer Bridge is not enabled for this deployment.")


def _authenticate(db: Session, authorization: str | None):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")
    token = authorization.split(" ", 1)[1]
    try:
        return service.resolve_access_token(db, access_token=token)
    except service.BridgeAccessTokenInvalidError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc


class DeviceAuthorizeRequest(BaseModel):
    client_id: str


class DeviceAuthorizeResponse(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class DeviceTokenRequest(BaseModel):
    client_id: str
    device_code: str


class DeviceTokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int


class DeviceTokenPendingResponse(BaseModel):
    status: str


class DeviceApproveRequest(BaseModel):
    user_code: str
    approved_by_user_id: uuid.UUID


class StatusReportRequest(BaseModel):
    status: str


class AssignedJobRepository(BaseModel):
    remote_url: str
    branch: str
    base_commit_sha: str


class AssignedJobResponse(BaseModel):
    job_id: uuid.UUID
    repository: AssignedJobRepository
    runtime_key: str
    credential_expires_in_seconds: int


class RejectJobRequest(BaseModel):
    reason: str


class UploadEvidenceRequest(BaseModel):
    events: list[dict]
    patch_hash: str | None
    test_results: list[dict]
    usage: dict
    repository_remote_url: str
    repository_base_commit_sha: str


@router.post("/device/authorize", response_model=DeviceAuthorizeResponse)
def device_authorize(payload: DeviceAuthorizeRequest, db: Session = Depends(get_db)):
    _require_enabled()
    auth = service.create_device_authorization(db, client_id=payload.client_id)
    db.commit()
    return DeviceAuthorizeResponse(
        device_code=auth.device_code, user_code=auth.user_code,
        verification_uri="/bridge/device/approve", expires_in=service.DEVICE_CODE_TTL_SECONDS, interval=5,
    )


@router.post("/device/approve")
def device_approve(payload: DeviceApproveRequest, db: Session = Depends(get_db)):
    """The developer's own authenticated web session calls this after
    visiting the verification page and entering their user_code — same
    explicit-actor-id pattern as every other route in this codebase
    (see docs/architecture/universal-agent-runtime-baseline.md §10)."""
    _require_enabled()
    try:
        service.approve_device_authorization(db, user_code=payload.user_code, approved_by_user_id=payload.approved_by_user_id)
    except service.DeviceAuthorizationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except service.BridgeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    db.commit()
    return {"approved": True}


@router.post("/device/token")
def device_token(payload: DeviceTokenRequest, db: Session = Depends(get_db)):
    _require_enabled()
    auth = service.poll_device_token(db, device_code=payload.device_code)
    if auth is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown device_code.")
    if auth.status == BridgeDeviceAuthorizationStatus.APPROVED and auth.access_token:
        return {
            "status": "approved",
            "token": DeviceTokenResponse(
                access_token=auth.access_token, token_type="Bearer", expires_in=service.ACCESS_TOKEN_TTL_SECONDS
            ),
        }
    if auth.status == BridgeDeviceAuthorizationStatus.DENIED:
        return {"status": "denied"}
    expires_at = auth.expires_at if auth.expires_at.tzinfo is not None else auth.expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        return {"status": "denied"}
    return {"status": "pending"}


@router.post("/status")
def report_status(payload: StatusReportRequest, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    _require_enabled()
    developer = _authenticate(db, authorization)
    if payload.status not in ("connected", "offline"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "status must be 'connected' or 'offline'.")
    service.report_status(
        db, developer_user_id=developer.id,
        status=BridgeSessionStatus.CONNECTED if payload.status == "connected" else BridgeSessionStatus.OFFLINE,
    )
    db.commit()
    return {"ok": True}


@router.get("/jobs/next")
def get_next_job(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    _require_enabled()
    developer = _authenticate(db, authorization)
    job = service.next_assigned_job(db, developer_user_id=developer.id)
    if job is None:
        return None
    return AssignedJobResponse(
        job_id=job.id,
        repository=AssignedJobRepository(
            remote_url=job.repository_remote_url, branch=job.repository_branch, base_commit_sha=job.repository_base_commit_sha
        ),
        runtime_key=job.runtime_key,
        credential_expires_in_seconds=service.ACCESS_TOKEN_TTL_SECONDS,
    )


@router.post("/jobs/{job_id}/accept")
def accept_job(job_id: uuid.UUID, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    _require_enabled()
    developer = _authenticate(db, authorization)
    try:
        service.accept_job(db, job_id=job_id, developer_user_id=developer.id)
    except service.BridgeJobNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    db.commit()
    return {"accepted": True}


@router.post("/jobs/{job_id}/reject")
def reject_job(
    job_id: uuid.UUID, payload: RejectJobRequest, authorization: str | None = Header(default=None), db: Session = Depends(get_db)
):
    _require_enabled()
    developer = _authenticate(db, authorization)
    try:
        service.reject_job(db, job_id=job_id, developer_user_id=developer.id, reason=payload.reason)
    except service.BridgeJobNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    db.commit()
    return {"rejected": True}


@router.get("/jobs/{job_id}/work-packet")
def get_work_packet(job_id: uuid.UUID, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """A minimal, honest stub: real WorkPacket construction for a bridge
    job reuses app/services/story_git_pr_flow.py's build_work_packet_for_run
    once a bridge job is wired to a real ImplementationRun — not yet done
    in this phase (see docs/architecture/developer-local-bridge.md,
    Remaining risks). This returns the job's own identifying fields so the
    CLI's contract is exercisable end to end without fabricating a signed
    packet this endpoint cannot yet honestly produce."""
    _require_enabled()
    developer = _authenticate(db, authorization)
    try:
        job = service.get_job_for_developer(db, job_id=job_id, developer_user_id=developer.id)
    except service.BridgeJobNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {"packet": {"job_id": str(job.id), "task_type": "IMPLEMENT_STORY"}, "signature": "unsigned-stub"}


@router.post("/jobs/{job_id}/evidence")
def upload_evidence(
    job_id: uuid.UUID, payload: UploadEvidenceRequest, authorization: str | None = Header(default=None), db: Session = Depends(get_db)
):
    _require_enabled()
    developer = _authenticate(db, authorization)
    try:
        service.upload_evidence(
            db, job_id=job_id, developer_user_id=developer.id,
            evidence={"events": payload.events, "patch_hash": payload.patch_hash, "test_results": payload.test_results, "usage": payload.usage},
            claimed_repository_remote_url=payload.repository_remote_url,
            claimed_repository_base_commit_sha=payload.repository_base_commit_sha,
        )
    except service.BridgeJobNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except service.BridgeJobWrongRepositoryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except service.BridgeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    db.commit()
    return {"uploaded": True, "evidence_trusted": False}
