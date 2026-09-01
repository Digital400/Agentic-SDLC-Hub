import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ReviewStatus
from app.schemas.agent_run import AgentRunRead
from app.schemas.validators import NonBlankStr


class ReviewCreate(BaseModel):
    artifact_id: uuid.UUID = Field(..., description="Must currently be READY_FOR_REVIEW.")
    reviewer_id: uuid.UUID = Field(..., description="Existing user id.")


class ReviewCommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    review_id: uuid.UUID
    author_id: uuid.UUID
    body: str
    # Matches an ArtifactSection heading on the frontend when the reviewer
    # linked this comment to one — see ReviewComment.section_title. Null
    # for general, document-wide feedback.
    section_title: str | None = None
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
    comment: NonBlankStr | None = Field(default=None, description="Optional — approving without comment is fine.")


class ReviewDecisionWithReasonRequest(BaseModel):
    """Body for reject — a reason is required: a reviewer can't reject with
    no feedback. See ReviewRequestChangesRequest for the structured-comment
    equivalent used by request-changes."""

    comment: NonBlankStr


class ReviewCommentInput(BaseModel):
    """One structured piece of reviewer feedback — see
    ReviewComment.section_title. `section_title` should match one of the
    artifact's current section headings (case-insensitively) when the
    reviewer could point at a specific one; omit it for feedback that
    applies to the document as a whole."""

    body: NonBlankStr
    section_title: str | None = Field(
        default=None, description="An ArtifactSection title this comment is about, if it can be linked to one."
    )


class ReviewRequestChangesRequest(BaseModel):
    """Body for request-changes — rule 1 of the review-gate revision loop:
    at least one structured comment is required (a reviewer can't ask for
    changes with nothing to change), each optionally linked to a section
    (rule 2) so app/services/revision_agent.py can scope its revision."""

    comments: list[ReviewCommentInput] = Field(..., min_length=1)


class ReviewCommentCreate(BaseModel):
    author_id: uuid.UUID = Field(..., description="Existing user id.")
    body: NonBlankStr
    section_title: str | None = None


class RunRevisionAgentRequest(BaseModel):
    triggered_by_user_id: uuid.UUID = Field(
        ..., description="Existing user id — attributes the revision agent run and the new artifact version."
    )


class RevisionAgentRunResponse(BaseModel):
    """Response for POST /reviews/{id}/run-revision-agent — see
    app/services/revision_agent.py. `new_review` is set only when the
    revision succeeded and was resubmitted for review (rule 9); it's null
    when the agent instead asked a clarification question
    (`needs_clarification`), since nothing was saved or resubmitted in
    that case."""

    model_config = ConfigDict(from_attributes=True)

    agent_run: AgentRunRead
    needs_clarification: bool
    # The artifact section titles the revision actually rewrote — see
    # app/services/revision_agent.py's merge_section_revisions. Empty when
    # nothing was saved (needs_clarification or a failed run).
    sections_updated: list[str] = []
    artifact_version_id: uuid.UUID | None = None
    artifact_status: str | None = None
    workflow_node_status: str
    new_review: ReviewRead | None = None
