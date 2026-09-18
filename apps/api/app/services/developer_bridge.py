"""Developer Bridge server-side logic (Phase 13).

Implements the server half of docs/architecture/developer-local-bridge.md:
issuing/approving device authorization codes, assigning a job to a
developer's own machine instead of the company sandbox, and receiving
(untrusted-until-CI-verified) evidence back. Every mutation here is
audited via app.services.audit.record_audit_log, matching this
codebase's existing convention (see app/api/routes/sprints.py's own
module docstring, rule 4).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import (
    BridgeDeviceAuthorization,
    BridgeDeviceAuthorizationStatus,
    BridgeJobAssignment,
    BridgeJobAssignmentStatus,
    BridgeSession,
    BridgeSessionStatus,
    ImplementationTask,
    User,
)
from app.services.audit import record_audit_log

DEVICE_CODE_TTL_SECONDS = 600
ACCESS_TOKEN_TTL_SECONDS = 3600


class BridgeError(Exception):
    """Base class for expected, user-facing Developer Bridge failures."""


class DeviceAuthorizationNotFoundError(BridgeError):
    pass


class DeviceAuthorizationNotApprovedError(BridgeError):
    pass


class BridgeAccessTokenInvalidError(BridgeError):
    pass


class BridgeJobNotFoundError(BridgeError):
    pass


class BridgeJobWrongRepositoryError(BridgeError):
    """"Reject evidence for the wrong repository or commit" — Phase 13's
    own literal server-side requirement."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime) -> datetime:
    """SQLite (used by this codebase's test fixture — see tests/conftest.py)
    has no real timezone type and hands back naive datetimes for a
    DateTime(timezone=True) column even though Postgres round-trips them
    correctly; assume UTC rather than let every comparison below crash
    with "can't compare offset-naive and offset-aware datetimes"."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def create_device_authorization(db: Session, *, client_id: str) -> BridgeDeviceAuthorization:
    auth = BridgeDeviceAuthorization(
        client_id=client_id,
        device_code=secrets.token_urlsafe(32),
        user_code=secrets.token_hex(4).upper(),
        status=BridgeDeviceAuthorizationStatus.PENDING,
        expires_at=_now() + timedelta(seconds=DEVICE_CODE_TTL_SECONDS),
    )
    db.add(auth)
    db.flush()
    return auth


def approve_device_authorization(db: Session, *, user_code: str, approved_by_user_id: uuid.UUID) -> BridgeDeviceAuthorization:
    """A developer's own web session (existing actor-id pattern — see
    docs/architecture/universal-agent-runtime-baseline.md §10) approves
    their own pending device code after visiting the verification URL."""
    auth = db.query(BridgeDeviceAuthorization).filter(BridgeDeviceAuthorization.user_code == user_code).one_or_none()
    if auth is None:
        raise DeviceAuthorizationNotFoundError(f"No device authorization with user_code {user_code!r}.")
    approver = db.get(User, approved_by_user_id)
    if approver is None:
        raise BridgeError(f"approved_by_user_id {approved_by_user_id} does not match an existing user.")
    if auth.status != BridgeDeviceAuthorizationStatus.PENDING or _as_aware(auth.expires_at) <= _now():
        auth.status = BridgeDeviceAuthorizationStatus.EXPIRED
        db.flush()
        raise DeviceAuthorizationNotFoundError("This device authorization is no longer pending.")

    auth.status = BridgeDeviceAuthorizationStatus.APPROVED
    auth.approved_by_user_id = approved_by_user_id
    auth.access_token = secrets.token_urlsafe(32)
    auth.access_token_expires_at = _now() + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS)
    record_audit_log(
        db, action="bridge_device.approved", entity_type="BridgeDeviceAuthorization", entity_id=auth.id,
        actor_user_id=approved_by_user_id,
    )
    db.flush()
    return auth


def poll_device_token(db: Session, *, device_code: str) -> BridgeDeviceAuthorization | None:
    return db.query(BridgeDeviceAuthorization).filter(BridgeDeviceAuthorization.device_code == device_code).one_or_none()


def resolve_access_token(db: Session, *, access_token: str) -> User:
    """Resolves a bearer token issued by approve_device_authorization to the
    developer who approved it — fails closed on missing/expired tokens."""
    auth = (
        db.query(BridgeDeviceAuthorization)
        .filter(BridgeDeviceAuthorization.access_token == access_token)
        .filter(BridgeDeviceAuthorization.status == BridgeDeviceAuthorizationStatus.APPROVED)
        .one_or_none()
    )
    if auth is None or auth.access_token_expires_at is None or _as_aware(auth.access_token_expires_at) <= _now():
        raise BridgeAccessTokenInvalidError("Bridge access token is missing, unknown or expired.")
    return db.get(User, auth.approved_by_user_id)  # type: ignore[return-value]


def report_status(db: Session, *, developer_user_id: uuid.UUID, status: BridgeSessionStatus) -> BridgeSession:
    session = db.query(BridgeSession).filter(BridgeSession.developer_user_id == developer_user_id).one_or_none()
    if session is None:
        session = BridgeSession(developer_user_id=developer_user_id, status=status, last_seen_at=_now())
        db.add(session)
    else:
        session.status = status
        session.last_seen_at = _now()
    db.flush()
    return session


def assign_job(
    db: Session,
    *,
    implementation_task_id: uuid.UUID,
    developer_user_id: uuid.UUID,
    runtime_key: str,
    repository_remote_url: str,
    repository_branch: str,
    repository_base_commit_sha: str,
    credential_ttl_seconds: int = ACCESS_TOKEN_TTL_SECONDS,
) -> BridgeJobAssignment:
    task = db.get(ImplementationTask, implementation_task_id)
    if task is None:
        raise BridgeJobNotFoundError(f"ImplementationTask {implementation_task_id} does not exist.")
    developer = db.get(User, developer_user_id)
    if developer is None:
        raise BridgeError(f"developer_user_id {developer_user_id} does not match an existing user.")

    job = BridgeJobAssignment(
        implementation_task_id=implementation_task_id,
        developer_user_id=developer_user_id,
        runtime_key=runtime_key,
        repository_remote_url=repository_remote_url,
        repository_branch=repository_branch,
        repository_base_commit_sha=repository_base_commit_sha,
        status=BridgeJobAssignmentStatus.ASSIGNED,
        credential_expires_at=_now() + timedelta(seconds=credential_ttl_seconds),
    )
    db.add(job)
    record_audit_log(
        db, action="bridge_job.assigned", entity_type="BridgeJobAssignment", entity_id=job.id,
        actor_user_id=developer_user_id,
        extra_data={"implementation_task_id": str(implementation_task_id), "runtime_key": runtime_key},
    )
    db.flush()
    return job


def next_assigned_job(db: Session, *, developer_user_id: uuid.UUID) -> BridgeJobAssignment | None:
    return (
        db.query(BridgeJobAssignment)
        .filter(BridgeJobAssignment.developer_user_id == developer_user_id)
        .filter(BridgeJobAssignment.status == BridgeJobAssignmentStatus.ASSIGNED)
        .order_by(BridgeJobAssignment.created_at.asc())
        .first()
    )


def accept_job(db: Session, *, job_id: uuid.UUID, developer_user_id: uuid.UUID) -> BridgeJobAssignment:
    job = _get_job_for_developer_or_404(db, job_id, developer_user_id)
    job.status = BridgeJobAssignmentStatus.ACCEPTED
    record_audit_log(db, action="bridge_job.accepted", entity_type="BridgeJobAssignment", entity_id=job.id, actor_user_id=developer_user_id)
    db.flush()
    return job


def reject_job(db: Session, *, job_id: uuid.UUID, developer_user_id: uuid.UUID, reason: str) -> BridgeJobAssignment:
    job = _get_job_for_developer_or_404(db, job_id, developer_user_id)
    job.status = BridgeJobAssignmentStatus.REJECTED
    job.rejected_reason = reason
    record_audit_log(
        db, action="bridge_job.rejected", entity_type="BridgeJobAssignment", entity_id=job.id,
        actor_user_id=developer_user_id, extra_data={"reason": reason},
    )
    db.flush()
    return job


def upload_evidence(
    db: Session,
    *,
    job_id: uuid.UUID,
    developer_user_id: uuid.UUID,
    evidence: dict,
    claimed_repository_remote_url: str,
    claimed_repository_base_commit_sha: str,
) -> BridgeJobAssignment:
    """"Reject evidence for the wrong repository or commit" and "treat
    local evidence as untrusted until CI verification" — both Phase 13's
    own literal server-side requirements, enforced here structurally:
    evidence_trusted is never set True by this function, and a
    repository/commit mismatch is refused outright rather than stored."""
    job = _get_job_for_developer_or_404(db, job_id, developer_user_id)
    if job.credential_expires_at is not None and _as_aware(job.credential_expires_at) <= _now():
        job.status = BridgeJobAssignmentStatus.CREDENTIAL_EXPIRED
        db.flush()
        raise BridgeError(f"Job {job_id}'s credential has expired; evidence refused.")
    if (
        job.repository_remote_url != claimed_repository_remote_url
        or job.repository_base_commit_sha != claimed_repository_base_commit_sha
    ):
        raise BridgeJobWrongRepositoryError(
            f"Evidence for job {job_id} claims a different repository/commit than was assigned."
        )
    job.evidence = evidence
    job.evidence_trusted = False
    job.status = BridgeJobAssignmentStatus.EVIDENCE_UPLOADED
    record_audit_log(
        db, action="bridge_job.evidence_uploaded", entity_type="BridgeJobAssignment", entity_id=job.id,
        actor_user_id=developer_user_id, extra_data={"evidence_trusted": False},
    )
    db.flush()
    return job


def get_job_for_developer(db: Session, *, job_id: uuid.UUID, developer_user_id: uuid.UUID) -> BridgeJobAssignment:
    return _get_job_for_developer_or_404(db, job_id, developer_user_id)


def _get_job_for_developer_or_404(db: Session, job_id: uuid.UUID, developer_user_id: uuid.UUID) -> BridgeJobAssignment:
    job = db.get(BridgeJobAssignment, job_id)
    if job is None or job.developer_user_id != developer_user_id:
        raise BridgeJobNotFoundError(f"No job {job_id} assigned to developer {developer_user_id}.")
    return job
