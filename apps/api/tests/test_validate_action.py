"""The "Validate" action runs the stage's validator on the current draft and
reports a verdict without rewriting the document — see
app/services/validate_action.py."""

import app.api.routes.agent_runs as agent_runs
import app.services.validate_action as validate_action
from app.models import AgentPromptRole, Artifact, ArtifactStatus, ArtifactVersion, WorkflowStatus
from app.schemas.agent_run import AgentRunCreate
from app.services.validator_agent import ValidatorResult
from tests.conftest import make_agent_prompt, make_node

DOC = "## Overview\n\nA simple to-do app.\n\n## Constraints\n\n- Low budget.\n"


def _setup(db, project, actor, *, content=DOC, requires_approval=True):
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    node.requires_human_approval = requires_approval
    make_agent_prompt(db, stage="node_a")  # DRAFT prompt only — there is deliberately no VALIDATE prompt
    artifact = Artifact(
        project_id=project.id, workflow_node_id=node.id, artifact_type=node.output_artifact_type,
        title="Doc", status=ArtifactStatus.DRAFT, created_by_id=actor.id,
    )
    db.add(artifact)
    db.flush()
    version = ArtifactVersion(artifact_id=artifact.id, version_number=1, content_markdown=content, created_by_id=actor.id)
    db.add(version)
    db.flush()
    artifact.current_version_id = version.id
    db.flush()
    return node, artifact


def _validate(db, project, actor, node):
    return agent_runs.start_agent_run(
        AgentRunCreate(project_id=project.id, workflow_node_id=node.id, action=AgentPromptRole.VALIDATE, triggered_by_user_id=actor.id), db
    )


def _stub_validator(monkeypatch, *, score, critical=None):
    monkeypatch.setattr(
        validate_action, "run_validator",
        lambda **kw: ValidatorResult(
            quality_score=score, completeness_score=score, clarity_score=score, risk_coverage_score=score,
            critical_issues=critical or [], suggestions=["Add a success metric."], approval_recommendation="APPROVE" if score >= 0.8 else "REVISE",
        ),
    )


def test_validate_works_without_a_validate_prompt_and_does_not_touch_the_document(db, project, actor, monkeypatch):
    node, artifact = _setup(db, project, actor)
    _stub_validator(monkeypatch, score=0.9)

    run = _validate(db, project, actor, node)

    assert run.status.value == "COMPLETED", run.error_message
    db.refresh(artifact)
    assert artifact.current_version.content_markdown == DOC and len(artifact.versions) == 1
    assert run.loop_validation_result["passed"] is True and run.loop_quality_score == 0.9


def test_passing_validation_moves_the_draft_to_review(db, project, actor, monkeypatch):
    node, artifact = _setup(db, project, actor)
    _stub_validator(monkeypatch, score=0.9)
    _validate(db, project, actor, node)
    db.refresh(artifact)
    db.refresh(node)
    assert artifact.status == ArtifactStatus.READY_FOR_REVIEW and node.status == WorkflowStatus.WAITING_FOR_REVIEW


def test_failing_validation_keeps_it_a_draft_and_reports_issues(db, project, actor, monkeypatch):
    node, artifact = _setup(db, project, actor)
    _stub_validator(monkeypatch, score=0.4, critical=["No success metric."])

    run = _validate(db, project, actor, node)

    db.refresh(artifact)
    db.refresh(node)
    assert artifact.status == ArtifactStatus.DRAFT and node.status == WorkflowStatus.READY
    assert run.loop_validation_result["passed"] is False
    assert "No success metric." in run.output_text and "needs work" in run.output_text


def test_stage_without_approval_gate_is_finalised_on_pass(db, project, actor, monkeypatch):
    node, artifact = _setup(db, project, actor, requires_approval=False)
    _stub_validator(monkeypatch, score=0.95)
    _validate(db, project, actor, node)
    db.refresh(artifact)
    db.refresh(node)
    assert artifact.status == ArtifactStatus.APPROVED and node.status == WorkflowStatus.COMPLETED


def test_nothing_to_validate_is_a_clear_failure(db, project, actor):
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    make_agent_prompt(db, stage="node_a")
    run = _validate(db, project, actor, node)
    assert run.status.value == "FAILED" and "nothing to validate" in run.error_message.lower()


def test_clarification_request_is_not_validated(db, project, actor):
    node, _ = _setup(db, project, actor, content="# Clarification Needed\n\n- What is the budget?\n")
    run = _validate(db, project, actor, node)
    assert run.status.value == "FAILED" and "more information" in run.error_message


def test_already_submitted_document_cannot_be_validated_again(db, project, actor):
    node, artifact = _setup(db, project, actor)
    artifact.status = ArtifactStatus.READY_FOR_REVIEW
    db.flush()
    run = _validate(db, project, actor, node)
    assert run.status.value == "FAILED" and "ready for review" in run.error_message


def test_real_heuristic_validator_runs_end_to_end(db, project, actor, monkeypatch):
    """No stubbing of the validator: the offline heuristic must produce a verdict."""
    monkeypatch.setattr("app.services.validator_agent.get_active_provider", lambda: "mock")
    node, _ = _setup(db, project, actor)
    run = _validate(db, project, actor, node)
    assert run.status.value == "COMPLETED"
    assert run.loop_quality_score is not None and "passed" in run.loop_validation_result
