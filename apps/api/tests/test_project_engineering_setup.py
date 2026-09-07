"""Tests for Project Engineering Setup — see
app/models/project_engineering_setup.py and
app/api/routes/project_engineering_setup.py.

Follows this codebase's established convention: plain functions, direct
calls into the real route functions (no TestClient in this repo), the
db/project/actor fixtures.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.agent_runs import _merge_engineering_setup_context
from app.api.routes.implementation_runs import start_implementation_run
from app.api.routes.jira_integration import push_to_jira
from app.api.routes.project_engineering_setup import create_engineering_setup, get_engineering_setup
from app.models import (
    GithubSetupOption,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    JiraProjectLink,
    JiraSetupOption,
    JiraSourceType,
    User,
    UserRole,
    WorkflowStatus,
)
from app.schemas.implementation_run import StartImplementationRunRequest
from app.schemas.jira_integration import JiraPushRequest, JiraPushSelectionItem
from app.schemas.project_engineering_setup import (
    CodingStandardInput,
    CommandSetupInput,
    CreateEngineeringSetupRequest,
    DocumentationSetupInput,
    GuardrailInput,
    JiraSetupInput,
    RepositorySetupInput,
    TechnologyStackInput,
)
from app.services import implementation_agent
from app.services.graph_engine import GraphValidationResult
from tests.conftest import make_approved_artifact, make_implementation_task, make_node
from tests.test_implementation_runs import _add_repository, _chain

SAMPLE_LLD = "## Password Reset Endpoint\n\nAdd a POST /auth/password-reset endpoint.\n"


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    """Same reasoning as test_implementation_runs.py's own fixture of this
    name: without it, run_implementation_agent's real get_active_provider()
    reads the REAL environment — which may have a real provider key
    configured (as this dev environment currently does) — and every test
    here that reaches start_implementation_run would make a real, slow
    network call instead of exercising the deterministic heuristic path."""
    monkeypatch.setattr(implementation_agent, "get_active_provider", lambda: "mock")


def _full_setup_request(*, created_by_id: uuid.UUID, github_option=GithubSetupOption.CONNECT_EXISTING_REPO, jira_option=JiraSetupOption.CONNECT_EXISTING_PROJECT) -> CreateEngineeringSetupRequest:
    return CreateEngineeringSetupRequest(
        created_by_id=created_by_id,
        technology_stack=TechnologyStackInput(
            application_type="Web Application", primary_language="TypeScript",
            frontend_framework="Next.js", backend_framework="FastAPI", database="PostgreSQL", cloud_provider="AWS",
        ),
        repository=RepositorySetupInput(option=github_option),
        jira=JiraSetupInput(option=jira_option),
        coding_standards=[CodingStandardInput(title="Naming", content="Use camelCase for variables.")],
        guardrails=[GuardrailInput(rule_text="Never touch payment code without human review.")],
        documentation=DocumentationSetupInput(),
        commands=CommandSetupInput(build_command="npm run build", test_commands=["npm test"], lint_command="npm run lint"),
    )


# --- create project with full engineering setup ------------------------------------------


def test_create_engineering_setup_with_full_configuration(db, project, actor):
    result = create_engineering_setup(project.id, _full_setup_request(created_by_id=actor.id), db)

    assert result.application_type == "Web Application"
    assert result.primary_language == "TypeScript"
    assert result.repository_config.option == GithubSetupOption.CONNECT_EXISTING_REPO
    assert result.jira_config.option == JiraSetupOption.CONNECT_EXISTING_PROJECT
    assert len(result.coding_standards) == 1
    assert result.coding_standards[0].title == "Naming"
    assert len(result.guardrails) == 1
    assert result.command_config.build_command == "npm run build"
    assert result.command_config.test_commands == ["npm test"]

    fetched = get_engineering_setup(project.id, db)
    assert fetched is not None
    assert fetched.id == result.id


def test_creating_a_second_setup_for_the_same_project_is_rejected(db, project, actor):
    create_engineering_setup(project.id, _full_setup_request(created_by_id=actor.id), db)

    with pytest.raises(HTTPException) as exc_info:
        create_engineering_setup(project.id, _full_setup_request(created_by_id=actor.id), db)
    assert exc_info.value.status_code == 409


# --- create project with GitHub skipped ---------------------------------------------------


def test_create_engineering_setup_with_github_skipped(db, project, actor):
    payload = _full_setup_request(created_by_id=actor.id, github_option=GithubSetupOption.SKIP_FOR_NOW)
    result = create_engineering_setup(project.id, payload, db)

    assert result.repository_config.option == GithubSetupOption.SKIP_FOR_NOW
    assert result.repository_config.repository_id is None


# --- block Implementation when GitHub is missing (rule 4) ---------------------------------


def _developer(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(user)
    db.flush()
    return user


def test_implementation_is_blocked_when_engineering_setup_skipped_github(db, project, actor):
    create_engineering_setup(project.id, _full_setup_request(created_by_id=actor.id, github_option=GithubSetupOption.SKIP_FOR_NOW), db)
    implementation, task = _chain(db, project, actor)
    del implementation
    _add_repository(db, project)  # a real Repository exists — the SKIP_FOR_NOW *choice* is what should still block this
    dev = _developer(db)

    with pytest.raises(HTTPException) as exc_info:
        start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=dev.id), db)
    assert exc_info.value.status_code == 409
    assert "skipped GitHub" in exc_info.value.detail


def test_implementation_proceeds_when_engineering_setup_connects_github(db, project, actor):
    """A project whose setup chose CONNECT_EXISTING_REPO (not SKIP_FOR_NOW)
    is unaffected by the new gate — the existing "is there a real repo"
    check (already covered by test_implementation_runs.py) is all that
    still applies."""
    create_engineering_setup(project.id, _full_setup_request(created_by_id=actor.id, github_option=GithubSetupOption.CONNECT_EXISTING_REPO), db)
    implementation, task = _chain(db, project, actor)
    del implementation
    _add_repository(db, project)
    dev = _developer(db)

    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=dev.id), db)
    assert run.status.value == "COMPLETED"


def test_implementation_is_ungated_for_a_project_with_no_engineering_setup(db, project, actor):
    """Rule 10 — a project created before this feature existed has no
    ProjectEngineeringSetup row at all, and must behave exactly as before."""
    implementation, task = _chain(db, project, actor)
    del implementation
    _add_repository(db, project)
    dev = _developer(db)

    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=dev.id), db)
    assert run.status.value == "COMPLETED"


# --- block Jira sync when Jira is missing (rule 3) -----------------------------------------


def test_jira_sync_is_blocked_when_engineering_setup_skipped_jira(db, project, actor):
    create_engineering_setup(project.id, _full_setup_request(created_by_id=actor.id, jira_option=JiraSetupOption.SKIP_FOR_NOW), db)

    with pytest.raises(HTTPException) as exc_info:
        push_to_jira(
            JiraPushRequest(
                project_id=project.id, triggered_by_user_id=actor.id,
                selections=[JiraPushSelectionItem(source_type=JiraSourceType.STORY, source_key="Some Story")],
            ),
            db,
        )
    assert exc_info.value.status_code == 409
    assert "skipped Jira" in exc_info.value.detail


def test_jira_sync_proceeds_when_a_real_jira_project_is_connected(db, project, actor):
    create_engineering_setup(project.id, _full_setup_request(created_by_id=actor.id, jira_option=JiraSetupOption.CONNECT_EXISTING_PROJECT), db)
    integration = Integration(integration_name="Jira", provider=IntegrationProvider.JIRA, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(
        integration=integration, access_token_encrypted="not-a-real-fernet-token", token_last_four="7890",
        status=IntegrationStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    db.add(JiraProjectLink(project=project, connection=connection, jira_project_key="PROJ", jira_project_name="My Project"))
    db.flush()

    # Reaches past the engineering-setup gate and the "link exists" 404 —
    # fails later (decrypting a fake token), which is a *different*,
    # unrelated failure than the one this test is checking for.
    with pytest.raises(Exception) as exc_info:
        push_to_jira(
            JiraPushRequest(
                project_id=project.id, triggered_by_user_id=actor.id,
                selections=[JiraPushSelectionItem(source_type=JiraSourceType.STORY, source_key="Some Story")],
            ),
            db,
        )
    assert "skipped Jira" not in str(getattr(exc_info.value, "detail", exc_info.value))


# --- guardrails in agent context (rule 6, generic pipeline) --------------------------------


def test_merge_engineering_setup_context_includes_guardrails_and_standards(db, project, actor):
    create_engineering_setup(project.id, _full_setup_request(created_by_id=actor.id), db)
    validation = GraphValidationResult(can_run=True)

    _merge_engineering_setup_context(db, project, validation)

    assert "project_guardrails" in validation.approved_artifact_content
    assert "Never touch payment code without human review." in validation.approved_artifact_content["project_guardrails"]
    assert "project_coding_standards" in validation.approved_artifact_content
    assert "Naming" in validation.approved_artifact_content["project_coding_standards"]


def test_merge_engineering_setup_context_is_a_no_op_with_no_setup(db, project):
    validation = GraphValidationResult(can_run=True)
    _merge_engineering_setup_context(db, project, validation)
    assert validation.approved_artifact_content == {}


# --- coding standards in implementation agent context (rule 6, bespoke agent) --------------


def test_implementation_agent_includes_project_coding_standards_and_guardrails_in_context(monkeypatch, db, project, actor):
    monkeypatch.setattr(implementation_agent, "get_active_provider", lambda: "anthropic")
    captured = {}

    def _fake_generate_raw_text(*, system_prompt, user_content, output_token_budget):
        captured["user_content"] = user_content
        return '{"proposed_file_changes": [], "explanation": "ok", "test_command": "pytest", "risks": []}'

    monkeypatch.setattr(implementation_agent, "generate_raw_text", _fake_generate_raw_text)

    node = make_node(db, project, node_key="implementation_planning", order_index=0, output_artifact_type="implementation_plan")
    artifact = make_approved_artifact(db, project, node, actor, content="# Plan")
    task = make_implementation_task(db, project, node, artifact)
    repo_context = _fake_repo_context()

    implementation_agent.run_implementation_agent(
        task=task, repo_context=repo_context, story=None, lld_summary=SAMPLE_LLD,
        project_coding_standards=["Naming: Use camelCase for variables."],
        project_guardrails=["Never touch payment code without human review."],
    )

    assert "Use camelCase for variables." in captured["user_content"]
    assert "Never touch payment code without human review." in captured["user_content"]


def _fake_repo_context():
    from app.services.repo_context_builder import RepoContextPreviewResult

    return RepoContextPreviewResult(architecture_summary="A simple app.", relevant_folders=[], relevant_files=[], suggested_edit_scope=[])
