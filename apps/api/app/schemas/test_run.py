from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import TestAgentType, TestRunStatus


class StartTestRunRequest(BaseModel):
    implementation_task_id: uuid.UUID = Field(..., description="Task with a latest ACCEPTED ImplementationRun.")
    agent_type: TestAgentType
    triggered_by_user_id: uuid.UUID = Field(..., description="Existing user id — attributes this run.")
    reviewer_id: uuid.UUID = Field(..., description="Existing user id — who the opened QA review is against.")


class TestToAddRead(BaseModel):
    name: str
    description: str
    area: str


class TestExecutedRead(BaseModel):
    name: str
    result: str
    notes: str


class TestRunRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    workflow_node_id: uuid.UUID
    implementation_task_id: uuid.UUID
    implementation_run_id: uuid.UUID
    pull_request_link_id: uuid.UUID | None
    artifact_id: uuid.UUID | None
    artifact_version_id: uuid.UUID | None
    triggered_by_user_id: uuid.UUID | None
    agent_type: TestAgentType
    status: TestRunStatus
    test_plan: str
    tests_to_add: list[TestToAddRead]
    tests_executed: list[TestExecutedRead]
    pass_count: int
    fail_count: int
    bugs_found: list[str]
    suggested_fixes: list[str]
    coverage_impact: dict
    used_mock: bool
    token_usage: dict | None
    cost: float | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Populated once the run's test_report Artifact opens a Review — QA
    # approves at the existing /reviews/{id} screen, not here.
    review_id: uuid.UUID | None = None

    @classmethod
    def from_orm_run(cls, run, review_id: uuid.UUID | None = None) -> "TestRunRead":
        return cls(
            id=run.id, project_id=run.project_id, workflow_node_id=run.workflow_node_id,
            implementation_task_id=run.implementation_task_id, implementation_run_id=run.implementation_run_id,
            pull_request_link_id=run.pull_request_link_id, artifact_id=run.artifact_id,
            artifact_version_id=run.artifact_version_id, triggered_by_user_id=run.triggered_by_user_id,
            agent_type=run.agent_type, status=run.status, test_plan=run.test_plan,
            tests_to_add=[TestToAddRead.model_validate(t) for t in run.tests_to_add],
            tests_executed=[TestExecutedRead.model_validate(t) for t in run.tests_executed],
            pass_count=run.pass_count, fail_count=run.fail_count, bugs_found=run.bugs_found,
            suggested_fixes=run.suggested_fixes, coverage_impact=run.coverage_impact, used_mock=run.used_mock,
            token_usage=run.token_usage, cost=run.cost, error_message=run.error_message,
            started_at=run.started_at, completed_at=run.completed_at, created_at=run.created_at, updated_at=run.updated_at,
            review_id=review_id,
        )
