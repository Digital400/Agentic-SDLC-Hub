"""Agent Run endpoints — running an agent against a workflow node.

Covers: start a run, get one by id, and save a completed run's output into
an artifact draft. "List agent runs by project" lives in
app/api/routes/projects.py, next to that resource's other sub-lists.

Deliberately two-phase: starting a run executes it synchronously (real
Claude call or mock — see app/services/ai_generation.py) to
COMPLETED/FAILED, but does NOT touch any artifact or workflow node by
itself — saving the output is a separate, explicit step. This mirrors how
a real async pipeline should behave: a model call finishing shouldn't by
itself and irreversibly overwrite a shared artifact.

Guardrails enforced here, not just documented: a run is blocked (recorded
as a FAILED run, not a bare HTTP rejection — it's still a real, auditable
attempt) whenever a required upstream artifact is missing or not yet
APPROVED, so an agent can never be used to skip a stage. See
app/services/workflow_progress.py's `resolve_required_inputs`.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import (
    AgentDefinition,
    AgentPrompt,
    AgentPromptRole,
    AgentRun,
    AgentRunStatus,
    Artifact,
    ArtifactStatus,
    ArtifactVersion,
    Project,
    User,
    WorkflowNode,
    WorkflowStatus,
)
from app.schemas.agent_run import AgentRunCreate, AgentRunRead, SaveAgentOutputResponse
from app.services.ai_generation import CLARIFICATION_MARKER, AIGenerationError, generate
from app.services.audit import record_audit_log
from app.services.workflow_progress import resolve_required_inputs

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])

# The node status a run's action leaves the workflow node in once its
# output is saved: draft/improve mean the agent is still producing/revising
# content (node stays IN_PROGRESS); validate means the agent has confirmed
# the content meets its own bar and it's ready for a human (WAITING_FOR_REVIEW).
NODE_STATUS_BY_ACTION: dict[AgentPromptRole, WorkflowStatus] = {
    AgentPromptRole.DRAFT: WorkflowStatus.IN_PROGRESS,
    AgentPromptRole.IMPROVE: WorkflowStatus.IN_PROGRESS,
    AgentPromptRole.VALIDATE: WorkflowStatus.WAITING_FOR_REVIEW,
}
ARTIFACT_STATUS_BY_ACTION: dict[AgentPromptRole, ArtifactStatus] = {
    AgentPromptRole.DRAFT: ArtifactStatus.DRAFT,
    AgentPromptRole.IMPROVE: ArtifactStatus.DRAFT,
    AgentPromptRole.VALIDATE: ArtifactStatus.READY_FOR_REVIEW,
}


def _get_run_or_404(db: Session, run_id: uuid.UUID) -> AgentRun:
    run = db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Agent run {run_id} not found")
    return run


# 1. Start agent run ------------------------------------------------------------


@router.post("", response_model=AgentRunRead, status_code=status.HTTP_201_CREATED)
def start_agent_run(payload: AgentRunCreate, db: Session = Depends(get_db)) -> AgentRunRead:
    project = db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"project_id {payload.project_id} does not match an existing project")

    node = (
        db.query(WorkflowNode)
        .filter(WorkflowNode.id == payload.workflow_node_id, WorkflowNode.project_id == payload.project_id)
        .first()
    )
    if node is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"workflow_node_id {payload.workflow_node_id} is not a workflow node of project {payload.project_id}",
        )

    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")

    for artifact_id in payload.input_artifact_ids:
        if db.get(Artifact, artifact_id) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"input artifact {artifact_id} does not match an existing artifact")

    agent = db.query(AgentDefinition).filter(AgentDefinition.agent_key == node.agent_key).first()
    if agent is None:
        # Shouldn't happen for a node generated from the standard template,
        # but the node's agent_key is only a string reference — verify it.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"No agent is configured for agent_key '{node.agent_key}'")

    active_prompt = (
        db.query(AgentPrompt)
        .filter(AgentPrompt.agent_definition_id == agent.id, AgentPrompt.role == payload.action, AgentPrompt.is_active.is_(True))
        .first()
    )

    now = datetime.now(timezone.utc)
    run = AgentRun(
        project=project,
        workflow_node=node,
        agent_definition=agent,
        agent_prompt=active_prompt,
        triggered_by_user=triggered_by,
        action=payload.action,
        status=AgentRunStatus.RUNNING,
        input_context=payload.input_context,
        input_artifact_ids=[str(i) for i in payload.input_artifact_ids],
        started_at=now,
    )
    db.add(run)
    db.flush()

    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="agent_run.started",
        entity_type="AgentRun", entity_id=run.id,
        extra_data={"agent_key": agent.agent_key, "workflow_node": node.node_key, "action": payload.action.value},
    )

    def _fail(reason: str) -> AgentRunRead:
        run.status = AgentRunStatus.FAILED
        run.error_message = reason
        run.completed_at = datetime.now(timezone.utc)
        record_audit_log(
            db, project_id=project.id, action="agent_run.failed", entity_type="AgentRun", entity_id=run.id,
            extra_data={"error": reason},
        )
        db.commit()
        db.refresh(run)
        return AgentRunRead.from_orm_run(run)

    if active_prompt is None:
        # A real, demonstrable failure path — not every run can succeed:
        # this agent has no active prompt to run with.
        return _fail(f"No active {payload.action.value} prompt configured for agent '{agent.agent_key}'.")

    # The core "don't skip stages" guardrail: every required input must
    # already have an APPROVED artifact (or be present in input_context for
    # the freeform, no-prior-artifact case) before this stage may run at all.
    required_inputs = resolve_required_inputs(db, project=project, node=node, freeform_context=payload.input_context)
    if required_inputs.missing_reasons:
        return _fail("Cannot run — " + "; ".join(required_inputs.missing_reasons) + ".")

    try:
        result = generate(
            project=project,
            node=node,
            action=payload.action,
            active_prompt=active_prompt,
            approved_inputs=required_inputs.approved_artifact_content,
            freeform_context=payload.input_context,
        )
    except AIGenerationError as exc:
        return _fail(f"AI generation failed: {exc}")

    run.output_text = result.content_markdown
    run.token_usage = {
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
    }
    run.cost = result.cost
    run.status = AgentRunStatus.COMPLETED
    run.completed_at = datetime.now(timezone.utc)

    record_audit_log(
        db, project_id=project.id, action="agent_run.completed", entity_type="AgentRun", entity_id=run.id,
        extra_data={
            "agent_key": agent.agent_key,
            "prompt_version": active_prompt.version,
            "token_usage": run.token_usage,
            "used_mock": result.used_mock,
            "needs_clarification": result.needs_clarification,
        },
    )

    db.commit()
    db.refresh(run)
    return AgentRunRead.from_orm_run(run)


# 2. Get agent run status --------------------------------------------------------


@router.get("/{run_id}", response_model=AgentRunRead)
def get_agent_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> AgentRunRead:
    return AgentRunRead.from_orm_run(_get_run_or_404(db, run_id))


# 4. Save agent output to artifact draft --------------------------------------------


@router.post("/{run_id}/save-to-artifact", response_model=SaveAgentOutputResponse)
def save_agent_output_to_artifact(run_id: uuid.UUID, db: Session = Depends(get_db)) -> SaveAgentOutputResponse:
    run = _get_run_or_404(db, run_id)

    if run.status != AgentRunStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot save output from a run with status {run.status.value}.")
    if run.output_text is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This run has no output to save.")
    if run.output_artifact_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This run's output has already been saved to an artifact.")

    node = run.workflow_node

    artifact = (
        db.query(Artifact)
        .filter(Artifact.workflow_node_id == node.id, Artifact.artifact_type == node.output_artifact_type)
        .order_by(Artifact.created_at.desc())
        .first()
    )
    if artifact is None:
        artifact = Artifact(
            project_id=run.project_id,
            workflow_node=node,
            artifact_type=node.output_artifact_type,
            title=node.name,
            status=ArtifactStatus.DRAFT,
            created_by=run.triggered_by_user,
        )
        db.add(artifact)
        db.flush()
        record_audit_log(
            db, project_id=run.project_id, actor_user_id=run.triggered_by_user_id, actor_agent_run_id=run.id,
            action="artifact.created", entity_type="Artifact", entity_id=artifact.id,
            extra_data={"workflow_node": node.node_key, "artifact_type": artifact.artifact_type},
        )

    last_version_number = (
        db.query(ArtifactVersion.version_number)
        .filter(ArtifactVersion.artifact_id == artifact.id)
        .order_by(ArtifactVersion.version_number.desc())
        .limit(1)
        .scalar()
    )
    next_version_number = (last_version_number or 0) + 1

    version = ArtifactVersion(
        artifact=artifact,
        version_number=next_version_number,
        content_markdown=run.output_text,
        created_by=run.triggered_by_user,
        change_summary=f"Generated by {run.agent_definition.agent_key} ({run.action.value}), agent run {run.id}.",
    )
    db.add(version)
    db.flush()

    artifact.current_version = version
    # Clarification-needed output is never "ready for review" or a
    # finished draft, regardless of action — it's a request for more
    # information, so both statuses stay at their in-progress defaults.
    needs_clarification = run.output_text.startswith(CLARIFICATION_MARKER)
    artifact.status = ArtifactStatus.DRAFT if needs_clarification else ARTIFACT_STATUS_BY_ACTION[run.action]
    node.status = WorkflowStatus.IN_PROGRESS if needs_clarification else NODE_STATUS_BY_ACTION[run.action]
    run.output_artifact_id = artifact.id

    record_audit_log(
        db, project_id=run.project_id, actor_agent_run_id=run.id, action="artifact_version.created",
        entity_type="ArtifactVersion", entity_id=version.id,
        extra_data={"artifact_id": str(artifact.id), "version_number": next_version_number},
    )
    record_audit_log(
        db, project_id=run.project_id, actor_agent_run_id=run.id, action="workflow_node.status_changed",
        entity_type="WorkflowNode", entity_id=node.id,
        extra_data={"node_key": node.node_key, "to": node.status.value, "reason": f"agent_run action={run.action.value}"},
    )

    db.commit()
    db.refresh(run)
    db.refresh(artifact)

    return SaveAgentOutputResponse(
        agent_run=AgentRunRead.from_orm_run(run),
        artifact_id=artifact.id,
        artifact_version_id=version.id,
        artifact_status=artifact.status.value,
        workflow_node_status=node.status.value,
    )
