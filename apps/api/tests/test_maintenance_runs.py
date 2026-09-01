"""Route-level tests for the Maintenance Agent
(app/api/routes/maintenance_runs.py):
  Gate — a run requires an APPROVED deployment_record artifact.
  Repeatability — running twice appends a second ArtifactVersion under
    the same Artifact (the whole point of this being bespoke, not the
    generic one-shot AgentRun path).
  No Review — completing a run never creates a Review; the Artifact goes
    straight to APPROVED (rule: "recommend actions only").
  Permissions — DEVOPS/DEVELOPER (the existing "maintenance" stage edit
    roles) may trigger a run; other roles are rejected.
  Audit logs (requirement 7).

No TestClient exists in this repo — every call is a direct call into the
real route function, mirroring test_test_runs.py's conventions.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.maintenance_runs import start_maintenance_run
from app.models import (
    Artifact,
    ArtifactStatus,
    AuditLog,
    ImplementationRun,
    MaintenanceRun,
    PullRequestLink,
    PullRequestStatus,
    Repository,
    Review,
    TestRun,
    TestRunStatus,
    User,
    UserRole,
    WorkflowNode,
    WorkflowStatus,
)
from app.schemas.maintenance_run import StartMaintenanceRunRequest
from app.services import maintenance_agent
from tests.conftest import make_approved_artifact, make_implementation_task, make_node


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(maintenance_agent, "get_active_provider", lambda: "mock")


def _user(db, role: UserRole) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name=role.value, role=role)
    db.add(user)
    db.flush()
    return user


def _maintenance_node(db, project) -> WorkflowNode:
    return make_node(db, project, node_key="maintenance", order_index=10, output_artifact_type="maintenance_log", requires_human_approval=False)


def _release_node_and_approved_deployment(db, project, actor):
    node = make_node(db, project, node_key="release", order_index=9, output_artifact_type="deployment_record")
    return make_approved_artifact(db, project, node, actor, content="Deployed v1.2.0 to production.")


def _completed_test_run(db, project, task, implementation_run, *, bugs_found, pass_count, fail_count):
    testing_node = make_node(db, project, node_key="testing", order_index=8, output_artifact_type="test_report")
    run = TestRun(
        project_id=project.id, workflow_node_id=testing_node.id, implementation_task_id=task.id,
        implementation_run_id=implementation_run.id, agent_type="UNIT", status=TestRunStatus.COMPLETED,
        bugs_found=bugs_found, pass_count=pass_count, fail_count=fail_count,
    )
    db.add(run)
    db.flush()
    return run


def _implementation_task_and_run(db, project, actor):
    plan_node = make_node(db, project, node_key="implementation_planning", order_index=6, output_artifact_type="implementation_plan")
    plan_artifact = make_approved_artifact(db, project, plan_node, actor)
    task = make_implementation_task(db, project, plan_node, plan_artifact, linked_story="Story A")
    run = ImplementationRun(project_id=project.id, implementation_task=task, agent_type="backend-coding-agent")
    db.add(run)
    db.flush()
    return task, run


def _pr_link(db, project, task, implementation_run) -> PullRequestLink:
    impl_node = make_node(db, project, node_key="implementation", order_index=7, output_artifact_type="code_change", requires_human_approval=False)
    repository = Repository(project_id=project.id, connection_id=None, owner="octocat", name="hello-world", default_branch="main")
    # connection_id is required — build a minimal one.
    from app.models import Integration, IntegrationConnection, IntegrationProvider, IntegrationStatus

    integration = Integration(integration_name="GitHub", provider=IntegrationProvider.GITHUB, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(integration=integration, access_token_encrypted="x", token_last_four="1234", status=IntegrationStatus.CONNECTED)
    db.add(connection)
    db.flush()
    repository.connection_id = connection.id
    db.add(repository)
    db.flush()

    link = PullRequestLink(
        project_id=project.id, workflow_node_id=impl_node.id, implementation_task_id=task.id,
        implementation_run_id=implementation_run.id, repository_id=repository.id, branch_name="agent/x",
        base_branch="main", pr_number=1, pr_url="https://github.com/octocat/hello-world/pull/1",
        status=PullRequestStatus.MERGED, commit_message="Add login fix",
    )
    db.add(link)
    db.flush()
    return link


# --- Gate --------------------------------------------------------------------------------


def test_gate_blocks_without_an_approved_deployment_record(db, project, actor):
    _maintenance_node(db, project)

    with pytest.raises(HTTPException) as exc_info:
        start_maintenance_run(StartMaintenanceRunRequest(project_id=project.id, triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409


def test_gate_400s_when_project_has_no_maintenance_stage(db, project, actor):
    _release_node_and_approved_deployment(db, project, actor)

    with pytest.raises(HTTPException) as exc_info:
        start_maintenance_run(StartMaintenanceRunRequest(project_id=project.id, triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 400


# --- Success path, repeatability, no Review ------------------------------------------------


def test_successful_run_creates_an_approved_artifact_with_no_review(db, project, actor):
    _maintenance_node(db, project)
    _release_node_and_approved_deployment(db, project, actor)

    response = start_maintenance_run(StartMaintenanceRunRequest(project_id=project.id, triggered_by_user_id=actor.id), db)

    assert response.status == "COMPLETED"
    assert response.artifact_id is not None
    artifact = db.get(Artifact, response.artifact_id)
    assert artifact.artifact_type == "maintenance_report"
    assert artifact.status == ArtifactStatus.APPROVED
    assert db.query(Review).filter(Review.artifact_version_id == response.artifact_version_id).count() == 0


def test_running_twice_appends_a_second_version_under_the_same_artifact(db, project, actor):
    _maintenance_node(db, project)
    _release_node_and_approved_deployment(db, project, actor)

    first = start_maintenance_run(StartMaintenanceRunRequest(project_id=project.id, triggered_by_user_id=actor.id), db)
    second = start_maintenance_run(StartMaintenanceRunRequest(project_id=project.id, triggered_by_user_id=actor.id), db)

    assert first.artifact_id == second.artifact_id
    assert second.artifact_version_id != first.artifact_version_id
    assert db.query(MaintenanceRun).filter(MaintenanceRun.project_id == project.id).count() == 2


def test_run_includes_open_bugs_and_pr_history_from_real_data(db, project, actor):
    _maintenance_node(db, project)
    _release_node_and_approved_deployment(db, project, actor)
    task, impl_run = _implementation_task_and_run(db, project, actor)
    _pr_link(db, project, task, impl_run)
    _completed_test_run(db, project, task, impl_run, bugs_found=["Checkout crashes on submit."], pass_count=5, fail_count=1)

    response = start_maintenance_run(StartMaintenanceRunRequest(project_id=project.id, triggered_by_user_id=actor.id), db)

    assert "Checkout crashes on submit." in response.report_markdown
    assert "PR #1" in response.report_markdown


def test_optional_error_logs_and_feedback_are_stored_and_used(db, project, actor):
    _maintenance_node(db, project)
    _release_node_and_approved_deployment(db, project, actor)

    response = start_maintenance_run(
        StartMaintenanceRunRequest(
            project_id=project.id, triggered_by_user_id=actor.id,
            error_logs="500 error at /checkout", user_feedback="Users report slow checkout.",
        ),
        db,
    )

    assert response.error_logs_input == "500 error at /checkout"
    assert response.user_feedback_input == "Users report slow checkout."


# --- Permissions -----------------------------------------------------------------------


def test_devops_may_trigger_a_maintenance_run(db, project, actor):
    _maintenance_node(db, project)
    _release_node_and_approved_deployment(db, project, actor)
    devops = _user(db, UserRole.DEVOPS)

    response = start_maintenance_run(StartMaintenanceRunRequest(project_id=project.id, triggered_by_user_id=devops.id), db)
    assert response.status == "COMPLETED"


def test_qa_may_not_trigger_a_maintenance_run(db, project, actor):
    _maintenance_node(db, project)
    _release_node_and_approved_deployment(db, project, actor)
    qa = _user(db, UserRole.QA)

    with pytest.raises(HTTPException) as exc_info:
        start_maintenance_run(StartMaintenanceRunRequest(project_id=project.id, triggered_by_user_id=qa.id), db)
    assert exc_info.value.status_code == 403


# --- Audit logs (requirement 7) ---------------------------------------------------------


def test_audit_logs_recorded_for_start_and_completion(db, project, actor):
    _maintenance_node(db, project)
    _release_node_and_approved_deployment(db, project, actor)

    start_maintenance_run(StartMaintenanceRunRequest(project_id=project.id, triggered_by_user_id=actor.id), db)

    actions = [row.action for row in db.query(AuditLog).filter(AuditLog.project_id == project.id).all()]
    assert "maintenance_run.started" in actions
    assert "maintenance_run.completed" in actions
    assert "artifact.created" in actions
    assert "artifact_version.created" in actions


def test_failure_marks_run_failed_and_does_not_touch_any_artifact(db, project, actor, monkeypatch):
    _maintenance_node(db, project)
    _release_node_and_approved_deployment(db, project, actor)

    import app.api.routes.maintenance_runs as maintenance_routes

    def _boom(**kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(maintenance_routes, "run_maintenance_agent", _boom)

    response = start_maintenance_run(StartMaintenanceRunRequest(project_id=project.id, triggered_by_user_id=actor.id), db)

    assert response.status == "FAILED"
    assert response.artifact_id is None
    actions = [row.action for row in db.query(AuditLog).filter(AuditLog.project_id == project.id).all()]
    assert "maintenance_run.failed" in actions
