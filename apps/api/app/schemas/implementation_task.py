import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ImplementationTaskArea, ImplementationTaskRiskLevel, ImplementationTaskStatus
from app.schemas.review import ReviewRead


class ImplementationTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    story_id: uuid.UUID | None
    workflow_node_id: uuid.UUID | None
    artifact_id: uuid.UUID | None
    artifact_version_id: uuid.UUID | None
    repository_id: uuid.UUID | None
    title: str
    description: str
    linked_story: str | None
    linked_lld_section: str | None
    area: ImplementationTaskArea
    expected_paths: list[str]
    dependencies: list[str]
    acceptance_criteria: list[str]
    test_expectation: str
    risk_level: ImplementationTaskRiskLevel
    assigned_agent_type: str
    status: ImplementationTaskStatus
    order_index: int
    created_at: datetime
    updated_at: datetime


class UpdateImplementationTaskRepositoryRequest(BaseModel):
    """Body for PATCH /implementation-tasks/{id}/repository — assigns which
    of the project's (possibly several) connected repositories this task's
    code changes target. `repository_id: null` resets it back to "use the
    project's primary repository" (the default every task already had
    before multi-repo support existed)."""

    repository_id: uuid.UUID | None = Field(
        default=None, description="One of the task's own project's Repository ids, or null to use the project's primary repository."
    )


class GenerateImplementationPlanRequest(BaseModel):
    triggered_by_user_id: uuid.UUID = Field(
        ..., description="Existing user id — attributes the generated plan's artifact version."
    )
    reviewer_id: uuid.UUID = Field(..., description="Existing user id — who the resubmitted review is opened against.")


class GenerateImplementationPlanResponse(BaseModel):
    """Rule 6 ("Generate Implementation Plan") — one action that produces
    the plan artifact, its structured ImplementationTask rows, and
    resubmits it for review in a single step (see
    app/services/implementation_planner.py). Rule 7 ("Approve
    Implementation Plan") is deliberately NOT a field/action here — it's
    the existing POST /reviews/{id}/approve against `review` below, the
    same review-decision endpoint every other stage already uses."""

    artifact_id: uuid.UUID
    artifact_version_id: uuid.UUID
    tasks: list[ImplementationTaskRead]
    review: ReviewRead
