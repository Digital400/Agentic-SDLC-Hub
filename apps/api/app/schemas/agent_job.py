import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agent_runtime import WorkPacket
from app.models.enums import AgentJobEventType, AgentJobStatus, JobFailureCategory


class StartAgentJobRequest(BaseModel):
    work_packet: WorkPacket
    idempotency_key: str | None = Field(None, description="Opt-in — a repeated submission with the same key returns the existing job instead of creating a duplicate (see AgentJobService.create_job).")
    triggered_by_user_id: uuid.UUID | None = Field(None, description="None permitted for a system-triggered job.")
    handler_name: str = Field("default", description="Which registered job handler executes this WorkPacket — see app/services/agent_jobs/dispatcher.py.")


class AgentJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    idempotency_key: str | None
    project_id: uuid.UUID
    story_id: uuid.UUID | None
    task_type: str
    status: AgentJobStatus
    dispatcher_backend: str | None
    failure_category: JobFailureCategory | None
    error_message: str | None
    retry_count: int
    continuation_of_job_id: uuid.UUID | None
    cancellation_requested: bool
    heartbeat_at: datetime | None
    queued_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    result: dict[str, Any] | None
    created_by_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class AgentJobEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    sequence: int
    event_type: AgentJobEventType
    payload: dict[str, Any]
    created_at: datetime


class CancelAgentJobRequest(BaseModel):
    requested_by_user_id: uuid.UUID


class ContinueAgentJobRequest(BaseModel):
    """Resumes a WAITING_INPUT/WAITING_APPROVAL job with a NEW
    continuation job (see AgentJobService.create_continuation — never an
    in-place resume)."""

    updated_work_packet: WorkPacket = Field(..., description="Typically the original job's own WorkPacket with the human's answer merged into its objective/extensions.")
    triggered_by_user_id: uuid.UUID | None = None
    idempotency_key: str | None = None
