"""Tests for ProjectExecutionProfileService — propose (template + detection),
approve/reject, versioning, and the coding-runtime gate. See
app/services/execution_profile_service.py.
"""

import uuid

import pytest

from app.models import (
    AuditLog,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    ProjectExecutionProfileStatus,
    ProjectExecutionProfileType,
    Repository,
    RepositoryFileIndex,
    RepositorySnapshot,
    User,
    UserRole,
)
from app.services.execution_profile_service import (
    CodingRuntimeBlockedError,
    ExecutionProfileError,
    ProjectExecutionProfileService,
    require_active_profile_for_runtime_start,
)
from app.services.execution_profile_templates import ExecutionProfileTemplateError


def _product_owner(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="PO", role=UserRole.PRODUCT_OWNER)
    db.add(user)
    db.flush()
    return user


def _repo_with_snapshot(db, project, *, files=()):
    integration = Integration(integration_name="GitHub", provider=IntegrationProvider.GITHUB, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(
        integration=integration, access_token_encrypted="not-a-real-fernet-token",
        token_last_four="7890", github_username="octocat", status=IntegrationStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    repository = Repository(project=project, connection=connection, owner="octocat", name="hello-world", default_branch="main")
    db.add(repository)
    db.flush()
    snapshot = RepositorySnapshot(repository=repository, ref="main", commit_sha="abc123", file_count=len(files), truncated=False)
    db.add(snapshot)
    db.flush()
    for path, size in files:
        from app.models import RepositoryFileEntryType

        db.add(RepositoryFileIndex(snapshot=snapshot, path=path, entry_type=RepositoryFileEntryType.FILE, size=size, sha="deadbeef"))
    db.flush()
    db.refresh(snapshot)
    return repository, snapshot


# --- propose_from_template (NEW_PROJECT) ------------------------------------------------


def test_propose_from_template_creates_version_1_pending_approval(db, project, actor):
    profile = ProjectExecutionProfileService(db).propose_from_template(
        project=project, template_key="python-fastapi-postgres", triggered_by=actor,
    )
    db.flush()

    assert profile.version == 1
    assert profile.status == ProjectExecutionProfileStatus.PENDING_APPROVAL
    assert profile.is_active is False
    assert profile.profile_type == ProjectExecutionProfileType.NEW_PROJECT
    assert profile.template_key == "python-fastapi-postgres"
    assert profile.unit_test_command == "pytest -q"
    assert profile.data_classification is not None


def test_propose_from_template_rejects_an_unapproved_template_key(db, project, actor):
    with pytest.raises(ExecutionProfileTemplateError):
        ProjectExecutionProfileService(db).propose_from_template(
            project=project, template_key="not-a-real-template", triggered_by=actor,
        )


def test_propose_from_template_writes_an_audit_log_entry(db, project, actor):
    profile = ProjectExecutionProfileService(db).propose_from_template(
        project=project, template_key="node-nextjs-typescript", triggered_by=actor,
    )
    db.flush()

    entries = db.query(AuditLog).filter(AuditLog.entity_id == profile.id, AuditLog.action == "project_execution_profile.proposed").all()
    assert len(entries) == 1
    assert entries[0].extra_data["source"] == "TEMPLATE"


def test_second_proposal_increments_version(db, project, actor):
    service = ProjectExecutionProfileService(db)
    first = service.propose_from_template(project=project, template_key="python-fastapi-postgres", triggered_by=actor)
    db.flush()
    second = service.propose_from_template(project=project, template_key="node-nextjs-typescript", triggered_by=actor)
    db.flush()

    assert first.version == 1
    assert second.version == 2


# --- propose_from_detection (EXISTING_REPOSITORY) ----------------------------------------


def test_propose_from_detection_ties_profile_to_repository_and_snapshot(db, project, actor):
    _repository, snapshot = _repo_with_snapshot(db, project, files=[("requirements.txt", 50)])

    profile = ProjectExecutionProfileService(db).propose_from_detection(
        project=project, snapshot=snapshot, triggered_by=actor, github_token=None,
    )
    db.flush()

    assert profile.profile_type == ProjectExecutionProfileType.EXISTING_REPOSITORY
    assert profile.repository_snapshot_id == snapshot.id
    assert "Python" in profile.detected_languages
    assert profile.status == ProjectExecutionProfileStatus.PENDING_APPROVAL


def test_propose_from_detection_rejects_a_snapshot_from_another_project(db, project, actor):
    _repository, snapshot = _repo_with_snapshot(db, project, files=[])
    from app.models import Project, ProjectStatus

    unrelated_project = Project(
        name="Other Project", business_owner="Someone", workflow_template_id="sdlc-workflow",
        workflow_template_version="test", current_stage="node_a", status=ProjectStatus.ACTIVE, created_by_id=actor.id,
    )
    db.add(unrelated_project)
    db.flush()

    with pytest.raises(ExecutionProfileError):
        ProjectExecutionProfileService(db).propose_from_detection(
            project=unrelated_project, snapshot=snapshot, triggered_by=actor, github_token=None,
        )


# --- approve / reject --------------------------------------------------------------------


def test_approve_sets_approved_and_active(db, project, actor):
    po = _product_owner(db)
    profile = ProjectExecutionProfileService(db).propose_from_template(project=project, template_key="python-fastapi-postgres", triggered_by=actor)
    db.flush()

    ProjectExecutionProfileService(db).approve(profile=profile, approved_by=po)
    db.flush()

    assert profile.status == ProjectExecutionProfileStatus.APPROVED
    assert profile.is_active is True
    assert profile.approved_by_id == po.id
    assert profile.approved_at is not None


def test_approve_supersedes_the_previously_active_version(db, project, actor):
    po = _product_owner(db)
    service = ProjectExecutionProfileService(db)

    v1 = service.propose_from_template(project=project, template_key="python-fastapi-postgres", triggered_by=actor)
    db.flush()
    service.approve(profile=v1, approved_by=po)
    db.flush()

    v2 = service.propose_from_template(project=project, template_key="node-nextjs-typescript", triggered_by=actor)
    db.flush()
    service.approve(profile=v2, approved_by=po)
    db.flush()

    assert v1.status == ProjectExecutionProfileStatus.SUPERSEDED
    assert v1.is_active is False
    assert v2.status == ProjectExecutionProfileStatus.APPROVED
    assert v2.is_active is True
    assert service.get_active_profile(project.id).id == v2.id


def test_approving_an_already_approved_profile_raises(db, project, actor):
    po = _product_owner(db)
    service = ProjectExecutionProfileService(db)
    profile = service.propose_from_template(project=project, template_key="python-fastapi-postgres", triggered_by=actor)
    db.flush()
    service.approve(profile=profile, approved_by=po)
    db.flush()

    with pytest.raises(ExecutionProfileError):
        service.approve(profile=profile, approved_by=po)


def test_reject_sets_rejected_with_reason(db, project, actor):
    po = _product_owner(db)
    service = ProjectExecutionProfileService(db)
    profile = service.propose_from_template(project=project, template_key="python-fastapi-postgres", triggered_by=actor)
    db.flush()

    service.reject(profile=profile, rejected_by=po, reason="Wrong stack for this project.")
    db.flush()

    assert profile.status == ProjectExecutionProfileStatus.REJECTED
    assert profile.is_active is False
    assert profile.rejection_reason == "Wrong stack for this project."


def test_rejecting_an_already_rejected_profile_raises(db, project, actor):
    po = _product_owner(db)
    service = ProjectExecutionProfileService(db)
    profile = service.propose_from_template(project=project, template_key="python-fastapi-postgres", triggered_by=actor)
    db.flush()
    service.reject(profile=profile, rejected_by=po, reason="No.")
    db.flush()

    with pytest.raises(ExecutionProfileError):
        service.reject(profile=profile, rejected_by=po, reason="No, again.")


# --- list_versions / audit history --------------------------------------------------------


def test_list_versions_returns_full_history_newest_first(db, project, actor):
    po = _product_owner(db)
    service = ProjectExecutionProfileService(db)
    v1 = service.propose_from_template(project=project, template_key="python-fastapi-postgres", triggered_by=actor)
    db.flush()
    service.approve(profile=v1, approved_by=po)
    db.flush()
    v2 = service.propose_from_template(project=project, template_key="node-nextjs-typescript", triggered_by=actor)
    db.flush()

    versions = service.list_versions(project.id)

    assert [v.version for v in versions] == [2, 1]
    assert versions[1].status == ProjectExecutionProfileStatus.APPROVED  # v1, now approved
    assert versions[0].status == ProjectExecutionProfileStatus.PENDING_APPROVAL  # v2


# --- The coding-runtime gate --------------------------------------------------------------


def test_gate_blocks_when_no_active_profile_exists(db, project):
    with pytest.raises(CodingRuntimeBlockedError):
        require_active_profile_for_runtime_start(db, project.id)


def test_gate_passes_once_a_profile_is_approved_and_active(db, project, actor):
    po = _product_owner(db)
    service = ProjectExecutionProfileService(db)
    profile = service.propose_from_template(project=project, template_key="python-fastapi-postgres", triggered_by=actor)
    db.flush()
    service.approve(profile=profile, approved_by=po)
    db.flush()

    result = require_active_profile_for_runtime_start(db, project.id)

    assert result.id == profile.id


def test_gate_blocks_again_after_the_only_approved_profile_is_superseded_by_nothing(db, project, actor):
    """A rejected profile never satisfies the gate — only APPROVED +
    is_active does."""
    po = _product_owner(db)
    service = ProjectExecutionProfileService(db)
    profile = service.propose_from_template(project=project, template_key="python-fastapi-postgres", triggered_by=actor)
    db.flush()
    service.reject(profile=profile, rejected_by=po, reason="No.")
    db.flush()

    with pytest.raises(CodingRuntimeBlockedError):
        require_active_profile_for_runtime_start(db, project.id)
