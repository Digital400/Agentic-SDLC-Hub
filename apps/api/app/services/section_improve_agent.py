"""SectionImproveAgentService — a lighter sibling of
app/services/revision_agent.py: revises exactly one named section of a
still-DRAFT artifact from a single freeform instruction, with no Review
in play at all (unlike revision_agent.py, which is hard-gated on a Review
in NEEDS_CHANGES with reviewer comments — that's the review-gate's
"Request changes -> run revision agent" cycle; this is a pre-review
editing tool reachable directly from the Documents page's Agent Actions
panel).

Reuses every shared building block verbatim: ai_generation.generate()'s
existing IMPROVE contract (current_draft_content + review_comments, the
same "formatted comment" convention revision_agent.py uses), and
merge_section_revisions (app/services/revision_agent.py) built on
app/services/markdown_sections.py — so only the section actually named
can ever come back changed, regardless of what the model returns for the
rest of the document.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import (
    AgentDefinition,
    AgentPrompt,
    AgentPromptRole,
    AgentRun,
    AgentRunStatus,
    Artifact,
    ArtifactStatus,
    ArtifactVersion,
    User,
)
from app.services.ai_generation import AIGenerationError, generate
from app.services.audit import record_audit_log
from app.services.graph_engine import GraphEngineService
from app.services.markdown_sections import find_section, has_real_sections
from app.services.retrieval import retrieve_relevant_chunks
from app.services.revision_agent import merge_section_revisions


class SectionImproveAgentError(Exception):
    """A structural caller error — raised before any AgentRun exists,
    since there's nothing to attribute a FAILED run to yet (mirrors
    RevisionAgentError's exact role in revision_agent.py)."""


@dataclass
class SectionImproveResult:
    agent_run: AgentRun
    needs_clarification: bool = False
    artifact_version: ArtifactVersion | None = None
    # None only when nothing was saved (a failed run, or one that asked
    # for clarification instead).
    section_updated: str | None = None


def run_section_improve_agent(
    db: Session, *, artifact: Artifact, section_title: str, instruction: str, triggered_by: User,
) -> SectionImproveResult:
    """Revise exactly `section_title` in `artifact`'s current version,
    per `instruction`. Preconditions are raised as SectionImproveAgentError
    (no AgentRun row exists yet); every failure after that point is
    recorded as a FAILED AgentRun instead, same contract as every other
    agent path in this codebase."""
    node = artifact.workflow_node
    project = node.project

    # Editing rule — the same "only DRAFT is editable" boundary
    # PATCH /artifacts/{id} already enforces (see app/api/routes/artifacts.py's
    # update_artifact_content): once submitted for review, further changes
    # go through a new version, not an in-place agent edit either.
    if artifact.status != ArtifactStatus.DRAFT:
        raise SectionImproveAgentError(
            f"Cannot improve a section while artifact status is {artifact.status.value}; only a DRAFT artifact can be edited this way."
        )
    if artifact.current_version is None:
        raise SectionImproveAgentError("Artifact has no version yet; create one first.")

    current_version = artifact.current_version
    # A document with no real `##` headings has no genuine section to
    # scope an edit to — "Content" in that case is a synthetic fallback
    # label (see markdown_sections.py's split_into_sections), not text
    # that ever appears in the document, and asking the IMPROVE agent to
    # revise "the Content section" is really asking it to regenerate the
    # entire document from one instruction — a real bug this guards
    # against: the agent has previously returned a short, scoped-looking
    # response that then replaced the whole document, since there was
    # nothing else to preserve it against. Use the document-level Draft
    # or Improve action for a headingless document instead.
    if not has_real_sections(current_version.content_markdown):
        raise SectionImproveAgentError(
            "This document has no named sections yet to improve individually — use the document-level "
            '"Run Agent" (Draft or Improve) action instead of "Improve section" until it has real `## ` headings.'
        )
    if find_section(current_version.content_markdown, section_title) is None:
        raise SectionImproveAgentError(f'Section "{section_title}" was not found in the current document.')

    agent = db.query(AgentDefinition).filter(AgentDefinition.agent_key == node.agent_key).first()
    if agent is None:
        raise SectionImproveAgentError(f"No agent is configured for agent_key '{node.agent_key}'.")

    active_prompt = (
        db.query(AgentPrompt)
        .filter(AgentPrompt.agent_definition_id == agent.id, AgentPrompt.role == AgentPromptRole.IMPROVE, AgentPrompt.is_active.is_(True))
        .first()
    )
    if active_prompt is None:
        raise SectionImproveAgentError(f"No active improve prompt configured for agent '{agent.agent_key}'.")

    now = datetime.now(timezone.utc)
    run = AgentRun(
        project=project,
        workflow_node=node,
        agent_definition=agent,
        agent_prompt=active_prompt,
        triggered_by_user=triggered_by,
        action=AgentPromptRole.IMPROVE,
        status=AgentRunStatus.RUNNING,
        input_context={"target_section": section_title, "instruction": instruction},
        input_artifact_ids=[str(artifact.id)],
        started_at=now,
        context_token_budget=node.context_token_budget,
        output_token_budget=node.output_token_budget,
    )
    db.add(run)
    db.flush()

    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="agent_run.started",
        entity_type="AgentRun", entity_id=run.id,
        extra_data={"agent_key": agent.agent_key, "workflow_node": node.node_key, "action": "improve_section", "section_title": section_title},
    )

    def _fail(reason: str) -> SectionImproveResult:
        run.status = AgentRunStatus.FAILED
        run.error_message = reason
        run.completed_at = datetime.now(timezone.utc)
        record_audit_log(
            db, project_id=project.id, action="agent_run.failed", entity_type="AgentRun", entity_id=run.id,
            extra_data={"error": reason},
        )
        return SectionImproveResult(agent_run=run)

    graph_engine = GraphEngineService(db)
    # Read-only, best-effort context — never blocks (same as revision_agent.py).
    inputs = graph_engine.resolve_required_inputs(project=project, node=node, freeform_context={})
    retrieved_chunks = retrieve_relevant_chunks(
        db, project=project, node=node, freeform_context={},
        approved_inputs=inputs.approved_artifact_content, top_k=node.rag_top_k, max_rag_tokens=node.max_rag_tokens,
    )
    run.retrieved_sources = [
        {
            "chunk_id": c.chunk_id, "source_id": c.source_id, "source_title": c.source_title, "chunk_index": c.chunk_index,
            "snippet": c.content[:300], "similarity": c.similarity, "stage": c.stage, "domain": c.domain,
            "project_type": c.project_type, "content_type": c.content_type, "tags": c.tags,
        }
        for c in retrieved_chunks
    ]

    graph_engine.mark_running(node)

    try:
        result = generate(
            project=project, node=node, action=AgentPromptRole.IMPROVE, active_prompt=active_prompt,
            approved_artifact_content=inputs.approved_artifact_content, approved_artifact_summaries=inputs.approved_artifact_summaries,
            freeform_context={}, context_token_budget=node.context_token_budget, output_token_budget=node.output_token_budget,
            full_content_artifact_types=set(node.full_content_artifact_types), retrieved_chunks=retrieved_chunks,
            # Same "[Section: ...] <feedback>" convention revision_agent.py
            # uses for reviewer comments — the model already knows how to
            # read this shape.
            review_comments=[f"[Section: {section_title}] {instruction}"],
            current_draft_content=current_version.content_markdown,
        )
    except AIGenerationError as exc:
        graph_engine.mark_failed(node)
        return _fail(f"AI generation failed: {exc}")

    run.token_usage = {"prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens, "total_tokens": result.total_tokens}
    run.cost = result.cost
    run.estimated_context_tokens = result.estimated_context_tokens
    run.token_budget_report = result.token_budget_report
    run.status = AgentRunStatus.COMPLETED
    run.completed_at = datetime.now(timezone.utc)

    record_audit_log(
        db, project_id=project.id, action="agent_run.completed", entity_type="AgentRun", entity_id=run.id,
        extra_data={"agent_key": agent.agent_key, "section_title": section_title, "needs_clarification": result.needs_clarification, "retrieved_source_count": len(retrieved_chunks)},
    )

    if result.needs_clarification:
        # Nothing to save yet — mirrors revision_agent.py and
        # agent_runs.py's identical branch: the run itself still
        # COMPLETED, the node just moves to WAITING_FOR_INPUT.
        run.output_text = result.content_markdown
        graph_engine.mark_waiting_for_input(node)
        return SectionImproveResult(agent_run=run, needs_clarification=True)

    # Guarantee: only `section_title` can come back changed, regardless of
    # what the model returned for the rest of the document.
    merged_markdown, changed_titles = merge_section_revisions(
        original_markdown=current_version.content_markdown, revised_markdown=result.content_markdown, affected_section_titles={section_title},
    )

    next_version_number = (
        db.query(ArtifactVersion.version_number)
        .filter(ArtifactVersion.artifact_id == artifact.id)
        .order_by(ArtifactVersion.version_number.desc())
        .limit(1)
        .scalar()
        or 0
    ) + 1

    new_version = ArtifactVersion(
        artifact=artifact, version_number=next_version_number, content_markdown=merged_markdown, created_by=triggered_by,
        change_summary=f'Improved section "{section_title}" via agent instruction.',
    )
    db.add(new_version)
    db.flush()

    artifact.current_version = new_version
    # Stays DRAFT (matches agent_runs.py's ARTIFACT_STATUS_BY_ACTION[IMPROVE])
    # — an improve pass is still a draft; "Send for review" is the one
    # real submission action. No Review is ever created here.
    artifact.status = ArtifactStatus.DRAFT
    graph_engine.mark_ready(node)

    run.output_text = merged_markdown
    run.output_artifact_id = artifact.id
    db.flush()  # syncs artifact.current_version_id (post_update relationship)

    record_audit_log(
        db, project_id=project.id, actor_agent_run_id=run.id, action="artifact_version.created",
        entity_type="ArtifactVersion", entity_id=new_version.id,
        extra_data={"artifact_id": str(artifact.id), "version_number": next_version_number, "section_title": section_title},
    )

    return SectionImproveResult(
        agent_run=run, needs_clarification=False, artifact_version=new_version,
        section_updated=changed_titles[0] if changed_titles else section_title,
    )
