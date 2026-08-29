from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ReviewStatus


class Review(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A human review round against one specific artifact version.

    This is the record of the human-in-the-loop approval principle: a
    stage that `requires_human_approval` cannot advance until one of these
    reaches `APPROVED`.
    """

    __tablename__ = "reviews"

    artifact_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifact_versions.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalized alongside artifact_version_id to make "pending reviews
    # for this node" queries direct, without joining through artifacts.
    workflow_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_nodes.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, native_enum=False, length=20, validate_strings=True),
        default=ReviewStatus.PENDING,
        nullable=False,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    artifact_version: Mapped["ArtifactVersion"] = relationship("ArtifactVersion", back_populates="reviews")
    workflow_node: Mapped["WorkflowNode"] = relationship("WorkflowNode")
    reviewer: Mapped["User"] = relationship("User")
    comments: Mapped[list["ReviewComment"]] = relationship(
        "ReviewComment", back_populates="review", cascade="all, delete-orphan", order_by="ReviewComment.created_at"
    )


class ReviewComment(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """A single comment left on a review (feedback, rationale, discussion)."""

    __tablename__ = "review_comments"

    review_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    review: Mapped["Review"] = relationship("Review", back_populates="comments")
    author: Mapped["User"] = relationship("User")
