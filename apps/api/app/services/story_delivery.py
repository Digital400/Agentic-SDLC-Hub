"""Creates and advances one story's own delivery lane —
StoryDeliveryLane/StoryDeliveryNode/StoryDeliveryEdge — a dedicated,
self-contained per-story graph, deliberately NOT built on the
project-level WorkflowNode/WorkflowEdge graph engine (see
app/services/graph_engine.py). That engine models one shared graph per
*project*; a story delivery lane is a separate, simpler, strictly
sequential graph per *story*, and reusing the project-level engine here
would mean scoping every one of its rules by story_id — this module is
that "what if we didn't reuse it" alternative, built on its own tables
instead.

DEFAULT LANE SHAPE: a straight sequence, no fan-out or rework edges.
LLD_REVIEW / HUMAN_CODE_REVIEW / QA_APPROVAL are real, distinct nodes
here (unlike an earlier lane implementation in this codebase's history,
which folded review gates into their parent node) — each requires
approval (`requires_approval=True`) but this module does not itself
enforce who may approve one; that's for a future permissions layer, the
same way app/services/permissions.py's STAGE_APPROVE_ROLES is a separate
concern from app/services/graph_engine.py's own state machine.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import (
    Story,
    StoryActivityLog,
    StoryDeliveryEdge,
    StoryDeliveryLane,
    StoryDeliveryLaneStatus,
    StoryDeliveryNode,
    StoryDeliveryNodeStatus,
    User,
)


class StoryDeliveryError(Exception):
    """Raised when a story delivery lane action can't proceed."""


# (node_key, display name, requires_approval) — the only place this exact
# sequence/order is defined. `requires_approval` marks the three review
# gates (LLD_REVIEW, HUMAN_CODE_REVIEW, QA_APPROVAL) as real, distinct,
# approval-gated nodes rather than folding them into IMPLEMENTATION/
# PULL_REQUEST/TESTING's own status.
DEFAULT_STORY_DELIVERY_NODES: tuple[tuple[str, str, bool], ...] = (
    ("STORY_READY", "Story Ready", False),
    ("STORY_LLD", "Story LLD", False),
    ("LLD_REVIEW", "LLD Review", True),
    ("IMPLEMENTATION", "Implementation", False),
    ("PULL_REQUEST", "Pull Request", False),
    ("PR_REVIEW_AGENT", "PR Review Agent", False),
    ("HUMAN_CODE_REVIEW", "Human Code Review", True),
    ("TESTING", "Testing", False),
    ("QA_APPROVAL", "QA Approval", True),
    ("RELEASE_READY", "Release Ready", False),
)

# node_key -> the role advisory-assigned to it (StoryDeliveryNode.assigned_role)
# — mirrors app/services/permissions.py's STAGE_EDIT_ROLES/STAGE_APPROVE_ROLES
# spirit (who naturally owns this stage) without depending on that module,
# since these node_keys don't exist in its per-project-stage tables.
_NODE_ASSIGNED_ROLE: dict[str, str] = {
    "STORY_READY": "PRODUCT_OWNER",
    "STORY_LLD": "ARCHITECT",
    "LLD_REVIEW": "TECH_LEAD",
    "IMPLEMENTATION": "DEVELOPER",
    "PULL_REQUEST": "DEVELOPER",
    "PR_REVIEW_AGENT": "TECH_LEAD",
    "HUMAN_CODE_REVIEW": "TECH_LEAD",
    "TESTING": "QA",
    "QA_APPROVAL": "QA",
    "RELEASE_READY": "PRODUCT_OWNER",
}


def _log(db: Session, *, story: Story, lane: StoryDeliveryLane | None, node: StoryDeliveryNode | None,
         action: str, actor: User | None, details: dict | None = None) -> None:
    db.add(
        StoryActivityLog(
            story_id=story.id,
            lane_id=lane.id if lane is not None else None,
            node_id=node.id if node is not None else None,
            action=action,
            actor_user_id=actor.id if actor is not None else None,
            details=details,
        )
    )


def create_story_delivery_lane(db: Session, *, story: Story, created_by: User) -> StoryDeliveryLane:
    """Materializes the default 10-node sequential lane for `story`. One
    lane per story — raises if `story` already has one (see
    StoryDeliveryLane's unique `story_id`)."""
    if story.delivery_lane is not None:
        raise StoryDeliveryError(f"Story {story.id} already has a delivery lane.")

    lane = StoryDeliveryLane(
        project_id=story.project_id, story_id=story.id, status=StoryDeliveryLaneStatus.ACTIVE, created_by_id=created_by.id,
    )
    db.add(lane)
    db.flush()

    nodes: list[StoryDeliveryNode] = []
    for order_index, (node_key, name, requires_approval) in enumerate(DEFAULT_STORY_DELIVERY_NODES):
        node = StoryDeliveryNode(
            lane_id=lane.id,
            node_key=node_key,
            name=name,
            status=StoryDeliveryNodeStatus.READY if order_index == 0 else StoryDeliveryNodeStatus.LOCKED,
            assigned_role=_NODE_ASSIGNED_ROLE.get(node_key),
            requires_approval=requires_approval,
            order_index=order_index,
        )
        db.add(node)
        nodes.append(node)
    db.flush()

    for source, target in zip(nodes, nodes[1:]):
        db.add(StoryDeliveryEdge(lane_id=lane.id, source_node_id=source.id, target_node_id=target.id))
    db.flush()

    lane.current_node_id = nodes[0].id
    db.flush()

    _log(db, story=story, lane=lane, node=nodes[0], action="story_lane.created", actor=created_by,
         details={"node_count": len(nodes)})

    return lane


def advance_lane(db: Session, *, lane: StoryDeliveryLane, completed_node: StoryDeliveryNode) -> StoryDeliveryNode | None:
    """Called once `completed_node` reaches COMPLETED — unlocks its
    immediate successor (the lane is a straight sequence, so "immediate
    successor" is unambiguous) into READY, and advances the lane's
    `current_node_id`. Returns the newly-unlocked node, or None if
    `completed_node` was the last node in the lane (the lane itself is
    then marked COMPLETED)."""
    next_node = (
        db.query(StoryDeliveryNode)
        .filter(StoryDeliveryNode.lane_id == lane.id, StoryDeliveryNode.order_index == completed_node.order_index + 1)
        .first()
    )
    if next_node is None:
        lane.status = StoryDeliveryLaneStatus.COMPLETED
        db.flush()
        return None

    if next_node.status == StoryDeliveryNodeStatus.LOCKED:
        next_node.status = StoryDeliveryNodeStatus.READY
    lane.current_node_id = next_node.id
    db.flush()
    return next_node
