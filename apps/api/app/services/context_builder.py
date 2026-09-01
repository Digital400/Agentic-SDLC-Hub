"""ContextBuilderService — the single place that assembles everything an
agent call needs before generation.

Eight-step pipeline (`ContextBuilderService.build`):
    1. Load current project
    2. Load current workflow node
    3. Load required approved artifacts (full content)
    4. Load artifact summaries (agent_context_summary, or a fallback)
    5. Load latest human review comments
    6. Retrieve relevant RAG chunks (stage-aware, node-budgeted — see
       app/services/retrieval.py)
    7. Apply token budget rules (TokenBudgetService — see
       app/services/token_budget.py)
    8. Produce the final ContextBuilderResult

This is a structured, directly-inspectable/testable sibling to
app/services/ai_generation.py's build_prioritized_context, which remains
the actual prompt-text assembler `generate()` uses — the two apply the
same priority/compression rules over the same underlying primitives
(ContextBlock, TokenBudgetService), but this one reports every section as
its own named field (per the required output shape below) rather than one
flattened prompt string, and is meant to be callable on its own — e.g. a
"preview what the agent will actually see" endpoint — independent of
running a real model call.

Priority order for step 7 (highest to lowest — same shape as
build_prioritized_context's P0-P4, just without that module's "current
instruction" input, which isn't part of this service's job):
    P0 stageRules                 — never dropped, compressed if tight
    P1 projectSummary             — never dropped, compressed if tight
    P2 requiredArtifactSummaries  — never dropped, compressed if tight;
                                     escalates a specific artifact to its
                                     full content when the node's own
                                     config (full_content_artifact_types)
                                     or `force_full_content` says so —
                                     the same rule build_prioritized_context
                                     applies for IMPROVE/VALIDATE actions
    P3 ragContext                 — dropped whole, least-similar chunk
                                     first (retrieve_relevant_chunks
                                     already orders most-similar first)
    P4 humanComments              — dropped whole, oldest comment first
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models import AgentPrompt, Project, Review, WorkflowNode
from app.services.graph_engine import GraphEngineService
from app.services.retrieval import RetrievedChunk, retrieve_relevant_chunks
from app.services.token_budget import ContextBlock, TokenBudgetService


class ContextBuilderError(Exception):
    """The named project/node don't exist or don't belong together — a
    caller error (bad ids), not a runtime/provider failure."""


def fetch_recent_review_comments(db: Session, node: WorkflowNode) -> list[str]:
    """Step 5 — feedback from this node's most recent human review round,
    oldest comment first, so a re-draft/improve run can address what a
    reviewer actually said, not just what its own self-validation found.
    Most recent review by decided_at (an undecided review has no verdict
    yet but may still have discussion comments worth surfacing), falling
    back to created_at for a still-pending review."""
    review = (
        db.query(Review)
        .filter(Review.workflow_node_id == node.id)
        .order_by(Review.decided_at.desc().nulls_last(), Review.created_at.desc())
        .first()
    )
    return [c.body for c in review.comments] if review else []


@dataclass
class ContextBuilderResult:
    system_prompt: str
    stage_rules: str
    project_summary: str
    required_artifact_summaries: dict[str, str] = field(default_factory=dict)
    human_comments: list[str] = field(default_factory=list)
    # One entry per RAG chunk that survived step 7's budget: source_id,
    # source_title, chunk_id, content, similarity, content_type, stage,
    # tags — see app/services/retrieval.py's RetrievedChunk.
    rag_context: list[dict[str, Any]] = field(default_factory=list)
    output_format: str = ""
    validation_checklist: list[str] = field(default_factory=list)
    token_budget_report: dict[str, Any] = field(default_factory=dict)

    def assembled_context_text(self) -> str:
        """Step 8's actual "final LLM input context" as one string —
        stage_rules, project_summary, required_artifact_summaries,
        rag_context, and human_comments, in that priority order, exactly
        as they survived step 7's trimming. `system_prompt` is a separate
        channel to the model (the system turn, not the user turn) and is
        deliberately not included here."""
        parts = [self.stage_rules, self.project_summary]
        for artifact_type, content in self.required_artifact_summaries.items():
            parts.append(f"## {artifact_type}\n{content}")
        if self.rag_context:
            parts.append("# Internal Knowledge Base — relevant excerpts")
            for chunk in self.rag_context:
                parts.append(f"## Source: {chunk['source_title']}\n{chunk['content']}")
        if self.human_comments:
            parts.append("# Feedback from the most recent human review")
            parts += [f"- {c}" for c in self.human_comments]
        return "\n\n".join(p for p in parts if p)


class ContextBuilderService:
    def __init__(self, db: Session):
        self.db = db

    def build(
        self,
        *,
        project_id: uuid.UUID,
        node_id: uuid.UUID,
        active_prompt: AgentPrompt,
        freeform_context: dict[str, Any] | None = None,
        full_content_artifact_types: set[str] | None = None,
        force_full_content: bool = False,
    ) -> ContextBuilderResult:
        """Runs the full eight-step pipeline. `active_prompt` (which agent
        role/version to build for) is a caller input, not something this
        service selects — that's app/api/routes/agent_runs.py's job, same
        as it already is for the existing generation path."""
        freeform_context = freeform_context or {}
        full_content_artifact_types = full_content_artifact_types or set()

        # 1. Load current project.
        project = self.db.get(Project, project_id)
        if project is None:
            raise ContextBuilderError(f"project_id {project_id} does not match an existing project")

        # 2. Load current workflow node.
        node = (
            self.db.query(WorkflowNode)
            .filter(WorkflowNode.id == node_id, WorkflowNode.project_id == project_id)
            .first()
        )
        if node is None:
            raise ContextBuilderError(f"workflow_node_id {node_id} is not a workflow node of project {project_id}")

        # 3 + 4. Load required approved artifacts and their summaries —
        # reuses GraphEngineService's own resolution rather than
        # re-querying Artifact directly, so "what counts as approved" and
        # "what's a required input" stay defined in exactly one place.
        inputs = GraphEngineService(self.db).resolve_required_inputs(
            project=project, node=node, freeform_context=freeform_context
        )

        # 5. Load latest human review comments.
        review_comments = fetch_recent_review_comments(self.db, node)

        # 6. Retrieve relevant RAG chunks — stage-aware, capped by this
        # node's own rag_top_k/max_rag_tokens (see app/models/workflow.py).
        retrieved_chunks = retrieve_relevant_chunks(
            self.db,
            project=project,
            node=node,
            freeform_context=freeform_context,
            approved_inputs=inputs.approved_artifact_content,
            top_k=node.rag_top_k,
            max_rag_tokens=node.max_rag_tokens,
        )

        # 7. Apply token budget rules.
        blocks, artifact_labels, rag_labels, comment_labels = self._build_blocks(
            project=project,
            node=node,
            approved_artifact_content=inputs.approved_artifact_content,
            approved_artifact_summaries=inputs.approved_artifact_summaries,
            full_content_artifact_types=full_content_artifact_types,
            force_full_content=force_full_content,
            retrieved_chunks=retrieved_chunks,
            review_comments=review_comments,
        )
        budget_result = TokenBudgetService(
            context_token_budget=node.context_token_budget, output_token_budget=node.output_token_budget
        ).build(blocks)
        fitted_by_label = {b.label: b for b in budget_result.blocks}

        def _content(label: str) -> str:
            fitted = fitted_by_label.get(label)
            return fitted.content if fitted and fitted.included else ""

        required_artifact_summaries = {
            artifact_type: _content(label) for artifact_type, label in artifact_labels.items() if _content(label)
        }

        rag_context = [
            {
                "source_id": chunk.source_id,
                "source_title": chunk.source_title,
                "chunk_id": chunk.chunk_id,
                "content": chunk.content,
                "similarity": chunk.similarity,
                "content_type": chunk.content_type,
                "stage": chunk.stage,
                "tags": chunk.tags,
            }
            for chunk, label in zip(retrieved_chunks, rag_labels)
            if fitted_by_label.get(label) and fitted_by_label[label].included
        ]

        human_comments = [
            comment
            for comment, label in zip(review_comments, comment_labels)
            if fitted_by_label.get(label) and fitted_by_label[label].included
        ]

        # 8. Produce the final LLM input context.
        return ContextBuilderResult(
            system_prompt=active_prompt.system_prompt,
            stage_rules=_content("stage_rules"),
            project_summary=_content("project_summary"),
            required_artifact_summaries=required_artifact_summaries,
            human_comments=human_comments,
            rag_context=rag_context,
            output_format=active_prompt.output_format,
            validation_checklist=list(active_prompt.validation_checklist),
            token_budget_report=budget_result.to_report_dict(),
        )

    @staticmethod
    def _build_blocks(
        *,
        project: Project,
        node: WorkflowNode,
        approved_artifact_content: dict[str, str],
        approved_artifact_summaries: dict[str, str],
        full_content_artifact_types: set[str],
        force_full_content: bool,
        retrieved_chunks: list[RetrievedChunk],
        review_comments: list[str],
    ) -> tuple[list[ContextBlock], dict[str, str], list[str], list[str]]:
        """Builds the priority-tagged block list for step 7, plus the
        label lookups `build` needs to map fitted blocks back onto their
        named output fields (artifact_type -> label, and the RAG-chunk /
        comment label lists, positional with their own input lists)."""
        blocks: list[ContextBlock] = [
            ContextBlock(
                priority="P0",
                label="stage_rules",
                compressible=True,
                content=(
                    f"# Current stage: {node.name} (`{node.node_key}`)\n"
                    f"{node.description}\n"
                    f"Output artifact type to produce: `{node.output_artifact_type}`"
                ),
            ),
            ContextBlock(
                priority="P1",
                label="project_summary",
                compressible=True,
                content=(
                    f"# Project: {project.name}\n"
                    f"Business owner: {project.business_owner}\n"
                    f"Project description: {project.description or 'Not provided.'}"
                ),
            ),
        ]

        artifact_labels: dict[str, str] = {}
        for artifact_type, full_content in approved_artifact_content.items():
            label = f"artifact:{artifact_type}"
            artifact_labels[artifact_type] = label
            needs_full = force_full_content or artifact_type in full_content_artifact_types
            content = full_content if needs_full else (approved_artifact_summaries.get(artifact_type) or full_content)
            blocks.append(ContextBlock(priority="P2", label=label, content=content, compressible=True))

        rag_labels: list[str] = []
        for chunk in retrieved_chunks:
            label = f"rag:{chunk.chunk_id}"
            rag_labels.append(label)
            blocks.append(
                ContextBlock(priority="P3", label=label, content=f"## Source: {chunk.source_title}\n{chunk.content}")
            )

        comment_labels: list[str] = []
        for i, comment in enumerate(review_comments):
            label = f"comment:{i}"
            comment_labels.append(label)
            blocks.append(ContextBlock(priority="P4", label=label, content=comment))

        return blocks, artifact_labels, rag_labels, comment_labels
