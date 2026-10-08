from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import PRReviewRecommendation, PRReviewRunStatus


class StartPRReviewRunRequest(BaseModel):
    implementation_task_id: uuid.UUID = Field(..., description="Task with a latest ACCEPTED run and a created PR.")
    triggered_by_user_id: uuid.UUID = Field(..., description="Existing user id — attributes this run.")


class FindingRead(BaseModel):
    file: str
    detail: str


class SuggestedCommentRead(BaseModel):
    file: str
    body: str


class PostedCommentRead(BaseModel):
    file: str
    body: str
    github_comment_id: int
    github_comment_url: str
    posted_at: str


class PRReviewRunRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    workflow_node_id: uuid.UUID | None  # null for a story-scoped run — see the model's own docstring
    implementation_task_id: uuid.UUID
    implementation_run_id: uuid.UUID
    pull_request_link_id: uuid.UUID
    story_id: uuid.UUID | None
    triggered_by_user_id: uuid.UUID | None
    status: PRReviewRunStatus
    overall_recommendation: PRReviewRecommendation | None
    summary: str
    critical_findings: list[FindingRead]
    major_findings: list[FindingRead]
    minor_findings: list[FindingRead]
    missing_tests: list[str]
    unrelated_changes: list[str]
    suggested_comments: list[SuggestedCommentRead]
    risk_score: int | None
    final_reviewer_note: str
    posted_comments: list[PostedCommentRead]
    used_mock: bool
    token_usage: dict | None
    cost: float | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_run(cls, run) -> "PRReviewRunRead":
        return cls(
            id=run.id, project_id=run.project_id, workflow_node_id=run.workflow_node_id,
            implementation_task_id=run.implementation_task_id, implementation_run_id=run.implementation_run_id,
            pull_request_link_id=run.pull_request_link_id, story_id=run.story_id,
            triggered_by_user_id=run.triggered_by_user_id,
            status=run.status, overall_recommendation=run.overall_recommendation, summary=run.summary,
            critical_findings=[FindingRead.model_validate(f) for f in run.critical_findings],
            major_findings=[FindingRead.model_validate(f) for f in run.major_findings],
            minor_findings=[FindingRead.model_validate(f) for f in run.minor_findings],
            missing_tests=run.missing_tests,
            unrelated_changes=run.unrelated_changes,
            suggested_comments=[SuggestedCommentRead.model_validate(c) for c in run.suggested_comments],
            risk_score=run.risk_score, final_reviewer_note=run.final_reviewer_note,
            posted_comments=[PostedCommentRead.model_validate(c) for c in run.posted_comments],
            used_mock=run.used_mock, token_usage=run.token_usage, cost=run.cost, error_message=run.error_message,
            started_at=run.started_at, completed_at=run.completed_at, created_at=run.created_at, updated_at=run.updated_at,
        )


class PostCommentRequestItem(BaseModel):
    file: str = ""
    body: str = Field(..., min_length=1, description="Final, possibly human-edited comment text to post as-is.")


class PostPRReviewCommentsRequest(BaseModel):
    triggered_by_user_id: uuid.UUID = Field(..., description="Existing user id — who triggered posting.")
    comments: list[PostCommentRequestItem] = Field(..., min_length=1)


class PostedCommentResultRead(BaseModel):
    file: str
    body: str
    status: str  # "posted" | "failed"
    github_comment_id: int | None = None
    github_comment_url: str | None = None
    error: str | None = None


class PostPRReviewCommentsResponse(BaseModel):
    run: PRReviewRunRead
    results: list[PostedCommentResultRead]


class SendBackForReworkRequest(BaseModel):
    triggered_by_user_id: uuid.UUID = Field(..., description="Existing user id — who is sending this lane back.")
    target_node_key: str = Field(
        default="IMPLEMENTATION",
        description="Which lane node to reopen — 'IMPLEMENTATION' or 'IMPLEMENTATION_PLAN'.",
    )
