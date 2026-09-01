from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import MaintenanceRunStatus


class StartMaintenanceRunRequest(BaseModel):
    project_id: uuid.UUID = Field(..., description="Project with an approved deployment_record.")
    triggered_by_user_id: uuid.UUID = Field(..., description="Existing user id — attributes this run.")
    error_logs: str | None = Field(default=None, description="Optional — error logs, if any are available.")
    user_feedback: str | None = Field(default=None, description="Optional — user feedback, if any is available.")


class MaintenanceRunRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    workflow_node_id: uuid.UUID
    triggered_by_user_id: uuid.UUID | None
    status: MaintenanceRunStatus
    error_logs_input: str | None
    user_feedback_input: str | None
    report_markdown: str | None
    artifact_id: uuid.UUID | None
    artifact_version_id: uuid.UUID | None
    used_mock: bool
    token_usage: dict | None
    cost: float | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_run(cls, run) -> "MaintenanceRunRead":
        return cls(
            id=run.id, project_id=run.project_id, workflow_node_id=run.workflow_node_id,
            triggered_by_user_id=run.triggered_by_user_id, status=run.status,
            error_logs_input=run.error_logs_input, user_feedback_input=run.user_feedback_input,
            report_markdown=run.report_markdown, artifact_id=run.artifact_id, artifact_version_id=run.artifact_version_id,
            used_mock=run.used_mock, token_usage=run.token_usage, cost=run.cost, error_message=run.error_message,
            started_at=run.started_at, completed_at=run.completed_at, created_at=run.created_at, updated_at=run.updated_at,
        )
