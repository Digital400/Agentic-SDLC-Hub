"""Tests for the Implementation Planner:
  6. Generate implementation tasks from an approved LLD.
  6 (graph rule). Implementation Planning can start only once LLD is approved.
  8. Coding agents (the `implementation` stage) cannot start before the
     Implementation Plan is approved.
  9. Audit logs for generation + the resubmitted review.
  10. Locking/unlocking + regeneration-replaces-the-task-set.

Follows test_lld_workflow.py's exact conventions: plain functions, state
built via tests/conftest.py's factories, the db/project/actor fixtures,
direct calls into the real route function (no TestClient exists in this
repo), and the same `_force_mock_provider` fixture pattern used wherever a
route calls into real/mock AI generation.
"""

import uuid

import pytest

from app.api.routes.projects import generate_implementation_plan
from app.models import (
    Artifact,
    ArtifactStatus,
    ArtifactVersion,
    AuditLog,
    ImplementationTask,
    ImplementationTaskArea,
    User,
    UserRole,
    WorkflowStatus,
)
from app.schemas.implementation_task import GenerateImplementationPlanRequest
from app.services import implementation_planner
from app.services.graph_engine import GraphEngineService
from app.services.implementation_planner import AREA_TO_AGENT_TYPE, build_implementation_plan, render_plan_markdown
from tests.conftest import make_approved_artifact, make_node

SAMPLE_LLD = (
    "## Feature Overview\n\nLets a user reset their password via email.\n\n"
    "## Stories Covered\n\n- Password Reset Request — user requests a reset link\n"
    "- Password Reset Confirmation — user sets a new password\n\n"
    "## Implementation Task Breakdown\n\n"
    "- Add POST /auth/password-reset endpoint\n"
    "- Add password_resets table migration\n"
    "- Build the Reset Password page component\n"
    "- Add unit tests for the reset token expiry rule\n"
    "- Write the README documentation for this feature\n"
)


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(implementation_planner, "get_active_provider", lambda: "mock")


# --- build_implementation_plan (heuristic path) -------------------------------------


def test_heuristic_plan_splits_breakdown_bullets_into_tasks():
    tasks = build_implementation_plan(lld_content=SAMPLE_LLD, story_backlog_content="")

    assert len(tasks) == 5
    assert all(t.linked_lld_section == "Implementation Task Breakdown" for t in tasks)


def test_heuristic_plan_infers_area_by_keyword():
    tasks = build_implementation_plan(lld_content=SAMPLE_LLD, story_backlog_content="")
    by_title = {t.title: t for t in tasks}

    assert by_title["Add POST /auth/password-reset endpoint"].area == "BACKEND"
    assert by_title["Add password_resets table migration"].area == "DATABASE"
    assert by_title["Build the Reset Password page component"].area == "FRONTEND"
    assert by_title["Add unit tests for the reset token expiry rule"].area == "TESTING"
    assert by_title["Write the README documentation for this feature"].area == "DOCS"


def test_heuristic_plan_links_the_first_covered_story():
    tasks = build_implementation_plan(lld_content=SAMPLE_LLD, story_backlog_content="")

    assert all(t.linked_story == "Password Reset Request" for t in tasks)


def test_heuristic_plan_assigns_agent_type_from_area():
    tasks = build_implementation_plan(lld_content=SAMPLE_LLD, story_backlog_content="")

    for t in tasks:
        assert t.assigned_agent_type == AREA_TO_AGENT_TYPE[t.area]


def test_heuristic_plan_is_empty_when_lld_has_no_breakdown_section():
    tasks = build_implementation_plan(lld_content="## Feature Overview\n\nNothing else here.\n", story_backlog_content="")

    assert tasks == []


def test_render_plan_markdown_groups_by_area_in_enum_order():
    tasks = build_implementation_plan(lld_content=SAMPLE_LLD, story_backlog_content="")
    markdown = render_plan_markdown(tasks)

    backend_pos = markdown.index("## BACKEND")
    frontend_pos = markdown.index("## FRONTEND")
    database_pos = markdown.index("## DATABASE")
    assert backend_pos < frontend_pos < database_pos  # BACKEND, FRONTEND, DATABASE order per ImplementationTaskArea


def test_render_plan_markdown_on_empty_task_list_does_not_crash():
    markdown = render_plan_markdown([])
    assert "Implementation Plan" in markdown


# --- Graph rules ---------------------------------------------------------------------


def _chain(db, project):
    lld = make_node(db, project, node_key="lld", order_index=0, output_artifact_type="lld_document")
    story_crafting = make_node(db, project, node_key="story_crafting", order_index=1, output_artifact_type="story_backlog")
    planning = make_node(
        db, project, node_key="implementation_planning", order_index=2,
        required_inputs=["lld_document", "story_backlog"], output_artifact_type="implementation_plan",
    )
    implementation = make_node(
        db, project, node_key="implementation", order_index=3,
        required_inputs=["lld_document", "story_backlog", "implementation_plan"], output_artifact_type="code_change",
        requires_human_approval=False,
    )
    return lld, story_crafting, planning, implementation


def test_implementation_planning_cannot_run_before_lld_is_approved(db, project, actor):
    lld, story_crafting, planning, implementation = _chain(db, project)
    del implementation
    make_approved_artifact(db, project, story_crafting, actor, content="Stories.")
    del lld

    result = GraphEngineService(db).validate_can_run(project=project, node=planning, freeform_context={})

    assert result.can_run is False
    assert any("lld_document" in reason for reason in result.reasons)


def test_implementation_planning_can_run_once_lld_and_stories_are_approved(db, project, actor):
    lld, story_crafting, planning, implementation = _chain(db, project)
    del implementation
    planning.status = WorkflowStatus.READY
    make_approved_artifact(db, project, lld, actor, content=SAMPLE_LLD)
    make_approved_artifact(db, project, story_crafting, actor, content="Stories.")

    result = GraphEngineService(db).validate_can_run(project=project, node=planning, freeform_context={})

    assert result.can_run is True
    assert "lld_document" in result.approved_artifact_content


def test_implementation_cannot_run_before_implementation_plan_is_approved(db, project, actor):
    lld, story_crafting, planning, implementation = _chain(db, project)
    del planning
    implementation.status = WorkflowStatus.READY
    make_approved_artifact(db, project, lld, actor, content=SAMPLE_LLD)
    make_approved_artifact(db, project, story_crafting, actor, content="Stories.")

    result = GraphEngineService(db).validate_can_run(project=project, node=implementation, freeform_context={})

    assert result.can_run is False
    assert any("implementation_plan" in reason for reason in result.reasons)


def test_implementation_can_run_once_implementation_plan_is_approved(db, project, actor):
    lld, story_crafting, planning, implementation = _chain(db, project)
    implementation.status = WorkflowStatus.READY
    make_approved_artifact(db, project, lld, actor, content=SAMPLE_LLD)
    make_approved_artifact(db, project, story_crafting, actor, content="Stories.")
    make_approved_artifact(db, project, planning, actor, content="Plan.")

    result = GraphEngineService(db).validate_can_run(project=project, node=implementation, freeform_context={})

    assert result.can_run is True
    assert "implementation_plan" in result.approved_artifact_content


# --- generate_implementation_plan endpoint (direct call — no TestClient exists) ----


def _tech_lead(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Tech Lead", role=UserRole.TECH_LEAD)
    db.add(user)
    db.flush()
    return user


def test_generate_implementation_plan_persists_tasks_and_opens_a_review(db, project, actor):
    lld, story_crafting, planning, implementation = _chain(db, project)
    del implementation
    planning.status = WorkflowStatus.READY
    make_approved_artifact(db, project, lld, actor, content=SAMPLE_LLD)
    make_approved_artifact(db, project, story_crafting, actor, content="Stories.")
    tech_lead = _tech_lead(db)

    response = generate_implementation_plan(
        project.id, GenerateImplementationPlanRequest(triggered_by_user_id=actor.id, reviewer_id=tech_lead.id), db
    )

    assert len(response.tasks) == 5
    assert response.review.status == "PENDING"
    assert response.review.reviewer_id == tech_lead.id
    assert planning.status == WorkflowStatus.WAITING_FOR_REVIEW

    rows = db.query(ImplementationTask).filter(ImplementationTask.workflow_node_id == planning.id).all()
    assert len(rows) == 5
    assert {r.area for r in rows} <= set(ImplementationTaskArea)

    artifact = db.query(Artifact).filter(Artifact.id == response.artifact_id).first()
    assert artifact.status == ArtifactStatus.READY_FOR_REVIEW
    version = db.query(ArtifactVersion).filter(ArtifactVersion.id == response.artifact_version_id).first()
    assert "## BACKEND" in version.content_markdown


def test_generate_implementation_plan_writes_audit_log_entries(db, project, actor):
    lld, story_crafting, planning, implementation = _chain(db, project)
    del implementation
    planning.status = WorkflowStatus.READY
    make_approved_artifact(db, project, lld, actor, content=SAMPLE_LLD)
    make_approved_artifact(db, project, story_crafting, actor, content="Stories.")
    tech_lead = _tech_lead(db)

    generate_implementation_plan(
        project.id, GenerateImplementationPlanRequest(triggered_by_user_id=actor.id, reviewer_id=tech_lead.id), db
    )

    actions = [row.action for row in db.query(AuditLog).filter(AuditLog.project_id == project.id).all()]
    assert "implementation_plan.generated" in actions
    assert "artifact_version.created" in actions
    assert "workflow_node.status_changed" in actions
    assert "review.created" in actions


def test_regenerating_the_plan_replaces_the_prior_task_set(db, project, actor):
    lld, story_crafting, planning, implementation = _chain(db, project)
    del implementation
    make_approved_artifact(db, project, lld, actor, content=SAMPLE_LLD)
    make_approved_artifact(db, project, story_crafting, actor, content="Stories.")
    tech_lead = _tech_lead(db)

    planning.status = WorkflowStatus.READY
    generate_implementation_plan(
        project.id, GenerateImplementationPlanRequest(triggered_by_user_id=actor.id, reviewer_id=tech_lead.id), db
    )
    first_count = db.query(ImplementationTask).filter(ImplementationTask.workflow_node_id == planning.id).count()

    # A pending review blocks re-running (matches every other stage) —
    # reject it first, exactly like a human would ask for a fresh plan.
    GraphEngineService(db).mark_needs_changes(planning)
    generate_implementation_plan(
        project.id, GenerateImplementationPlanRequest(triggered_by_user_id=actor.id, reviewer_id=tech_lead.id), db
    )
    second_count = db.query(ImplementationTask).filter(ImplementationTask.workflow_node_id == planning.id).count()

    assert first_count == 5
    assert second_count == 5  # replaced, not accumulated (would be 10 if appended)
