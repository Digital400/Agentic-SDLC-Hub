"""Graph-rule and audit-log tests for the Low-Level Design stage.

Covers:
  6. LLD can start only after Story Crafting is approved.
  7. Implementation cannot start until LLD is approved.
  9. Audit logs for LLD generation, review request, approval, and rejection.
  10. LLD workflow locking/unlocking.

Follows test_graph_engine.py / test_pr_review_gate.py's exact conventions:
plain functions, state built via tests/conftest.py's factories, the
db/project/actor fixtures, a fresh GraphEngineService(db) per test.
"""

import uuid

import pytest

from app.api.routes.reviews import approve_review, reject_review, request_changes
from app.models import Artifact, ArtifactStatus, ArtifactVersion, AuditLog, Review, ReviewStatus, User, UserRole, WorkflowNode, WorkflowStatus
from app.schemas.review import ReviewApproveRequest, ReviewCommentInput, ReviewDecisionWithReasonRequest, ReviewRequestChangesRequest
from app.services import artifact_summary
from app.services.graph_engine import GraphEngineService
from app.services.permissions import (
    STAGE_APPROVE_ROLES,
    STAGE_EDIT_ROLES,
    require_can_approve_stage,
    require_can_edit_stage,
)
from tests.conftest import make_approved_artifact, make_edge, make_node


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    """approve_review calls apply_summaries_to_version for real — force the
    deterministic mock path (see test_artifact_summary.py's own fixture of
    the same name) so this doesn't depend on a real Ollama/Anthropic/Gemini
    key being configured or reachable."""
    monkeypatch.setattr(artifact_summary, "get_active_provider", lambda: "mock")


def _build_chain(db, project):
    """story_crafting -> lld -> implementation, matching
    workflows/sdlc-workflow.json's real node keys/output types exactly."""
    story_crafting = make_node(
        db, project, node_key="story_crafting", order_index=0,
        status=WorkflowStatus.READY, output_artifact_type="story_backlog",
    )
    lld = make_node(
        db, project, node_key="lld", order_index=1,
        required_inputs=["story_backlog"], output_artifact_type="lld_document",
    )
    implementation = make_node(
        db, project, node_key="implementation", order_index=2,
        required_inputs=["lld_document", "story_backlog"], output_artifact_type="code_change",
        requires_human_approval=False,
    )
    make_edge(db, project, story_crafting, lld)
    make_edge(db, project, lld, implementation)
    return story_crafting, lld, implementation


# --- Rule 6: LLD can start only after Story Crafting is approved -------------------


def test_lld_stays_locked_while_story_crafting_is_unapproved(db, project, actor):
    story_crafting, lld, implementation = _build_chain(db, project)
    del implementation

    unlocked = GraphEngineService(db).unlock_next_nodes(story_crafting)

    assert unlocked == []
    assert lld.status == WorkflowStatus.LOCKED


def test_lld_cannot_run_before_story_crafting_is_approved(db, project, actor):
    story_crafting, lld, implementation = _build_chain(db, project)
    del implementation
    lld.status = WorkflowStatus.READY  # even if somehow made runnable...
    del story_crafting

    result = GraphEngineService(db).validate_can_run(project=project, node=lld, freeform_context={})

    assert result.can_run is False
    assert any("story_backlog" in reason for reason in result.reasons)


def test_lld_unlocks_once_story_crafting_is_approved(db, project, actor):
    story_crafting, lld, implementation = _build_chain(db, project)
    del implementation
    make_approved_artifact(db, project, story_crafting, actor, content="## Story: Login\n...")
    story_crafting.status = WorkflowStatus.APPROVED

    unlocked = GraphEngineService(db).unlock_next_nodes(story_crafting)

    assert lld in unlocked
    assert lld.status == WorkflowStatus.READY

    result = GraphEngineService(db).validate_can_run(project=project, node=lld, freeform_context={})
    assert result.can_run is True
    assert "story_backlog" in result.approved_artifact_content


# --- Rule 7: Implementation cannot start until LLD is approved ---------------------


def test_implementation_stays_locked_while_lld_is_unapproved(db, project, actor):
    story_crafting, lld, implementation = _build_chain(db, project)
    del story_crafting
    lld.status = WorkflowStatus.READY

    unlocked = GraphEngineService(db).unlock_next_nodes(lld)

    assert unlocked == []
    assert implementation.status == WorkflowStatus.LOCKED


def test_implementation_cannot_run_before_lld_is_approved(db, project, actor):
    story_crafting, lld, implementation = _build_chain(db, project)
    del story_crafting
    implementation.status = WorkflowStatus.READY

    result = GraphEngineService(db).validate_can_run(project=project, node=implementation, freeform_context={})

    assert result.can_run is False
    assert any("lld_document" in reason for reason in result.reasons)


def test_implementation_unlocks_once_lld_is_approved(db, project, actor):
    story_crafting, lld, implementation = _build_chain(db, project)
    del story_crafting
    make_approved_artifact(db, project, lld, actor, content="## Feature Overview\n...")
    lld.status = WorkflowStatus.APPROVED

    unlocked = GraphEngineService(db).unlock_next_nodes(lld)

    assert implementation in unlocked
    assert implementation.status == WorkflowStatus.READY

    # Node-level unlock only needed its direct predecessor (lld); running
    # implementation still needs story_backlog too — see
    # test_full_chain_unlocks_end_to_end for that fuller check.
    result = GraphEngineService(db).validate_can_run(project=project, node=implementation, freeform_context={})
    assert "lld_document" in result.approved_artifact_content


def test_full_chain_unlocks_end_to_end(db, project, actor):
    """The whole story_crafting -> lld -> implementation chain, one
    approval at a time — implementation must stay locked until BOTH of
    its required inputs (lld_document AND story_backlog) are approved,
    not just the first one."""
    story_crafting, lld, implementation = _build_chain(db, project)
    engine = GraphEngineService(db)

    assert lld.status == WorkflowStatus.LOCKED
    assert implementation.status == WorkflowStatus.LOCKED

    make_approved_artifact(db, project, story_crafting, actor, content="Stories.")
    story_crafting.status = WorkflowStatus.APPROVED
    engine.unlock_next_nodes(story_crafting)
    assert lld.status == WorkflowStatus.READY
    # story_crafting being approved alone doesn't unlock implementation —
    # it's not a direct predecessor of implementation in this graph.
    assert implementation.status == WorkflowStatus.LOCKED

    make_approved_artifact(db, project, lld, actor, content="Design.")
    lld.status = WorkflowStatus.APPROVED
    engine.unlock_next_nodes(lld)
    assert implementation.status == WorkflowStatus.READY


# --- Rule 5: Tech Lead review gate for LLD -----------------------------------------


def test_lld_edit_and_approve_roles_are_tech_lead_owned():
    assert UserRole.TECH_LEAD in STAGE_EDIT_ROLES["lld"]
    assert STAGE_APPROVE_ROLES["lld"] == {UserRole.TECH_LEAD}


def test_require_can_approve_stage_rejects_non_tech_lead_for_lld():
    from fastapi import HTTPException

    qa = User(id=uuid.uuid4(), email="qa@example.com", full_name="QA Person", role=UserRole.QA)
    with pytest.raises(HTTPException) as exc_info:
        require_can_approve_stage(qa, "lld")
    assert exc_info.value.status_code == 403


def test_require_can_edit_stage_allows_tech_lead_and_architect_for_lld():
    tech_lead = User(id=uuid.uuid4(), email="tl@example.com", full_name="Tech Lead", role=UserRole.TECH_LEAD)
    architect = User(id=uuid.uuid4(), email="arch@example.com", full_name="Architect", role=UserRole.ARCHITECT)
    # Should not raise.
    require_can_edit_stage(tech_lead, "lld")
    require_can_edit_stage(architect, "lld")


# --- Rule 9: audit logs for LLD generation, review request, approval, rejection ----


def _audit_actions(db, entity_type: str, entity_id) -> list[str]:
    return [
        row.action
        for row in db.query(AuditLog).filter(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id).all()
    ]


def test_mark_blocked_on_lld_writes_an_audit_log_entry(db, project, actor):
    """Exercises the same audit path a rejected LLD review takes (see
    app/api/routes/reviews.py's reject_review -> GraphEngineService.
    mark_blocked) — this is the "review rejection" half of rule 9."""
    _, lld, _ = _build_chain(db, project)
    lld.status = WorkflowStatus.WAITING_FOR_REVIEW

    GraphEngineService(db).mark_blocked(lld, reason="Review rejected: missing API contracts.", actor_user_id=actor.id)
    db.flush()  # record_audit_log only adds to the session; matches production's autoflush=False

    actions = _audit_actions(db, "WorkflowNode", lld.id)
    assert "workflow_node.blocked" in actions
    entry = next(r for r in db.query(AuditLog).filter(AuditLog.entity_id == lld.id) if r.action == "workflow_node.blocked")
    assert entry.actor_user_id == actor.id
    assert "missing API contracts" in entry.extra_data["reason"]


def test_manual_override_on_lld_writes_an_audit_log_entry(db, project, actor):
    """Covers the "manual override" escape hatch's own audit trail for
    completeness alongside the standard review-decision path above."""
    _, lld, _ = _build_chain(db, project)
    lld.status = WorkflowStatus.BLOCKED

    GraphEngineService(db).manual_override(
        lld, new_status=WorkflowStatus.READY, reason="Unblocking after out-of-band fix.", actor_user_id=actor.id
    )
    db.flush()

    actions = _audit_actions(db, "WorkflowNode", lld.id)
    assert "workflow_node.manual_override" in actions


# --- Rule 9 (continued): the review-decision routes' own audit logs ---------------
#
# Route handlers take `db: Session` as a plain parameter (FastAPI's
# `Depends(get_db)` is only resolved by the ASGI app) — calling them
# directly with a real session exercises the exact same code the HTTP
# layer runs, without needing a TestClient (no such harness exists
# elsewhere in this suite; this stays consistent with that).


def _make_lld_review(db, project, actor) -> tuple[Review, WorkflowNode]:
    _, lld, _ = _build_chain(db, project)
    tech_lead = User(email=f"{uuid.uuid4()}@example.com", full_name="Tech Lead Reviewer", role=UserRole.TECH_LEAD)
    db.add(tech_lead)
    db.flush()

    artifact = Artifact(
        project_id=project.id, workflow_node=lld, artifact_type="lld_document", title="LLD",
        status=ArtifactStatus.READY_FOR_REVIEW, created_by_id=actor.id,
    )
    db.add(artifact)
    db.flush()
    version = ArtifactVersion(
        artifact_id=artifact.id, version_number=1,
        content_markdown="## Feature Overview\n\nDetails.\n", created_by_id=actor.id,
    )
    db.add(version)
    db.flush()
    artifact.current_version_id = version.id
    db.flush()

    lld.status = WorkflowStatus.WAITING_FOR_REVIEW
    review = Review(artifact_version_id=version.id, workflow_node_id=lld.id, reviewer_id=tech_lead.id, status=ReviewStatus.PENDING)
    db.add(review)
    db.flush()
    return review, lld


def test_request_changes_on_lld_review_writes_an_audit_log_entry(db, project, actor):
    review, lld = _make_lld_review(db, project, actor)

    request_changes(
        review.id,
        ReviewRequestChangesRequest(comments=[ReviewCommentInput(body="Missing API Contracts section.", section_title="API Contracts")]),
        db,
    )

    assert "review.changes_requested" in _audit_actions(db, "Review", review.id)
    assert lld.status == WorkflowStatus.NEEDS_CHANGES


def test_approve_lld_review_writes_an_audit_log_entry_and_unlocks_implementation(db, project, actor):
    review, lld = _make_lld_review(db, project, actor)
    implementation = (
        db.query(WorkflowNode).filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == "implementation").first()
    )

    approve_review(review.id, ReviewApproveRequest(comment="Looks good."), db)

    assert "review.approved" in _audit_actions(db, "Review", review.id)
    assert lld.status == WorkflowStatus.APPROVED
    assert implementation.status == WorkflowStatus.READY


def test_reject_lld_review_writes_an_audit_log_entry_and_blocks_the_node(db, project, actor):
    review, lld = _make_lld_review(db, project, actor)

    reject_review(review.id, ReviewDecisionWithReasonRequest(comment="Design contradicts the approved HLD."), db)

    assert "review.rejected" in _audit_actions(db, "Review", review.id)
    assert lld.status == WorkflowStatus.BLOCKED
    assert lld.blocked_reason is not None
