"""RevisionAgentService — the agentic revision loop behind the review gate.

Wires together what the review gate's "Request changes" -> "Run revision
agent" cycle needs (see app/api/routes/reviews.py's run_revision_agent
route and docs/product-vision.md's human-in-the-loop principle):

  1. structured, per-section reviewer comments (ReviewComment.section_title,
     captured at request-changes time)
  2. + this node's own last validation result, if any (the Loop Engine's
     ValidatorResult — see app/services/validator_agent.py)
  3. + the artifact's own CURRENT content (a genuine revision, not a
     from-scratch redraft — see ai_generation.generate's
     current_draft_content param)
  4. + stage rules (folded into build_prioritized_context's node_rules
     block automatically)

...and then trims the model's response back down to just the section(s)
the comments/validator feedback actually named (merge_section_revisions)
before saving a new artifact version with a change summary and
resubmitting it for review — so a revision can never silently rewrite an
untouched section, regardless of what the underlying model returns.

Mirrors app/api/routes/agent_runs.py's start_agent_run in structure and
error handling: once the AgentRun row exists, every further failure (a bad
precondition, or a real AIGenerationError) is recorded on it as a FAILED
run rather than raised as a bare HTTP error — a doomed attempt is still a
real, auditable one.
"""

from dataclasses import dataclass, field
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
    Review,
    ReviewStatus,
    User,
    WorkflowStatus,
)
from app.services.ai_generation import AIGenerationError, generate
from app.services.audit import record_audit_log
from app.services.graph_engine import GraphEngineService
from app.services.markdown_sections import join_sections, split_into_sections
from app.services.retrieval import retrieve_relevant_chunks


def merge_section_revisions(
    *, original_markdown: str, revised_markdown: str, affected_section_titles: set[str]
) -> tuple[str, list[str]]:
    """Rule 6: only the section(s) a reviewer's comments actually named get
    replaced with the agent's revised content — every other section is
    carried over from the original document verbatim, regardless of what
    the model returned for it. Returns (merged_markdown,
    section_titles_actually_changed) — the latter is what the new
    version's change summary reports (rule 8).

    When no comment named a specific section (every comment was general,
    document-wide feedback — section linking is "where possible", not
    guaranteed, see ReviewComment.section_title), there's nothing to scope
    a partial update to, so the full revised document is used as-is."""
    if not affected_section_titles:
        return revised_markdown, [s["title"] for s in split_into_sections(revised_markdown)]

    normalized_affected = {t.strip().lower() for t in affected_section_titles}
    original_sections = split_into_sections(original_markdown)
    revised_by_title = {s["title"].strip().lower(): s for s in split_into_sections(revised_markdown)}

    merged: list[dict[str, str]] = []
    changed: list[str] = []
    seen_normalized: set[str] = set()

    for section in original_sections:
        normalized = section["title"].strip().lower()
        seen_normalized.add(normalized)
        if normalized in normalized_affected and normalized in revised_by_title:
            merged.append(revised_by_title[normalized])
            changed.append(section["title"])
        else:
            merged.append(section)

    # A reviewer named a section that doesn't exist in the current document
    # yet (e.g. "add a Risks section") — append the agent's version of it
    # rather than silently dropping the request.
    for normalized in normalized_affected - seen_normalized:
        new_section = revised_by_title.get(normalized)
        if new_section:
            merged.append(new_section)
            changed.append(new_section["title"])

    return join_sections(merged), changed


class RevisionAgentError(Exception):
    """A structural caller error (no agent configured for this node at
    all) — raised before any AgentRun exists, since there's nothing to
    attribute a FAILED run to yet. Every other precondition failure (wrong
    review/node state, no active prompt, a real AIGenerationError) is
    instead recorded as a FAILED AgentRun — see run_revision_agent."""


@dataclass
class RevisionAgentResult:
    agent_run: AgentRun
    needs_clarification: bool = False
    artifact_version: ArtifactVersion | None = None
    new_review: Review | None = None
    # Section titles the revision actually rewrote — empty when nothing was
    # saved (a failed run, or one that asked for clarification instead).
    sections_updated: list[str] = field(default_factory=list)


def run_revision_agent(db: Session, *, review: Review, triggered_by: User) -> RevisionAgentResult:
    """Drives one full pass of the review-gate's agentic revision loop —
    see module docstring for the four inputs and the section-scoped merge.
    On success: a new ArtifactVersion (with a change summary), the
    workflow node moved to WAITING_FOR_REVIEW, and a new PENDING Review
    against the same reviewer (rule 9's resubmit)."""
    node = review.workflow_node
    project = node.project
    artifact: Artifact = review.artifact_version.artifact

    agent = db.query(AgentDefinition).filter(AgentDefinition.agent_key == node.agent_key).first()
    if agent is None:
        raise RevisionAgentError(f"No agent is configured for agent_key '{node.agent_key}'.")

    now = datetime.now(timezone.utc)
    run = AgentRun(
        project=project,
        workflow_node=node,
        agent_definition=agent,
        triggered_by_user=triggered_by,
        action=AgentPromptRole.IMPROVE,
        status=AgentRunStatus.RUNNING,
        input_context={"revision_of_review_id": str(review.id)},
        input_artifact_ids=[str(artifact.id)],
        started_at=now,
        context_token_budget=node.context_token_budget,
        output_token_budget=node.output_token_budget,
    )
    db.add(run)
    db.flush()

    record_audit_log(
        db,
        project_id=project.id,
        actor_user_id=triggered_by.id,
        action="agent_run.started",
        entity_type="AgentRun",
        entity_id=run.id,
        extra_data={
            "agent_key": agent.agent_key,
            "workflow_node": node.node_key,
            "action": "revise",
            "review_id": str(review.id),
        },
    )

    def _fail(reason: str) -> RevisionAgentResult:
        run.status = AgentRunStatus.FAILED
        run.error_message = reason
        run.completed_at = datetime.now(timezone.utc)
        record_audit_log(
            db, project_id=project.id, action="agent_run.failed", entity_type="AgentRun", entity_id=run.id,
            extra_data={"error": reason},
        )
        return RevisionAgentResult(agent_run=run)

    if review.status != ReviewStatus.NEEDS_CHANGES:
        return _fail(f"Review must be NEEDS_CHANGES to run the revision agent (current: {review.status.value}).")
    if node.status != WorkflowStatus.NEEDS_CHANGES:
        return _fail(f"Workflow node must be NEEDS_CHANGES to run the revision agent (current: {node.status.value}).")
    if not review.comments:
        return _fail("This review has no reviewer comments to revise from.")

    active_prompt = (
        db.query(AgentPrompt)
        .filter(
            AgentPrompt.agent_definition_id == agent.id,
            AgentPrompt.role == AgentPromptRole.IMPROVE,
            AgentPrompt.is_active.is_(True),
        )
        .first()
    )
    if active_prompt is None:
        return _fail(f"No active improve prompt configured for agent '{agent.agent_key}'.")
    run.agent_prompt = active_prompt

    current_draft_content = review.artifact_version.content_markdown

    # Rules 1-2: structured comments, linked to a section where the
    # reviewer could point at one.
    structured_comments = [{"body": c.body, "section_title": c.section_title} for c in review.comments]
    formatted_comments = [
        f"[Section: {c['section_title']}] {c['body']}" if c["section_title"] else f"[General feedback] {c['body']}"
        for c in structured_comments
    ]
    affected_section_titles = {c["section_title"] for c in structured_comments if c["section_title"]}

    # Rule 5's "validation result" — the most recent VALIDATE-bearing run
    # for this node (a Loop Engine DRAFT run's own VALIDATE step, or a
    # standalone VALIDATE action), if one exists. A stage with no validator
    # run yet simply has none — a normal outcome, not an error.
    last_validated_run = (
        db.query(AgentRun)
        .filter(AgentRun.workflow_node_id == node.id, AgentRun.loop_validation_result.isnot(None))
        .order_by(AgentRun.created_at.desc())
        .first()
    )
    validation_result = last_validated_run.loop_validation_result if last_validated_run else None
    validation_feedback: list[str] = []
    if validation_result:
        validation_feedback += [f"(validator) {issue}" for issue in validation_result.get("critical_issues") or []]
        validation_feedback += [f"(validator suggestion) {s}" for s in validation_result.get("suggestions") or []]

    graph_engine = GraphEngineService(db)
    # Not validate_can_run's stricter gate — this is a revision of an
    # already-drafted artifact, not a fresh run, so a freeform input that
    # isn't re-supplied here shouldn't block it. Only used for its approved
    # *upstream* artifact content/summaries.
    inputs = graph_engine.resolve_required_inputs(project=project, node=node, freeform_context={})

    retrieved_chunks = retrieve_relevant_chunks(
        db,
        project=project,
        node=node,
        freeform_context={},
        approved_inputs=inputs.approved_artifact_content,
        top_k=node.rag_top_k,
        max_rag_tokens=node.max_rag_tokens,
    )
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

    graph_engine.mark_running(node)

    try:
        result = generate(
            project=project,
            node=node,
            action=AgentPromptRole.IMPROVE,
            active_prompt=active_prompt,
            approved_artifact_content=inputs.approved_artifact_content,
            approved_artifact_summaries=inputs.approved_artifact_summaries,
            freeform_context={},
            context_token_budget=node.context_token_budget,
            output_token_budget=node.output_token_budget,
            full_content_artifact_types=set(node.full_content_artifact_types),
            retrieved_chunks=retrieved_chunks,
            review_comments=formatted_comments,
            validation_feedback=validation_feedback,
            current_draft_content=current_draft_content,
        )
    except AIGenerationError as exc:
        graph_engine.mark_failed(node)
        return _fail(f"AI generation failed: {exc}")

    run.token_usage = {
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
    }
    run.cost = result.cost
    run.estimated_context_tokens = result.estimated_context_tokens
    run.token_budget_report = result.token_budget_report
    run.status = AgentRunStatus.COMPLETED
    run.completed_at = datetime.now(timezone.utc)

    record_audit_log(
        db,
        project_id=project.id,
        action="agent_run.completed",
        entity_type="AgentRun",
        entity_id=run.id,
        extra_data={
            "agent_key": agent.agent_key,
            "review_id": str(review.id),
            "needs_clarification": result.needs_clarification,
            "retrieved_source_count": len(retrieved_chunks),
        },
    )

    if result.needs_clarification:
        # Nothing to revise yet — the agent needs more information before
        # it can act on the reviewer's feedback. Mirrors
        # app/api/routes/agent_runs.py: the run itself still COMPLETED, the
        # node just moves to WAITING_FOR_INPUT rather than back to review.
        run.output_text = result.content_markdown
        graph_engine.mark_waiting_for_input(node)
        return RevisionAgentResult(agent_run=run, needs_clarification=True)

    # Rule 6: reduce the model's response to just the section(s) the
    # reviewer/validator feedback actually named.
    merged_markdown, changed_titles = merge_section_revisions(
        original_markdown=current_draft_content,
        revised_markdown=result.content_markdown,
        affected_section_titles=affected_section_titles,
    )

    next_version_number = (
        db.query(ArtifactVersion.version_number)
        .filter(ArtifactVersion.artifact_id == artifact.id)
        .order_by(ArtifactVersion.version_number.desc())
        .limit(1)
        .scalar()
        or 0
    ) + 1

    # Rule 8: a change summary a human can skim without re-reading the
    # whole document — which comments were addressed, and which section(s)
    # actually changed as a result.
    comment_count = len(review.comments)
    change_summary = (
        f"Revision agent addressed {comment_count} reviewer comment{'s' if comment_count != 1 else ''}"
        + (f" — updated section(s): {', '.join(changed_titles)}." if changed_titles else ".")
    )

    # Rule 7: create new artifact version.
    new_version = ArtifactVersion(
        artifact=artifact,
        version_number=next_version_number,
        content_markdown=merged_markdown,
        created_by=triggered_by,
        change_summary=change_summary,
    )
    db.add(new_version)
    db.flush()

    artifact.current_version = new_version
    artifact.status = ArtifactStatus.READY_FOR_REVIEW
    graph_engine.mark_waiting_for_review(node)

    run.output_text = merged_markdown
    run.output_artifact_id = artifact.id

    # Rule 9: resubmit for review — same reviewer as the round that asked
    # for changes, against the freshly revised version.
    new_review = Review(
        artifact_version=new_version,
        workflow_node=node,
        reviewer_id=review.reviewer_id,
        status=ReviewStatus.PENDING,
    )
    db.add(new_review)
    db.flush()

    record_audit_log(
        db,
        project_id=project.id,
        actor_agent_run_id=run.id,
        action="artifact_version.created",
        entity_type="ArtifactVersion",
        entity_id=new_version.id,
        extra_data={
            "artifact_id": str(artifact.id),
            "version_number": next_version_number,
            "sections_updated": changed_titles,
        },
    )
    record_audit_log(
        db,
        project_id=project.id,
        actor_agent_run_id=run.id,
        action="workflow_node.status_changed",
        entity_type="WorkflowNode",
        entity_id=node.id,
        extra_data={"node_key": node.node_key, "to": WorkflowStatus.WAITING_FOR_REVIEW.value, "reason": "revision_agent"},
    )
    record_audit_log(
        db,
        project_id=project.id,
        actor_user_id=triggered_by.id,
        actor_agent_run_id=run.id,
        action="review.created",
        entity_type="Review",
        entity_id=new_review.id,
        extra_data={
            "artifact_id": str(artifact.id),
            "artifact_version_id": str(new_version.id),
            "resubmitted_from_review_id": str(review.id),
        },
    )

    return RevisionAgentResult(
        agent_run=run,
        needs_clarification=False,
        artifact_version=new_version,
        new_review=new_review,
        sections_updated=changed_titles,
    )
