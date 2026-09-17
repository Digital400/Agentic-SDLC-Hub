"""Tests for app/runtime_security/authorization.py — AuthorizationService:
role + project-isolation checks, the fixed human-approval requirement for
sensitive actions, data-classification restriction for external
runtimes, and that every decision is audited."""

import uuid

import pytest

from app.models import AuditLog, AuthenticationMethod, DataClassification, RoleAssignmentScope, RuntimeRole, SensitiveActionKind, User, UserRole
from app.runtime_security.authorization import AuthorizationDeniedError, AuthorizationService
from app.runtime_security.identity import AuthenticatedActor
from app.runtime_security.rbac import RBACService


def _actor_for(db, *, role: RuntimeRole | None = None, project_id=None, scope=RoleAssignmentScope.PROJECT) -> AuthenticatedActor:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="U", role=UserRole.VIEWER)
    db.add(user)
    db.flush()
    if role is not None:
        RBACService(db).grant(user_id=user.id, role=role, scope=scope, project_id=project_id, granted_by_id=None)
        db.flush()
    return AuthenticatedActor(user_id=user.id, email=user.email, auth_method=AuthenticationMethod.OIDC)


def _approver_id(db) -> uuid.UUID:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Approver", role=UserRole.VIEWER)
    db.add(user)
    db.flush()
    return user.id


# --- authorize_action: role + project isolation --------------------------------------------


def test_authorize_action_succeeds_with_the_right_role_on_the_right_project(db, project):
    actor = _actor_for(db, role=RuntimeRole.DEVELOPER, project_id=project.id)
    decision = AuthorizationService(db).authorize_action(actor=actor, project_id=project.id, required_role=RuntimeRole.DEVELOPER, action_name="test.action")
    assert decision.allowed is True


def test_authorize_action_denies_a_non_member_entirely(db, project):
    actor = _actor_for(db)  # no role granted at all
    with pytest.raises(AuthorizationDeniedError, match="no role on project"):
        AuthorizationService(db).authorize_action(actor=actor, project_id=project.id, required_role=RuntimeRole.VIEWER, action_name="test.action")


def test_authorize_action_denies_a_member_lacking_the_specific_role(db, project):
    actor = _actor_for(db, role=RuntimeRole.VIEWER, project_id=project.id)
    with pytest.raises(AuthorizationDeniedError, match="lacks required role"):
        AuthorizationService(db).authorize_action(actor=actor, project_id=project.id, required_role=RuntimeRole.DEVELOPER, action_name="test.action")


def test_authorize_action_every_decision_writes_an_audit_log_entry(db, project):
    actor = _actor_for(db, role=RuntimeRole.DEVELOPER, project_id=project.id)
    AuthorizationService(db).authorize_action(actor=actor, project_id=project.id, required_role=RuntimeRole.DEVELOPER, action_name="test.action")
    db.flush()

    entries = db.query(AuditLog).filter(AuditLog.action == "authorization.test.action").all()
    assert len(entries) == 1
    assert entries[0].extra_data["allowed"] is True
    assert entries[0].extra_data["auth_method"] == "OIDC"


def test_authorize_action_denial_is_also_audited(db, project):
    actor = _actor_for(db)
    with pytest.raises(AuthorizationDeniedError):
        AuthorizationService(db).authorize_action(actor=actor, project_id=project.id, required_role=RuntimeRole.VIEWER, action_name="test.action")
    db.flush()

    entries = db.query(AuditLog).filter(AuditLog.action == "authorization.test.action").all()
    assert len(entries) == 1
    assert entries[0].extra_data["allowed"] is False


# --- authorize_sensitive_action: unconditional human-approval requirement -----------------


@pytest.mark.parametrize("action_kind", list(SensitiveActionKind))
def test_every_sensitive_action_kind_is_denied_without_approval(db, project, action_kind):
    actor = _actor_for(db, role=RuntimeRole.DEVELOPER, project_id=project.id)
    with pytest.raises(AuthorizationDeniedError, match="requires human approval"):
        AuthorizationService(db).authorize_sensitive_action(actor=actor, project_id=project.id, action_kind=action_kind, human_approved_by=None)


@pytest.mark.parametrize("action_kind", list(SensitiveActionKind))
def test_every_sensitive_action_kind_is_allowed_once_approved(db, project, action_kind):
    actor = _actor_for(db, role=RuntimeRole.DEVELOPER, project_id=project.id)
    approver_id = _approver_id(db)
    decision = AuthorizationService(db).authorize_sensitive_action(actor=actor, project_id=project.id, action_kind=action_kind, human_approved_by=approver_id)
    assert decision.allowed is True
    assert decision.requires_human_approval is True


def test_sensitive_action_denial_is_audited_with_the_requires_approval_flag(db, project):
    actor = _actor_for(db, role=RuntimeRole.DEVELOPER, project_id=project.id)
    with pytest.raises(AuthorizationDeniedError):
        AuthorizationService(db).authorize_sensitive_action(actor=actor, project_id=project.id, action_kind=SensitiveActionKind.REPOSITORY_PUSH, human_approved_by=None)
    db.flush()

    entries = db.query(AuditLog).filter(AuditLog.action == "authorization.sensitive_action.repository_push").all()
    assert len(entries) == 1
    assert entries[0].extra_data["requires_human_approval"] is True


def test_sensitive_action_cannot_be_self_approved(db, project):
    """Segregation of duties: the actor cannot pass their own user_id as
    human_approved_by, no matter how privileged their role."""
    actor = _actor_for(db, role=RuntimeRole.ADMIN, project_id=None, scope=RoleAssignmentScope.ORGANIZATION)
    with pytest.raises(AuthorizationDeniedError, match="self-approved"):
        AuthorizationService(db).authorize_sensitive_action(actor=actor, project_id=project.id, action_kind=SensitiveActionKind.REPOSITORY_PUSH, human_approved_by=actor.user_id)


def test_sensitive_action_approval_records_who_approved_in_the_audit_entry(db, project):
    actor = _actor_for(db, role=RuntimeRole.DEVELOPER, project_id=project.id)
    approver_id = _approver_id(db)
    AuthorizationService(db).authorize_sensitive_action(actor=actor, project_id=project.id, action_kind=SensitiveActionKind.PULL_REQUEST_CREATE, human_approved_by=approver_id)
    db.flush()

    entries = db.query(AuditLog).filter(AuditLog.action == "authorization.sensitive_action.pull_request_create").all()
    assert entries[0].extra_data["approved_by"] == str(approver_id)


# --- Data-classification restriction for external runtimes --------------------------------


@pytest.mark.parametrize("classification", [DataClassification.PUBLIC, DataClassification.INTERNAL])
def test_public_and_internal_classifications_are_allowed_on_external_runtimes(db, classification):
    AuthorizationService(db).check_data_classification_for_external_runtime(data_classification=classification, runtime_name="celery-worker-1")


@pytest.mark.parametrize("classification", [DataClassification.CONFIDENTIAL, DataClassification.RESTRICTED])
def test_confidential_and_restricted_classifications_are_denied_on_external_runtimes(db, classification):
    with pytest.raises(AuthorizationDeniedError, match="may not be sent to external runtime"):
        AuthorizationService(db).check_data_classification_for_external_runtime(data_classification=classification, runtime_name="celery-worker-1")
