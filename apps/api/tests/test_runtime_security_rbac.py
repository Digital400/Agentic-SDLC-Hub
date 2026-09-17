"""Tests for app/runtime_security/rbac.py — RBACService, with a strong
focus on project isolation (the mechanism this phase's "project isolation
... tests" requirement is centrally about)."""

import uuid

import pytest

from app.models import RoleAssignmentScope, RuntimeRole, User, UserRole
from app.runtime_security.rbac import RBACService


def _user(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="U", role=UserRole.VIEWER)
    db.add(user)
    db.flush()
    return user


def _second_project(db, actor):
    from app.models import Project, ProjectStatus

    project = Project(
        name="Project B", business_owner="Owner", workflow_template_id="sdlc-workflow", workflow_template_version="test",
        current_stage="node_a", status=ProjectStatus.ACTIVE, created_by_id=actor.id,
    )
    db.add(project)
    db.flush()
    return project


# --- Grant / revoke --------------------------------------------------------------------


def test_grant_organization_scope_requires_no_project_id(db, project):
    user = _user(db)
    rbac = RBACService(db)
    assignment = rbac.grant(user_id=user.id, role=RuntimeRole.DEVELOPER, scope=RoleAssignmentScope.ORGANIZATION, project_id=None, granted_by_id=None)
    assert assignment.project_id is None


def test_grant_organization_scope_rejects_a_project_id(db, project):
    user = _user(db)
    rbac = RBACService(db)
    with pytest.raises(ValueError):
        rbac.grant(user_id=user.id, role=RuntimeRole.DEVELOPER, scope=RoleAssignmentScope.ORGANIZATION, project_id=project.id, granted_by_id=None)


def test_grant_project_scope_requires_a_project_id(db, project):
    user = _user(db)
    rbac = RBACService(db)
    with pytest.raises(ValueError):
        rbac.grant(user_id=user.id, role=RuntimeRole.DEVELOPER, scope=RoleAssignmentScope.PROJECT, project_id=None, granted_by_id=None)


def test_revoke_removes_the_assignment(db, project):
    user = _user(db)
    rbac = RBACService(db)
    assignment = rbac.grant(user_id=user.id, role=RuntimeRole.DEVELOPER, scope=RoleAssignmentScope.PROJECT, project_id=project.id, granted_by_id=None)
    db.flush()
    rbac.revoke(assignment)
    db.flush()
    assert not rbac.has_role(user.id, RuntimeRole.DEVELOPER, project_id=project.id)


# --- roles_for / has_role -----------------------------------------------------------------


def test_organization_scoped_role_applies_to_every_project(db, project, actor):
    user = _user(db)
    other_project = _second_project(db, actor)
    rbac = RBACService(db)
    rbac.grant(user_id=user.id, role=RuntimeRole.QA, scope=RoleAssignmentScope.ORGANIZATION, project_id=None, granted_by_id=None)
    db.flush()

    assert rbac.has_role(user.id, RuntimeRole.QA, project_id=project.id)
    assert rbac.has_role(user.id, RuntimeRole.QA, project_id=other_project.id)


def test_project_scoped_role_does_not_apply_to_a_different_project(db, project, actor):
    """THE project isolation invariant: a role granted for project A
    confers nothing on project B."""
    user = _user(db)
    other_project = _second_project(db, actor)
    rbac = RBACService(db)
    rbac.grant(user_id=user.id, role=RuntimeRole.DEVELOPER, scope=RoleAssignmentScope.PROJECT, project_id=project.id, granted_by_id=None)
    db.flush()

    assert rbac.has_role(user.id, RuntimeRole.DEVELOPER, project_id=project.id)
    assert not rbac.has_role(user.id, RuntimeRole.DEVELOPER, project_id=other_project.id)


def test_admin_role_satisfies_every_other_roles_check(db, project):
    user = _user(db)
    rbac = RBACService(db)
    rbac.grant(user_id=user.id, role=RuntimeRole.ADMIN, scope=RoleAssignmentScope.ORGANIZATION, project_id=None, granted_by_id=None)
    db.flush()

    for role in RuntimeRole:
        assert rbac.has_role(user.id, role, project_id=project.id)


def test_a_role_never_implies_a_different_specific_role(db, project):
    user = _user(db)
    rbac = RBACService(db)
    rbac.grant(user_id=user.id, role=RuntimeRole.REVIEWER, scope=RoleAssignmentScope.PROJECT, project_id=project.id, granted_by_id=None)
    db.flush()

    assert rbac.has_role(user.id, RuntimeRole.REVIEWER, project_id=project.id)
    assert not rbac.has_role(user.id, RuntimeRole.DEVELOPER, project_id=project.id)
    assert not rbac.has_role(user.id, RuntimeRole.PROJECT_OWNER, project_id=project.id)


def test_no_assignment_means_no_role_at_all(db, project):
    user = _user(db)
    rbac = RBACService(db)
    assert rbac.roles_for(user.id, project_id=project.id) == set()
    for role in RuntimeRole:
        assert not rbac.has_role(user.id, role, project_id=project.id)


# --- is_project_member (the actual isolation gate) -----------------------------------------


def test_is_project_member_false_with_no_assignment_at_all(db, project):
    user = _user(db)
    assert RBACService(db).is_project_member(user.id, project.id) is False


def test_is_project_member_true_with_a_project_scoped_assignment(db, project):
    user = _user(db)
    rbac = RBACService(db)
    rbac.grant(user_id=user.id, role=RuntimeRole.VIEWER, scope=RoleAssignmentScope.PROJECT, project_id=project.id, granted_by_id=None)
    db.flush()
    assert rbac.is_project_member(user.id, project.id) is True


def test_is_project_member_true_with_an_organization_scoped_assignment(db, project):
    user = _user(db)
    rbac = RBACService(db)
    rbac.grant(user_id=user.id, role=RuntimeRole.VIEWER, scope=RoleAssignmentScope.ORGANIZATION, project_id=None, granted_by_id=None)
    db.flush()
    assert rbac.is_project_member(user.id, project.id) is True


def test_is_project_member_false_for_a_role_scoped_to_a_different_project(db, project, actor):
    user = _user(db)
    other_project = _second_project(db, actor)
    rbac = RBACService(db)
    rbac.grant(user_id=user.id, role=RuntimeRole.DEVELOPER, scope=RoleAssignmentScope.PROJECT, project_id=other_project.id, granted_by_id=None)
    db.flush()

    assert rbac.is_project_member(user.id, other_project.id) is True
    assert rbac.is_project_member(user.id, project.id) is False
