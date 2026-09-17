"""Tests for the Phase 03 coding-runtime gate — "a coding runtime cannot
start without an approved active profile" — wired into
start_implementation_run behind Settings.
REQUIRE_EXECUTION_PROFILE_FOR_CODING_RUNTIME (default False; see
app/core/config.py and app/api/routes/implementation_runs.py).

Mirrors test_implementation_runs.py's own _chain/_add_repository setup
pattern rather than importing its private helpers cross-file.
"""

from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException

from app.api.routes import implementation_runs as implementation_runs_route
from app.api.routes.implementation_runs import start_implementation_run
from app.core.config import Settings
from app.models import (
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    Repository,
    RepositoryFileEntryType,
    RepositoryFileIndex,
    RepositorySnapshot,
    User,
    UserRole,
    WorkflowStatus,
)
from app.schemas.implementation_run import StartImplementationRunRequest
from app.services import implementation_agent
from app.services.execution_profile_service import ProjectExecutionProfileService
from tests.conftest import make_approved_artifact, make_implementation_task, make_node

SAMPLE_LLD = "## Password Reset Endpoint\n\nAdd a POST /auth/password-reset endpoint.\n"


def _settings(**overrides) -> SimpleNamespace:
    base = Settings().model_dump()
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(implementation_agent, "get_active_provider", lambda: "mock")


def _developer(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(user)
    db.flush()
    return user


def _product_owner(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="PO", role=UserRole.PRODUCT_OWNER)
    db.add(user)
    db.flush()
    return user


def _chain(db, project, actor):
    lld = make_node(db, project, node_key="lld", order_index=0, output_artifact_type="lld_document")
    story_crafting = make_node(db, project, node_key="story_crafting", order_index=1, output_artifact_type="story_backlog")
    planning = make_node(
        db, project, node_key="implementation_planning", order_index=2,
        required_inputs=["lld_document", "story_backlog"], output_artifact_type="implementation_plan",
    )
    implementation = make_node(
        db, project, node_key="implementation", order_index=3,
        required_inputs=["lld_document", "story_backlog", "implementation_plan"], output_artifact_type="code_change",
        requires_human_approval=False, status=WorkflowStatus.READY,
    )
    make_approved_artifact(db, project, lld, actor, content=SAMPLE_LLD)
    make_approved_artifact(db, project, story_crafting, actor, content="## Story: Password Reset Request\n\n**User Story:** As a user...\n")
    plan_artifact = make_approved_artifact(db, project, planning, actor, content="# Implementation Plan\n")
    task = make_implementation_task(
        db, project, planning, plan_artifact,
        linked_story="Password Reset Request", linked_lld_section="Password Reset Endpoint",
        expected_paths=["apps/api/app/api/routes/auth.py"],
    )
    return implementation, task


def _add_repository(db, project):
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
    snapshot = RepositorySnapshot(repository=repository, ref="main", commit_sha="abc123", file_count=1, truncated=False)
    db.add(snapshot)
    db.flush()
    db.add(RepositoryFileIndex(snapshot=snapshot, path="apps/api/app/api/routes/auth.py", entry_type=RepositoryFileEntryType.FILE, size=200, sha="deadbeef"))
    db.flush()
    return repository


def test_flag_defaults_to_false_and_existing_behavior_is_unblocked(db, project, actor):
    """The strangler-migration guarantee: with the flag at its default
    (False), an implementation run starts exactly as it always did, even
    though this project has NO ProjectExecutionProfile at all."""
    implementation, task = _chain(db, project, actor)
    _add_repository(db, project)
    developer = _developer(db)

    result = start_implementation_run(
        StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=developer.id), db,
    )

    assert result.status.value == "COMPLETED"


def test_flag_on_blocks_when_no_active_profile_exists(db, project, actor, monkeypatch):
    monkeypatch.setattr(implementation_runs_route, "get_settings", lambda: _settings(REQUIRE_EXECUTION_PROFILE_FOR_CODING_RUNTIME=True))
    implementation, task = _chain(db, project, actor)
    _add_repository(db, project)
    developer = _developer(db)

    with pytest.raises(HTTPException) as exc_info:
        start_implementation_run(
            StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=developer.id), db,
        )
    assert exc_info.value.status_code == 409
    assert "execution profile" in exc_info.value.detail.lower() or "ProjectExecutionProfile" in exc_info.value.detail


def test_flag_on_allows_once_a_profile_is_approved_and_active(db, project, actor, monkeypatch):
    monkeypatch.setattr(implementation_runs_route, "get_settings", lambda: _settings(REQUIRE_EXECUTION_PROFILE_FOR_CODING_RUNTIME=True))
    implementation, task = _chain(db, project, actor)
    _add_repository(db, project)
    developer = _developer(db)
    po = _product_owner(db)

    service = ProjectExecutionProfileService(db)
    profile = service.propose_from_template(project=project, template_key="python-fastapi-postgres", triggered_by=developer)
    db.flush()
    service.approve(profile=profile, approved_by=po)
    db.flush()

    result = start_implementation_run(
        StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=developer.id), db,
    )

    assert result.status.value == "COMPLETED"
