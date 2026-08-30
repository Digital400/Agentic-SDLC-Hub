"""Review-gate endpoints — the human-in-the-loop approval workflow.

Covers: request a review, list pending reviews, get one by id, approve /
request changes / reject, and add a standalone comment. See
docs/product-vision.md's human-in-the-loop principle: an artifact can only
move past a human-approval gate through one of these decisions.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import (
    Artifact,
    ArtifactStatus,
    Review,
    ReviewComment,
    ReviewStatus,
    User,
    WorkflowStatus,
)
from app.schemas.review import (
    ReviewApproveRequest,
    ReviewCommentCreate,
    ReviewCommentRead,
    ReviewCreate,
    ReviewDecisionWithReasonRequest,
    ReviewRead,
)
from app.services.audit import record_audit_log
from app.services.graph_engine import GraphEngineService
from app.services.permissions import require_can_approve_stage

router = APIRouter(prefix="/reviews", tags=["reviews"])


def _get_review_or_404(db: Session, review_id: uuid.UUID) -> Review:
    review = db.get(Review, review_id)
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Review {review_id} not found")
    return review


def _get_user_or_400(db: Session, user_id: uuid.UUID, field_name: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{field_name} {user_id} does not match an existing user")
    return user


# 1. Create review request -----------------------------------------------------


@router.post("", response_model=ReviewRead, status_code=status.HTTP_201_CREATED)
def create_review(payload: ReviewCreate, db: Session = Depends(get_db)) -> ReviewRead:
    """Open a review round against an artifact's current version.

    Only READY_FOR_REVIEW artifacts can be reviewed — that's the whole
    point of the gate: an artifact must be explicitly submitted (see
    `POST /artifacts/{id}/submit-for-review`) before anyone can act on it.
    """
    artifact = db.get(Artifact, payload.artifact_id)
    if artifact is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"artifact_id {payload.artifact_id} does not match an existing artifact")

    if artifact.status != ArtifactStatus.READY_FOR_REVIEW:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only READY_FOR_REVIEW artifacts can be reviewed (current status: {artifact.status.value}).",
        )
    if artifact.current_version_id is None:
        # Shouldn't happen if the artifact reached READY_FOR_REVIEW through
        # the normal submit-for-review gate, but guard against it directly
        # rather than trust that invariant blindly.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Artifact has no version to review.")

    existing_pending = (
        db.query(Review)
        .filter(Review.artifact_version_id == artifact.current_version_id, Review.status == ReviewStatus.PENDING)
        .first()
    )
    if existing_pending is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A review is already pending for this artifact's current version.")

    reviewer = _get_user_or_400(db, payload.reviewer_id, "reviewer_id")

    review = Review(
        artifact_version_id=artifact.current_version_id,
        workflow_node_id=artifact.workflow_node_id,
        reviewer=reviewer,
        status=ReviewStatus.PENDING,
    )
    db.add(review)
    db.flush()

    record_audit_log(
        db,
        project_id=artifact.project_id,
        actor_user_id=reviewer.id,
        action="review.created",
        entity_type="Review",
        entity_id=review.id,
        extra_data={"artifact_id": str(artifact.id), "artifact_version_id": str(artifact.current_version_id)},
    )

    db.commit()
    db.refresh(review)
    return ReviewRead.from_orm_review(review)


# 2. List pending reviews -------------------------------------------------------


@router.get("", response_model=list[ReviewRead])
def list_reviews(
    db: Session = Depends(get_db),
    review_status: ReviewStatus = Query(default=ReviewStatus.PENDING, alias="status"),
) -> list[ReviewRead]:
    """Defaults to pending reviews; pass `?status=APPROVED` etc. for others."""
    reviews = db.query(Review).filter(Review.status == review_status).order_by(Review.created_at).all()
    return [ReviewRead.from_orm_review(r) for r in reviews]


# 3. Get review by id -----------------------------------------------------------


@router.get("/{review_id}", response_model=ReviewRead)
def get_review(review_id: uuid.UUID, db: Session = Depends(get_db)) -> ReviewRead:
    return ReviewRead.from_orm_review(_get_review_or_404(db, review_id))


def _get_reviewable_artifact_or_409(db: Session, review: Review) -> Artifact:
    """Shared precondition for every decision endpoint: the review must
    still be open, and its artifact must still be READY_FOR_REVIEW (it
    could in principle have moved on some other way between the review
    being opened and decided)."""
    if review.status != ReviewStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Review already decided ({review.status.value}).")

    artifact = (
        db.query(Artifact)
        .filter(Artifact.current_version_id == review.artifact_version_id)
        .first()
    )
    if artifact is None or artifact.status != ArtifactStatus.READY_FOR_REVIEW:
        current = artifact.status.value if artifact else "unknown"
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only READY_FOR_REVIEW artifacts can be reviewed (current status: {current}).",
        )
    return artifact


def _record_decision(
    db: Session,
    *,
    review: Review,
    artifact: Artifact,
    decision_status: ReviewStatus,
    action: str,
    comment: str | None,
) -> None:
    review.status = decision_status
    review.decided_at = datetime.now(timezone.utc)

    if comment is not None:
        db.add(ReviewComment(review=review, author_id=review.reviewer_id, body=comment))

    record_audit_log(
        db,
        project_id=artifact.project_id,
        actor_user_id=review.reviewer_id,
        action=action,
        entity_type="Review",
        entity_id=review.id,
        extra_data={"artifact_id": str(artifact.id), "comment": comment},
    )


# 4. Approve review ---------------------------------------------------------------


@router.post("/{review_id}/approve", response_model=ReviewRead)
def approve_review(review_id: uuid.UUID, payload: ReviewApproveRequest, db: Session = Depends(get_db)) -> ReviewRead:
    review = _get_review_or_404(db, review_id)
    artifact = _get_reviewable_artifact_or_409(db, review)
    node = review.workflow_node
    require_can_approve_stage(review.reviewer, node.node_key)

    _record_decision(
        db, review=review, artifact=artifact, decision_status=ReviewStatus.APPROVED,
        action="review.approved", comment=payload.comment,
    )

    artifact.status = ArtifactStatus.APPROVED
    graph_engine = GraphEngineService(db)
    graph_engine.mark_approved(node)
    record_audit_log(
        db,
        project_id=artifact.project_id,
        actor_user_id=review.reviewer_id,
        action="workflow_node.status_changed",
        entity_type="WorkflowNode",
        entity_id=node.id,
        extra_data={"node_key": node.node_key, "to": WorkflowStatus.APPROVED.value},
    )

    unlocked = graph_engine.unlock_next_nodes(node)
    for next_node in unlocked:
        record_audit_log(
            db,
            project_id=artifact.project_id,
            actor_user_id=review.reviewer_id,
            action="workflow_node.unlocked",
            entity_type="WorkflowNode",
            entity_id=next_node.id,
            extra_data={"node_key": next_node.node_key, "unlocked_by": node.node_key},
        )

    db.commit()
    db.refresh(review)
    return ReviewRead.from_orm_review(review)


# 5. Request changes ---------------------------------------------------------------


@router.post("/{review_id}/request-changes", response_model=ReviewRead)
def request_changes(
    review_id: uuid.UUID, payload: ReviewDecisionWithReasonRequest, db: Session = Depends(get_db)
) -> ReviewRead:
    review = _get_review_or_404(db, review_id)
    artifact = _get_reviewable_artifact_or_409(db, review)
    node = review.workflow_node
    require_can_approve_stage(review.reviewer, node.node_key)

    _record_decision(
        db, review=review, artifact=artifact, decision_status=ReviewStatus.NEEDS_CHANGES,
        action="review.changes_requested", comment=payload.comment,
    )

    artifact.status = ArtifactStatus.NEEDS_CHANGES
    GraphEngineService(db).mark_needs_changes(node)
    record_audit_log(
        db,
        project_id=artifact.project_id,
        actor_user_id=review.reviewer_id,
        action="workflow_node.status_changed",
        entity_type="WorkflowNode",
        entity_id=node.id,
        extra_data={"node_key": node.node_key, "to": WorkflowStatus.NEEDS_CHANGES.value},
    )

    db.commit()
    db.refresh(review)
    return ReviewRead.from_orm_review(review)


# 6. Reject review ------------------------------------------------------------------


@router.post("/{review_id}/reject", response_model=ReviewRead)
def reject_review(
    review_id: uuid.UUID, payload: ReviewDecisionWithReasonRequest, db: Session = Depends(get_db)
) -> ReviewRead:
    review = _get_review_or_404(db, review_id)
    artifact = _get_reviewable_artifact_or_409(db, review)
    node = review.workflow_node
    require_can_approve_stage(review.reviewer, node.node_key)

    _record_decision(
        db, review=review, artifact=artifact, decision_status=ReviewStatus.REJECTED,
        action="review.rejected", comment=payload.comment,
    )

    artifact.status = ArtifactStatus.REJECTED
    # A rejected review is a hard stop, not just "needs rework" — see
    # app/services/graph_engine.py's WorkflowStatus docstring on why
    # REJECTED (the old node status) became BLOCKED rather than staying its
    # own status: it now needs a deliberate manual_override to move past,
    # the same as any other blocked node, rather than a plain resubmit.
    GraphEngineService(db).mark_blocked(
        node, reason=f"Review rejected: {payload.comment}", actor_user_id=review.reviewer_id
    )

    db.commit()
    db.refresh(review)
    return ReviewRead.from_orm_review(review)


# 7. Add review comment ---------------------------------------------------------------


@router.post("/{review_id}/comments", response_model=ReviewCommentRead, status_code=status.HTTP_201_CREATED)
def add_review_comment(review_id: uuid.UUID, payload: ReviewCommentCreate, db: Session = Depends(get_db)) -> ReviewComment:
    """Add discussion to a review — doesn't record a decision on its own.
    Use `/approve`, `/request-changes`, or `/reject` for that (each of
    which can also carry its own comment)."""
    review = _get_review_or_404(db, review_id)
    author = _get_user_or_400(db, payload.author_id, "author_id")

    comment = ReviewComment(review=review, author=author, body=payload.body)
    db.add(comment)
    db.flush()

    record_audit_log(
        db,
        project_id=review.workflow_node.project_id,
        actor_user_id=author.id,
        action="review_comment.created",
        entity_type="ReviewComment",
        entity_id=comment.id,
        extra_data={"review_id": str(review.id)},
    )

    db.commit()
    db.refresh(comment)
    return comment
