"""Advances — and gates — a project's workflow graph.

Kept separate from individual routes since "what happens to the graph when
a stage is approved" (`unlock_next_nodes`) and "what does this stage need
before it can run" (`resolve_required_inputs`) are graph logic, not
review/agent-run request bookkeeping.
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import Artifact, ArtifactStatus, Project, WorkflowNode, WorkflowStatus

# A predecessor counts as satisfied whether it required human approval
# (ending at APPROVED) or not (ending at COMPLETED once that stage's own
# lifecycle finishes) — see docs/architecture.md on WorkflowStatus vs
# ArtifactStatus.
_SATISFIED_PREDECESSOR_STATUSES = (WorkflowStatus.APPROVED, WorkflowStatus.COMPLETED)


def unlock_next_nodes(db: Session, approved_node: WorkflowNode) -> list[WorkflowNode]:
    """Set each eligible downstream node to IN_PROGRESS, return the ones unlocked.

    A downstream node is eligible when every non-rework edge pointing at it
    comes from a node that's already APPROVED/COMPLETED, and it hasn't
    already moved past NOT_STARTED (so this never regresses or re-triggers
    a node that's already running, done, or blocked).
    """
    unlocked: list[WorkflowNode] = []

    # "rework" edges point backward (e.g. Testing -> Implementation) and
    # don't represent a forward prerequisite relationship, so they're
    # excluded from both the outgoing traversal and the incoming check
    # below.
    forward_targets = [edge.target_node for edge in approved_node.outgoing_edges if edge.label != "rework"]

    for candidate in forward_targets:
        if candidate.status != WorkflowStatus.NOT_STARTED:
            continue

        prerequisite_edges = [edge for edge in candidate.incoming_edges if edge.label != "rework"]
        all_prerequisites_satisfied = all(
            edge.source_node.status in _SATISFIED_PREDECESSOR_STATUSES for edge in prerequisite_edges
        )

        if all_prerequisites_satisfied:
            candidate.status = WorkflowStatus.IN_PROGRESS
            unlocked.append(candidate)

    return unlocked


@dataclass
class RequiredInputsResult:
    approved_artifact_content: dict[str, str] = field(default_factory=dict)  # artifact_type -> content_markdown
    missing_reasons: list[str] = field(default_factory=list)  # empty means the run is not blocked


def resolve_required_inputs(
    db: Session, *, project: Project, node: WorkflowNode, freeform_context: dict
) -> RequiredInputsResult:
    """Loads `node.required_inputs`, split into two kinds, and reports
    anything missing — this is the guardrail against skipping stages.

    - **Artifact-type inputs**: a required_inputs entry that matches some
      other node's `output_artifact_type` in this project's own workflow
      graph. Each must have an APPROVED artifact in this project, or the
      run is blocked — you cannot generate this stage's output until its
      prerequisite's output has actually been approved by a human.
    - **Freeform inputs**: anything else (e.g. "stakeholder_request" for
      the very first stage, which has no upstream artifact). Must be
      supplied via `freeform_context`, or the run is blocked.
    """
    known_artifact_types = {n.output_artifact_type for n in project.workflow_nodes}
    result = RequiredInputsResult()

    for required in node.required_inputs:
        if required in known_artifact_types:
            artifact = (
                db.query(Artifact)
                .filter(
                    Artifact.project_id == project.id,
                    Artifact.artifact_type == required,
                    Artifact.status == ArtifactStatus.APPROVED,
                )
                .order_by(Artifact.updated_at.desc())
                .first()
            )
            if artifact is None or artifact.current_version is None:
                result.missing_reasons.append(f"required input '{required}' has no approved artifact yet")
            else:
                result.approved_artifact_content[required] = artifact.current_version.content_markdown
        elif not freeform_context.get(required):
            result.missing_reasons.append(f"required input '{required}' was not provided in input_context")

    return result
