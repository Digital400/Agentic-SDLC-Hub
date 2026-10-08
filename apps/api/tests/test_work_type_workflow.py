"""Tests for WorkType-driven workflow template selection (see
app/models/enums.py's WorkType docstring and
workflows/existing-project-feature-workflow.json's own header comment):
  - NEW_PROJECT keeps using the existing default-sdlc-workflow template,
    unchanged.
  - EXISTING_PROJECT_FEATURE, BUG_FIX, and TECHNICAL_IMPROVEMENT all use
    the new existing-project-feature-workflow template.
  - Story Crafting stays locked until HLD Delta is approved, on that
    template.

Follows test_github_integration.py's convention: plain functions, direct
calls into the real route function (no TestClient in this repo).
"""

import uuid

from app.api.routes.projects import create_project
from app.models import User, UserRole, WorkflowNode, WorkflowStatus
from app.models.enums import WorkType
from app.schemas.project import ProjectCreate
from app.services.graph_engine import GraphEngineService
from tests.conftest import make_approved_artifact


def _actor(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Creator", role=UserRole.ADMIN)
    db.add(user)
    db.flush()
    return user


def _create(db, actor, work_type: WorkType | None = None):
    kwargs = dict(name="Test Project", business_owner="BA", created_by_id=actor.id)
    if work_type is not None:
        kwargs["work_type"] = work_type
    return create_project(ProjectCreate(**kwargs), db)


def _node(db, project_id, node_key: str) -> WorkflowNode:
    return db.query(WorkflowNode).filter(WorkflowNode.project_id == project_id, WorkflowNode.node_key == node_key).first()


def test_new_project_defaults_to_the_default_sdlc_workflow(db):
    actor = _actor(db)
    project = _create(db, actor)  # work_type omitted -> NEW_PROJECT default

    assert project.work_type == WorkType.NEW_PROJECT
    assert project.workflow_template_id == "default-sdlc-workflow"
    assert _node(db, project.id, "hld") is not None
    assert _node(db, project.id, "story_crafting") is not None
    assert _node(db, project.id, "feature_intake") is None


def test_new_project_explicit_also_uses_the_default_workflow(db):
    actor = _actor(db)
    project = _create(db, actor, WorkType.NEW_PROJECT)

    assert project.workflow_template_id == "default-sdlc-workflow"


def test_existing_project_feature_uses_the_new_workflow(db):
    actor = _actor(db)
    project = _create(db, actor, WorkType.EXISTING_PROJECT_FEATURE)

    assert project.work_type == WorkType.EXISTING_PROJECT_FEATURE
    assert project.workflow_template_id == "existing-project-feature-workflow"
    assert project.current_stage == "feature_intake"
    for key in ["feature_intake", "existing_system_context_scan", "impact_analysis", "mini_solution_discovery", "hld_delta", "story_crafting"]:
        assert _node(db, project.id, key) is not None, f"missing node {key}"
    # No Problem Discovery / full HLD / Requirement Intake on this template.
    assert _node(db, project.id, "problem_discovery") is None
    assert _node(db, project.id, "hld") is None
    assert _node(db, project.id, "requirement_intake") is None


def test_bug_fix_uses_the_existing_project_feature_workflow(db):
    actor = _actor(db)
    project = _create(db, actor, WorkType.BUG_FIX)

    assert project.workflow_template_id == "existing-project-feature-workflow"


def test_technical_improvement_uses_the_existing_project_feature_workflow(db):
    actor = _actor(db)
    project = _create(db, actor, WorkType.TECHNICAL_IMPROVEMENT)

    assert project.workflow_template_id == "existing-project-feature-workflow"


def test_explicit_workflow_template_file_overrides_work_type(db):
    """Scrum Story Lanes stays reachable only by explicit override — an
    unrelated axis from work_type (see ProjectCreate.workflow_template_file's
    own docstring)."""
    actor = _actor(db)
    project = create_project(
        ProjectCreate(
            name="Test", business_owner="BA", created_by_id=actor.id,
            work_type=WorkType.EXISTING_PROJECT_FEATURE, workflow_template_file="scrum-story-lanes-workflow.json",
        ),
        db,
    )
    assert project.workflow_template_id == "scrum-story-lanes-workflow"


# --- Story Crafting stays locked until HLD Delta is approved ------------------------


def test_story_crafting_is_locked_until_hld_delta_is_approved(db):
    actor = _actor(db)
    project = _create(db, actor, WorkType.EXISTING_PROJECT_FEATURE)
    engine = GraphEngineService(db)

    story_crafting = _node(db, project.id, "story_crafting")
    assert story_crafting.status.value == "LOCKED"

    validation = engine.validate_can_run(project=project, node=story_crafting, freeform_context={})
    assert not validation.can_run
    assert any("hld_delta" in reason for reason in validation.reasons)


def test_story_crafting_unlocks_once_hld_delta_is_approved(db):
    actor = _actor(db)
    project = _create(db, actor, WorkType.EXISTING_PROJECT_FEATURE)
    engine = GraphEngineService(db)

    for key in ["feature_intake", "existing_system_context_scan", "impact_analysis", "mini_solution_discovery", "hld_delta"]:
        node = _node(db, project.id, key)
        make_approved_artifact(db, project, node, actor, content=f"{key} content")
        node.status = WorkflowStatus.APPROVED
        db.flush()
        engine.unlock_next_nodes(node)

    story_crafting = _node(db, project.id, "story_crafting")
    assert story_crafting.status.value == "READY"

    validation = engine.validate_can_run(project=project, node=story_crafting, freeform_context={})
    assert validation.can_run
