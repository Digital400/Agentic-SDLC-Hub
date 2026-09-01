"""Tests for the Infrastructure Planning stage:
  - the workflow template's node config (requirement 2's HLD+LLD gate,
    requirement 4's 10 output sections, requirement 1's artifact type),
  - the graph engine's fan-in unlock against the real template edges
    (only READY once *both* HLD and LLD are approved),
  - the DevOps-only approval gate (rule: DevOps approval is required),
  - agent_runs.py's optional-context merge (requirement 3's "implementation
    summary" — best-effort, never blocking).
"""

import pytest
from fastapi import HTTPException

from app.api.routes.agent_runs import OPTIONAL_CONTEXT_ARTIFACT_TYPES, _merge_optional_context
from app.db.seed import RICH_DEFAULT_PROMPTS
from app.models import User, UserRole, WorkflowStatus
from app.services.graph_engine import GraphEngineService, GraphValidationResult
from app.services.permissions import require_can_approve_stage, require_can_edit_stage
from app.services.workflow_templates import load_workflow_template
from tests.conftest import make_approved_artifact, make_edge, make_node

_REQUIRED_SECTIONS = [
    "Environment Plan",
    "Service Architecture",
    "CI/CD Pipeline",
    "Database Migration Plan",
    "Secrets Management",
    "Monitoring & Logging",
    "Security Controls",
    "Rollback Plan",
    "Cost Considerations",
    "Release Checklist",
]


# --- Workflow template: node config ------------------------------------------------------


def test_infrastructure_planning_node_requires_hld_and_lld_only():
    template = load_workflow_template("sdlc-workflow.json")
    node = next(n for n in template["nodes"] if n["id"] == "infrastructure_planning")

    assert set(node["requiredInputs"]) == {"hld_document", "lld_document"}
    assert node["outputArtifactType"] == "infrastructure_plan_document"
    assert node["requiresHumanApproval"] is True
    assert set(node.get("fullContentArtifactTypes", [])) == {"hld_document", "lld_document"}


def test_infrastructure_planning_node_does_not_collide_with_the_existing_placeholder():
    template = load_workflow_template("sdlc-workflow.json")
    placeholder = next(n for n in template["nodes"] if n["id"] == "infrastructure")

    assert placeholder["outputArtifactType"] == "infrastructure_plan"  # untouched
    assert placeholder["outputArtifactType"] != "infrastructure_plan_document"


def test_infrastructure_planning_has_fan_in_edges_from_hld_and_lld():
    template = load_workflow_template("sdlc-workflow.json")
    sources = {e["source"] for e in template["edges"] if e["target"] == "infrastructure_planning"}

    assert sources == {"hld", "lld"}


def test_infrastructure_planning_prompt_lists_all_10_sections_in_order():
    output_format = RICH_DEFAULT_PROMPTS["infrastructure_planning"]["output_format"]

    positions = [output_format.index(section) for section in _REQUIRED_SECTIONS]

    assert positions == sorted(positions), "sections must appear in the documented order"


def test_infrastructure_planning_prompt_forbids_auto_deploy_and_requires_devops_approval():
    system_prompt = RICH_DEFAULT_PROMPTS["infrastructure_planning"]["system_prompt"].lower()
    checklist = " ".join(RICH_DEFAULT_PROMPTS["infrastructure_planning"]["validation_checklist"]).lower()

    assert "not an action itself" in system_prompt
    assert "devops approval is required" in checklist


# --- Fan-in unlock against the real template edges -----------------------------------------


def _seeded_hld_lld_and_target(db, project):
    hld = make_node(db, project, node_key="hld", order_index=0, output_artifact_type="hld_document", status=WorkflowStatus.APPROVED)
    lld = make_node(db, project, node_key="lld", order_index=1, output_artifact_type="lld_document", status=WorkflowStatus.APPROVED)
    infra = make_node(
        db, project, node_key="infrastructure_planning", order_index=2,
        required_inputs=["hld_document", "lld_document"], output_artifact_type="infrastructure_plan_document",
        status=WorkflowStatus.LOCKED,
    )
    make_edge(db, project, hld, infra)
    make_edge(db, project, lld, infra)
    return hld, lld, infra


def test_infrastructure_planning_stays_locked_until_both_hld_and_lld_are_approved(db, project, actor):
    hld, lld, infra = _seeded_hld_lld_and_target(db, project)
    lld.status = WorkflowStatus.READY  # LLD not yet approved
    db.flush()

    engine = GraphEngineService(db)
    unlocked = engine.unlock_next_nodes(hld)  # only HLD reporting in

    assert unlocked == []
    assert infra.status == WorkflowStatus.LOCKED


def test_infrastructure_planning_unlocks_once_both_hld_and_lld_are_approved(db, project, actor):
    hld, lld, infra = _seeded_hld_lld_and_target(db, project)
    lld.status = WorkflowStatus.READY  # LLD not yet approved when HLD reports in
    db.flush()

    engine = GraphEngineService(db)
    engine.unlock_next_nodes(hld)  # HLD reports in first — not enough alone
    assert infra.status == WorkflowStatus.LOCKED

    lld.status = WorkflowStatus.APPROVED  # LLD is now approved too
    db.flush()
    unlocked = engine.unlock_next_nodes(lld)  # LLD reports in — both satisfied now

    assert unlocked == [infra]
    assert infra.status == WorkflowStatus.READY


# --- Permissions: DevOps-only approval (rule) -----------------------------------------------


def _user(db, role: UserRole) -> User:
    import uuid

    user = User(email=f"{uuid.uuid4()}@example.com", full_name=role.value, role=role)
    db.add(user)
    db.flush()
    return user


def test_devops_may_approve_infrastructure_planning(db):
    devops = _user(db, UserRole.DEVOPS)
    require_can_approve_stage(devops, "infrastructure_planning")  # must not raise


def test_qa_may_not_approve_infrastructure_planning(db):
    qa = _user(db, UserRole.QA)
    with pytest.raises(HTTPException) as exc_info:
        require_can_approve_stage(qa, "infrastructure_planning")
    assert exc_info.value.status_code == 403


def test_devops_and_architect_may_edit_infrastructure_planning(db):
    devops = _user(db, UserRole.DEVOPS)
    architect = _user(db, UserRole.ARCHITECT)
    require_can_edit_stage(devops, "infrastructure_planning")  # must not raise
    require_can_edit_stage(architect, "infrastructure_planning")  # must not raise


def test_developer_may_not_edit_infrastructure_planning(db):
    developer = _user(db, UserRole.DEVELOPER)
    with pytest.raises(HTTPException) as exc_info:
        require_can_edit_stage(developer, "infrastructure_planning")
    assert exc_info.value.status_code == 403


# --- _merge_optional_context: best-effort, non-blocking implementation summary -------------


def test_merge_optional_context_folds_in_an_approved_implementation_plan(db, project, actor):
    node = make_node(db, project, node_key="infrastructure_planning", order_index=0, output_artifact_type="infrastructure_plan_document")
    plan_node = make_node(db, project, node_key="implementation_planning", order_index=1, output_artifact_type="implementation_plan")
    make_approved_artifact(db, project, plan_node, actor, content="## Backend\nAdd endpoint.")

    validation = GraphValidationResult(can_run=True)
    _merge_optional_context(db, project, node, validation)

    assert "implementation_plan" in validation.approved_artifact_content
    assert validation.approved_artifact_content["implementation_plan"] == "## Backend\nAdd endpoint."


def test_merge_optional_context_is_a_noop_when_no_implementation_plan_exists_yet(db, project, actor):
    node = make_node(db, project, node_key="infrastructure_planning", order_index=0, output_artifact_type="infrastructure_plan_document")

    validation = GraphValidationResult(can_run=True)
    _merge_optional_context(db, project, node, validation)  # must not raise

    assert "implementation_plan" not in validation.approved_artifact_content


def test_merge_optional_context_never_overrides_an_already_required_input(db, project, actor):
    node = make_node(db, project, node_key="infrastructure_planning", order_index=0, output_artifact_type="infrastructure_plan_document")
    plan_node = make_node(db, project, node_key="implementation_planning", order_index=1, output_artifact_type="implementation_plan")
    make_approved_artifact(db, project, plan_node, actor, content="Real content.")

    validation = GraphValidationResult(can_run=True, approved_artifact_content={"implementation_plan": "Already resolved."})
    _merge_optional_context(db, project, node, validation)

    assert validation.approved_artifact_content["implementation_plan"] == "Already resolved."


def test_optional_context_dict_only_configures_infrastructure_planning():
    assert OPTIONAL_CONTEXT_ARTIFACT_TYPES == {"infrastructure_planning": ["implementation_plan"]}
