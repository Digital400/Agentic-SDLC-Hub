"""The Documents/Workflow "Validate" action: run the stage's validator (see
app/services/validator_agent.py and its ValidatorDefinition) on the artifact's
CURRENT draft and report a verdict — without rewriting the document.

Why this isn't an AgentPrompt role: the validator is a separate concept from
the drafting agent's prompts (see app/models/validator.py), so no VALIDATE
AgentPrompt is ever seeded. Routing "Validate" through the generic
prompt-based run failed with "No active validate prompt configured", and even
with a prompt its output would have replaced the document with a model's
rewrite. Validation should judge the draft, not overwrite it.

Outcome:
- passes (quality >= the validator's threshold and no critical issues): the
  artifact becomes READY_FOR_REVIEW and the node WAITING_FOR_REVIEW; a stage
  with no human approval gate is finalised the same way saving a validate
  output used to (APPROVED, node COMPLETED, next stages unlocked).
- fails: the artifact stays a DRAFT (node READY) and the issues are reported so
  the user can improve it and validate again.
Either way the document's content and versions are untouched.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import (
    AgentDefinition,
    AgentPromptRole,
    AgentRun,
    AgentRunStatus,
    Artifact,
    ArtifactStatus,
    Project,
    User,
    ValidatorDefinition,
    WorkflowNode,
)
from app.services.artifact_summary import apply_summaries_to_version
from app.services.audit import record_audit_log
from app.services.graph_engine import GraphEngineService
from app.services.loop_engine import DEFAULT_QUALITY_THRESHOLD
from app.services.markdown_sections import split_into_sections
from app.services.validator_agent import ValidatorResult, run_validator

CLARIFICATION_MARKER = "# Clarification Needed"


def _report_markdown(result: ValidatorResult, *, threshold: float, passed: bool) -> str:
    lines = [
        f"# Validation {'passed' if passed else 'needs work'}",
        "",
        f"Quality score: **{result.quality_score:.0%}** (needs {threshold:.0%}) · completeness {result.completeness_score:.0%} · "
        f"clarity {result.clarity_score:.0%} · risk coverage {result.risk_coverage_score:.0%}",
    ]
    for title, items in (
        ("Critical issues", result.critical_issues),
        ("Missing details", result.missing_details),
        ("Suggestions", result.suggestions),
        ("Risks noted", result.risks),
    ):
        if items:
            lines += ["", f"## {title}", *[f"- {i}" for i in items]]
    return "\n".join(lines)


def run_validate_action(
    db: Session, *, project: Project, node: WorkflowNode, agent: AgentDefinition, triggered_by: User
) -> AgentRun:
    """Creates and completes (or fails) one VALIDATE AgentRun. The caller
    commits."""
    run = AgentRun(
        project=project,
        workflow_node=node,
        agent_definition=agent,
        agent_prompt=None,
        triggered_by_user=triggered_by,
        action=AgentPromptRole.VALIDATE,
        status=AgentRunStatus.RUNNING,
        input_context={},
        input_artifact_ids=[],
        started_at=datetime.now(timezone.utc),
        context_token_budget=node.context_token_budget,
        output_token_budget=node.output_token_budget,
    )
    db.add(run)
    db.flush()
    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="agent_run.started",
        entity_type="AgentRun", entity_id=run.id,
        extra_data={"agent_key": agent.agent_key, "workflow_node": node.node_key, "action": "validate"},
    )

    def fail(reason: str) -> AgentRun:
        run.status = AgentRunStatus.FAILED
        run.error_message = reason
        run.completed_at = datetime.now(timezone.utc)
        record_audit_log(
            db, project_id=project.id, action="agent_run.failed", entity_type="AgentRun", entity_id=run.id, extra_data={"error": reason}
        )
        return run

    artifact = (
        db.query(Artifact)
        .filter(Artifact.workflow_node_id == node.id, Artifact.artifact_type == node.output_artifact_type)
        .order_by(Artifact.created_at.desc())
        .first()
    )
    if artifact is None or artifact.current_version is None:
        return fail("There is nothing to validate yet — run this stage's agent (Draft) first.")
    content = artifact.current_version.content_markdown
    if content.lstrip().startswith(CLARIFICATION_MARKER):
        return fail("The current document is the agent's request for more information, not a draft. Answer its questions first.")
    if artifact.status != ArtifactStatus.DRAFT:
        return fail(f"Only a DRAFT can be validated; this document is already {artifact.status.value.replace('_', ' ').lower()}.")

    validator = (
        db.query(ValidatorDefinition)
        .filter(ValidatorDefinition.stage == node.node_key, ValidatorDefinition.is_active.is_(True))
        .first()
    )
    threshold = validator.quality_threshold if validator else DEFAULT_QUALITY_THRESHOLD
    result = run_validator(validator=validator, stage_name=node.name, content_markdown=content)
    passed = result.quality_score >= threshold and not result.has_critical_issues

    run.loop_quality_score = result.quality_score
    run.loop_quality_threshold = threshold
    run.loop_validation_issues = result.critical_issues + result.suggestions
    run.loop_validation_result = {**result.to_dict(), "passed": passed}
    run.output_text = _report_markdown(result, threshold=threshold, passed=passed)
    run.output_artifact_id = artifact.id
    run.status = AgentRunStatus.COMPLETED
    run.completed_at = datetime.now(timezone.utc)

    graph_engine = GraphEngineService(db)
    if not passed:
        graph_engine.mark_ready(node)
    elif node.requires_human_approval:
        artifact.status = ArtifactStatus.READY_FOR_REVIEW
        graph_engine.mark_waiting_for_review(node)
    else:
        artifact.status = ArtifactStatus.APPROVED
        graph_engine.mark_completed(node)
        apply_summaries_to_version(artifact.current_version, artifact_type=artifact.artifact_type)
        unlocked = graph_engine.unlock_next_nodes(node)
        record_audit_log(
            db, project_id=project.id, actor_agent_run_id=run.id, action="artifact.auto_approved",
            entity_type="Artifact", entity_id=artifact.id,
            extra_data={"reason": "stage does not require human approval", "unlocked": [n.node_key for n in unlocked]},
        )

    record_audit_log(
        db, project_id=project.id, action="agent_run.completed", entity_type="AgentRun", entity_id=run.id,
        extra_data={
            "agent_key": agent.agent_key, "action": "validate", "quality_score": result.quality_score,
            "threshold": threshold, "passed": passed, "sections": len(split_into_sections(content)),
        },
    )
    return run
