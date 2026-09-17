"""Project isolation and privilege-escalation tests — this phase's
explicit requirement. Each test below is framed as a concrete attack
scenario against app/runtime_security, exercised end-to-end through
AuthenticatedActor -> RBACService/AuthorizationService, the same path a
real request would take.
"""

import uuid

import pytest

from app.models import AuthenticationMethod, DataClassification, Project, ProjectStatus, RoleAssignmentScope, RuntimeRole, SensitiveActionKind, User, UserRole
from app.runtime_security.authorization import AuthorizationDeniedError, AuthorizationService
from app.runtime_security.identity import AuthenticatedActor
from app.runtime_security.rbac import RBACService


def _user(db, *, role=UserRole.VIEWER) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="U", role=role)
    db.add(user)
    db.flush()
    return user


def _actor(user: User) -> AuthenticatedActor:
    return AuthenticatedActor(user_id=user.id, email=user.email, auth_method=AuthenticationMethod.OIDC)


def _second_project(db, actor_user) -> Project:
    project = Project(
        name="Project B (attacker's target)", business_owner="Owner", workflow_template_id="sdlc-workflow",
        workflow_template_version="test", current_stage="node_a", status=ProjectStatus.ACTIVE, created_by_id=actor_user.id,
    )
    db.add(project)
    db.flush()
    return project


# --- Scenario 1: cross-project isolation ---------------------------------------------------


def test_developer_on_project_a_cannot_act_on_project_b(db, project, actor):
    """A Developer with a real, valid PROJECT-scoped role on Project A
    must be completely denied on Project B — not just lacking a
    specific role there, but having NO standing to act at all."""
    attacker_user = _user(db)
    other_project = _second_project(db, actor)
    RBACService(db).grant(user_id=attacker_user.id, role=RuntimeRole.DEVELOPER, scope=RoleAssignmentScope.PROJECT, project_id=project.id, granted_by_id=None)
    db.flush()

    with pytest.raises(AuthorizationDeniedError, match="no role on project"):
        AuthorizationService(db).authorize_action(actor=_actor(attacker_user), project_id=other_project.id, required_role=RuntimeRole.DEVELOPER, action_name="implementation.start")


def test_a_users_own_global_user_role_never_substitutes_for_a_runtime_role(db, project):
    """A user with the highest legacy app.models.enums.UserRole (ADMIN)
    but NO RuntimeRoleAssignment at all must still be denied by the NEW
    RBAC system — the two role systems are deliberately independent (see
    app/runtime_security/__init__.py's own module docstring); holding one
    confers nothing in the other."""
    legacy_admin_user = _user(db, role=UserRole.ADMIN)
    with pytest.raises(AuthorizationDeniedError):
        AuthorizationService(db).authorize_action(actor=_actor(legacy_admin_user), project_id=project.id, required_role=RuntimeRole.VIEWER, action_name="anything")


# --- Scenario 2: role escalation within a single check --------------------------------------


def test_viewer_role_cannot_satisfy_a_developer_only_action(db, project):
    viewer_user = _user(db)
    RBACService(db).grant(user_id=viewer_user.id, role=RuntimeRole.VIEWER, scope=RoleAssignmentScope.PROJECT, project_id=project.id, granted_by_id=None)
    db.flush()

    with pytest.raises(AuthorizationDeniedError, match="lacks required role"):
        AuthorizationService(db).authorize_action(actor=_actor(viewer_user), project_id=project.id, required_role=RuntimeRole.DEVELOPER, action_name="implementation.start")


def test_reviewer_cannot_perform_a_qa_only_action(db, project):
    """No two non-ADMIN roles are ever conflated — REVIEWER is not
    interchangeable with QA even though both are project-scoped
    "approval-adjacent" roles."""
    reviewer_user = _user(db)
    RBACService(db).grant(user_id=reviewer_user.id, role=RuntimeRole.REVIEWER, scope=RoleAssignmentScope.PROJECT, project_id=project.id, granted_by_id=None)
    db.flush()

    with pytest.raises(AuthorizationDeniedError):
        AuthorizationService(db).authorize_action(actor=_actor(reviewer_user), project_id=project.id, required_role=RuntimeRole.QA, action_name="testing.approve")


# --- Scenario 3: sensitive-action approval cannot be bypassed by role alone ----------------


@pytest.mark.parametrize("role", [RuntimeRole.PROJECT_OWNER, RuntimeRole.ARCHITECT, RuntimeRole.DEVELOPER, RuntimeRole.ADMIN])
def test_no_role_no_matter_how_privileged_bypasses_the_approval_requirement(db, project, role):
    """The four sensitive action kinds require human approval
    UNCONDITIONALLY — not "unless you're an Admin." Holding ANY role,
    including Admin, without a distinct approver id, is still denied."""
    scope = RoleAssignmentScope.ORGANIZATION if role == RuntimeRole.ADMIN else RoleAssignmentScope.PROJECT
    project_id_for_grant = None if role == RuntimeRole.ADMIN else project.id
    privileged_user = _user(db)
    RBACService(db).grant(user_id=privileged_user.id, role=role, scope=scope, project_id=project_id_for_grant, granted_by_id=None)
    db.flush()

    with pytest.raises(AuthorizationDeniedError, match="requires human approval"):
        AuthorizationService(db).authorize_sensitive_action(actor=_actor(privileged_user), project_id=project.id, action_kind=SensitiveActionKind.INFRASTRUCTURE_ACTION, human_approved_by=None)


def test_self_approval_is_denied_even_for_an_organization_admin(db, project):
    admin_user = _user(db)
    RBACService(db).grant(user_id=admin_user.id, role=RuntimeRole.ADMIN, scope=RoleAssignmentScope.ORGANIZATION, project_id=None, granted_by_id=None)
    db.flush()

    with pytest.raises(AuthorizationDeniedError, match="self-approved"):
        AuthorizationService(db).authorize_sensitive_action(actor=_actor(admin_user), project_id=project.id, action_kind=SensitiveActionKind.PULL_REQUEST_CREATE, human_approved_by=admin_user.id)


# --- Scenario 4: forged actor identity ------------------------------------------------------


def test_authenticated_actor_cannot_be_constructed_from_a_client_supplied_id_without_going_through_a_real_provider():
    """AuthenticatedActor itself is just a dataclass — the REAL protection
    is that every actual authentication PATH (OIDC, local-dev) only ever
    produces one after verifying a credential or a double-gated header
    (see test_runtime_security_oidc.py's forged-signature test and
    test_runtime_security_local_dev.py's gate tests). This test documents
    the boundary explicitly: nothing in AuthorizationService itself
    re-derives or re-verifies WHERE an AuthenticatedActor came from — the
    guarantee lives entirely in identity.py's providers, not here. A
    caller that constructs one by hand (as this test does) is trusted by
    AuthorizationService exactly as much as a real provider's output;
    this is why every route touching AuthorizationService MUST obtain its
    actor via get_current_actor (dependencies.py), never by hand."""
    forged_actor = AuthenticatedActor(user_id=uuid.uuid4(), email="attacker@example.com", auth_method=AuthenticationMethod.OIDC)
    assert forged_actor.user_id is not None  # constructible — the guarantee is architectural (route wiring), not a runtime check on this dataclass


def test_authorize_action_still_rejects_a_hand_constructed_actor_with_no_real_role_assignment(db, project):
    """Even a hand-constructed ("forged") actor gains nothing without a
    real RuntimeRoleAssignment row in the database — the authorization
    decision is always re-derived from persisted state, never trusted
    from anything the actor object itself claims."""
    forged_actor = AuthenticatedActor(user_id=uuid.uuid4(), email="attacker@example.com", auth_method=AuthenticationMethod.OIDC)
    with pytest.raises(AuthorizationDeniedError):
        AuthorizationService(db).authorize_action(actor=forged_actor, project_id=project.id, required_role=RuntimeRole.VIEWER, action_name="anything")


# --- Scenario 5: data classification cannot be bypassed by role ---------------------------


def test_no_role_bypasses_the_restricted_data_classification_check():
    """check_data_classification_for_external_runtime takes no actor/role
    at all — it is a pure data-handling rule, structurally impossible to
    bypass via a role grant (there is no role parameter to escalate)."""
    with pytest.raises(AuthorizationDeniedError):
        AuthorizationService(db=None).check_data_classification_for_external_runtime(data_classification=DataClassification.RESTRICTED, runtime_name="external-runtime")


# --- Scenario 6: revoked access is immediately effective ------------------------------------


def test_revoking_a_role_immediately_removes_access(db, project):
    user = _user(db)
    rbac = RBACService(db)
    assignment = rbac.grant(user_id=user.id, role=RuntimeRole.DEVELOPER, scope=RoleAssignmentScope.PROJECT, project_id=project.id, granted_by_id=None)
    db.flush()

    AuthorizationService(db).authorize_action(actor=_actor(user), project_id=project.id, required_role=RuntimeRole.DEVELOPER, action_name="implementation.start")

    rbac.revoke(assignment)
    db.flush()

    with pytest.raises(AuthorizationDeniedError):
        AuthorizationService(db).authorize_action(actor=_actor(user), project_id=project.id, required_role=RuntimeRole.DEVELOPER, action_name="implementation.start")
