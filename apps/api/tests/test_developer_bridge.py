"""Phase 13: Developer Bridge server-side logic.

Follows this codebase's established test convention (direct calls into
real service/route functions, no TestClient — see
test_implementation_runs.py's own module docstring).

Covers:
  1. Device authorization flow: request -> approve -> poll -> resolve token.
  2. Job assignment lifecycle: assign -> accept/reject -> upload evidence.
  3. "Reject evidence for the wrong repository or commit."
  4. "Treat local evidence as untrusted until CI verification" —
     evidence_trusted is never True.
  5. Every mutation is audited.
  6. The DEVELOPER_BRIDGE_ENABLED route-level gate.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes import developer_bridge as routes
from app.api.routes.developer_bridge import (
    DeviceApproveRequest,
    DeviceAuthorizeRequest,
    DeviceTokenRequest,
    RejectJobRequest,
    StatusReportRequest,
    UploadEvidenceRequest,
)
from app.core.config import get_settings
from app.models import (
    AuditLog,
    BridgeDeviceAuthorizationStatus,
    BridgeJobAssignmentStatus,
    BridgeSessionStatus,
    User,
    UserRole,
    WorkflowStatus,
)
from app.services import developer_bridge as service
from tests.conftest import make_approved_artifact, make_implementation_task, make_node


@pytest.fixture(autouse=True)
def _enable_bridge(monkeypatch):
    monkeypatch.setattr(get_settings(), "DEVELOPER_BRIDGE_ENABLED", True, raising=False)


def _developer(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(user)
    db.flush()
    return user


def _task(db, project, actor):
    node = make_node(
        db, project, node_key="implementation", order_index=0, output_artifact_type="code_change",
        requires_human_approval=False, status=WorkflowStatus.READY,
    )
    artifact = make_approved_artifact(db, project, node, actor, content="# Plan\n")
    return make_implementation_task(db, project, node, artifact)


def _approved_auth(db, developer):
    auth = service.create_device_authorization(db, client_id="bridge-cli")
    service.approve_device_authorization(db, user_code=auth.user_code, approved_by_user_id=developer.id)
    db.commit()
    return auth


class TestDeviceAuthorizationFlow:
    def test_full_flow_request_approve_poll(self, db, actor):
        auth = service.create_device_authorization(db, client_id="bridge-cli")
        db.commit()
        assert auth.status == BridgeDeviceAuthorizationStatus.PENDING

        polled = service.poll_device_token(db, device_code=auth.device_code)
        assert polled is not None and polled.access_token is None  # still pending

        service.approve_device_authorization(db, user_code=auth.user_code, approved_by_user_id=actor.id)
        db.commit()

        polled = service.poll_device_token(db, device_code=auth.device_code)
        assert polled.status == BridgeDeviceAuthorizationStatus.APPROVED
        assert polled.access_token is not None

    def test_resolve_access_token_returns_the_approving_developer(self, db, actor):
        auth = _approved_auth(db, actor)
        resolved = service.resolve_access_token(db, access_token=auth.access_token)
        assert resolved.id == actor.id

    def test_resolve_access_token_rejects_unknown_token(self, db):
        with pytest.raises(service.BridgeAccessTokenInvalidError):
            service.resolve_access_token(db, access_token="not-a-real-token")

    def test_approve_unknown_user_code_raises(self, db, actor):
        with pytest.raises(service.DeviceAuthorizationNotFoundError):
            service.approve_device_authorization(db, user_code="NOPE0000", approved_by_user_id=actor.id)

    def test_route_level_device_authorize_returns_a_pollable_code(self, db):
        response = routes.device_authorize(DeviceAuthorizeRequest(client_id="bridge-cli"), db=db)
        assert response.user_code
        assert response.expires_in == service.DEVICE_CODE_TTL_SECONDS

    def test_routes_return_404_when_bridge_disabled(self, db, monkeypatch):
        monkeypatch.setattr(get_settings(), "DEVELOPER_BRIDGE_ENABLED", False, raising=False)
        with pytest.raises(HTTPException) as exc_info:
            routes.device_authorize(DeviceAuthorizeRequest(client_id="bridge-cli"), db=db)
        assert exc_info.value.status_code == 404


class TestJobAssignmentLifecycle:
    def test_assign_then_fetch_next_job(self, db, project, actor):
        developer = _developer(db)
        task = _task(db, project, actor)
        job = service.assign_job(
            db, implementation_task_id=task.id, developer_user_id=developer.id, runtime_key="acp:my-agent",
            repository_remote_url="https://github.com/acme/widgets.git", repository_branch="main",
            repository_base_commit_sha="abc123",
        )
        db.commit()

        fetched = service.next_assigned_job(db, developer_user_id=developer.id)
        assert fetched is not None and fetched.id == job.id
        assert fetched.status == BridgeJobAssignmentStatus.ASSIGNED

    def test_accept_job_transitions_status_and_audits(self, db, project, actor):
        developer = _developer(db)
        task = _task(db, project, actor)
        job = service.assign_job(
            db, implementation_task_id=task.id, developer_user_id=developer.id, runtime_key="acp:my-agent",
            repository_remote_url="https://github.com/acme/widgets.git", repository_branch="main",
            repository_base_commit_sha="abc123",
        )
        db.commit()

        service.accept_job(db, job_id=job.id, developer_user_id=developer.id)
        db.commit()
        db.refresh(job)
        assert job.status == BridgeJobAssignmentStatus.ACCEPTED

        actions = db.query(AuditLog).filter(AuditLog.entity_id == job.id).all()
        assert any(a.action == "bridge_job.accepted" for a in actions)

    def test_reject_job_records_reason(self, db, project, actor):
        developer = _developer(db)
        task = _task(db, project, actor)
        job = service.assign_job(
            db, implementation_task_id=task.id, developer_user_id=developer.id, runtime_key="acp:my-agent",
            repository_remote_url="https://github.com/acme/widgets.git", repository_branch="main",
            repository_base_commit_sha="abc123",
        )
        db.commit()

        service.reject_job(db, job_id=job.id, developer_user_id=developer.id, reason="wrong runtime")
        db.commit()
        db.refresh(job)
        assert job.status == BridgeJobAssignmentStatus.REJECTED
        assert job.rejected_reason == "wrong runtime"

    def test_a_job_cannot_be_accepted_by_a_different_developer(self, db, project, actor):
        developer = _developer(db)
        other_developer = _developer(db)
        task = _task(db, project, actor)
        job = service.assign_job(
            db, implementation_task_id=task.id, developer_user_id=developer.id, runtime_key="acp:my-agent",
            repository_remote_url="https://github.com/acme/widgets.git", repository_branch="main",
            repository_base_commit_sha="abc123",
        )
        db.commit()

        with pytest.raises(service.BridgeJobNotFoundError):
            service.accept_job(db, job_id=job.id, developer_user_id=other_developer.id)


class TestEvidenceUpload:
    def _assigned_job(self, db, project, actor, developer):
        task = _task(db, project, actor)
        job = service.assign_job(
            db, implementation_task_id=task.id, developer_user_id=developer.id, runtime_key="acp:my-agent",
            repository_remote_url="https://github.com/acme/widgets.git", repository_branch="main",
            repository_base_commit_sha="abc123",
        )
        db.commit()
        return job

    def test_upload_evidence_marks_it_untrusted(self, db, project, actor):
        developer = _developer(db)
        job = self._assigned_job(db, project, actor, developer)

        service.upload_evidence(
            db, job_id=job.id, developer_user_id=developer.id,
            evidence={"events": [], "patch_hash": "deadbeef", "test_results": [], "usage": {}},
            claimed_repository_remote_url="https://github.com/acme/widgets.git",
            claimed_repository_base_commit_sha="abc123",
        )
        db.commit()
        db.refresh(job)
        assert job.status == BridgeJobAssignmentStatus.EVIDENCE_UPLOADED
        assert job.evidence_trusted is False  # never set True by this function

    def test_upload_evidence_refuses_a_mismatched_repository(self, db, project, actor):
        developer = _developer(db)
        job = self._assigned_job(db, project, actor, developer)

        with pytest.raises(service.BridgeJobWrongRepositoryError):
            service.upload_evidence(
                db, job_id=job.id, developer_user_id=developer.id,
                evidence={"events": [], "patch_hash": None, "test_results": [], "usage": {}},
                claimed_repository_remote_url="https://github.com/someone-else/widgets.git",
                claimed_repository_base_commit_sha="abc123",
            )

    def test_upload_evidence_refuses_a_mismatched_commit(self, db, project, actor):
        developer = _developer(db)
        job = self._assigned_job(db, project, actor, developer)

        with pytest.raises(service.BridgeJobWrongRepositoryError):
            service.upload_evidence(
                db, job_id=job.id, developer_user_id=developer.id,
                evidence={"events": [], "patch_hash": None, "test_results": [], "usage": {}},
                claimed_repository_remote_url="https://github.com/acme/widgets.git",
                claimed_repository_base_commit_sha="wrong-sha",
            )

    def test_route_level_upload_evidence_reports_untrusted(self, db, project, actor):
        developer = _developer(db)
        job = self._assigned_job(db, project, actor, developer)
        auth = _approved_auth(db, developer)

        result = routes.upload_evidence(
            job.id,
            UploadEvidenceRequest(
                events=[], patch_hash="deadbeef", test_results=[], usage={},
                repository_remote_url="https://github.com/acme/widgets.git", repository_base_commit_sha="abc123",
            ),
            authorization=f"Bearer {auth.access_token}",
            db=db,
        )
        assert result == {"uploaded": True, "evidence_trusted": False}


class TestBridgeSessionStatus:
    def test_report_status_creates_then_updates_a_session(self, db, actor):
        developer = _developer(db)
        session = service.report_status(db, developer_user_id=developer.id, status=BridgeSessionStatus.CONNECTED)
        db.commit()
        assert session.status == BridgeSessionStatus.CONNECTED

        session = service.report_status(db, developer_user_id=developer.id, status=BridgeSessionStatus.OFFLINE)
        db.commit()
        assert session.status == BridgeSessionStatus.OFFLINE

    def test_route_level_status_report_requires_bearer_token(self, db):
        with pytest.raises(HTTPException) as exc_info:
            routes.report_status(StatusReportRequest(status="connected"), authorization=None, db=db)
        assert exc_info.value.status_code == 401
