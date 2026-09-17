"""Tests for the ProjectExecutionProfile HTTP-layer functions — see
app/api/routes/execution_profiles.py. Direct calls into the real route
function, same convention as test_implementation_runs.py (no TestClient
exists in this repo).
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.execution_profiles import (
    approve_execution_profile,
    get_active_execution_profile,
    get_execution_profile,
    list_execution_profile_templates,
    list_execution_profiles,
    propose_execution_profile_from_template,
    reject_execution_profile,
)
from app.models import ProjectExecutionProfileStatus, User, UserRole
from app.schemas.project_execution_profile import (
    ApproveExecutionProfileRequest,
    ProposeFromTemplateRequest,
    RejectExecutionProfileRequest,
)


def _user(db, role: UserRole) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name=role.value, role=role)
    db.add(user)
    db.flush()
    return user


# --- Template catalog --------------------------------------------------------------------


def test_list_templates_returns_the_approved_catalog():
    templates = list_execution_profile_templates()
    keys = {t.template_key for t in templates}
    assert keys == {"python-fastapi-postgres", "node-nextjs-typescript", "python-django-postgres"}


# --- Propose from template ----------------------------------------------------------------


def test_propose_from_template_route_returns_a_pending_profile(db, project, actor):
    result = propose_execution_profile_from_template(
        project.id, ProposeFromTemplateRequest(template_key="python-fastapi-postgres", triggered_by_user_id=actor.id), db,
    )
    assert result.status == ProjectExecutionProfileStatus.PENDING_APPROVAL
    assert result.version == 1


def test_propose_from_template_route_rejects_unknown_template(db, project, actor):
    with pytest.raises(HTTPException) as exc_info:
        propose_execution_profile_from_template(
            project.id, ProposeFromTemplateRequest(template_key="not-real", triggered_by_user_id=actor.id), db,
        )
    assert exc_info.value.status_code == 400


def test_propose_from_template_route_rejects_unknown_project(db, actor):
    with pytest.raises(HTTPException) as exc_info:
        propose_execution_profile_from_template(
            uuid.uuid4(), ProposeFromTemplateRequest(template_key="python-fastapi-postgres", triggered_by_user_id=actor.id), db,
        )
    assert exc_info.value.status_code == 400


def test_propose_from_template_route_rejects_unknown_user(db, project):
    with pytest.raises(HTTPException) as exc_info:
        propose_execution_profile_from_template(
            project.id, ProposeFromTemplateRequest(template_key="python-fastapi-postgres", triggered_by_user_id=uuid.uuid4()), db,
        )
    assert exc_info.value.status_code == 400


# --- Approve / reject: Project Owner (PRODUCT_OWNER) only -------------------------------


def test_approve_route_rejects_a_non_product_owner_role(db, project, actor):
    profile = propose_execution_profile_from_template(
        project.id, ProposeFromTemplateRequest(template_key="python-fastapi-postgres", triggered_by_user_id=actor.id), db,
    )
    developer = _user(db, UserRole.DEVELOPER)

    with pytest.raises(HTTPException) as exc_info:
        approve_execution_profile(profile.id, ApproveExecutionProfileRequest(approved_by_user_id=developer.id), db)
    assert exc_info.value.status_code == 403


def test_approve_route_succeeds_for_product_owner_and_sets_it_active(db, project, actor):
    profile = propose_execution_profile_from_template(
        project.id, ProposeFromTemplateRequest(template_key="python-fastapi-postgres", triggered_by_user_id=actor.id), db,
    )
    po = _user(db, UserRole.PRODUCT_OWNER)

    approved = approve_execution_profile(profile.id, ApproveExecutionProfileRequest(approved_by_user_id=po.id), db)

    assert approved.status == ProjectExecutionProfileStatus.APPROVED
    assert approved.is_active is True

    active = get_active_execution_profile(project.id, db)
    assert active.id == approved.id


def test_approve_route_succeeds_for_admin_bypass(db, project, actor):
    profile = propose_execution_profile_from_template(
        project.id, ProposeFromTemplateRequest(template_key="python-fastapi-postgres", triggered_by_user_id=actor.id), db,
    )
    admin = _user(db, UserRole.ADMIN)

    approved = approve_execution_profile(profile.id, ApproveExecutionProfileRequest(approved_by_user_id=admin.id), db)
    assert approved.status == ProjectExecutionProfileStatus.APPROVED


def test_reject_route_rejects_a_non_product_owner_role(db, project, actor):
    profile = propose_execution_profile_from_template(
        project.id, ProposeFromTemplateRequest(template_key="python-fastapi-postgres", triggered_by_user_id=actor.id), db,
    )
    qa = _user(db, UserRole.QA)

    with pytest.raises(HTTPException) as exc_info:
        reject_execution_profile(profile.id, RejectExecutionProfileRequest(rejected_by_user_id=qa.id, reason="no"), db)
    assert exc_info.value.status_code == 403


def test_reject_route_succeeds_for_product_owner(db, project, actor):
    profile = propose_execution_profile_from_template(
        project.id, ProposeFromTemplateRequest(template_key="python-fastapi-postgres", triggered_by_user_id=actor.id), db,
    )
    po = _user(db, UserRole.PRODUCT_OWNER)

    rejected = reject_execution_profile(profile.id, RejectExecutionProfileRequest(rejected_by_user_id=po.id, reason="Wrong stack."), db)
    assert rejected.status == ProjectExecutionProfileStatus.REJECTED
    assert rejected.rejection_reason == "Wrong stack."


def test_approving_twice_returns_409(db, project, actor):
    profile = propose_execution_profile_from_template(
        project.id, ProposeFromTemplateRequest(template_key="python-fastapi-postgres", triggered_by_user_id=actor.id), db,
    )
    po = _user(db, UserRole.PRODUCT_OWNER)
    approve_execution_profile(profile.id, ApproveExecutionProfileRequest(approved_by_user_id=po.id), db)

    with pytest.raises(HTTPException) as exc_info:
        approve_execution_profile(profile.id, ApproveExecutionProfileRequest(approved_by_user_id=po.id), db)
    assert exc_info.value.status_code == 409


# --- Read paths --------------------------------------------------------------------------


def test_get_active_profile_404s_when_none_approved(db, project, actor):
    propose_execution_profile_from_template(
        project.id, ProposeFromTemplateRequest(template_key="python-fastapi-postgres", triggered_by_user_id=actor.id), db,
    )  # PENDING_APPROVAL, not APPROVED — should not count

    with pytest.raises(HTTPException) as exc_info:
        get_active_execution_profile(project.id, db)
    assert exc_info.value.status_code == 404


def test_list_profiles_returns_full_version_history(db, project, actor):
    propose_execution_profile_from_template(
        project.id, ProposeFromTemplateRequest(template_key="python-fastapi-postgres", triggered_by_user_id=actor.id), db,
    )
    propose_execution_profile_from_template(
        project.id, ProposeFromTemplateRequest(template_key="node-nextjs-typescript", triggered_by_user_id=actor.id), db,
    )

    versions = list_execution_profiles(project.id, db)
    assert [v.version for v in versions] == [2, 1]


def test_get_execution_profile_by_id(db, project, actor):
    profile = propose_execution_profile_from_template(
        project.id, ProposeFromTemplateRequest(template_key="python-fastapi-postgres", triggered_by_user_id=actor.id), db,
    )
    fetched = get_execution_profile(profile.id, db)
    assert fetched.id == profile.id


def test_get_execution_profile_404_for_unknown_id(db):
    with pytest.raises(HTTPException) as exc_info:
        get_execution_profile(uuid.uuid4(), db)
    assert exc_info.value.status_code == 404
