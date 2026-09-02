from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ImplementationRunReviewStatus, ImplementationRunStatus, PullRequestStatus


class StartImplementationRunRequest(BaseModel):
    implementation_task_id: uuid.UUID = Field(..., description="Existing, PENDING ImplementationTask.")
    triggered_by_user_id: uuid.UUID = Field(..., description="Existing user id — attributes this run.")


class ProposedFileChangeRead(BaseModel):
    path: str
    change_type: str
    summary: str
    after_content: str | None = None


class PullRequestLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    workflow_node_id: uuid.UUID | None  # null for a story-scoped PR — see the model's own docstring
    implementation_task_id: uuid.UUID
    implementation_run_id: uuid.UUID
    repository_id: uuid.UUID
    branch_name: str
    base_branch: str
    pr_number: int
    pr_url: str
    status: PullRequestStatus
    created_by_agent: bool
    triggered_by_user_id: uuid.UUID | None
    commit_message: str
    created_at: datetime


class ImplementationRunRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    implementation_task_id: uuid.UUID
    repository_snapshot_id: uuid.UUID | None
    triggered_by_user_id: uuid.UUID | None
    story_id: uuid.UUID | None
    lane_id: uuid.UUID | None
    story_lld_artifact_id: uuid.UUID | None
    assigned_user_id: uuid.UUID | None
    agent_type: str
    assigned_agent_key: str | None
    status: ImplementationRunStatus
    proposed_file_changes: list[ProposedFileChangeRead]
    diff_text: str
    explanation: str
    test_command: str
    risks: list[str]
    pr_description: str
    used_mock: bool
    token_usage: dict | None
    cost: float | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    review_status: ImplementationRunReviewStatus
    reviewed_by_user_id: uuid.UUID | None
    reviewed_at: datetime | None
    review_comment: str | None
    created_at: datetime
    updated_at: datetime
    # Populated only by GET (an existing run) and create_pull_request
    # (the one just created) — start/review never attach one, since a PR
    # can only ever exist after both of those have already happened.
    pull_request: PullRequestLinkRead | None = None

    @classmethod
    def from_orm_run(cls, run, pull_request=None) -> "ImplementationRunRead":
        return cls(
            id=run.id, project_id=run.project_id, implementation_task_id=run.implementation_task_id,
            repository_snapshot_id=run.repository_snapshot_id, triggered_by_user_id=run.triggered_by_user_id,
            story_id=run.story_id, lane_id=run.lane_id, story_lld_artifact_id=run.story_lld_artifact_id,
            assigned_user_id=run.assigned_user_id,
            agent_type=run.agent_type, assigned_agent_key=run.assigned_agent_key, status=run.status,
            proposed_file_changes=[ProposedFileChangeRead.model_validate(c) for c in run.proposed_file_changes],
            diff_text=run.diff_text, explanation=run.explanation, test_command=run.test_command, risks=run.risks,
            pr_description=run.pr_description,
            used_mock=run.used_mock, token_usage=run.token_usage, cost=run.cost, error_message=run.error_message,
            started_at=run.started_at, completed_at=run.completed_at, review_status=run.review_status,
            reviewed_by_user_id=run.reviewed_by_user_id, reviewed_at=run.reviewed_at, review_comment=run.review_comment,
            created_at=run.created_at, updated_at=run.updated_at,
            pull_request=PullRequestLinkRead.model_validate(pull_request) if pull_request else None,
        )


class ReviewImplementationRunRequest(BaseModel):
    decision: Literal["ACCEPTED", "REJECTED"]
    reviewed_by_user_id: uuid.UUID = Field(..., description="Existing user id — who made this review decision.")
    comment: str | None = None


class CreatePullRequestRequest(BaseModel):
    triggered_by_user_id: uuid.UUID = Field(..., description="Existing user id — who triggered PR creation.")
    base_branch: str | None = Field(
        default=None, description="Defaults to the repository's default_branch. Never itself written to."
    )
