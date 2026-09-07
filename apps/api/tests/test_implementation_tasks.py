"""Tests for PATCH /implementation-tasks/{id}/repository — see
app/api/routes/implementation_tasks.py. Assigns which of a project's
(possibly several) connected repositories a task's code changes target;
app/api/routes/implementation_runs.py's `_resolve_repository_for_task` is
what actually reads this field back at run time (covered separately in
test_implementation_runs.py).
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.implementation_tasks import update_task_repository
from app.models import AuditLog, Integration, IntegrationConnection, IntegrationProvider, IntegrationStatus, Project, ProjectStatus, Repository
from app.schemas.implementation_task import UpdateImplementationTaskRepositoryRequest
from tests.conftest import make_approved_artifact, make_implementation_task, make_node


def _other_project(db, actor) -> Project:
    proj = Project(
        name="Other Project", business_owner="Other Owner", workflow_template_id="sdlc-workflow",
        workflow_template_version="test", current_stage="node_a", status=ProjectStatus.ACTIVE, created_by_id=actor.id,
    )
    db.add(proj)
    db.flush()
    return proj


def _repository(db, project, *, owner="octocat", name="hello-world") -> Repository:
    integration = Integration(integration_name="GitHub", provider=IntegrationProvider.GITHUB, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(
        integration=integration, access_token_encrypted="x", token_last_four="1234",
        github_username="octocat", status=IntegrationStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    repository = Repository(project=project, connection=connection, owner=owner, name=name, default_branch="main")
    db.add(repository)
    db.flush()
    return repository


def _task(db, project, actor):
    node = make_node(db, project, node_key="implementation_planning", order_index=0, output_artifact_type="implementation_plan")
    artifact = make_approved_artifact(db, project, node, actor, content="# Plan")
    return make_implementation_task(db, project, node, artifact)


def test_assigns_a_repository_belonging_to_the_same_project(db, project, actor):
    task = _task(db, project, actor)
    repository = _repository(db, project)

    result = update_task_repository(task.id, UpdateImplementationTaskRepositoryRequest(repository_id=repository.id), db)

    assert result.repository_id == repository.id
    assert any(row.action == "implementation_task.repository_assigned" for row in db.query(AuditLog).all())


def test_clearing_repository_id_resets_it_to_null(db, project, actor):
    task = _task(db, project, actor)
    repository = _repository(db, project)
    update_task_repository(task.id, UpdateImplementationTaskRepositoryRequest(repository_id=repository.id), db)

    result = update_task_repository(task.id, UpdateImplementationTaskRepositoryRequest(repository_id=None), db)

    assert result.repository_id is None


def test_rejects_a_repository_from_a_different_project(db, project, actor):
    task = _task(db, project, actor)
    other_project = _other_project(db, actor)
    foreign_repository = _repository(db, other_project)

    with pytest.raises(HTTPException) as exc_info:
        update_task_repository(task.id, UpdateImplementationTaskRepositoryRequest(repository_id=foreign_repository.id), db)
    assert exc_info.value.status_code == 400


def test_rejects_a_nonexistent_repository_id(db, project, actor):
    task = _task(db, project, actor)

    with pytest.raises(HTTPException) as exc_info:
        update_task_repository(task.id, UpdateImplementationTaskRepositoryRequest(repository_id=uuid.uuid4()), db)
    assert exc_info.value.status_code == 400


def test_404_for_a_nonexistent_task(db):
    with pytest.raises(HTTPException) as exc_info:
        update_task_repository(uuid.uuid4(), UpdateImplementationTaskRepositoryRequest(repository_id=None), db)
    assert exc_info.value.status_code == 404
