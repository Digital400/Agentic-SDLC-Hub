"""Unit tests for GraphEngineService — see app/services/graph_engine.py.

Covers the four scenarios required alongside the graph-engine rework:
node unlock logic, blocked node logic, the rejected-review loop, and
parallel node unlock (including its fan-in mirror: a node with two
prerequisites must wait for both).
"""

import uuid

from app.models import AuditLog, User, WorkflowStatus
from app.services.graph_engine import GraphEngineService
from tests.conftest import make_approved_artifact, make_edge, make_node


# --- 1. Node unlock logic --------------------------------------------------------


def test_unlock_next_nodes_unlocks_locked_successor_once_predecessor_satisfied(db, project, actor):
    node_a = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.APPROVED)
    node_b = make_node(db, project, node_key="node_b", order_index=1, status=WorkflowStatus.LOCKED)
    make_edge(db, project, node_a, node_b)

    engine = GraphEngineService(db)
    unlocked = engine.unlock_next_nodes(node_a)

    assert unlocked == [node_b]
    assert node_b.status == WorkflowStatus.READY


def test_unlock_next_nodes_leaves_successor_locked_if_predecessor_not_satisfied(db, project):
    node_a = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    node_b = make_node(db, project, node_key="node_b", order_index=1, status=WorkflowStatus.LOCKED)
    make_edge(db, project, node_a, node_b)

    engine = GraphEngineService(db)
    unlocked = engine.unlock_next_nodes(node_a)

    assert unlocked == []
    assert node_b.status == WorkflowStatus.LOCKED


def test_unlock_next_nodes_does_not_regress_a_node_past_locked(db, project):
    """A successor that's already moved past LOCKED (e.g. already RUNNING)
    must never be reset back to READY by a later unlock call."""
    node_a = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.APPROVED)
    node_b = make_node(db, project, node_key="node_b", order_index=1, status=WorkflowStatus.RUNNING)
    make_edge(db, project, node_a, node_b)

    engine = GraphEngineService(db)
    unlocked = engine.unlock_next_nodes(node_a)

    assert unlocked == []
    assert node_b.status == WorkflowStatus.RUNNING


def test_validate_can_run_requires_approved_upstream_artifact(db, project, actor):
    node_a = make_node(db, project, node_key="node_a", order_index=0, output_artifact_type="doc_a")
    node_b = make_node(
        db, project, node_key="node_b", order_index=1, status=WorkflowStatus.READY, required_inputs=["doc_a"]
    )

    engine = GraphEngineService(db)
    result = engine.validate_can_run(project=project, node=node_b, freeform_context={})
    assert result.can_run is False
    assert any("doc_a" in reason for reason in result.reasons)

    make_approved_artifact(db, project, node_a, actor, content="Approved doc A")
    result = engine.validate_can_run(project=project, node=node_b, freeform_context={})
    assert result.can_run is True
    assert result.approved_artifact_content["doc_a"] == "Approved doc A"


# --- 2. Blocked node logic --------------------------------------------------------


def test_mark_blocked_sets_status_reason_and_writes_audit_log(db, project, actor):
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.WAITING_FOR_REVIEW)

    engine = GraphEngineService(db)
    engine.mark_blocked(node, reason="Review rejected: needs rework", actor_user_id=actor.id)
    db.flush()  # record_audit_log only adds to the session; matches production's autoflush=False

    assert node.status == WorkflowStatus.BLOCKED
    assert node.blocked_reason == "Review rejected: needs rework"

    logs = db.query(AuditLog).filter(AuditLog.entity_id == node.id, AuditLog.action == "workflow_node.blocked").all()
    assert len(logs) == 1
    assert logs[0].actor_user_id == actor.id
    assert logs[0].extra_data["reason"] == "Review rejected: needs rework"


def test_validate_can_run_rejects_blocked_node(db, project):
    engine = GraphEngineService(db)
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    engine.mark_blocked(node, reason="Something went wrong")

    result = engine.validate_can_run(project=project, node=node, freeform_context={})
    assert result.can_run is False
    assert any("BLOCKED" in reason for reason in result.reasons)


def test_manual_override_unblocks_a_node_and_writes_its_own_audit_log(db, project, actor):
    engine = GraphEngineService(db)
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    engine.mark_blocked(node, reason="Blocked for testing")

    engine.manual_override(
        node, new_status=WorkflowStatus.READY, reason="Resolved out of band", actor_user_id=actor.id
    )
    db.flush()

    assert node.status == WorkflowStatus.READY
    assert node.blocked_reason is None  # cleared, since the new status isn't BLOCKED
    assert node.override_reason == "Resolved out of band"

    logs = (
        db.query(AuditLog)
        .filter(AuditLog.entity_id == node.id, AuditLog.action == "workflow_node.manual_override")
        .all()
    )
    assert len(logs) == 1
    assert logs[0].extra_data == {
        "node_key": "node_a",
        "from": "BLOCKED",
        "to": "READY",
        "reason": "Resolved out of band",
    }


# --- 3. Rejected review loop --------------------------------------------------------


def test_rejected_review_loop_blocks_then_overrides_then_reworks_and_unlocks_successor(db, project, actor):
    """Models the full cycle app/api/routes/reviews.py drives:
    a node whose review gets rejected -> BLOCKED (can't run) -> a manual
    override reopens it -> rework -> approved -> its successor unlocks.
    """
    engine = GraphEngineService(db)
    node_a = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.WAITING_FOR_REVIEW)
    node_b = make_node(db, project, node_key="node_b", order_index=1, status=WorkflowStatus.LOCKED)
    make_edge(db, project, node_a, node_b)

    # Review rejected — reviews.py's reject_review calls mark_blocked, not
    # a plain status set, and it's a hard stop distinct from "needs changes".
    engine.mark_blocked(node_a, reason="Review rejected: missing acceptance criteria", actor_user_id=actor.id)
    assert node_a.status == WorkflowStatus.BLOCKED

    # Can't run while blocked.
    validation = engine.validate_can_run(project=project, node=node_a, freeform_context={})
    assert validation.can_run is False

    # A manual override (Admin only, per permissions.py) reopens it for rework.
    engine.manual_override(
        node_a, new_status=WorkflowStatus.NEEDS_CHANGES, reason="Reopening for rework", actor_user_id=actor.id
    )
    assert node_a.status == WorkflowStatus.NEEDS_CHANGES

    # Now runnable again.
    validation = engine.validate_can_run(project=project, node=node_a, freeform_context={})
    assert validation.can_run is True

    # Rework happens (a new agent run / artifact version — out of scope
    # here), it goes back to review, and this time it's approved.
    engine.mark_waiting_for_review(node_a)
    engine.mark_approved(node_a)
    assert node_a.status == WorkflowStatus.APPROVED

    # The successor unlocks only now, not any earlier in the cycle.
    unlocked = engine.unlock_next_nodes(node_a)
    assert unlocked == [node_b]
    assert node_b.status == WorkflowStatus.READY


# --- 4. Parallel node unlock --------------------------------------------------------


def test_unlock_next_nodes_supports_fan_out_to_multiple_parallel_successors(db, project):
    """One node with two independent outgoing edges unlocks both
    downstream nodes in a single call."""
    source = make_node(db, project, node_key="source", order_index=0, status=WorkflowStatus.APPROVED)
    branch_a = make_node(db, project, node_key="branch_a", order_index=1, status=WorkflowStatus.LOCKED)
    branch_b = make_node(db, project, node_key="branch_b", order_index=1, status=WorkflowStatus.LOCKED)
    make_edge(db, project, source, branch_a)
    make_edge(db, project, source, branch_b)

    engine = GraphEngineService(db)
    unlocked = engine.unlock_next_nodes(source)

    assert {n.node_key for n in unlocked} == {"branch_a", "branch_b"}
    assert branch_a.status == WorkflowStatus.READY
    assert branch_b.status == WorkflowStatus.READY


def test_unlock_next_nodes_fan_in_waits_for_every_prerequisite(db, project):
    """The mirror case: a downstream node with two upstream prerequisites
    must not unlock until BOTH have reported in, however many separate
    unlock_next_nodes calls that takes."""
    upstream_a = make_node(db, project, node_key="upstream_a", order_index=0, status=WorkflowStatus.APPROVED)
    upstream_b = make_node(db, project, node_key="upstream_b", order_index=0, status=WorkflowStatus.READY)
    joined = make_node(db, project, node_key="joined", order_index=1, status=WorkflowStatus.LOCKED)
    make_edge(db, project, upstream_a, joined)
    make_edge(db, project, upstream_b, joined)

    engine = GraphEngineService(db)

    # Only the first of two prerequisites is satisfied — must stay LOCKED.
    unlocked = engine.unlock_next_nodes(upstream_a)
    assert unlocked == []
    assert joined.status == WorkflowStatus.LOCKED

    # The second prerequisite reports in — now it unlocks.
    engine.mark_approved(upstream_b)
    unlocked = engine.unlock_next_nodes(upstream_b)
    assert unlocked == [joined]
    assert joined.status == WorkflowStatus.READY


def test_unlock_next_nodes_ignores_rework_edges_in_both_directions(db, project):
    """A "rework" edge (e.g. Testing -> Implementation) is backward and
    must not count as a forward unlock path, nor as a prerequisite the
    target waits on."""
    forward_source = make_node(db, project, node_key="forward_source", order_index=0, status=WorkflowStatus.APPROVED)
    rework_source = make_node(db, project, node_key="rework_source", order_index=2, status=WorkflowStatus.LOCKED)
    target = make_node(db, project, node_key="target", order_index=1, status=WorkflowStatus.LOCKED)

    make_edge(db, project, forward_source, target)
    make_edge(db, project, rework_source, target, label="rework")

    engine = GraphEngineService(db)
    unlocked = engine.unlock_next_nodes(forward_source)

    # target unlocks from forward_source alone — rework_source (still
    # LOCKED) is excluded from the prerequisite check because its edge is
    # labeled "rework".
    assert unlocked == [target]
    assert target.status == WorkflowStatus.READY
