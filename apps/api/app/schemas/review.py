import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ReviewStatus


class ReviewCreate(BaseModel):
    artifact_id: uuid.UUID = Field(..., description="Must currently be READY_FOR_REVIEW.")
    reviewer_id: uuid.UUID = Field(..., description="Existing user id.")


class ReviewCommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    review_id: uuid.UUID
    author_id: uuid.UUID
    body: str
    created_at: datetime


class ReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    artifact_version_id: uuid.UUID
    workflow_node_id: uuid.UUID
    reviewer_id: uuid.UUID
    status: ReviewStatus
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime
    comments: list[ReviewCommentRead] = []

    # Denormalized for list/detail views — avoids the frontend needing
    # separate project/artifact/reviewer fetches just to render a review row.
    project_id: uuid.UUID
    project_name: str
    artifact_id: uuid.UUID
    artifact_title: str
    workflow_stage_name: str
    reviewer_name: str

    @classmethod
    def from_orm_review(cls, review) -> "ReviewRead":
        artifact = review.artifact_version.artifact
        return cls(
            id=review.id,
            artifact_version_id=review.artifact_version_id,
            workflow_node_id=review.workflow_node_id,
            reviewer_id=review.reviewer_id,
            status=review.status,
            decided_at=review.decided_at,
            created_at=review.created_at,
            updated_at=review.updated_at,
            comments=[ReviewCommentRead.model_validate(c) for c in review.comments],
            project_id=review.workflow_node.project_id,
            project_name=review.workflow_node.project.name,
            artifact_id=artifact.id,
            artifact_title=artifact.title,
            workflow_stage_name=review.workflow_node.name,
            reviewer_name=review.reviewer.full_name,
        )


class ReviewApproveRequest(BaseModel):
    comment: str | None = Field(default=None, description="Optional — approving without comment is fine.")


class ReviewDecisionWithReasonRequest(BaseModel):
    """Body for request-changes / reject — a reason is required for both:
    a reviewer can't ask for changes or reject with no feedback."""

    comment: str = Field(..., min_length=1)


class ReviewCommentCreate(BaseModel):
    author_id: uuid.UUID = Field(..., description="Existing user id.")
    body: str = Field(..., min_length=1)
