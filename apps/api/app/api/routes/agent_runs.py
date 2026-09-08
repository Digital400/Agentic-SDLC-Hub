"""Agent Run endpoints — running an agent against a workflow node.

Covers: start a run, get one by id, view a run's loop execution history
(see app/services/loop_engine.py), and save a completed run's output into
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
attempt) whenever the workflow node isn't in a runnable state, or a
required upstream artifact is missing or not yet APPROVED, so an agent can
never be used to skip a stage. See
app/services/graph_engine.py's GraphEngineService.validate_can_run.
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
    KnowledgeContentType,
    Project,
    User,
    ValidatorDefinition,
    WorkflowNode,
    WorkflowStatus,
)
from app.schemas.agent_run import AgentRunCreate, AgentRunLoopEventRead, AgentRunRead, SaveAgentOutputResponse
from app.services.agent_context_builder import build_engineering_setup_context, infer_agent_context_type
from app.services.ai_generation import CLARIFICATION_MARKER, AIGenerationError, generate
from app.services.artifact_summary import apply_summaries_to_version
from app.services.audit import record_audit_log
from app.services.context_builder import fetch_recent_review_comments
from app.services.graph_engine import GraphEngineService
from app.services.loop_engine import DEFAULT_QUALITY_THRESHOLD, LoopEngineService
from app.services.permissions import require_can_edit_stage
from app.services.retrieval import retrieve_relevant_chunks

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])


def _merge_optional_context(db: Session, project: Project, node: WorkflowNode, validation) -> None:
    """Best-effort, non-blocking extra context (see
    OPTIONAL_CONTEXT_ARTIFACT_TYPES above): unlike node.required_inputs,
    an entry here never blocks the run if no APPROVED artifact of that
    type exists yet — it's purely additive to what build_prioritized_context
    (see app/services/ai_generation.py) ends up seeing, since that
    function iterates whatever keys are present in approved_artifact_content,
    not just node.required_inputs."""
    for extra_type in OPTIONAL_CONTEXT_ARTIFACT_TYPES.get(node.node_key, []):
        if extra_type in validation.approved_artifact_content:
            continue  # already a required input — don't override it
        artifact = (
            db.query(Artifact)
            .filter(Artifact.project_id == project.id, Artifact.artifact_type == extra_type, Artifact.status == ArtifactStatus.APPROVED)
            .order_by(Artifact.updated_at.desc())
            .first()
        )
        if artifact is not None and artifact.current_version is not None:
            validation.approved_artifact_content[extra_type] = artifact.current_version.content_markdown
            if artifact.current_version.agent_context_summary:
                validation.approved_artifact_summaries[extra_type] = artifact.current_version.agent_context_summary


def _merge_engineering_setup_context(db: Session, project: Project, node: WorkflowNode, validation) -> dict:
    """Project Engineering Setup rule 6 — "Agents must receive coding
    standards and guardrails in context" (plus, per node type, the
    stack/Jira/documentation specifics — see
    app/services/agent_context_builder.py) — for every generic
    drafting-agent stage (Requirement Intake, HLD, Story Crafting, ...),
    not just the bespoke Implementation Agent (see
    app/api/routes/implementation_runs.py's own separate wiring for that
    one). A project with no ProjectEngineeringSetup row is untouched
    (rule 10). Returns the context snapshot (rule 5) for the caller to
    persist on this run — empty dict when there was nothing to include."""
    agent_type = infer_agent_context_type(node.node_key)
    # Capped against a fraction of the node's own context budget, not the
    # whole thing — this is one of several context blocks
    # build_prioritized_context assembles (P0 instructions, P1 rules, P2
    # approved-artifact summaries including this one, P3 RAG, ...), not
    # the only thing competing for room in it.
    budget = max(500, (node.context_token_budget or 8000) // 4)
    result = build_engineering_setup_context(db, project=project, agent_type=agent_type, output_token_budget=budget)
    if result.context_text:
        validation.approved_artifact_content["project_engineering_setup"] = result.context_text
        validation.approved_artifact_summaries["project_engineering_setup"] = result.context_text
    return result.snapshot


# The node status a run's action leaves the workflow node in once its
# output is saved: draft/improve mean the agent is still producing/revising
# content (node goes back to READY, ready for further work); validate means
# the agent has confirmed the content meets its own bar and it's ready for
# a human (WAITING_FOR_REVIEW).
NODE_STATUS_BY_ACTION: dict[AgentPromptRole, WorkflowStatus] = {
    AgentPromptRole.DRAFT: WorkflowStatus.READY,
    AgentPromptRole.IMPROVE: WorkflowStatus.READY,
    AgentPromptRole.VALIDATE: WorkflowStatus.WAITING_FOR_REVIEW,
}
ARTIFACT_STATUS_BY_ACTION: dict[AgentPromptRole, ArtifactStatus] = {
    AgentPromptRole.DRAFT: ArtifactStatus.DRAFT,
    AgentPromptRole.IMPROVE: ArtifactStatus.DRAFT,
    AgentPromptRole.VALIDATE: ArtifactStatus.READY_FOR_REVIEW,
}

# Best-effort, non-blocking extra context for specific stages: an artifact
# type here is folded into approved_artifact_content/summaries if an
# APPROVED version of it exists, but — unlike node.required_inputs — its
# absence never blocks the run (see GraphEngineService.resolve_required_
# inputs, which only gates on node.required_inputs). Currently just
# Infrastructure Planning's optional Implementation Plan summary: that
# stage is only required to wait on approved HLD+LLD (see
# workflows/sdlc-workflow.json), but benefits from the Implementation
# Plan's task breakdown when one happens to already be approved.
OPTIONAL_CONTEXT_ARTIFACT_TYPES: dict[str, list[str]] = {
    "infrastructure_planning": ["implementation_plan"],
}

# Per-stage RAG narrowing: omitted for every stage except where one
# specifically needs a narrower slice of the Knowledge Base than "whatever
# is similar enough" — see retrieve_relevant_chunks's content_types param.
# Mirrors the same narrowing app/api/routes/pr_review_runs.py and
# test_runs.py already do for their own bespoke retrieval calls.
NODE_CONTENT_TYPE_FILTERS: dict[str, list[KnowledgeContentType]] = {
    "infrastructure_planning": [KnowledgeContentType.COMPANY_STANDARD, KnowledgeContentType.ARCHITECTURE_RULE],
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
    require_can_edit_stage(triggered_by, node.node_key)

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
        # Snapshot the node's budgets as they are right now (see
        # app/services/token_budget.py) — a node's config can change later,
        # this is what actually applies to this run.
        context_token_budget=node.context_token_budget,
        output_token_budget=node.output_token_budget,
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

    # The graph-rule gate (see app/services/graph_engine.py): the node's own
    # status must allow running at all, AND every required input must
    # already have an APPROVED artifact (or be present in input_context for
    # the freeform, no-prior-artifact case). Node status is left untouched
    # on either failure — nothing actually ran.
    graph_engine = GraphEngineService(db)
    validation = graph_engine.validate_can_run(project=project, node=node, freeform_context=payload.input_context)
    if not validation.can_run:
        return _fail("Cannot run — " + "; ".join(validation.reasons) + ".")

    _merge_optional_context(db, project, node, validation)
    engineering_setup_snapshot = _merge_engineering_setup_context(db, project, node, validation)
    if engineering_setup_snapshot:
        run.engineering_setup_context_snapshot = engineering_setup_snapshot

    # Retrieval-augmented context: pull whatever the Knowledge Base has that's
    # relevant to this project/stage/input/upstream-artifacts combination
    # (see app/services/retrieval.py) — stage-aware, and capped by this
    # node's own rag_top_k/max_rag_tokens config. An empty result is a
    # normal outcome, not a failure — the run proceeds on project context
    # alone.
    retrieved_chunks = retrieve_relevant_chunks(
        db,
        project=project,
        node=node,
        freeform_context=payload.input_context,
        approved_inputs=validation.approved_artifact_content,
        top_k=node.rag_top_k,
        max_rag_tokens=node.max_rag_tokens,
        content_types=NODE_CONTENT_TYPE_FILTERS.get(node.node_key),
    )
    # This run's AgentRunContext for RAG (rule 6): the exact chunks that
    # were selected and made it into the prompt, with enough metadata to
    # show sources/filter by kind later without re-querying the Knowledge
    # Base — see app/services/retrieval.py's RetrievedChunk.
    run.retrieved_sources = [
        {
            "chunk_id": c.chunk_id,
            "source_id": c.source_id,
            "source_title": c.source_title,
            "chunk_index": c.chunk_index,
            "snippet": c.content[:300],
            "similarity": c.similarity,
            "stage": c.stage,
            "domain": c.domain,
            "project_type": c.project_type,
            "content_type": c.content_type,
            "tags": c.tags,
        }
        for c in retrieved_chunks
    ]
    review_comments = fetch_recent_review_comments(db, node)
    # IMPROVE/VALIDATE both need the artifact's own current text to act on
    # (see ai_generation.generate's docstring: "IMPROVE — a human explicitly
    # asked to revise this stage's own artifact"; "VALIDATE — a validator
    # checking the full document") — without it, the model has nothing to
    # revise/check and effectively just re-drafts from the same upstream
    # inputs, which (absent fresh review_comments) tends to come back
    # near-identical to what's already there. This mirrors the
    # current_draft_content already passed by revision_agent.py and
    # section_improve_agent.py for their own IMPROVE calls.
    current_draft_content: str | None = None
    if payload.action in (AgentPromptRole.IMPROVE, AgentPromptRole.VALIDATE):
        current_artifact = (
            db.query(Artifact)
            .filter(Artifact.workflow_node_id == node.id, Artifact.artifact_type == node.output_artifact_type)
            .order_by(Artifact.created_at.desc())
            .first()
        )
        if current_artifact is not None and current_artifact.current_version is not None:
            current_draft_content = current_artifact.current_version.content_markdown
    # One ValidatorDefinition per workflow stage (see
    # app/models/validator.py) — None is a normal outcome for a stage that
    # hasn't had one configured yet; run_validator falls back to a
    # criteria-less heuristic check rather than blocking the loop.
    validator = (
        db.query(ValidatorDefinition)
        .filter(ValidatorDefinition.stage == node.node_key, ValidatorDefinition.is_active.is_(True))
        .first()
    )

    # Only now, having passed every pre-flight check, does the node itself
    # start reflecting that a run is actually in progress.
    graph_engine.mark_running(node)

    # DRAFT runs go through the Loop Engine's self-improvement cycle (see
    # app/services/loop_engine.py) — generate, validate, improve on
    # critical issues, repeat until quality is good enough or iterations
    # run out. VALIDATE/IMPROVE runs are already a single well-defined
    # human-triggered agent step, so they keep the direct one-shot call.
    try:
        if payload.action == AgentPromptRole.DRAFT:
            loop_result = LoopEngineService(db).run_loop(
                run=run,
                project=project,
                node=node,
                action=payload.action,
                active_prompt=active_prompt,
                approved_artifact_content=validation.approved_artifact_content,
                approved_artifact_summaries=validation.approved_artifact_summaries,
                freeform_context=payload.input_context,
                full_content_artifact_types=set(node.full_content_artifact_types),
                retrieved_chunks=retrieved_chunks,
                review_comments=review_comments,
                validator=validator,
                quality_threshold=validator.quality_threshold if validator else DEFAULT_QUALITY_THRESHOLD,
            )
            result_content = loop_result.content_markdown
            result_needs_clarification = loop_result.needs_clarification
            result_used_mock = loop_result.used_mock
            token_usage = {
                "prompt_tokens": loop_result.prompt_tokens,
                "completion_tokens": loop_result.completion_tokens,
                "total_tokens": loop_result.total_tokens,
            }
            cost = loop_result.cost
            estimated_context_tokens = loop_result.estimated_context_tokens
            token_budget_report = loop_result.token_budget_report
            extra_audit_data = {
                "loop_status": loop_result.loop_status.value,
                "loop_iterations": loop_result.iterations_run,
                "loop_quality_score": loop_result.quality_score,
                "approval_recommendation": loop_result.validation_result.get("approval_recommendation"),
            }
        else:
            result = generate(
                project=project,
                node=node,
                action=payload.action,
                active_prompt=active_prompt,
                approved_artifact_content=validation.approved_artifact_content,
                approved_artifact_summaries=validation.approved_artifact_summaries,
                freeform_context=payload.input_context,
                context_token_budget=node.context_token_budget,
                output_token_budget=node.output_token_budget,
                full_content_artifact_types=set(node.full_content_artifact_types),
                retrieved_chunks=retrieved_chunks,
                review_comments=review_comments,
                current_draft_content=current_draft_content,
            )
            result_content = result.content_markdown
            result_needs_clarification = result.needs_clarification
            result_used_mock = result.used_mock
            token_usage = {
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.total_tokens,
            }
            cost = result.cost
            estimated_context_tokens = result.estimated_context_tokens
            token_budget_report = result.token_budget_report
            extra_audit_data = {}
    except AIGenerationError as exc:
        graph_engine.mark_failed(node)
        return _fail(f"AI generation failed: {exc}")

    run.output_text = result_content
    run.token_usage = token_usage
    run.cost = cost
    run.estimated_context_tokens = estimated_context_tokens
    run.token_budget_report = token_budget_report
    run.status = AgentRunStatus.COMPLETED
    run.completed_at = datetime.now(timezone.utc)

    # The run itself is done, but its output isn't saved to an artifact yet
    # (see module docstring) — the node just reflects that a human/agent
    # now has something to act on: either answer a clarification, or look
    # at the draft and decide whether to save it.
    if result_needs_clarification:
        graph_engine.mark_waiting_for_input(node)
    else:
        graph_engine.mark_ready(node)

    record_audit_log(
        db, project_id=project.id, action="agent_run.completed", entity_type="AgentRun", entity_id=run.id,
        extra_data={
            "agent_key": agent.agent_key,
            "prompt_version": active_prompt.version,
            "token_usage": run.token_usage,
            "estimated_context_tokens": estimated_context_tokens,
            "used_mock": result_used_mock,
            "needs_clarification": result_needs_clarification,
            "retrieved_source_count": len(retrieved_chunks),
            **extra_audit_data,
        },
    )

    db.commit()
    db.refresh(run)
    return AgentRunRead.from_orm_run(run)


# 2. Get agent run status --------------------------------------------------------


@router.get("/{run_id}", response_model=AgentRunRead)
def get_agent_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> AgentRunRead:
    return AgentRunRead.from_orm_run(_get_run_or_404(db, run_id))


# 3. View loop execution history --------------------------------------------------


@router.get("/{run_id}/loop-events", response_model=list[AgentRunLoopEventRead])
def list_agent_run_loop_events(run_id: uuid.UUID, db: Session = Depends(get_db)) -> list[AgentRunLoopEventRead]:
    """The full PLAN/RETRIEVE_CONTEXT/GENERATE_DRAFT/VALIDATE/IMPROVE/
    READY_FOR_REVIEW trail behind a run's loop_* summary fields (see
    AgentRunRead) — empty for a run whose action wasn't DRAFT, since only
    DRAFT runs go through the Loop Engine. Ordered by iteration, then by
    creation time within an iteration."""
    run = _get_run_or_404(db, run_id)
    return [AgentRunLoopEventRead.model_validate(e) for e in run.loop_events]


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
    node.status = WorkflowStatus.WAITING_FOR_INPUT if needs_clarification else NODE_STATUS_BY_ACTION[run.action]
    run.output_artifact_id = artifact.id

    graph_engine = GraphEngineService(db)
    # A VALIDATE run is normally what pushes an artifact to
    # READY_FOR_REVIEW/WAITING_FOR_REVIEW for a human review gate — but a
    # stage with requires_human_approval=False (e.g. Implementation,
    # Maintenance) has no review gate to wait on. Without this, its
    # artifact would sit at READY_FOR_REVIEW forever: nothing else in the
    # codebase ever moves a no-approval artifact to APPROVED (see
    # GraphEngineService.mark_completed's docstring), so a downstream
    # stage listing it as a required_input could never be satisfied.
    # Auto-finalize instead, the same way a human approval would, minus
    # the human: APPROVED (the "usable downstream" artifact state),
    # COMPLETED (the node's own "no approval gate" terminal status), a
    # compression summary, and unlocking whatever comes next.
    auto_finalized = (
        not needs_clarification and run.action == AgentPromptRole.VALIDATE and not node.requires_human_approval
    )
    if auto_finalized:
        artifact.status = ArtifactStatus.APPROVED
        graph_engine.mark_completed(node)
        apply_summaries_to_version(version, artifact_type=artifact.artifact_type)
        unlocked = graph_engine.unlock_next_nodes(node)
        record_audit_log(
            db, project_id=run.project_id, actor_agent_run_id=run.id, action="artifact.auto_approved",
            entity_type="Artifact", entity_id=artifact.id,
            extra_data={"reason": "stage does not require human approval", "unlocked": [n.node_key for n in unlocked]},
        )

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
