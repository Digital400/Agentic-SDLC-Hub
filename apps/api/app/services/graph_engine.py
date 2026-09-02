"""GraphEngineService — the one place that reads or changes a workflow
node's status.

Supersedes the old `app/services/workflow_progress.py` (its two functions,
`resolve_required_inputs` and `unlock_next_nodes`, now live here as methods,
with the same behavior plus the graph-engine rules below). Nothing else in
the codebase should set `WorkflowNode.status`, `blocked_reason`, or
`override_reason` directly — route handlers call this service instead, so
every transition is validated and audited the same way regardless of which
endpoint triggered it.

Core rules:
1. A node can only run (`validate_can_run`) while its status is one of
   `RUNNABLE_STATUSES` *and* every required upstream artifact exists and is
   APPROVED (or, for a freeform input with no upstream artifact, was
   supplied in the run's own input_context). Both halves are graph rules —
   the first checks the node's own state, the second checks its
   prerequisites' state.
2. A node only unlocks a downstream node (`unlock_next_nodes`) once *every*
   non-rework incoming edge's source is in `_SATISFIED_PREDECESSOR_STATUSES`
   — this is what makes parallel branches wait for each other, and what
   lets a node with two independent downstream nodes unlock both at once
   (fan-out) rather than just the first one found.
3. `mark_blocked` is the only path to BLOCKED, always with a reason.
4. `manual_override` is the only path that bypasses rules 1-2 — it can put
   a node in any status regardless of graph state, but always requires a
   reason and always writes an audit log entry (see `record_audit_log`).
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models import Artifact, ArtifactStatus, ArtifactVersion, Project, WorkflowNode, WorkflowStatus
from app.services.audit import record_audit_log
from app.services.markdown_sections import find_section

# A node may only be run from one of these states. Everything else is
# rejected by validate_can_run with a clear reason:
#   LOCKED              -> prerequisites not satisfied yet (rule 1)
#   RUNNING             -> a run is already in flight for this node
#   WAITING_FOR_REVIEW, APPROVED, COMPLETED, SKIPPED -> already past this
#     stage's own work; re-running needs a new version/manual override, not
#     a plain run
#   BLOCKED             -> needs manual_override first, not a plain run
RUNNABLE_STATUSES = frozenset(
    {
        WorkflowStatus.READY,
        WorkflowStatus.NEEDS_CHANGES,
        WorkflowStatus.WAITING_FOR_INPUT,
        WorkflowStatus.FAILED,  # retrying a failed run is allowed directly
    }
)

# A predecessor counts as satisfied whether it required human approval
# (ending at APPROVED), completed without one (COMPLETED), or was
# deliberately bypassed (SKIPPED, via manual override) — see
# docs/architecture.md on WorkflowStatus vs ArtifactStatus.
_SATISFIED_PREDECESSOR_STATUSES = (WorkflowStatus.APPROVED, WorkflowStatus.COMPLETED, WorkflowStatus.SKIPPED)


@dataclass
class RequiredInputsResult:
    approved_artifact_content: dict[str, str] = field(default_factory=dict)  # artifact_type -> content_markdown (fullContent)
    # artifact_type -> agentContextSummary (see app/services/artifact_summary.py)
    # — what app/services/ai_generation.py's build_prioritized_context uses
    # by default instead of the full content above.
    approved_artifact_summaries: dict[str, str] = field(default_factory=dict)
    missing_reasons: list[str] = field(default_factory=list)  # empty means nothing is missing


@dataclass
class GraphValidationResult:
    can_run: bool
    reasons: list[str] = field(default_factory=list)
    approved_artifact_content: dict[str, str] = field(default_factory=dict)
    approved_artifact_summaries: dict[str, str] = field(default_factory=dict)


class GraphEngineService:
    def __init__(self, db: Session):
        self.db = db

    # --- Rule 1: can this node run? ------------------------------------------------

    def resolve_required_inputs(
        self, *, project: Project, node: WorkflowNode, freeform_context: dict[str, Any]
    ) -> RequiredInputsResult:
        """Loads `node.required_inputs`, split into two kinds, and reports
        anything missing.

        - **Artifact-type inputs**: a required_inputs entry that matches
          some other node's `output_artifact_type` in this project's own
          workflow graph. Each must have an APPROVED artifact in this
          project, or it's reported missing — this is what makes rule 1's
          "required artifacts exist and are approved" real rather than
          advisory.
        - **Freeform inputs**: anything else (e.g. "stakeholder_request"
          for the very first stage, which has no upstream artifact). Must
          be supplied via `freeform_context`, or it's reported missing.

        SCOPING (Scrum story lanes): a project-level node (`story_id is
        None`) only ever sees other project-level nodes/artifacts —
        unchanged, a no-op for the existing MVP workflow. A per-story lane
        node additionally sees the project-level scope (so a lane's
        `story_lld` can still resolve the shared, project-level
        `story_backlog`/`hld_document` as required inputs) PLUS its own
        lane's nodes/artifacts — but never another lane's, so two lanes'
        same-named nodes (e.g. both called "testing") never resolve each
        other's artifacts as a shared "approved input" — see
        app/models/workflow.py's `story_id` column and
        app/services/story_lane_templates.py.
        """
        scope_nodes = [
            n for n in project.workflow_nodes if n.story_id is None or n.story_id == node.story_id
        ]
        known_artifact_types = {n.output_artifact_type for n in scope_nodes}
        result = RequiredInputsResult()

        for required in node.required_inputs:
            if required in known_artifact_types:
                query = self.db.query(Artifact).filter(
                    Artifact.project_id == project.id,
                    Artifact.artifact_type == required,
                    Artifact.status == ArtifactStatus.APPROVED,
                )
                if node.story_id is not None:
                    query = query.filter(
                        (Artifact.story_id == node.story_id) | (Artifact.story_id.is_(None))
                    )
                else:
                    query = query.filter(Artifact.story_id.is_(None))
                artifact = query.order_by(Artifact.updated_at.desc()).first()
                if artifact is None or artifact.current_version is None:
                    result.missing_reasons.append(f"required input '{required}' has no approved artifact yet")
                else:
                    version = artifact.current_version
                    result.approved_artifact_content[required] = version.content_markdown
                    # Falls back to the full content itself only if this
                    # version predates app/services/artifact_summary.py or
                    # summarization hasn't run for some other reason —
                    # build_prioritized_context truncates that fallback
                    # itself (see its _fallback_summary), so the full text
                    # is passed through here rather than pre-truncated.
                    result.approved_artifact_summaries[required] = version.agent_context_summary or version.content_markdown
            elif not freeform_context.get(required):
                result.missing_reasons.append(f"required input '{required}' was not provided in input_context")

        return result

    def validate_can_run(
        self, *, project: Project, node: WorkflowNode, freeform_context: dict[str, Any]
    ) -> GraphValidationResult:
        """The full graph-rule gate a run must pass before an agent is
        ever invoked — combines the node's own state (must be
        RUNNABLE_STATUSES) with its prerequisites' state (resolved inputs).
        Called by app/api/routes/agent_runs.py before every run."""
        reasons: list[str] = []

        if node.status not in RUNNABLE_STATUSES:
            allowed = ", ".join(sorted(s.value for s in RUNNABLE_STATUSES))
            reasons.append(f"node status is {node.status.value}; must be one of: {allowed}")

        inputs = self.resolve_required_inputs(project=project, node=node, freeform_context=freeform_context)
        reasons.extend(inputs.missing_reasons)

        return GraphValidationResult(
            can_run=not reasons,
            reasons=reasons,
            approved_artifact_content=inputs.approved_artifact_content,
            approved_artifact_summaries=inputs.approved_artifact_summaries,
        )

    # --- State transitions a run drives -------------------------------------------

    def mark_running(self, node: WorkflowNode) -> None:
        node.status = WorkflowStatus.RUNNING

    def mark_waiting_for_input(self, node: WorkflowNode) -> None:
        node.status = WorkflowStatus.WAITING_FOR_INPUT

    def mark_ready(self, node: WorkflowNode) -> None:
        """Back to READY — e.g. a run failed and should be retryable, or a
        clarification was answered without changing the node's place in
        the graph."""
        node.status = WorkflowStatus.READY

    def mark_failed(self, node: WorkflowNode) -> None:
        node.status = WorkflowStatus.FAILED

    def mark_waiting_for_review(self, node: WorkflowNode) -> None:
        node.status = WorkflowStatus.WAITING_FOR_REVIEW

    def mark_completed(self, node: WorkflowNode) -> None:
        """For a stage with no approval gate reaching its natural end —
        distinct from APPROVED, which only ever comes from a review
        decision."""
        node.status = WorkflowStatus.COMPLETED

    def mark_approved(self, node: WorkflowNode) -> None:
        """A review decision approved this node's artifact — see
        app/api/routes/reviews.py's approve_review. A satisfied
        predecessor for unlock_next_nodes."""
        node.status = WorkflowStatus.APPROVED

    def mark_needs_changes(self, node: WorkflowNode) -> None:
        """A review decision asked for changes — see
        app/api/routes/reviews.py's request_changes. Runnable again once
        reworked (see RUNNABLE_STATUSES)."""
        node.status = WorkflowStatus.NEEDS_CHANGES

    # --- Rule 3: blocked -----------------------------------------------------------

    def mark_blocked(
        self, node: WorkflowNode, *, reason: str, actor_user_id: uuid.UUID | None = None
    ) -> None:
        """The only path to BLOCKED. A rejected review is the most common
        cause (see app/api/routes/reviews.py's reject_review), but this is
        also called directly for any other hard stop."""
        previous_status = node.status
        node.status = WorkflowStatus.BLOCKED
        node.blocked_reason = reason

        record_audit_log(
            self.db,
            project_id=node.project_id,
            actor_user_id=actor_user_id,
            action="workflow_node.blocked",
            entity_type="WorkflowNode",
            entity_id=node.id,
            extra_data={"node_key": node.node_key, "from": previous_status.value, "reason": reason},
        )

    # --- Rule 4: manual override -----------------------------------------------------

    def manual_override(
        self,
        node: WorkflowNode,
        *,
        new_status: WorkflowStatus,
        reason: str,
        actor_user_id: uuid.UUID,
    ) -> None:
        """Force a node to any status regardless of rules 1-2 — the escape
        hatch for "the graph engine's rules don't fit this real situation"
        (e.g. skipping a stage that doesn't apply to this project, or
        unblocking a node after resolving whatever blocked it out of
        band). Always requires a reason and always audited: this bypasses
        the rules that keep the graph consistent, so unlike every other
        transition above, it must be traceable to a specific person and a
        specific justification.
        """
        previous_status = node.status
        node.status = new_status
        node.override_reason = reason
        if new_status != WorkflowStatus.BLOCKED:
            node.blocked_reason = None

        record_audit_log(
            self.db,
            project_id=node.project_id,
            actor_user_id=actor_user_id,
            action="workflow_node.manual_override",
            entity_type="WorkflowNode",
            entity_id=node.id,
            extra_data={
                "node_key": node.node_key,
                "from": previous_status.value,
                "to": new_status.value,
                "reason": reason,
            },
        )

    # --- Rule 2: unlocking downstream nodes -------------------------------------------

    def unlock_next_nodes(self, approved_node: WorkflowNode) -> list[WorkflowNode]:
        """Set each eligible downstream node to READY, return the ones
        unlocked.

        A downstream node is eligible when every non-rework edge pointing
        at it comes from a node that's already satisfied (APPROVED,
        COMPLETED, or SKIPPED), and it hasn't already moved past LOCKED —
        so this never regresses or re-triggers a node that's already
        running, done, or blocked. Iterating every forward target (rather
        than stopping at the first) is what makes parallel fan-out work: a
        node with two independent downstream nodes unlocks both in the
        same call, and a node with two upstream prerequisites only unlocks
        once *both* have reported in, however many calls that takes.
        """
        unlocked: list[WorkflowNode] = []

        # "rework" edges point backward (e.g. Testing -> Implementation)
        # and don't represent a forward prerequisite relationship, so
        # they're excluded from both the outgoing traversal and the
        # incoming check below.
        forward_targets = [edge.target_node for edge in approved_node.outgoing_edges if edge.label != "rework"]

        for candidate in forward_targets:
            if candidate.status != WorkflowStatus.LOCKED:
                continue

            prerequisite_edges = [edge for edge in candidate.incoming_edges if edge.label != "rework"]
            all_prerequisites_satisfied = all(
                edge.source_node.status in _SATISFIED_PREDECESSOR_STATUSES for edge in prerequisite_edges
            )

            if all_prerequisites_satisfied:
                candidate.status = WorkflowStatus.READY
                unlocked.append(candidate)

        return unlocked

    # --- Evidence gate: approval can require a documented, non-empty section ---------

    def validate_evidence_requirement(self, node: WorkflowNode, version: ArtifactVersion) -> str | None:
        """A stage can name a required section (`WorkflowNode.
        required_evidence_section`, e.g. "Test Evidence" on Testing) that
        must actually be present, and non-empty, in the artifact version
        being approved — not just a human's checklist tick. Returns None
        when the node has no such requirement, or when it's satisfied;
        otherwise a human-readable reason to reject the approval.

        This is a structural check (the heading exists and has real
        content) — it does not verify the substance of what's written
        there (e.g. that linked CI results are genuine)."""
        if not node.required_evidence_section:
            return None

        section = find_section(version.content_markdown, node.required_evidence_section)
        if section is None:
            return (
                f"Cannot approve — this stage requires a '{node.required_evidence_section}' section "
                "documenting evidence, and the current version doesn't have one."
            )
        if not section["content"].strip():
            return (
                f"Cannot approve — the '{node.required_evidence_section}' section is present but empty; "
                "it must document real evidence before this stage can be approved."
            )
        return None
